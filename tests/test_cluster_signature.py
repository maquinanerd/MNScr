from app.feeds import cluster_signature, enrich_feed_item, normalize_item


def test_cluster_signature_is_stable_for_same_urls_in_different_order():
    urls_a = [
        "https://www.example.com/news/movie?utm_source=x",
        "https://screenrant.com/show/story/",
    ]
    urls_b = list(reversed(urls_a))

    assert cluster_signature(urls_a) == cluster_signature(urls_b)


def test_cluster_signature_uses_canonical_urls():
    urls_a = ["https://www.example.com/news/movie/"]
    urls_b = ["https://example.com/news/movie"]

    assert cluster_signature(urls_a) == cluster_signature(urls_b)


def test_cluster_signature_changes_when_url_set_changes():
    urls_a = ["https://example.com/news/movie", "https://screenrant.com/story"]
    urls_b = ["https://example.com/news/movie", "https://deadline.com/story"]

    assert cluster_signature(urls_a) != cluster_signature(urls_b)


def test_superfeed_item_gets_cluster_signature():
    raw = {
        "guid": "event-1",
        "link": "https://deadline.com/story",
        "title": "Movie story",
        "sf_event_key": "event-1",
        "sf_multi_source": "true",
        "sf_source_count": "2",
        "sf_urls": "https://deadline.com/story|https://screenrant.com/story",
    }

    item = enrich_feed_item(
        normalize_item(raw),
        {"origin": "superfeed", "topic": "movies", "source_name": "RSSPrime"},
        "rssprime_movies",
    )

    assert item["cluster_signature"] == cluster_signature(item["urls"])


def test_fallback_item_does_not_get_cluster_signature():
    raw = {
        "guid": "screenrant-1",
        "link": "https://screenrant.com/story",
        "title": "Movie story",
    }

    item = enrich_feed_item(
        normalize_item(raw),
        {"origin": "fallback", "topic": "movies", "source_name": "ScreenRant"},
        "screenrant_movies",
    )

    assert "cluster_signature" not in item
