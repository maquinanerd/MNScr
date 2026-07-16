import app.policy_engine as policy_engine


def _low_quality_article_kwargs():
    return {
        "structural_score": 1,
        "publish_allowed": True,
        "word_count": 100,
        "internal_links": 0,
        "min_acceptable_words": 500,
        "editorial_status": "WARNING_ONLY",
    }


def test_force_index_all_posts_returns_index_follow(monkeypatch):
    monkeypatch.setattr(policy_engine, "INDEX_GATE_ENABLED", True)
    monkeypatch.setattr(policy_engine, "FORCE_INDEX_ALL_POSTS", True)

    result = policy_engine.decide_index_status(**_low_quality_article_kwargs())

    assert result["robots"] == "index,follow"
    assert result["allowed"] is True
    assert result["indexable"] is True
    assert result["noindex_value"] == "0"
    assert result["failed"] == []


def test_noindex_pipeline_block_overrides_disabled_force_flag(monkeypatch):
    monkeypatch.setattr(policy_engine, "INDEX_GATE_ENABLED", True)
    monkeypatch.setattr(policy_engine, "FORCE_INDEX_ALL_POSTS", False)

    result = policy_engine.decide_index_status(**_low_quality_article_kwargs())

    assert result["robots"] == "index,follow"
    assert result["allowed"] is True
    assert result["indexable"] is True
    assert result["noindex_value"] == "0"
    assert result["failed"] == []
