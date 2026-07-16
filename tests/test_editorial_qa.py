from app.editorial_qa import run_editorial_style_qa


def _paragraph_with_words(count: int) -> str:
    return "<p>" + " ".join(f"palavra{i}" for i in range(count)) + "</p>"


def test_clean_content_passes():
    result = run_editorial_style_qa(
        "Shogun retorna",
        "<p><b>Shogun</b> retorna com novos detalhes sobre a produção.</p><h2>Nova temporada amplia conflito politico</h2>",
    )

    assert result["passed"] is True
    assert result["warnings"] == []


def test_vague_phrase_triggers_warning():
    result = run_editorial_style_qa(
        "Teste",
        "<p>A expectativa é que o filme chegue ao streaming em breve.</p>",
    )

    assert result["passed"] is False
    assert result["warnings"][0]["type"] == "vague_phrase"
    assert result["warnings"][0]["match"] == "a expectativa é que"


def test_generic_h2_triggers_warning():
    result = run_editorial_style_qa("Teste", "<p>Texto inicial.</p><h2>O que esperar</h2>")

    assert result["passed"] is False
    assert {"type": "generic_h2", "match": "O que esperar"} in result["warnings"]


def test_suspicious_lead_triggers_warning():
    result = run_editorial_style_qa("Teste", "<p>A franquia X prepara um novo retorno.</p>")

    assert result["passed"] is False
    assert {"type": "suspicious_lead", "match": "A franquia"} in result["warnings"]


def test_missing_quotes_when_source_had_quotes():
    result = run_editorial_style_qa(
        "Teste",
        "<p>O diretor comentou a mudança no projeto.</p>",
        source_had_quotes=True,
    )

    assert result["passed"] is False
    assert {"type": "missing_quotes", "source_had_quotes": True} in result["warnings"]


def test_long_content_without_h2_triggers_missing_h2():
    result = run_editorial_style_qa("Teste", _paragraph_with_words(500))

    assert result["passed"] is False
    assert result["counts"]["missing_h2"] == 1
    assert result["warnings"][-1]["type"] == "missing_h2"
    assert result["warnings"][-1]["word_count"] == 500


def test_multiple_vague_phrase_occurrences_are_aggregated():
    html = (
        "<p>A expectativa é que o filme estreie. "
        "A expectativa é que a série retorne. "
        "A expectativa é que o jogo chegue.</p>"
    )
    result = run_editorial_style_qa("Teste", html)

    warning = next(w for w in result["warnings"] if w["type"] == "vague_phrase")
    assert warning["count"] == 3
    assert result["counts"]["vague_phrases"] == 3


def test_vague_phrase_detection_is_case_insensitive():
    result = run_editorial_style_qa("Teste", "<p>A EXPECTATIVA É QUE o filme retorne.</p>")

    assert result["passed"] is False
    assert result["warnings"][0]["type"] == "vague_phrase"


def test_short_content_without_h2_does_not_trigger_missing_h2():
    result = run_editorial_style_qa("Teste", _paragraph_with_words(399))

    assert result["counts"]["missing_h2"] == 0
    assert not any(w["type"] == "missing_h2" for w in result["warnings"])
