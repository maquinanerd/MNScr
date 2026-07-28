from inspect import getsource

import pytest

from app import pipeline
from app.store import Database
from app.task_queue import ArticleQueue


@pytest.mark.parametrize("path", ["single", "cluster"])
def test_claim_survives_article_reconstruction_and_reaches_save(tmp_path, path):
    db_path = tmp_path / f"{path}.db"
    db = Database(str(db_path))
    db.initialize()
    claim_owner = f"4321:worker:{path}-claim"
    cursor = db.conn.execute(
        """
        INSERT INTO seen_articles (
            source_id, external_id, url, status, fail_count
        )
        VALUES (?, ?, ?, 'NEW', 0)
        """,
        (
            f"claim-{path}",
            f"article-{path}",
            f"https://example.com/{path}",
        ),
    )
    db.conn.commit()
    article_id = int(cursor.lastrowid)
    queue = ArticleQueue(str(db_path))
    queue.push({
        "db_id": article_id,
        "source_id": f"claim-{path}",
        "url": f"https://example.com/{path}",
    })
    claimed_payload = queue.pop_claimed(claim_owner)
    assert claimed_payload is not None

    if path == "cluster":
        rebuilt = {
            "db_id": article_id,
            "source_id": claimed_payload["source_id"],
            "is_cluster": True,
            "cluster_item": {"event_key": "event-test"},
            "extracted": {"content": "cluster content"},
        }
    else:
        rebuilt = {
            "db_id": article_id,
            "source_id": claimed_payload["source_id"],
            "extracted": {"content": "single content"},
        }

    art_data = pipeline._preserve_claim_metadata(claimed_payload, rebuilt)
    saved = db.record_draft_generated(
        article_id,
        draft_id="draft-test",
        draft_output_hash="h" * 64,
        claimed_by=claimed_payload["_claim_owner"],
    )

    assert art_data["_claim_owner"] == claim_owner
    assert saved is True
    assert db.get_article_status(article_id) == "DRAFT_GENERATED"


def test_all_extracted_article_rebuild_sites_preserve_claim_metadata():
    source = getsource(pipeline.process_batch)

    assert source.count(
        "extracted_articles.append(_preserve_claim_metadata(article_data, {"
    ) == 2
