from app.entity_validator import extract_canonical_entities, validate_and_fix_entities


def test_entity_validator_fixes_near_miss_names_and_studio_punctuation():
    source = (
        "<p>Ana Nogueira escreveu o filme da DC Studios para Warner Bros. Pictures "
        "em 2026. Milly Alcock vive Kara Zor-El.</p>"
    )
    rewritten = (
        "<p>Ana Noguiera escreveu para Warner Bros.. Pictures em 2026. "
        "Milly Alcok vive Kara Zor-El.</p>"
    )

    fixed, corrections = validate_and_fix_entities(source, rewritten)

    assert "Ana Nogueira" in fixed
    assert "Ana Noguiera" not in fixed
    assert "Warner Bros. Pictures" in fixed
    assert "Warner Bros.. Pictures" not in fixed
    assert "Milly Alcock" in fixed
    assert "Milly Alcok" not in fixed
    assert {"original": "Ana Noguiera", "corrigido": "Ana Nogueira", "tipo": "pessoa"} in corrections
    assert {"original": "Warner Bros..", "corrigido": "Warner Bros.", "tipo": "estudio"} in corrections


def test_entity_validator_does_not_change_unmatched_entities():
    source = "<p>Craig Gillespie dirige Supergirl para a DC Studios.</p>"
    rewritten = "<p>Outro Nome aparece no texto sem correspondente na fonte.</p>"

    fixed, corrections = validate_and_fix_entities(source, rewritten)

    assert fixed == rewritten
    assert corrections == []


def test_entity_validator_does_not_rewrite_different_first_name():
    source = "<p>Oliva Cooke aparece na matéria original com erro de grafia.</p>"
    rewritten = "<p>Olivia Cooke está no elenco de House of the Dragon.</p>"

    fixed, corrections = validate_and_fix_entities(source, rewritten)

    assert "Olivia Cooke" in fixed
    assert "Oliva Cooke" not in fixed
    assert corrections == []


def test_entity_validator_skips_english_possessive_only_fix():
    source = "<p>Taylor Sheridan's Landman foi citado na fonte original.</p>"
    rewritten = "<p>Taylor Sheridan aparece no texto em português.</p>"

    fixed, corrections = validate_and_fix_entities(source, rewritten)

    assert "Taylor Sheridan aparece" in fixed
    assert "Taylor Sheridan's" not in fixed
    assert corrections == []


def test_extract_canonical_entities_drops_trailing_connectors_and_periods():
    entities = extract_canonical_entities(
        "<p>Ana Nogueira e Craig Gillespie falaram sobre Kara Zor-El.</p>"
    )

    assert "Ana Nogueira" in entities.values()
    assert "Craig Gillespie" in entities.values()
    assert "Kara Zor-El" in entities.values()
    assert "Ana Nogueira e" not in entities.values()
    assert "Kara Zor-El." not in entities.values()


def test_entity_validator_fixes_canonical_entity_casing_inside_anchor_text():
    source = "<p>Steven Spielberg comentou Harry Potter em entrevista.</p>"
    rewritten = (
        '<p>O diretor citou a adaptaÃ§Ã£o cinematogrÃ¡fica de '
        '<a href="https://cinerie.example/harry-potter/">harry potter</a>.</p>'
    )

    fixed, corrections = validate_and_fix_entities(source, rewritten)

    assert ">Harry Potter<" in fixed
    assert ">harry potter<" not in fixed
    assert {"original": "harry potter", "corrigido": "Harry Potter", "tipo": "pessoa"} in corrections


def test_the_distance_pass_does_not_put_back_the_period_the_first_pass_removed():
    """Regressao do artigo 53, publicado em 27/08/2026 com "Warner Bros..".

    O log daquela execucao registrou as DUAS correcoes como aplicadas — e o
    ponto duplo foi para o ar mesmo assim. O motivo e de ordem: `Warner Bros`,
    extraido de `Warner Bros. Discovery`, tem distancia 1 para o canonico
    `Warner Bros.` e e trocado por ele DEPOIS da limpeza de pontuacao,
    devolvendo o ponto que ja estava no texto.
    """
    source = "<p>Warner Bros. Discovery negocia a fusao com a Paramount.</p>"
    rewritten = "<p>A fusao entre a Warner Bros Discovery e a Paramount segue.</p>"

    fixed, _corrections = validate_and_fix_entities(source, rewritten)

    assert "Warner Bros. Discovery" in fixed
    assert "Warner Bros.." not in fixed


def test_an_ellipsis_survives_the_studio_period_collapse():
    """O colapso e por entidade conhecida. Reticencia e do reporter."""
    source = "<p>Warner Bros. Pictures confirmou.</p>"
    rewritten = "<p>A Warner Bros. Pictures confirmou... e depois recuou.</p>"

    fixed, _corrections = validate_and_fix_entities(source, rewritten)

    assert "confirmou... e depois" in fixed
