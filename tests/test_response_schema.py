from app.ai_client_gemini import ARTICLE_RESPONSE_SCHEMA


def test_article_response_schema_has_canonical_wrapper_shape():
    schema = ARTICLE_RESPONSE_SCHEMA

    assert isinstance(schema, dict)
    assert schema["type"] == "object"
    assert schema["required"] == ["resultados"]
    assert isinstance(schema["properties"], dict)

    resultados = schema["properties"]["resultados"]
    assert resultados["type"] == "array"
    assert resultados["minItems"] == 1
    assert resultados["maxItems"] == 1

    item_required = resultados["items"]["required"]
    assert "titulo_final" in item_required
    assert "conteudo_final" in item_required


def test_article_response_schema_import_constant():
    assert ARTICLE_RESPONSE_SCHEMA["required"] == ["resultados"]
