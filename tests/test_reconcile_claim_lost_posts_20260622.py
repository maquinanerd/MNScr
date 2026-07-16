import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "reconcile_claim_lost_posts_20260622.py"
)
SPEC = importlib.util.spec_from_file_location("claim_reconciliation", SCRIPT_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def _database(tmp_path, rows):
    path = tmp_path / "app.db"
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE seen_articles (
            id INTEGER PRIMARY KEY,
            status TEXT,
            fail_reason TEXT,
            retry_at TEXT,
            fail_count INTEGER DEFAULT 0,
            claimed_by TEXT,
            claimed_at TEXT
        );
        CREATE TABLE posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            seen_article_id INTEGER,
            wp_post_id INTEGER,
            published_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    for item in rows:
        conn.execute(
            """
            INSERT INTO seen_articles (
                id, status, fail_reason, retry_at, fail_count, claimed_by, claimed_at
            )
            VALUES (?, 'PROCESSING', 'old failure', '2099-01-01', 2, ?, '2026-06-23')
            """,
            (item.seen_article_id, item.expected_claimed_by),
        )
    conn.commit()
    return conn


def test_reconcile_inserts_posts_and_publishes_articles(tmp_path):
    rows = MODULE.INCIDENT_ROWS[:2]
    conn = _database(tmp_path, rows)

    result = MODULE.reconcile(conn, rows)

    assert result == {"inserted": 2, "updated": 2, "already_reconciled": 0}
    articles = conn.execute(
        "SELECT status, fail_count, claimed_by, claimed_at FROM seen_articles ORDER BY id"
    ).fetchall()
    assert all(row["status"] == "PUBLISHED" for row in articles)
    assert all(row["fail_count"] == 0 for row in articles)
    assert all(row["claimed_by"] is None for row in articles)
    assert all(row["claimed_at"] is None for row in articles)
    posts = conn.execute(
        "SELECT seen_article_id, wp_post_id FROM posts ORDER BY seen_article_id"
    ).fetchall()
    assert [(row[0], row[1]) for row in posts] == sorted(
        (item.seen_article_id, item.wp_post_id) for item in rows
    )


def test_reconcile_is_idempotent(tmp_path):
    rows = MODULE.INCIDENT_ROWS[:2]
    conn = _database(tmp_path, rows)
    MODULE.reconcile(conn, rows)

    result = MODULE.reconcile(conn, rows)

    assert result == {"inserted": 0, "updated": 0, "already_reconciled": 2}
    assert conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0] == 2


def test_reconcile_rolls_back_everything_if_claim_changed(tmp_path):
    rows = MODULE.INCIDENT_ROWS[:2]
    conn = _database(tmp_path, rows)
    conn.execute(
        "UPDATE seen_articles SET claimed_by = 'another-owner' WHERE id = ?",
        (rows[1].seen_article_id,),
    )
    conn.commit()

    with pytest.raises(RuntimeError, match="claim changed"):
        MODULE.reconcile(conn, rows)

    assert conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0] == 0
    statuses = conn.execute(
        "SELECT status FROM seen_articles ORDER BY id"
    ).fetchall()
    assert [row["status"] for row in statuses] == ["PROCESSING", "PROCESSING"]
