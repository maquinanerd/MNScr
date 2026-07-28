"""Score estrutural: diagnostico, nunca aprovacao.

Na MS-1 o score alimenta warnings do draft. Nao existe mais gate de indexacao.
"""

from app.pipeline import assess_content_quality
from app.policy_engine import calculate_dynamic_word_policy, decide_draft_status


def _article_html() -> str:
    return (
        "<p>Primeiro paragrafo com o fato principal e a entidade central da materia.</p>"
        "<h2>O que muda</h2>"
        "<p>Segundo paragrafo com contexto editorial suficiente para a leitura.</p>"
        "<h3>Detalhe</h3>"
        "<p><a href=\"https://cinerie.example/a/\">A</a> "
        "<a href=\"https://cinerie.example/b/\">B</a></p>"
    )


def test_short_news_that_meets_word_policy_scores_above_zero():
    policy = calculate_dynamic_word_policy(source_words=400, article_type="news")
    quality = assess_content_quality(_article_html(), word_policy=policy)

    assert quality["score"] > 0
    assert "score_breakdown" in quality
    assert quality["word_count"] > 0


def test_score_never_decides_publication():
    """Score baixo nao impede a geracao do draft: vira warning para o editor."""
    policy = calculate_dynamic_word_policy(source_words=2000, article_type="news")
    weak_html = "<p>Texto muito curto.</p>"
    quality = assess_content_quality(weak_html, word_policy=policy)

    assert quality["score"] < 45

    decision = decide_draft_status(
        content_html=weak_html,
        title="Titulo valido da materia",
        structural_score=quality["score"],
    )
    assert decision["allowed"] is True


def test_quality_result_has_no_index_decision():
    quality = assess_content_quality(_article_html())
    assert "should_index" not in quality
    assert "robots" not in quality
    assert "noindex_value" not in quality
