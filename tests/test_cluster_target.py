from app.ai_processor import AIProcessor
from app.policy_engine import calculate_dynamic_word_policy


def _cluster_payload_from_policy(source_words: int) -> dict:
    policy = calculate_dynamic_word_policy(
        source_words=source_words,
        article_type="news",
        origin="superfeed",
        source_id="test-cluster",
    )
    return {
        "title": "Cluster de teste",
        "content_html": "<p>Texto do cluster</p>",
        "source_url": "https://example.com/cluster",
        "source_word_count": source_words,
        "target_words": policy["target_words"],
        "min_acceptable_words": policy["min_acceptable_words"],
        "max_recommended_words": policy["max_recommended_words"],
        "word_policy": policy,
    }


def test_cluster_target_min_does_not_exceed_max_and_stays_int():
    payload = _cluster_payload_from_policy(1452)

    # Fonte de 1.452 palavras: a proporcao pede 1.089 de piso e 1.307 de alvo.
    # Ate 28/08/2026 o teto de 550/1000 esmagava os tres numeros aqui.
    assert payload["target_words"] == 1306
    assert payload["min_acceptable_words"] == 1089
    assert payload["max_recommended_words"] == 1597

    fields = AIProcessor._build_prompt_fields(payload)

    assert isinstance(fields["target_min_words"], int)
    assert isinstance(fields["max_recommended_words"], int)
    assert fields["target_min_words"] == payload["min_acceptable_words"]
    assert fields["target_min_words"] <= fields["max_recommended_words"]


def test_non_cluster_target_fields_remain_consistent_and_int():
    policy = calculate_dynamic_word_policy(
        source_words=820,
        article_type="news",
        origin="fallback",
        source_id="test-news",
    )
    payload = {
        "title": "Noticia simples",
        "content_html": "<p>Texto da noticia</p>",
        "source_url": "https://example.com/news",
        "source_word_count": 820,
        "target_words": policy["target_words"],
        "min_acceptable_words": policy["min_acceptable_words"],
        "max_recommended_words": policy["max_recommended_words"],
        "word_policy": policy,
    }

    fields = AIProcessor._build_prompt_fields(payload)

    assert isinstance(fields["target_min_words"], int)
    assert isinstance(fields["max_recommended_words"], int)
    assert fields["target_min_words"] == policy["min_acceptable_words"]
    assert fields["target_min_words"] <= fields["max_recommended_words"]
