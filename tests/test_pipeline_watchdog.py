from datetime import datetime, timedelta
from inspect import getsource

from app import pipeline
from app.store import Database


def _insert_claimed_article(db: Database, *, fail_count: int = 0) -> tuple[int, str]:
    claim_owner = "1234:worker:test-claim"
    cursor = db.conn.execute(
        """
        INSERT INTO seen_articles (
            source_id, external_id, url, status, fail_count, claimed_by, claimed_at
        )
        VALUES (?, ?, ?, 'PROCESSING', ?, ?, ?)
        """,
        (
            "watchdog-test",
            f"article-{datetime.utcnow().timestamp()}",
            "https://example.com/story",
            fail_count,
            claim_owner,
            datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S.%f"),
        ),
    )
    db.conn.commit()
    return int(cursor.lastrowid), claim_owner


def test_watchdog_timeout_keeps_live_claim_and_original_worker_can_save(tmp_path, monkeypatch):
    db = Database(str(tmp_path / "watchdog.db"))
    db.initialize()
    article_id, claim_owner = _insert_claimed_article(db)
    monkeypatch.setattr(pipeline, "Database", lambda: db)
    monkeypatch.setattr(db, "close", lambda: None)

    should_requeue = pipeline._handle_watchdog_timeout(
        {"db_id": article_id, "_claim_owner": claim_owner}
    )
    saved = db.record_draft_generated(
        article_id,
        draft_id="draft-watchdog",
        draft_output_hash="h" * 64,
        claimed_by=claim_owner,
    )

    row = db.conn.execute(
        """
        SELECT status, fail_count, fail_reason, claimed_by, claimed_at
        FROM seen_articles WHERE id = ?
        """,
        (article_id,),
    ).fetchone()
    assert should_requeue is False
    assert saved is True
    assert row["status"] == "DRAFT_GENERATED"
    assert row["fail_count"] == 0
    assert row["fail_reason"] is None
    assert row["claimed_by"] is None
    assert row["claimed_at"] is None


def test_stale_recovery_during_live_processing_preserves_claim_and_save(tmp_path, monkeypatch):
    db = Database(str(tmp_path / "watchdog.db"))
    db.initialize()
    article_id, claim_owner = _insert_claimed_article(db)
    stale_claimed_at = (datetime.utcnow() - timedelta(minutes=30)).strftime(
        "%Y-%m-%d %H:%M:%S.%f"
    )
    db.conn.execute(
        "UPDATE seen_articles SET claimed_at = ? WHERE id = ?",
        (stale_claimed_at, article_id),
    )
    db.conn.commit()
    monkeypatch.setattr(db, "_pid_is_alive", lambda pid: True)

    recovery = db.recover_stale_processing_claims(stale_seconds=60, max_recoveries=5)
    claimed_row = db.conn.execute(
        "SELECT status, claimed_by FROM seen_articles WHERE id = ?",
        (article_id,),
    ).fetchone()
    saved = db.record_draft_generated(
        article_id,
        draft_id="draft-watchdog",
        draft_output_hash="h" * 64,
        claimed_by=claim_owner,
    )

    row = db.conn.execute(
        """
        SELECT status, fail_count, fail_reason, claimed_by, claimed_at
        FROM seen_articles WHERE id = ?
        """,
        (article_id,),
    ).fetchone()
    assert recovery == {
        "draft_recovered": 0,
        "requeued": 0,
        "failed_permanent": 0,
        "still_alive": 1,
    }
    assert claimed_row["status"] == "PROCESSING"
    assert claimed_row["claimed_by"] == claim_owner
    assert saved is True
    assert row["status"] == "DRAFT_GENERATED"
    assert row["fail_count"] == 0
    assert row["claimed_by"] is None
    assert row["claimed_at"] is None


def test_zombie_thread_cannot_save_post_after_dead_owner_recovery_and_reclaim(
    tmp_path, monkeypatch
):
    db = Database(str(tmp_path / "watchdog.db"))
    db.initialize()
    article_id, claim_owner = _insert_claimed_article(db)
    stale_claimed_at = (datetime.utcnow() - timedelta(minutes=30)).strftime(
        "%Y-%m-%d %H:%M:%S.%f"
    )
    db.conn.execute(
        "UPDATE seen_articles SET claimed_at = ? WHERE id = ?",
        (stale_claimed_at, article_id),
    )
    db.conn.commit()
    monkeypatch.setattr(db, "_pid_is_alive", lambda pid: False)

    recovery = db.recover_stale_processing_claims(stale_seconds=60, max_recoveries=5)
    new_claim_owner = "1234:worker:new-claim"
    db.conn.execute(
        """
        UPDATE seen_articles
        SET status = 'PROCESSING', claimed_by = ?, claimed_at = ?
        WHERE id = ?
        """,
        (
            new_claim_owner,
            datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S.%f"),
            article_id,
        ),
    )
    db.conn.commit()
    saved = db.record_draft_generated(
        article_id,
        draft_id="draft-watchdog",
        draft_output_hash="h" * 64,
        claimed_by=claim_owner,
    )

    row = db.conn.execute(
        "SELECT status, fail_count FROM seen_articles WHERE id = ?",
        (article_id,),
    ).fetchone()
    post_count = db.conn.execute(
        "SELECT COUNT(*) FROM posts WHERE seen_article_id = ?",
        (article_id,),
    ).fetchone()[0]
    assert recovery["requeued"] == 1
    assert saved is False
    assert row["status"] == "PROCESSING"
    assert row["fail_count"] == 1
    assert post_count == 0


def test_worker_creates_a_unique_owner_for_each_claim():
    source = getsource(pipeline.worker_loop)

    assert 'claim_owner = f"{worker_id}:{uuid.uuid4().hex[:12]}"' in source
    assert "article_queue.pop_claimed(claim_owner)" in source
