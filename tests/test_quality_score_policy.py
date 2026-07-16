from app.pipeline import assess_content_quality
from app.policy_engine import decide_index_status


def _paragraph_with_words(count: int) -> str:
    return "<p>" + " ".join(f"palavra{i}" for i in range(count)) + "</p>"


def test_short_news_that_meets_word_policy_scores_above_index_gate():
    html = (
        _paragraph_with_words(393)
        + '<p><a href="https://maquinanerd.com.br/a/">A</a> '
        + '<a href="https://maquinanerd.com.br/b/">B</a> '
        + '<a href="https://maquinanerd.com.br/c/">C</a> '
        + '<a href="https://maquinanerd.com.br/d/">D</a></p>'
    )
    word_policy = {
        "source_words": 458,
        "min_acceptable_words": 343,
        "target_words": 458,
        "max_recommended_words": 572,
    }

    quality = assess_content_quality(html, word_policy=word_policy)
    decision = decide_index_status(
        structural_score=quality["score"],
        publish_allowed=True,
        word_count=quality["word_count"],
        internal_links=quality["internal_links"],
        min_acceptable_words=word_policy["min_acceptable_words"],
        editorial_status="OK",
        qa_llm_result={"originality_score": 75},
    )

    assert quality["score"] >= 45
    assert quality["score_breakdown"]["length"]["points"] >= 20
    assert decision["robots"] == "index,follow"


def test_genuinely_weak_article_still_indexes_under_force_index_policy():
    word_policy = {
        "source_words": 458,
        "min_acceptable_words": 343,
        "target_words": 458,
        "max_recommended_words": 572,
    }
    html = _paragraph_with_words(180)

    quality = assess_content_quality(html, word_policy=word_policy)
    decision = decide_index_status(
        structural_score=quality["score"],
        publish_allowed=True,
        word_count=quality["word_count"],
        internal_links=quality["internal_links"],
        min_acceptable_words=word_policy["min_acceptable_words"],
        editorial_status="OK",
        qa_llm_result={"originality_score": 40},
    )

    assert decision["robots"] == "index,follow"
    assert decision["failed"] == []
    assert decision["reason"] == "force_index_all_posts"
