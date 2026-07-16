import sqlite3
from pathlib import Path

from app.feeds import cluster_signature
from app.store import Database


def _db(tmp_path):
    db = Database(str(tmp_path / "app.db"))
    db.initialize()
    return db


def _item(**overrides):
    base = {
        "id": "item-1",
        "url": "https://screenrant.com/movie-story/",
        "title": "Movie story",
        "published": "2026-05-04",
        "origin": "fallback",
        "topic": "movies",
        "is_cluster": False,
        "multi_source": False,
        "source_count": 1,
    }
    base.update(overrides)
    return base


def _superfeed_item(**overrides):
    urls = overrides.pop(
        "urls",
        [
            "https://deadline.com/movie-story/",
            "https://screenrant.com/movie-story/",
        ],
    )
    base = _item(
        id="sf-1",
        url=urls[0],
        origin="superfeed",
        is_cluster=True,
        event_key="event-1",
        multi_source=True,
        source_count=len(urls),
        urls=urls,
        all_sources=["deadline.com", "screenrant.com"],
        cluster_signature=cluster_signature(urls),
    )
    base.update(overrides)
    return base


def test_filter_new_articles_blocks_existing_published_cluster_signature(tmp_path):
    db = _db(tmp_path)
    try:
        first = _superfeed_item(id="sf-1", event_key="event-1")
        inserted = db.filter_new_articles("rssprime_movies", [first])
        assert len(inserted) == 1
        db.update_article_status(inserted[0]["db_id"], "PUBLISHED")

        duplicate = _superfeed_item(id="sf-2", event_key="event-2")
        assert db.filter_new_articles("rssprime_movies", [duplicate]) == []
    finally:
        db.close()


def test_register_covered_urls_saves_canonicalized_urls(tmp_path):
    db = _db(tmp_path)
    try:
        db.register_covered_urls(
            "event-1",
            ["https://www.screenrant.com/movie-story/"],
            seen_article_id=123,
        )
        row = db.conn.execute(
            "SELECT canonical_url, source_url, seen_article_id FROM superfeed_covered_urls"
        ).fetchone()

        assert row["canonical_url"] == "screenrant.com/movie-story"
        assert row["source_url"] == "https://www.screenrant.com/movie-story/"
        assert row["seen_article_id"] == 123
    finally:
        db.close()


def test_is_url_covered_true_for_registered_url(tmp_path):
    db = _db(tmp_path)
    try:
        db.register_covered_urls("event-1", ["https://screenrant.com/movie-story/"])

        assert db.is_url_covered("https://www.screenrant.com/movie-story") is True
    finally:
        db.close()


def test_is_url_covered_false_for_unregistered_url(tmp_path):
    db = _db(tmp_path)
    try:
        assert db.is_url_covered("https://screenrant.com/new-story") is False
    finally:
        db.close()


def test_rss_fallback_is_blocked_when_url_is_covered(tmp_path):
    db = _db(tmp_path)
    try:
        db.register_covered_urls("event-1", ["https://screenrant.com/movie-story/"])

        fallback = _item(id="screenrant-1", url="https://www.screenrant.com/movie-story")
        assert db.filter_new_articles("screenrant_movies", [fallback]) == []
    finally:
        db.close()


def test_screenrant_fallback_with_uncovered_url_is_allowed(tmp_path):
    db = _db(tmp_path)
    try:
        fallback = _item(id="screenrant-2", url="https://screenrant.com/new-story")
        inserted = db.filter_new_articles("screenrant_movies", [fallback])

        assert len(inserted) == 1
        assert inserted[0]["url"] == "https://screenrant.com/new-story"
    finally:
        db.close()


def test_screenrant_fallback_with_covered_url_is_blocked(tmp_path):
    db = _db(tmp_path)
    try:
        db.register_covered_urls("event-1", ["https://screenrant.com/movie-story/"])

        fallback = _item(id="screenrant-3", url="https://screenrant.com/movie-story")
        assert db.filter_new_articles("screenrant_tv", [fallback]) == []
    finally:
        db.close()


def test_superfeed_item_is_not_blocked_by_coverage_table(tmp_path):
    db = _db(tmp_path)
    try:
        db.register_covered_urls("event-old", ["https://deadline.com/movie-story/"])

        superfeed = _superfeed_item(id="sf-new", event_key="event-new")
        inserted = db.filter_new_articles("rssprime_movies", [superfeed])

        assert len(inserted) == 1
        assert inserted[0]["event_key"] == "event-new"
    finally:
        db.close()


def test_is_url_covered_returns_false_if_table_is_missing(tmp_path):
    db_path = tmp_path / "missing-table.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE placeholder (id INTEGER PRIMARY KEY)")
    conn.close()

    db = Database(str(db_path))
    try:
        assert db.is_url_covered("https://screenrant.com/movie-story") is False
    finally:
        db.close()


def test_pipeline_does_not_register_coverage_immediately_after_builder_extraction():
    source = Path("app/pipeline.py").read_text(encoding="utf-8")
    extraction_start = source.index("multi_source_payload = build_multi_source_payload")
    append_start = source.index("extracted_articles.append({", extraction_start)
    extraction_block = source[extraction_start:append_start]

    assert "register_covered_urls" not in extraction_block
    assert "extract_cluster_documents" not in source


def test_pipeline_registers_coverage_after_successful_post_persistence():
    source = Path("app/pipeline.py").read_text(encoding="utf-8")
    save_pos = source.index("published_recorded = db.save_processed_post(")
    register_pos = source.index("db.register_covered_urls", save_pos)

    assert register_pos > save_pos
    assert "[SUPERFEED_COVERAGE] URLs registradas apos sucesso editorial/publicacao" in source
