"""Persistent article queue backed by SQLite.

This keeps the same interface used by the pipeline, but survives process
restarts and avoids losing queued articles on crashes.
"""

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from .config import RSS_FEEDS
from .sqlite_utils import connect_sqlite

logger = logging.getLogger(__name__)


class ArticleQueue:
    def __init__(self, db_path: str = "data/app.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_table()

    def _get_conn(self) -> sqlite3.Connection:
        return connect_sqlite(self.db_path, row_factory=sqlite3.Row)

    def _ensure_table(self) -> None:
        with self._get_conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS article_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    article_db_id INTEGER,
                    payload TEXT NOT NULL,
                    inserted_at DATETIME DEFAULT (strftime('%Y-%m-%d %H:%M:%f', 'now'))
                )
                """
            )
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_article_queue_article_db_id "
                "ON article_queue(article_db_id) WHERE article_db_id IS NOT NULL"
            )

    @staticmethod
    def _apply_runtime_metadata(item: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(item, dict):
            return item

        source_id = item.get("source_id")
        feed_config = RSS_FEEDS.get(source_id or "", {})

        if "origin" not in item and feed_config.get("origin"):
            item["origin"] = feed_config["origin"]
        if "topic" not in item and feed_config.get("topic"):
            item["topic"] = feed_config["topic"]
        if "priority" not in item:
            item["priority"] = int(feed_config.get("priority", 0))

        return item

    @staticmethod
    def _serialize(item: Dict[str, Any]) -> tuple[Optional[int], str]:
        item = ArticleQueue._apply_runtime_metadata(dict(item))
        article_db_id = item.get("db_id") if isinstance(item, dict) else None
        payload = json.dumps(item, ensure_ascii=False)
        return article_db_id, payload

    @staticmethod
    def _deserialize(payload: str) -> Dict[str, Any]:
        item = json.loads(payload)
        return ArticleQueue._apply_runtime_metadata(item)

    def _upsert(self, conn: sqlite3.Connection, article_db_id: Optional[int], payload: str) -> None:
        if article_db_id is None:
            conn.execute(
                "INSERT INTO article_queue (article_db_id, payload) VALUES (?, ?)",
                (None, payload),
            )
            return

        existing = conn.execute(
            "SELECT id FROM article_queue WHERE article_db_id = ?",
            (article_db_id,),
        ).fetchone()

        if existing:
            conn.execute(
                "UPDATE article_queue SET payload = ? WHERE article_db_id = ?",
                (payload, article_db_id),
            )
        else:
            conn.execute(
                "INSERT INTO article_queue (article_db_id, payload) VALUES (?, ?)",
                (article_db_id, payload),
            )

    def push(self, item: Dict[str, Any]) -> None:
        article_db_id, payload = self._serialize(item)
        with self._get_conn() as conn:
            self._upsert(conn, article_db_id, payload)

    def push_many(self, items: Iterable[Dict[str, Any]]) -> None:
        serialized_items = [self._serialize(item) for item in items]
        if not serialized_items:
            return

        with self._get_conn() as conn:
            for article_db_id, payload in serialized_items:
                self._upsert(conn, article_db_id, payload)

    def pop(self) -> Optional[Dict[str, Any]]:
        """Deprecated compatibility pop without an article claim; worker_loop uses pop_claimed."""
        with self._get_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                "SELECT id, payload FROM article_queue ORDER BY id ASC"
            ).fetchall()

            if not rows:
                conn.commit()
                return None

            chosen_row = None
            chosen_payload = None
            for row in rows:
                payload = self._deserialize(row["payload"])
                if payload.get("origin") == "superfeed":
                    chosen_row = row
                    chosen_payload = payload
                    break

            if chosen_row is None:
                chosen_row = rows[0]
                chosen_payload = self._deserialize(chosen_row["payload"])

            conn.execute("DELETE FROM article_queue WHERE id = ?", (chosen_row["id"],))
            conn.commit()
            return chosen_payload

    def pop_claimed(self, worker_id: str) -> Optional[Dict[str, Any]]:
        """Atomically remove one queued item and claim its seen_articles row.

        Non-claimable queue entries are discarded as stale queue payloads. Deferred
        rows are re-enqueued from seen_articles by Database.get_articles_to_process
        once retry_at is due, so they do not need to wait inside article_queue.
        """
        with self._get_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                "SELECT id, article_db_id, payload FROM article_queue ORDER BY id ASC"
            ).fetchall()

            if not rows:
                conn.commit()
                return None

            superfeed_rows = []
            fallback_rows = []
            for row in rows:
                payload = self._deserialize(row["payload"])
                if payload.get("origin") == "superfeed":
                    superfeed_rows.append((row, payload))
                else:
                    fallback_rows.append((row, payload))

            now = datetime.utcnow()
            for row, payload in [*superfeed_rows, *fallback_rows]:
                article_db_id = payload.get("db_id") or payload.get("id") or row["article_db_id"]
                if article_db_id is None:
                    conn.execute("DELETE FROM article_queue WHERE id = ?", (row["id"],))
                    continue

                cursor = conn.execute(
                    """
                    UPDATE seen_articles
                    SET status = 'PROCESSING',
                        claimed_by = ?,
                        claimed_at = strftime('%Y-%m-%d %H:%M:%f', 'now'),
                        fail_reason = NULL
                    WHERE id = ?
                      AND NOT EXISTS (
                          SELECT 1 FROM posts WHERE posts.seen_article_id = seen_articles.id
                      )
                      AND (
                          status IN ('NEW', 'QUEUED')
                          OR (status = 'DEFERRED' AND (retry_at IS NULL OR retry_at < ?))
                      )
                    """,
                    (worker_id, int(article_db_id), now),
                )
                conn.execute("DELETE FROM article_queue WHERE id = ?", (row["id"],))
                if cursor.rowcount == 1:
                    claimed_row = conn.execute(
                        "SELECT claimed_by FROM seen_articles WHERE id = ?",
                        (int(article_db_id),),
                    ).fetchone()
                    logger.info(
                        "[TOKEN_TRACE] stage=pop db_id=%s token_generated=%r "
                        "token_in_db_after_pop=%r",
                        int(article_db_id),
                        worker_id,
                        claimed_row["claimed_by"] if claimed_row else None,
                    )
                    payload["db_id"] = int(article_db_id)
                    payload["_claim_owner"] = worker_id
                    conn.commit()
                    return payload

            conn.commit()
            return None

    def list_items(self) -> list[Dict[str, Any]]:
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT id, article_db_id, payload, inserted_at FROM article_queue ORDER BY id ASC"
            ).fetchall()
            items = []
            for row in rows:
                item = self._deserialize(row["payload"])
                item["_queue_id"] = row["id"]
                item["_queue_article_db_id"] = row["article_db_id"]
                item["_queue_inserted_at"] = row["inserted_at"]
                items.append(item)
            return items

    def count_by_origin(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        with self._get_conn() as conn:
            rows = conn.execute("SELECT payload FROM article_queue").fetchall()
            for row in rows:
                item = self._deserialize(row["payload"])
                origin = str(item.get("origin") or "fallback")
                counts[origin] = counts.get(origin, 0) + 1
        return counts

    def remove_article_ids(self, article_db_ids: Iterable[int]) -> int:
        ids = [int(article_id) for article_id in article_db_ids if article_id is not None]
        if not ids:
            return 0

        placeholders = ",".join("?" for _ in ids)
        with self._get_conn() as conn:
            cursor = conn.execute(
                f"DELETE FROM article_queue WHERE article_db_id IN ({placeholders})",
                ids,
            )
            return cursor.rowcount

    def __len__(self) -> int:
        with self._get_conn() as conn:
            row = conn.execute("SELECT COUNT(*) AS total FROM article_queue").fetchone()
            return int(row["total"]) if row else 0
