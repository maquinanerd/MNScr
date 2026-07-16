import unittest
from datetime import datetime, timedelta

from app.store import Database


def _insert_seen_article(db: Database, *, source_id: str, external_id: str, status: str, retry_at=None, url_suffix: str):
    cursor = db._get_cursor()
    cursor.execute(
        """
        INSERT INTO seen_articles (
            source_id,
            external_id,
            url,
            canonical_url,
            normalized_title,
            origin,
            topic,
            status,
            retry_at,
            published_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            source_id,
            external_id,
            f"https://example.com/{url_suffix}",
            f"https://example.com/{url_suffix}",
            f"title-{external_id}",
            "fallback",
            "test",
            status,
            retry_at,
            datetime.utcnow(),
        ),
    )
    db.conn.commit()


def _ensure_article_queue_table(db: Database):
    cursor = db._get_cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS article_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            article_db_id INTEGER,
            payload TEXT NOT NULL,
            inserted_at DATETIME DEFAULT (strftime('%Y-%m-%d %H:%M:%f', 'now'))
        )
        """
    )
    cursor.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_article_queue_article_db_id "
        "ON article_queue(article_db_id) WHERE article_db_id IS NOT NULL"
    )
    db.conn.commit()


class GetArticlesToProcessTests(unittest.TestCase):
    def test_includes_queued_and_respects_deferred_retry(self):
        db = Database(":memory:")
        try:
            db.initialize()
            _ensure_article_queue_table(db)

            source_id = "test-source"
            ready_retry_at = datetime.utcnow() - timedelta(minutes=5)
            future_retry_at = datetime.utcnow() + timedelta(minutes=5)

            _insert_seen_article(
                db,
                source_id=source_id,
                external_id="new-1",
                status="NEW",
                retry_at=None,
                url_suffix="new-1",
            )
            _insert_seen_article(
                db,
                source_id=source_id,
                external_id="queued-1",
                status="QUEUED",
                retry_at=None,
                url_suffix="queued-1",
            )
            _insert_seen_article(
                db,
                source_id=source_id,
                external_id="deferred-ready-1",
                status="DEFERRED",
                retry_at=ready_retry_at,
                url_suffix="deferred-ready-1",
            )
            _insert_seen_article(
                db,
                source_id=source_id,
                external_id="deferred-future-1",
                status="DEFERRED",
                retry_at=future_retry_at,
                url_suffix="deferred-future-1",
            )

            articles = db.get_articles_to_process(source_id, limit=10)
            returned_statuses = {article["external_id"]: article["status"] for article in articles}

            self.assertEqual(returned_statuses["new-1"], "NEW")
            self.assertEqual(returned_statuses["queued-1"], "QUEUED")
            self.assertEqual(returned_statuses["deferred-ready-1"], "DEFERRED")
            self.assertNotIn("deferred-future-1", returned_statuses)
        finally:
            if db.conn:
                db.conn.close()


if __name__ == "__main__":
    unittest.main()
