from app import pipeline
from app.config import PIPELINE_ORDER, RSS_FEEDS, SOURCE_CATEGORY_MAP


def test_games_feed_removed_from_ingestion_config():
    assert "rssprime_games" not in PIPELINE_ORDER
    assert "rssprime_games" not in RSS_FEEDS
    assert "rssprime_games" not in SOURCE_CATEGORY_MAP
    from app.config import EDITORIAL_CATEGORIES
    assert "Games" not in EDITORIAL_CATEGORIES
    assert all(feed.get("topic") != "games" for feed in RSS_FEEDS.values())


def test_legacy_games_queue_item_is_blocked_before_publication():
    blocked, reason = pipeline._article_is_blocked_for_publication(
        {
            "source_id": "rssprime_games",
            "topic": "games",
            "category": "Games",
        }
    )

    assert blocked is True
    assert "blocked" in reason.lower()


def test_games_category_suggestions_are_filtered():
    candidates = ["Filmes", "Games", "Jogos", "Video Games", "Nintendo"]
    allowed = [name for name in candidates if not pipeline._is_blocked_category_name(name)]
    blocked = [name for name in candidates if pipeline._is_blocked_category_name(name)]

    assert allowed == ["Filmes", "Nintendo"]
    assert blocked == ["Games", "Jogos", "Video Games"]
