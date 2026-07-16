from app.html_utils import normalize_meta_description


def _text(chars: int) -> str:
    base = "Filme ganha detalhes importantes e amplia o contexto da noticia para os leitores do portal com informacoes claras."
    while len(base) < chars:
        base += " " + base
    return base[:chars].rsplit(" ", 1)[0] + "."


def test_normalize_meta_description_keeps_valid_meta():
    meta = _text(145)
    final, status = normalize_meta_description(meta, "Titulo", "Primeiro paragrafo", "Filme")

    assert final == meta
    assert status == "OK"


def test_normalize_meta_description_truncates_too_long_meta():
    meta = _text(240)
    first = _text(180)
    final, status = normalize_meta_description(meta, "Titulo", first, "Filme")

    assert status == "REGENERATED_TOO_LONG"
    assert len(final) <= 155


def test_normalize_meta_description_regenerates_too_short_meta():
    final, status = normalize_meta_description("curta", "Titulo", _text(170), "Filme")

    assert status == "REGENERATED_TOO_SHORT"
    assert len(final) >= 120
    assert len(final) <= 155


def test_normalize_meta_description_uses_focus_keyword_when_missing_from_fallback():
    final, status = normalize_meta_description("", "Titulo", _text(170).replace("Filme", "Serie"), "Filme")

    assert status == "REGENERATED_TOO_SHORT"
    assert final.lower().startswith("filme")
    assert not final.lower().startswith("filme:")
    assert len(final) <= 155


def test_normalize_meta_description_regenerates_cta_style_meta():
    meta = "Saiba todos os detalhes sobre o filme com novidades importantes para leitores do portal e contexto editorial claro."
    final, status = normalize_meta_description(meta, "Titulo", _text(170), "Filme")

    assert status == "REGENERATED_BAD_STYLE"
    assert not final.lower().startswith("saiba")
    assert len(final) <= 155


def test_normalize_meta_description_regenerates_when_equal_to_title():
    title = "Filme ganha detalhes importantes e amplia contexto para leitores do portal"
    meta = title
    final, status = normalize_meta_description(meta, title, _text(170), "Filme")

    assert status == "REGENERATED_BAD_STYLE"
    assert final != title
    assert len(final) <= 155
