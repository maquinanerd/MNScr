import json

from app.ai_processor import AIProcessor
from app.html_utils import normalize_subtitle


def _text(chars: int) -> str:
    base = "Este resumo editorial apresenta os fatos principais da noticia com clareza e contexto para o leitor do portal."
    while len(base) < chars:
        base += " " + base
    return base[:chars].rsplit(" ", 1)[0] + "."


def test_normalize_subtitle_accepts_ideal_length_when_distinct_from_meta():
    subtitle = _text(160)
    meta = _text(150).replace("resumo editorial", "descricao meta")
    final, status = normalize_subtitle(subtitle, "Titulo diferente", "Primeiro paragrafo", meta)

    assert final == subtitle
    assert status == "OK"


def test_normalize_subtitle_regenerates_from_first_paragraph_when_missing():
    first_paragraph = _text(180)
    final, status = normalize_subtitle("", "Titulo diferente", first_paragraph)

    assert final
    assert status == "REGENERATED"
    assert len(final) <= 220


def test_normalize_subtitle_returns_missing_without_safe_fallback():
    final, status = normalize_subtitle("", "Mesmo titulo", "curto")

    assert final == ""
    assert status == "MISSING"


def test_normalize_subtitle_regenerates_when_equal_to_title():
    title = _text(120)
    first_paragraph = _text(180)
    final, status = normalize_subtitle(title, title, first_paragraph)

    assert final != title
    assert status == "REGENERATED"


def test_normalize_subtitle_regenerates_when_equal_to_meta_description():
    meta = _text(150)
    first_paragraph = _text(180).replace("resumo editorial", "primeiro paragrafo")
    final, status = normalize_subtitle(meta, "Titulo diferente", first_paragraph, meta)

    assert final != meta
    assert status == "REGENERATED"


def test_normalize_subtitle_missing_when_equal_to_meta_without_safe_fallback():
    meta = _text(150)
    final, status = normalize_subtitle(meta, "Titulo diferente", "curto", meta)

    assert final == ""
    assert status == "MISSING"


def test_ai_parse_response_accepts_missing_subtitle():
    payload = {
        "resultados": [
            {
                "titulo_final": "Filme ganha novidades importantes para os fas",
                "conteudo_final": "<p>Texto final.</p>",
                "meta_description": "Meta description valida para teste do parser.",
                "focus_keyphrase": "Filme",
                "tags_sugeridas": ["Filme"],
                "yoast_meta": {},
            }
        ]
    }

    parsed = AIProcessor._parse_response(json.dumps(payload))

    assert parsed is not None
    assert parsed["subtitle"] == ""


def test_ai_parse_batch_response_accepts_subtitle():
    payload = {
        "resultados": [
            {
                "titulo_final": "Filme ganha novidades importantes para os fas",
                "conteudo_final": "<p>Texto final.</p>",
                "meta_description": "Meta description valida para teste do parser.",
                "focus_keyphrase": "Filme",
                "tags_sugeridas": ["Filme"],
                "yoast_meta": {},
                "subtitle": "Resumo editorial valido para teste do parser em modo lote.",
            }
        ]
    }

    parsed = AIProcessor._parse_batch_response(json.dumps(payload), 1)

    assert parsed is not None
    assert parsed[0]["subtitle"].startswith("Resumo editorial")
