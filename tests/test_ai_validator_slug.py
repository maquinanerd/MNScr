from app.ai_validator import _apply_local_fixes


def test_local_entity_fixes_do_not_change_slug_case():
    result = _apply_local_fixes(
        {
            "titulo_final": "xbox confirma novidade",
            "conteudo_final": "<p>xbox e netflix aparecem no texto.</p>",
            "meta_description": "xbox e netflix aparecem em uma meta description suficientemente longa.",
            "subtitle": "xbox e netflix aparecem em um subtitulo suficientemente longo.",
            "slug": "xbox-netflix-novidade",
        },
        db_id=123,
    )

    assert result["slug"] == "xbox-netflix-novidade"
    assert "Xbox" in result["titulo_final"]
    assert "Xbox" in result["conteudo_final"]
