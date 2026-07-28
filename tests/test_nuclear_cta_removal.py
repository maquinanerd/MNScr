"""Remocao de CTA antes da construcao do draft.

Substitui o script legado que reimplementava a limpeza em regex proprio. Aqui o
alvo e o codigo real do pipeline (``app.html_utils``), sem rede, sem CMS e sem
execucao no import.
"""

import pytest

from app.html_utils import detect_forbidden_cta, strip_forbidden_cta_sentences

EDITORIAL_PARAGRAPH = (
    "<p>O estudio confirmou a nova temporada com estreia prevista para 2027 "
    "e retorno do elenco principal.</p>"
)

CTA_CASES = [
    ("cta_exato", "<p>Thank you for reading this post, don't forget to subscribe!</p>"),
    ("cta_lowercase", "<p>thank you for reading this post, don't forget to subscribe!</p>"),
    ("cta_sem_pontuacao", "<p>Thank you for reading this post, don't forget to subscribe</p>"),
    ("cta_parcial", "<p>Thank you for reading.</p>"),
    ("cta_thanks_for_visiting", "<p>Thanks for visiting, subscribe for more!</p>"),
    ("cta_please_subscribe", "<p>Please subscribe to our newsletter.</p>"),
    ("cta_follow_us", "<p>Follow us on social media.</p>"),
    ("cta_pt_br", "<p>Obrigado por ler, nao esqueca de se inscrever!</p>"),
]


@pytest.mark.parametrize("label, cta_html", CTA_CASES, ids=[case[0] for case in CTA_CASES])
def test_forbidden_cta_is_detected_and_removed(label, cta_html):
    content = f"<article>{EDITORIAL_PARAGRAPH}{cta_html}{EDITORIAL_PARAGRAPH}</article>"

    assert detect_forbidden_cta(content) is not None, f"CTA nao detectado em {label}"

    cleaned, removed = strip_forbidden_cta_sentences(content)

    assert removed is True
    assert detect_forbidden_cta(cleaned) is None, f"CTA sobreviveu em {label}"


@pytest.mark.parametrize("label, cta_html", CTA_CASES, ids=[case[0] for case in CTA_CASES])
def test_editorial_content_survives_cta_removal(label, cta_html):
    content = f"<article>{EDITORIAL_PARAGRAPH}{cta_html}{EDITORIAL_PARAGRAPH}</article>"

    cleaned, _ = strip_forbidden_cta_sentences(content)

    assert "O estudio confirmou a nova temporada" in cleaned
    assert "retorno do elenco principal" in cleaned


def test_multiple_ctas_are_all_removed():
    content = (
        "<article>"
        f"{EDITORIAL_PARAGRAPH}"
        "<p>Thank you for reading this post, don't forget to subscribe!</p>"
        f"{EDITORIAL_PARAGRAPH}"
        "<p>Thanks for visiting, subscribe for more!</p>"
        f"{EDITORIAL_PARAGRAPH}"
        "</article>"
    )

    cleaned, removed = strip_forbidden_cta_sentences(content)

    assert removed is True
    assert detect_forbidden_cta(cleaned) is None
    assert cleaned.count("O estudio confirmou a nova temporada") == 3


def test_ambiguous_courtesy_phrase_is_not_treated_as_a_blocking_cta():
    """"Thanks for visiting" isolado nao e CTA: so o par com "subscribe" e.

    A regra e deliberadamente conservadora para nao remover copy editorial
    legitima. A limpeza agressiva fica nos limpadores por veiculo do extrator.
    """
    content = f"<article>{EDITORIAL_PARAGRAPH}<p>Thanks for visiting.</p></article>"

    assert detect_forbidden_cta(content) is None


def test_clean_content_is_left_untouched():
    content = (
        "<article>"
        f"{EDITORIAL_PARAGRAPH}"
        "<p>A producao sera retomada em Vancouver com o mesmo showrunner.</p>"
        "</article>"
    )

    assert detect_forbidden_cta(content) is None

    cleaned, removed = strip_forbidden_cta_sentences(content)

    assert removed is False
    assert "Vancouver" in cleaned


def test_pipeline_strips_cta_before_building_the_draft():
    """A remocao de CTA precisa acontecer antes da montagem do EditorialDraft."""
    from pathlib import Path

    source = Path(__file__).resolve().parents[1].joinpath("app", "pipeline.py").read_text(
        encoding="utf-8"
    )

    idx_strip = source.index("strip_forbidden_cta_sentences(")
    idx_draft = source.index("draft = build_editorial_draft(")

    assert idx_strip < idx_draft
