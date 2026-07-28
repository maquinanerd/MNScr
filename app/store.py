#!/usr/bin/env python3
"""
Database management for the application using SQLite.
"""

import hashlib
import logging
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import PIPELINE_ORDER, RSS_FEEDS
from .feeds import canonicalize_url, normalize_title_for_match
from .sqlite_utils import connect_sqlite

logger = logging.getLogger(__name__)


def _is_superfeed_like_item(item: Dict[str, Any]) -> bool:
    """Local check to avoid importing cluster adapter into the store layer."""
    if item.get("origin") == "superfeed":
        return True
    if item.get("is_cluster") and item.get("event_key"):
        return True
    if item.get("cluster_signature"):
        return True
    if item.get("event_key") and item.get("multi_source"):
        return True
    if item.get("event_key") and item.get("urls") and int(item.get("source_count") or 0) >= 2:
        return True
    return False

@dataclass
class Article:
    """Representa um artigo para processamento."""
    wp_id: str
    title: str
    excerpt: str
    content: str
    status: str
    source_url: Optional[str] = None
    category: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

class Database:
    """Handles all database operations for the application."""

    def __init__(self, db_path: str = 'data/app.db'):
        """
        Initializes the database connection.

        Args:
            db_path: The path to the SQLite database file.
        """
        db_file = Path(db_path)
        db_file.parent.mkdir(parents=True, exist_ok=True)

        self.db_path = db_path
        self.conn = None
        try:
            self.conn = connect_sqlite(
                self.db_path,
                detect_types=sqlite3.PARSE_DECLTYPES,
                row_factory=sqlite3.Row,
            )
        except sqlite3.Error as e:
            logger.critical(f"Database connection error: {e}")
            raise
        try:
            if self._table_exists("seen_articles"):
                self._ensure_seen_articles_schema()
                self._ensure_superfeed_covered_urls_schema()
                self.conn.commit()
        except sqlite3.Error as e:
            logger.error(f"Database schema check failed: {e}", exc_info=True)

    def _get_cursor(self):
        """Returns a cursor for the database connection."""
        if not self.conn:
            raise sqlite3.Error("Database connection is not available.")
        return self.conn.cursor()

    def _table_exists(self, table_name: str) -> bool:
        """Check whether a SQLite table exists."""
        cursor = self._get_cursor()
        cursor.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table_name,),
        )
        return cursor.fetchone() is not None

    def _column_exists(self, table_name: str, column_name: str) -> bool:
        """Check whether a given column exists in a SQLite table."""
        cursor = self._get_cursor()
        cursor.execute(f"PRAGMA table_info({table_name})")
        return any(row["name"] == column_name for row in cursor.fetchall())

    def _ensure_seen_articles_schema(self):
        """Apply lightweight migrations required by the current runtime."""
        cursor = self._get_cursor()

        missing_columns = {
            "event_key": "ALTER TABLE seen_articles ADD COLUMN event_key TEXT",
            "is_cluster": "ALTER TABLE seen_articles ADD COLUMN is_cluster INTEGER DEFAULT 0",
            "cluster_size": "ALTER TABLE seen_articles ADD COLUMN cluster_size INTEGER DEFAULT 1",
            "sources_json": "ALTER TABLE seen_articles ADD COLUMN sources_json TEXT",
            "fail_count": "ALTER TABLE seen_articles ADD COLUMN fail_count INTEGER DEFAULT 0",
            "origin": "ALTER TABLE seen_articles ADD COLUMN origin TEXT DEFAULT 'fallback'",
            "topic": "ALTER TABLE seen_articles ADD COLUMN topic TEXT",
            "canonical_url": "ALTER TABLE seen_articles ADD COLUMN canonical_url TEXT",
            "normalized_title": "ALTER TABLE seen_articles ADD COLUMN normalized_title TEXT",
            "multi_source": "ALTER TABLE seen_articles ADD COLUMN multi_source INTEGER DEFAULT 0",
            "source_count": "ALTER TABLE seen_articles ADD COLUMN source_count INTEGER DEFAULT 1",
            "fonte_nome": "ALTER TABLE seen_articles ADD COLUMN fonte_nome TEXT",
            "primary_source": "ALTER TABLE seen_articles ADD COLUMN primary_source TEXT",
            "urls_json": "ALTER TABLE seen_articles ADD COLUMN urls_json TEXT",
            "first_seen": "ALTER TABLE seen_articles ADD COLUMN first_seen TEXT",
            "last_seen": "ALTER TABLE seen_articles ADD COLUMN last_seen TEXT",
            "cluster_signature": "ALTER TABLE seen_articles ADD COLUMN cluster_signature TEXT",
            "sources_used": "ALTER TABLE seen_articles ADD COLUMN sources_used TEXT",
            "sources_skipped": "ALTER TABLE seen_articles ADD COLUMN sources_skipped TEXT",
            "publication_basis": "ALTER TABLE seen_articles ADD COLUMN publication_basis TEXT",
            "featured_image_source": "ALTER TABLE seen_articles ADD COLUMN featured_image_source TEXT",
            "subtitle": "ALTER TABLE seen_articles ADD COLUMN subtitle TEXT",
            "subtitle_status": "ALTER TABLE seen_articles ADD COLUMN subtitle_status TEXT",
            "meta_desc_status": "ALTER TABLE seen_articles ADD COLUMN meta_desc_status TEXT",
            "claimed_by": "ALTER TABLE seen_articles ADD COLUMN claimed_by TEXT",
            "claimed_at": "ALTER TABLE seen_articles ADD COLUMN claimed_at TEXT",
            # MS-1: registro do draft editorial (substitui o registro de publicação)
            "draft_id": "ALTER TABLE seen_articles ADD COLUMN draft_id TEXT",
            "draft_status": "ALTER TABLE seen_articles ADD COLUMN draft_status TEXT",
            "draft_artifact_path": "ALTER TABLE seen_articles ADD COLUMN draft_artifact_path TEXT",
            "draft_output_hash": "ALTER TABLE seen_articles ADD COLUMN draft_output_hash TEXT",
            "draft_revision": "ALTER TABLE seen_articles ADD COLUMN draft_revision INTEGER DEFAULT 1",
            "draft_generated_at": "ALTER TABLE seen_articles ADD COLUMN draft_generated_at TEXT",
            "submission_destination": "ALTER TABLE seen_articles ADD COLUMN submission_destination TEXT",
            "submission_status": "ALTER TABLE seen_articles ADD COLUMN submission_status TEXT",
            "submission_error": "ALTER TABLE seen_articles ADD COLUMN submission_error TEXT",
            # MS-1: proveniência que a MS-0 apontou como calculada e descartada
            "sources_used_json": "ALTER TABLE seen_articles ADD COLUMN sources_used_json TEXT",
        }

        for column_name, sql in missing_columns.items():
            if not self._column_exists("seen_articles", column_name):
                logger.info(f"Applying SQLite migration: adding seen_articles.{column_name}")
                cursor.execute(sql)

        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_seen_articles_event_key ON seen_articles(event_key)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_seen_articles_origin_topic ON seen_articles(origin, topic)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_seen_articles_canonical_url ON seen_articles(canonical_url)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_seen_articles_cluster_signature ON seen_articles(cluster_signature)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_seen_articles_status_claimed_at "
            "ON seen_articles(status, claimed_at)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_seen_articles_draft_id ON seen_articles(draft_id)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_seen_articles_draft_status ON seen_articles(draft_status)"
        )
        self._backfill_seen_articles_runtime_metadata()

    @staticmethod
    def _parse_claim_pid(claimed_by: str | None) -> int | None:
        if not claimed_by:
            return None
        try:
            return int(str(claimed_by).split(":", 1)[0])
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _pid_is_alive(pid: int | None) -> bool:
        if not pid or pid <= 0:
            return False
        # PID reuse can make a dead claim look alive if the OS has assigned the
        # same PID to another process. That is intentionally conservative: it
        # may leave an article PROCESSING, but it avoids duplicate publication.
        if os.name == "nt":
            try:
                import ctypes

                kernel32 = ctypes.windll.kernel32
                process_query_limited_information = 0x1000
                still_active = 259
                handle = kernel32.OpenProcess(
                    process_query_limited_information,
                    False,
                    int(pid),
                )
                if not handle:
                    return False
                exit_code = ctypes.c_ulong()
                try:
                    if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                        return False
                    return exit_code.value == still_active
                finally:
                    kernel32.CloseHandle(handle)
            except Exception:
                logger.warning("[CLAIM_STALE] falha verificando pid=%s no Windows; assumindo vivo", pid)
                return True
        try:
            os.kill(pid, 0)
        except PermissionError:
            return True
        except OSError:
            return False
        return True

    def _ensure_superfeed_covered_urls_schema(self) -> None:
        """Create the Superfeed coverage table used to protect fallback feeds."""
        cursor = self._get_cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS superfeed_covered_urls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_key TEXT,
                canonical_url TEXT NOT NULL UNIQUE,
                source_url TEXT NOT NULL,
                seen_article_id INTEGER,
                status TEXT DEFAULT 'COVERED_BY_SUPERFEED_EVENT',
                created_at DATETIME DEFAULT (strftime('%Y-%m-%d %H:%M:%f', 'now')),
                updated_at DATETIME DEFAULT (strftime('%Y-%m-%d %H:%M:%f', 'now'))
            )
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_superfeed_covered_urls_canonical_url
            ON superfeed_covered_urls(canonical_url)
            """
        )

    def _backfill_seen_articles_runtime_metadata(self) -> None:
        """Backfill origin/topic/canonical metadata for legacy rows created before superfeed rollout."""
        cursor = self._get_cursor()
        rows = cursor.execute(
            """
            SELECT id, source_id, url, origin, topic, canonical_url, fonte_nome
            FROM seen_articles
            WHERE origin IS NULL
               OR origin = ''
               OR topic IS NULL
               OR topic = ''
               OR canonical_url IS NULL
               OR canonical_url = ''
               OR fonte_nome IS NULL
               OR fonte_nome = ''
            """
        ).fetchall()

        if not rows:
            return

        updated = 0
        for row in rows:
            source_id = row["source_id"]
            feed_config = RSS_FEEDS.get(source_id, {})
            origin = row["origin"] or feed_config.get("origin") or "fallback"
            topic = row["topic"] or feed_config.get("topic")
            canonical_url = row["canonical_url"] or canonicalize_url(row["url"] or "")
            fonte_nome = row["fonte_nome"] or feed_config.get("source_name")

            cursor.execute(
                """
                UPDATE seen_articles
                SET origin = ?, topic = ?, canonical_url = ?, fonte_nome = ?
                WHERE id = ?
                """,
                (origin, topic, canonical_url, fonte_nome, row["id"]),
            )
            updated += 1

        if updated:
            logger.info("Backfilled runtime metadata for %s legacy seen_articles rows.", updated)

    def save_article(self, article: Article) -> str:
        """
        Salva um artigo no banco de dados.
        Retorna o ID do artigo salvo.
        """
        cursor = self._get_cursor()
        cursor.execute("""
            INSERT INTO seen_articles (
                source_id,
                external_id,
                url,
                status,
                published_at
            ) VALUES (?, ?, ?, ?, ?)
        """, (
            "batch",  # source_id fixo para artigos em lote
            article.wp_id or str(hash(article.title)),
            article.source_url,
            article.status,
            datetime.now()
        ))
        article.wp_id = str(cursor.lastrowid)
        self.conn.commit()
        return article.wp_id

    def get_pending_articles(self, limit: int = 3) -> List[Article]:
        """
        Retorna os próximos N artigos pendentes.
        """
        cursor = self._get_cursor()
        cursor.execute("""
            SELECT id, source_id, external_id, url, status
            FROM seen_articles
            WHERE status = 'PENDING'
            ORDER BY published_at DESC
            LIMIT ?
        """, (limit,))

        articles = []
        for row in cursor.fetchall():
            articles.append(Article(
                wp_id=str(row["id"]),
                title="",  # será preenchido depois
                excerpt="",
                content="",  # será preenchido depois
                status=row["status"],
                source_url=row["url"],
                category=""
            ))
        return articles

    def initialize(self):
        """Creates the necessary tables if they don't exist."""
        logger.info("Initializing database tables if they don't exist...")
        try:
            cursor = self._get_cursor()
            # Tabela para rastrear artigos e seu estado
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS seen_articles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_id TEXT NOT NULL,
                    external_id TEXT NOT NULL,
                    url TEXT,
                    canonical_url   TEXT,
                    normalized_title TEXT,
                    origin          TEXT DEFAULT 'fallback',
                    topic           TEXT,
                    event_key       TEXT,
                    is_cluster      INTEGER DEFAULT 0,
                    multi_source    INTEGER DEFAULT 0,
                    source_count    INTEGER DEFAULT 1,
                    cluster_size    INTEGER DEFAULT 1,
                    fonte_nome      TEXT,
                    primary_source  TEXT,
                    sources_json    TEXT,
                    urls_json       TEXT,
                    cluster_signature TEXT,
                    sources_used    TEXT,
                    sources_skipped TEXT,
                    publication_basis TEXT,
                    featured_image_source TEXT,
                    subtitle        TEXT,
                    subtitle_status TEXT,
                    meta_desc_status TEXT,
                    first_seen      TEXT,
                    last_seen       TEXT,
                    published_at DATETIME,
                    inserted_at DATETIME DEFAULT (strftime('%Y-%m-%d %H:%M:%f', 'now')),
                    status TEXT DEFAULT 'NEW', -- NEW, PROCESSING, DRAFT_GENERATED, DRAFT_FAILED, FAILED, SUPERSEDED, SKIPPED
                    retry_at DATETIME,
                    fail_reason TEXT,
                    fail_count INTEGER DEFAULT 0,
                    UNIQUE(source_id, external_id)
                )
            ''')
            self._ensure_seen_articles_schema()
            self._ensure_superfeed_covered_urls_schema()
            # LEGADO (MS-1): tabela mantida apenas para leitura de dados antigos.
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS posts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    seen_article_id INTEGER,
                    wp_post_id INTEGER,
                    created_at DATETIME DEFAULT (strftime('%Y-%m-%d %H:%M:%f', 'now')),
                    FOREIGN KEY(seen_article_id) REFERENCES seen_articles(id)
                )
            ''')
            # Tabela para rastrear o estado do pipeline (qual feed processar)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS pipeline_state (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            ''')
            cursor.execute("INSERT OR IGNORE INTO pipeline_state (key, value) VALUES ('last_processed_feed_index', '-1')")

            # Tabela para rastrear o estado do circuit breaker por feed
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS feed_status (
                    source_id TEXT PRIMARY KEY,
                    consecutive_failures INTEGER NOT NULL DEFAULT 0
                )
            ''')
            for feed_id in PIPELINE_ORDER:
                cursor.execute("INSERT OR IGNORE INTO feed_status (source_id) VALUES (?)", (feed_id,))

            # Tabela para gerenciar o status e cooldown das chaves de API
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS api_key_status (
                    key_hash TEXT PRIMARY KEY,
                    api_key TEXT NOT NULL,
                    category TEXT NOT NULL,
                    is_valid BOOLEAN DEFAULT 1,
                    cooldown_until DATETIME
                )
            ''')

            # Tabela para rastrear o uso da API para o dashboard
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS api_usage (
                    api_type TEXT PRIMARY KEY,
                    usage_count INTEGER NOT NULL DEFAULT 0,
                    last_used DATETIME
                )
            ''')

            # Tabela para logs de falhas
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS failures (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_id TEXT,
                    article_url TEXT,
                    error_message TEXT,
                    failed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            self.conn.commit()
            logger.info("Database initialized successfully.")
        except sqlite3.Error as e:
            logger.error(f"Database initialization failed: {e}", exc_info=True)
            raise

    def filter_new_articles(
        self,
        source_id: str,
        items: List[Dict[str, Any]],
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        Filters a list of feed items, returning only those not already in the database.
        New articles are inserted into the 'seen_articles' table with 'NEW' status.

        Args:
            source_id: The ID of the feed source.
            items: A list of normalized feed items.

        Returns:
            A list of new articles that were added to the database.
        """
        new_articles = []
        try:
            cursor = self._get_cursor()
            for item in items:
                if limit is not None and len(new_articles) >= limit:
                    break

                ext_id = item.get("id")
                # Defensive check: if 'id' is missing, generate it from the URL.
                if not ext_id:
                    url = item.get("url") or ""
                    if url:
                        ext_id = hashlib.sha256(url.encode("utf-8")).hexdigest()
                        item["id"] = ext_id  # Add it back to the item for later use
                        logger.warning(f"Item for source '{source_id}' missing 'id'. Generated from URL: {item.get('title')}")
                    else:
                        logger.warning(f"Item for source '{source_id}' missing both 'id' and 'url', skipping: {item.get('title', 'No Title')}")
                        continue

                already_seen = False
                item_url = item.get("url") or ""
                is_superfeed_like_item = _is_superfeed_like_item(item)

                # Fallback coberto por Superfeed: bloqueia apenas a URL especifica.
                if not is_superfeed_like_item and item_url and self.is_url_covered(item_url):
                    already_seen = True
                    logger.info(
                        f"[SUPERFEED_COVERAGE] Fallback bloqueado porque URL ja foi coberta: {item_url}"
                    )

                # Dedup por event_key (clusters RSSPRIME)
                if not already_seen and item.get("is_cluster") and item.get("event_key"):
                    cursor.execute(
                        "SELECT id FROM seen_articles WHERE event_key = ?",
                        (item["event_key"],)
                    )
                    already_seen = cursor.fetchone() is not None

                # Dedup estrutural por assinatura de cluster.
                if not already_seen and item.get("cluster_signature"):
                    cursor.execute(
                        "SELECT id, status FROM seen_articles WHERE cluster_signature = ?",
                        (item["cluster_signature"],)
                    )
                    row = cursor.fetchone()
                    if row and row["status"] in ("DRAFT_GENERATED", "PUBLISHED", "REWRITTEN"):
                        already_seen = True
                        logger.info(
                            f"[CLUSTER_SIG] Bloqueado por cluster_signature={item['cluster_signature']}"
                        )

                # Dedup padrão por source_id + external_id
                if not already_seen:
                    cursor.execute(
                        "SELECT id FROM seen_articles WHERE source_id = ? AND external_id = ?",
                        (source_id, ext_id)
                    )
                    already_seen = cursor.fetchone() is not None

                if not already_seen:
                    import json as _json
                    urls = item.get("urls") or ([item.get("url")] if item.get("url") else [])
                    cursor.execute(
                        """INSERT INTO seen_articles
                           (source_id, external_id, url, canonical_url, normalized_title,
                            origin, topic, event_key, is_cluster, multi_source, source_count,
                            cluster_size, fonte_nome, primary_source, sources_json, urls_json,
                            cluster_signature, first_seen, last_seen, published_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            source_id,
                            ext_id,
                            item.get("url"),
                            item.get("canonical_url") or canonicalize_url(item.get("url") or ""),
                            item.get("normalized_title") or normalize_title_for_match(item.get("title") or ""),
                            item.get("origin", "fallback"),
                            item.get("topic"),
                            item.get("event_key"),
                            int(item.get("is_cluster", False)),
                            int(item.get("multi_source", False)),
                            int(item.get("source_count", 1) or 1),
                            item.get("cluster_size", 1),
                            item.get("fonte_nome"),
                            item.get("primary_source"),
                            _json.dumps(item.get("all_sources", [])),
                            _json.dumps(urls),
                            item.get("cluster_signature"),
                            item.get("first_seen"),
                            item.get("last_seen"),
                            item.get("published"),
                        )
                    )
                    item["db_id"] = cursor.lastrowid
                    new_articles.append(item)
            self.conn.commit()
        except sqlite3.Error as e:
            logger.error(f"Database error filtering new articles for {source_id}: {e}", exc_info=True)
            self.conn.rollback()
            return []
        except Exception as e:
            logger.error(f"Unexpected error filtering new articles for {source_id}: {e}", exc_info=True)
            self.conn.rollback()
            return []
        return new_articles

    def register_covered_urls(
        self,
        event_key: str,
        urls: list[str],
        seen_article_id: int | None = None,
    ) -> None:
        """
        Registra URLs cobertas por um cluster Superfeed para impedir republicacao futura por RSS fallback.
        """
        try:
            self._ensure_superfeed_covered_urls_schema()
            cursor = self._get_cursor()
            for source_url in urls or []:
                if not source_url:
                    continue
                canonical_url = canonicalize_url(source_url)
                if not canonical_url:
                    continue
                cursor.execute(
                    """
                    INSERT INTO superfeed_covered_urls (
                        event_key,
                        canonical_url,
                        source_url,
                        seen_article_id,
                        status,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?, 'COVERED_BY_SUPERFEED_EVENT', strftime('%Y-%m-%d %H:%M:%f', 'now'))
                    ON CONFLICT(canonical_url) DO UPDATE SET
                        event_key = excluded.event_key,
                        source_url = excluded.source_url,
                        seen_article_id = COALESCE(excluded.seen_article_id, superfeed_covered_urls.seen_article_id),
                        status = 'COVERED_BY_SUPERFEED_EVENT',
                        updated_at = strftime('%Y-%m-%d %H:%M:%f', 'now')
                    """,
                    (event_key, canonical_url, source_url, seen_article_id),
                )
            self.conn.commit()
        except sqlite3.Error as e:
            logger.error(f"Failed to register Superfeed covered URLs: {e}", exc_info=True)
            self.conn.rollback()

    def is_url_covered(self, url: str) -> bool:
        """
        Retorna True se a URL ja estiver coberta por um cluster Superfeed.
        """
        try:
            canonical_url = canonicalize_url(url)
            if not canonical_url:
                return False
            cursor = self._get_cursor()
            cursor.execute(
                """
                SELECT 1
                FROM superfeed_covered_urls
                WHERE canonical_url = ?
                  AND status = 'COVERED_BY_SUPERFEED_EVENT'
                LIMIT 1
                """,
                (canonical_url,),
            )
            return cursor.fetchone() is not None
        except sqlite3.Error as e:
            logger.error(f"Failed to check Superfeed covered URL '{url}': {e}")
            return False


    def record_draft_generated(
        self,
        article_db_id: int,
        *,
        draft_id: str,
        draft_output_hash: str,
        draft_revision: int = 1,
        artifact_path: Optional[str] = None,
        submission_destination: Optional[str] = None,
        submission_status: Optional[str] = None,
        claimed_by: Optional[str] = None,
    ) -> bool:
        """Record a generated editorial draft, guarded by the worker claim.

        This replaces ``save_processed_post``: MNScr no longer publishes, so the
        terminal success state is DRAFT_GENERATED, never PUBLISHED.
        """
        try:
            cursor = self._get_cursor()
            now = datetime.utcnow().isoformat()
            if claimed_by:
                cursor.execute(
                    """
                    UPDATE seen_articles
                    SET status = 'DRAFT_GENERATED',
                        draft_status = 'DRAFT_GENERATED',
                        draft_id = ?,
                        draft_output_hash = ?,
                        draft_revision = ?,
                        draft_artifact_path = ?,
                        draft_generated_at = ?,
                        submission_destination = ?,
                        submission_status = ?,
                        submission_error = NULL,
                        fail_reason = NULL,
                        retry_at = NULL,
                        fail_count = 0,
                        claimed_by = NULL,
                        claimed_at = NULL
                    WHERE id = ?
                      AND status = 'PROCESSING'
                      AND claimed_by = ?
                    """,
                    (
                        draft_id,
                        draft_output_hash,
                        int(draft_revision or 1),
                        artifact_path,
                        now,
                        submission_destination,
                        submission_status,
                        article_db_id,
                        claimed_by,
                    ),
                )
            else:
                cursor.execute(
                    """
                    UPDATE seen_articles
                    SET status = 'DRAFT_GENERATED',
                        draft_status = 'DRAFT_GENERATED',
                        draft_id = ?,
                        draft_output_hash = ?,
                        draft_revision = ?,
                        draft_artifact_path = ?,
                        draft_generated_at = ?,
                        submission_destination = ?,
                        submission_status = ?,
                        submission_error = NULL,
                        fail_reason = NULL,
                        retry_at = NULL,
                        fail_count = 0,
                        claimed_by = NULL,
                        claimed_at = NULL
                    WHERE id = ?
                    """,
                    (
                        draft_id,
                        draft_output_hash,
                        int(draft_revision or 1),
                        artifact_path,
                        now,
                        submission_destination,
                        submission_status,
                        article_db_id,
                    ),
                )
            if cursor.rowcount != 1:
                self.conn.rollback()
                logger.warning(
                    "[CLAIM_LOST] recusando registro do draft %s para artigo %s",
                    draft_id,
                    article_db_id,
                )
                return False
            self.conn.commit()
            logger.info(
                "Draft registrado para artigo %s (draft_id=%s destino=%s).",
                article_db_id,
                draft_id,
                submission_destination,
            )
            return True
        except sqlite3.Error as e:
            logger.error(f"Error recording draft for article DB ID {article_db_id}: {e}")
            self.conn.rollback()
            return False

    def record_draft_failure(
        self,
        article_db_id: int,
        *,
        reason: str,
        draft_id: Optional[str] = None,
        draft_output_hash: Optional[str] = None,
        draft_revision: Optional[int] = None,
        submission_destination: Optional[str] = None,
        submission_status: Optional[str] = None,
        submission_error: Optional[str] = None,
        artifact_path: Optional[str] = None,
    ) -> bool:
        """Mark an article as DRAFT_FAILED, preserving the diagnostic reason."""
        try:
            cursor = self._get_cursor()
            cursor.execute(
                """
                UPDATE seen_articles
                SET status = 'DRAFT_FAILED',
                    draft_status = 'DRAFT_FAILED',
                    draft_id = COALESCE(?, draft_id),
                    draft_output_hash = COALESCE(?, draft_output_hash),
                    draft_revision = COALESCE(?, draft_revision),
                    draft_artifact_path = COALESCE(?, draft_artifact_path),
                    submission_destination = COALESCE(?, submission_destination),
                    submission_status = COALESCE(?, submission_status),
                    submission_error = ?,
                    fail_reason = ?,
                    claimed_by = NULL,
                    claimed_at = NULL
                WHERE id = ?
                """,
                (
                    draft_id,
                    draft_output_hash,
                    int(draft_revision) if draft_revision is not None else None,
                    artifact_path,
                    submission_destination,
                    submission_status,
                    submission_error,
                    reason,
                    article_db_id,
                ),
            )
            self.conn.commit()
            logger.info(
                "[DRAFT_FAILED] db_id=%s reason=%s", article_db_id, reason,
            )
            return cursor.rowcount == 1
        except sqlite3.Error as e:
            logger.error(f"Failed to record draft failure for id {article_db_id}: {e}")
            self.conn.rollback()
            return False

    def get_pipeline_state(self, key: str) -> str | None:
        """Gets a value from the pipeline state table."""
        try:
            cursor = self._get_cursor()
            cursor.execute("SELECT value FROM pipeline_state WHERE key = ?", (key,))
            row = cursor.fetchone()
            return row['value'] if row else None
        except sqlite3.Error as e:
            logger.error(f"Failed to get pipeline state for key '{key}': {e}")
            return None

    def set_pipeline_state(self, key: str, value: str):
        """Sets a value in the pipeline state table."""
        try:
            cursor = self._get_cursor()
            cursor.execute("INSERT OR REPLACE INTO pipeline_state (key, value) VALUES (?, ?)", (key, value))
            self.conn.commit()
        except sqlite3.Error as e:
            logger.error(f"Failed to set pipeline state for key '{key}': {e}")

    def get_consecutive_failures(self, source_id: str) -> int:
        """Gets the consecutive failure count for a feed source."""
        try:
            cursor = self._get_cursor()
            cursor.execute("SELECT consecutive_failures FROM feed_status WHERE source_id = ?", (source_id,))
            row = cursor.fetchone()
            return row['consecutive_failures'] if row else 0
        except sqlite3.Error as e:
            logger.error(f"Failed to get consecutive failures for '{source_id}': {e}")
            return 0 # Assume 0 on error to avoid breaking the pipeline

    def increment_consecutive_failures(self, source_id: str):
        """Increments the consecutive failure count for a feed source."""
        try:
            cursor = self._get_cursor()
            cursor.execute(
                "UPDATE feed_status SET consecutive_failures = consecutive_failures + 1 WHERE source_id = ?",
                (source_id,)
            )
            self.conn.commit()
        except sqlite3.Error as e:
            logger.error(f"Failed to increment consecutive failures for '{source_id}': {e}")

    def reset_consecutive_failures(self, source_id: str):
        """Resets the consecutive failure count for a feed source."""
        try:
            cursor = self._get_cursor()
            cursor.execute("UPDATE feed_status SET consecutive_failures = 0 WHERE source_id = ?", (source_id,))
            self.conn.commit()
        except sqlite3.Error as e:
            logger.error(f"Failed to reset consecutive failures for '{source_id}': {e}")

    def update_article_status(
        self,
        article_id: int,
        status: str,
        retry_at: datetime | None = None,
        reason: str | None = None,
        *,
        expected_claimed_by: str | None = None,
        increment_fail_count: bool = False,
    ) -> bool:
        """Updates the status of an article in the seen_articles table."""
        try:
            cursor = self._get_cursor()
            trace_before = cursor.execute(
                "SELECT status, claimed_by FROM seen_articles WHERE id = ?",
                (article_id,),
            ).fetchone()
            if expected_claimed_by:
                if status == 'QUEUED':
                    cursor.execute(
                        """
                        UPDATE seen_articles
                        SET status = 'QUEUED',
                            retry_at = ?,
                            fail_reason = ?,
                            fail_count = fail_count + 1,
                            claimed_by = NULL,
                            claimed_at = NULL
                        WHERE id = ?
                          AND status = 'PROCESSING'
                          AND claimed_by = ?
                        """,
                        (retry_at, reason, article_id, expected_claimed_by),
                    )
                elif status == 'FAILED_PERMANENT' and increment_fail_count:
                    cursor.execute(
                        """
                        UPDATE seen_articles
                        SET status = 'FAILED_PERMANENT',
                            fail_reason = ?,
                            fail_count = fail_count + 1,
                            claimed_by = NULL,
                            claimed_at = NULL
                        WHERE id = ?
                          AND status = 'PROCESSING'
                          AND claimed_by = ?
                        """,
                        (reason, article_id, expected_claimed_by),
                    )
                else:
                    logger.error(
                        "Unsupported guarded status transition for article id %s: %s",
                        article_id,
                        status,
                    )
                    return False
                trace_after = self.conn.execute(
                    "SELECT status, claimed_by FROM seen_articles WHERE id = ?",
                    (article_id,),
                ).fetchone()
                logger.info(
                    "[TOKEN_TRACE] stage=intermediate_write caller=update_article_status "
                    "db_id=%s status_before=%r status_after=%r token_before=%r token_after=%r",
                    article_id,
                    trace_before["status"] if trace_before else None,
                    trace_after["status"] if trace_after else None,
                    trace_before["claimed_by"] if trace_before else None,
                    trace_after["claimed_by"] if trace_after else None,
                )
                self.conn.commit()
                return cursor.rowcount == 1

            if status in ('DEFERRED', 'QUEUED'):
                cursor.execute(
                    """
                    UPDATE seen_articles
                    SET status = ?,
                        retry_at = ?,
                        fail_reason = ?,
                        fail_count = fail_count + 1,
                        claimed_by = NULL,
                        claimed_at = NULL
                    WHERE id = ?
                    """,
                    (status, retry_at, reason, article_id)
                )
            else:
                terminal_statuses = {
                    'DRAFT_GENERATED', 'DRAFT_FAILED', 'PUBLISHED',
                    'FAILED', 'FAILED_PERMANENT', 'SUPERSEDED', 'SKIPPED',
                }
                if reason:
                    if status in terminal_statuses:
                        cursor.execute(
                            """
                            UPDATE seen_articles
                            SET status = ?, fail_reason = ?, claimed_by = NULL, claimed_at = NULL
                            WHERE id = ?
                            """,
                            (status, reason, article_id))
                    else:
                        cursor.execute(
                            "UPDATE seen_articles SET status = ?, fail_reason = ? WHERE id = ?",
                            (status, reason, article_id))
                else:
                    if status in terminal_statuses:
                        cursor.execute(
                            """
                            UPDATE seen_articles
                            SET status = ?, claimed_by = NULL, claimed_at = NULL
                            WHERE id = ?
                            """,
                            (status, article_id))
                    else:
                        cursor.execute("UPDATE seen_articles SET status = ? WHERE id = ?", (status, article_id))
            trace_after = self.conn.execute(
                "SELECT status, claimed_by FROM seen_articles WHERE id = ?",
                (article_id,),
            ).fetchone()
            logger.info(
                "[TOKEN_TRACE] stage=intermediate_write caller=update_article_status "
                "db_id=%s status_before=%r status_after=%r token_before=%r token_after=%r",
                article_id,
                trace_before["status"] if trace_before else None,
                trace_after["status"] if trace_after else None,
                trace_before["claimed_by"] if trace_before else None,
                trace_after["claimed_by"] if trace_after else None,
            )
            self.conn.commit()
            return cursor.rowcount == 1
        except sqlite3.Error as e:
            logger.error(f"Failed to update article status for id {article_id}: {e}")
            self.conn.rollback()
            return False

    def article_claim_is_current(self, article_id: int, claimed_by: str) -> bool:
        """Return True only if this worker still owns the article and no post exists."""
        try:
            cursor = self._get_cursor()
            cursor.execute(
                """
                SELECT status, claimed_by
                FROM seen_articles
                WHERE id = ?
                  AND draft_id IS NULL
                """,
                (article_id,),
            )
            row = cursor.fetchone()
            return bool(row and row["status"] == "PROCESSING" and row["claimed_by"] == claimed_by)
        except sqlite3.Error as e:
            logger.error(f"Failed to verify claim for article id {article_id}: {e}")
            return False

    def recover_stale_processing_claims(self, stale_seconds: int, max_recoveries: int) -> dict:
        """Recover PROCESSING rows only when the owning process is gone."""
        result = {"draft_recovered": 0, "requeued": 0, "failed_permanent": 0, "still_alive": 0}
        cutoff = (datetime.utcnow() - timedelta(seconds=stale_seconds)).strftime("%Y-%m-%d %H:%M:%S.%f")
        try:
            cursor = self._get_cursor()
            cursor.execute("BEGIN IMMEDIATE")
            cursor.execute(
                """
                UPDATE seen_articles
                SET status = 'DRAFT_GENERATED',
                    draft_status = 'DRAFT_GENERATED',
                    fail_reason = NULL,
                    retry_at = NULL,
                    claimed_by = NULL,
                    claimed_at = NULL
                WHERE status = 'PROCESSING'
                  AND draft_id IS NOT NULL
                """
            )
            result["draft_recovered"] = cursor.rowcount

            rows = cursor.execute(
                """
                SELECT id, claimed_by, claimed_at, fail_count
                FROM seen_articles
                WHERE status = 'PROCESSING'
                  AND claimed_at IS NOT NULL
                  AND claimed_at < ?
                  AND draft_id IS NULL
                """,
                (cutoff,),
            ).fetchall()

            for row in rows:
                pid = self._parse_claim_pid(row["claimed_by"])
                if self._pid_is_alive(pid):
                    result["still_alive"] += 1
                    logger.warning(
                        "[CLAIM_STALE] artigo %s stale mas pid %s ainda vivo; mantendo PROCESSING",
                        row["id"],
                        pid,
                    )
                    continue

                next_count = int(row["fail_count"] or 0) + 1
                trace_status_before = "PROCESSING"
                trace_token_before = row["claimed_by"]
                if next_count >= max_recoveries:
                    cursor.execute(
                        """
                        UPDATE seen_articles
                        SET status = 'FAILED_PERMANENT',
                            fail_count = ?,
                            fail_reason = ?,
                            claimed_by = NULL,
                            claimed_at = NULL
                        WHERE id = ?
                          AND status = 'PROCESSING'
                          AND claimed_by IS ?
                        """,
                        (
                            next_count,
                            f"Stale claim owner pid dead after {next_count} recoveries",
                            row["id"],
                            row["claimed_by"],
                        ),
                    )
                    result["failed_permanent"] += cursor.rowcount
                else:
                    cursor.execute(
                        """
                        UPDATE seen_articles
                        SET status = 'QUEUED',
                            fail_count = ?,
                            fail_reason = ?,
                            claimed_by = NULL,
                            claimed_at = NULL
                        WHERE id = ?
                          AND status = 'PROCESSING'
                          AND claimed_by IS ?
                        """,
                        (
                            next_count,
                            f"Recovered stale claim from dead pid {pid}",
                            row["id"],
                            row["claimed_by"],
                        ),
                    )
                    result["requeued"] += cursor.rowcount
                trace_after = cursor.execute(
                    "SELECT status, claimed_by FROM seen_articles WHERE id = ?",
                    (row["id"],),
                ).fetchone()
                logger.info(
                    "[TOKEN_TRACE] stage=intermediate_write "
                    "caller=recover_stale_processing_claims db_id=%s "
                    "status_before=%r status_after=%r token_before=%r token_after=%r",
                    row["id"],
                    trace_status_before,
                    trace_after["status"] if trace_after else None,
                    trace_token_before,
                    trace_after["claimed_by"] if trace_after else None,
                )

            self.conn.commit()
            return result
        except sqlite3.Error as e:
            logger.error(f"Failed to recover stale claims: {e}", exc_info=True)
            self.conn.rollback()
            return result

    def update_article_subtitle(self, article_id: int, subtitle: str, subtitle_status: str) -> None:
        """Persist normalized subtitle metadata for an article."""
        try:
            cursor = self._get_cursor()
            cursor.execute(
                """
                UPDATE seen_articles
                SET subtitle = ?, subtitle_status = ?
                WHERE id = ?
                """,
                (subtitle, subtitle_status, article_id),
            )
            self.conn.commit()
        except sqlite3.Error as e:
            logger.error(f"Failed to update subtitle for article id {article_id}: {e}")

    def update_article_meta_description_status(self, article_id: int, meta_desc_status: str) -> None:
        """Persist normalized meta description status for an article."""
        try:
            cursor = self._get_cursor()
            cursor.execute(
                """
                UPDATE seen_articles
                SET meta_desc_status = ?
                WHERE id = ?
                """,
                (meta_desc_status, article_id),
            )
            self.conn.commit()
        except sqlite3.Error as e:
            logger.error(f"Failed to update meta description status for article id {article_id}: {e}")

    def get_article_fail_count(self, article_id: int) -> int:
        """Returns the current fail_count for a given article."""
        try:
            cursor = self._get_cursor()
            cursor.execute("SELECT fail_count FROM seen_articles WHERE id = ?", (article_id,))
            row = cursor.fetchone()
            return int(row["fail_count"]) if row else 0
        except sqlite3.Error as e:
            logger.error(f"Failed to get fail_count for article id {article_id}: {e}")
            return 0

    def get_article_status(self, article_id: int) -> Optional[str]:
        """Returns the current status for a given article."""
        try:
            cursor = self._get_cursor()
            cursor.execute("SELECT status FROM seen_articles WHERE id = ?", (article_id,))
            row = cursor.fetchone()
            return str(row["status"]) if row and row["status"] is not None else None
        except sqlite3.Error as e:
            logger.error(f"Failed to get status for article id {article_id}: {e}")
            return None

    def get_articles_to_process(self, source_id: str, limit: int, clusters_only: bool = False) -> list:
        """Gets eligible pending articles for a given feed source."""
        try:
            cursor = self._get_cursor()
            now = datetime.utcnow()
            # Prioritizes deferred articles that are ready for retry, then queued and new ones.
            cluster_filter = "AND sa.is_cluster = 1" if clusters_only else ""
            cursor.execute("""
                SELECT
                    sa.id,
                    sa.source_id,
                    sa.external_id,
                    sa.url,
                    sa.canonical_url,
                    sa.normalized_title,
                    sa.origin,
                    sa.topic,
                    sa.status,
                    sa.event_key,
                    sa.is_cluster,
                    sa.multi_source,
                    sa.source_count,
                    sa.cluster_signature,
                    sa.cluster_size,
                    sa.fonte_nome,
                    sa.primary_source,
                    sa.sources_json,
                    sa.urls_json,
                    sa.first_seen,
                    sa.last_seen,
                    sa.published_at
                FROM seen_articles sa
                LEFT JOIN article_queue aq ON aq.article_db_id = sa.id
                WHERE
                    sa.source_id = ?
                    AND aq.article_db_id IS NULL
                    AND (
                        sa.status IN ('NEW', 'QUEUED')
                        OR (sa.status = 'DEFERRED' AND sa.retry_at < ?)
                    )
                    {cluster_filter}
                ORDER BY
                    CASE sa.status
                        WHEN 'DEFERRED' THEN 0
                        WHEN 'QUEUED' THEN 1
                        WHEN 'NEW' THEN 2
                        ELSE 2
                    END,
                    sa.published_at DESC,
                    sa.id DESC
                LIMIT ?
            """.format(cluster_filter=cluster_filter), (source_id, now, limit))
            import json as _json

            articles = []
            for row in cursor.fetchall():
                article = dict(row)
                article["all_sources"] = _json.loads(article.get("sources_json") or "[]")
                article["urls"] = _json.loads(article.get("urls_json") or "[]")
                article["additional_urls"] = [
                    url for url in article["urls"] if url and url != article.get("url")
                ]
                article["is_cluster"] = bool(article.get("is_cluster"))
                article["multi_source"] = bool(article.get("multi_source"))
                articles.append(article)
            return articles
        except sqlite3.Error as e:
            logger.error(f"Failed to get articles to process for source_id '{source_id}': {e}")
            return []

    def get_recent_superfeed_items(self, topic: str, days: int = 14) -> list:
        """Return recent superfeed items for a topic to power fallback deduplication."""
        try:
            cursor = self._get_cursor()
            cutoff = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S.%f")
            cursor.execute(
                """
                SELECT
                    id,
                    url,
                    canonical_url,
                    normalized_title,
                    event_key,
                    multi_source,
                    source_count,
                    cluster_signature,
                    fonte_nome,
                    primary_source,
                    sources_json,
                    urls_json,
                    topic,
                    origin,
                    published_at,
                    inserted_at
                FROM seen_articles
                WHERE origin = 'superfeed'
                  AND topic = ?
                  AND inserted_at >= ?
                ORDER BY inserted_at DESC
                """,
                (topic, cutoff),
            )
            import json as _json

            items = []
            for row in cursor.fetchall():
                item = dict(row)
                item["all_sources"] = _json.loads(item.get("sources_json") or "[]")
                item["urls"] = _json.loads(item.get("urls_json") or "[]")
                item["multi_source"] = bool(item.get("multi_source"))
                items.append(item)
            return items
        except sqlite3.Error as e:
            logger.error(f"Failed to get recent superfeed coverage for topic '{topic}': {e}")
            return []

    def mark_articles_superseded(self, article_ids: List[int], reason: str = "Coberto por item do superfeed") -> int:
        """Mark pending fallback items as superseded so they stop reappearing in ingestion."""
        if not article_ids:
            return 0
        try:
            cursor = self._get_cursor()
            placeholders = ",".join("?" for _ in article_ids)
            cursor.execute(
                f"""
                UPDATE seen_articles
                SET status = 'SUPERSEDED', fail_reason = ?
                WHERE id IN ({placeholders})
                  AND status IN ('NEW', 'DEFERRED')
                """,
                (reason, *article_ids),
            )
            updated = cursor.rowcount
            self.conn.commit()
            return updated
        except sqlite3.Error as e:
            logger.error(f"Failed to mark articles as superseded: {e}")
            self.conn.rollback()
            return 0

    def get_pending_fallback_articles(self, topic: str) -> list:
        """List fallback articles for a topic that are still pending and eligible to be superseded."""
        try:
            cursor = self._get_cursor()
            cursor.execute(
                """
                SELECT
                    id,
                    source_id,
                    external_id,
                    url,
                    canonical_url,
                    normalized_title,
                    origin,
                    topic,
                    status,
                    event_key,
                    is_cluster,
                    multi_source,
                    source_count,
                    cluster_signature,
                    cluster_size,
                    fonte_nome,
                    primary_source,
                    sources_json,
                    urls_json,
                    first_seen,
                    last_seen,
                    published_at
                FROM seen_articles
                WHERE COALESCE(origin, 'fallback') = 'fallback'
                  AND topic = ?
                  AND status IN ('NEW', 'DEFERRED')
                ORDER BY published_at DESC, id DESC
                """,
                (topic,),
            )
            import json as _json

            items = []
            for row in cursor.fetchall():
                item = dict(row)
                item["all_sources"] = _json.loads(item.get("sources_json") or "[]")
                item["urls"] = _json.loads(item.get("urls_json") or "[]")
                item["additional_urls"] = [
                    url for url in item["urls"] if url and url != item.get("url")
                ]
                item["is_cluster"] = bool(item.get("is_cluster"))
                item["multi_source"] = bool(item.get("multi_source"))
                items.append(item)
            return items
        except sqlite3.Error as e:
            logger.error(f"Failed to get pending fallback articles for topic '{topic}': {e}")
            return []

    def cleanup_old_entries(self, cutoff_time: datetime) -> int:
        """
        Deletes records from seen_articles and posts older than the cutoff time.
        Only deletes articles with status 'PUBLISHED' or 'FAILED'.

        Args:
            cutoff_time: The datetime threshold. Records older than this will be deleted.

        Returns:
            The number of records deleted from seen_articles.
        """
        try:
            cursor = self._get_cursor()

            # Find IDs of old articles to delete
            cursor.execute(
                "SELECT id FROM seen_articles WHERE inserted_at < ? AND status IN "
                "('DRAFT_GENERATED', 'DRAFT_FAILED', 'PUBLISHED', 'FAILED', 'SUPERSEDED')",
                (cutoff_time,)
            )
            article_ids_to_delete = [row['id'] for row in cursor.fetchall()]

            if not article_ids_to_delete:
                return 0

            placeholders = ','.join('?' for _ in article_ids_to_delete)

            cursor.execute(f"DELETE FROM posts WHERE seen_article_id IN ({placeholders})", article_ids_to_delete)
            cursor.execute(f"DELETE FROM seen_articles WHERE id IN ({placeholders})", article_ids_to_delete)

            deleted_count = cursor.rowcount
            self.conn.commit()
            return deleted_count
        except sqlite3.Error as e:
            logger.error(f"Error during database cleanup: {e}", exc_info=True)
            self.conn.rollback()
            return 0

    def close(self):
        """Closes the database connection."""
        if self.conn:
            self.conn.close()
            self.conn = None
            logger.info("Database connection closed.")
