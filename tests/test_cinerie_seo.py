"""SEO neutro, slug, keyphrases, matriz de Schema.org, alt, links e blocos.

Dominio puro: nenhum socket, garantido pela fixture ``_no_network``.

O fio condutor e a distincao que a fase inteira existe para estabelecer —
**transporte** (o que o contrato aceita) nao e **autopublicacao** (o que sai sem
humano). Um titulo de 100 caracteres e transportavel e nao e publicavel, e antes
desta fase o projeto tinha tres numeros diferentes para isso sem nada que
explicasse a diferenca.
"""

import socket

import pytest

from app.cinerie.blocks import (
    BLOCK_DIVIDER,
    BLOCK_HEADING,
    BLOCK_PARAGRAPH,
    BLOCK_QUOTE,
    html_to_blocks,
    max_blocks,
)
from app.cinerie.identity import (
    build_idempotency_key,
    build_identity,
    build_request_id,
    build_source_id,
    is_stable_id,
    normalize_cluster_id,
)
from app.cinerie.policy import AUTO_PUBLISH_INELIGIBLE, BLOCKING, WARNING
from app.cinerie.request import resolve_content_type
from app.cinerie.seo import (
    LEGACY_SEO_MIGRATION,
    build_alt_suggestions,
    build_internal_links,
    build_seo_proposal,
    migrate_legacy_seo,
    resolve_schema_type,
)
from app.cinerie.slug import RESERVED_SLUGS, is_valid_slug, normalize_slug, slug_issue
from app.cinerie.text import (
    collapse,
    count_occurrences,
    dedupe_keyphrases,
    find_forbidden_markup,
    normalize_keyphrase,
    plain_text,
)

TITLE = "Estudio confirma a data de estreia da nova serie"
META = (
    "O estudio confirmou a data de estreia da nova serie, que chega ao catalogo "
    "em julho com oito episodios e lancamento semanal na plataforma."
)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _blocked(*args, **kwargs):
        raise AssertionError("Teste de SEO do Cinerie tentou acessar a rede")

    monkeypatch.setattr(socket, "socket", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)


def proposal(**overrides):
    kwargs = dict(
        title=TITLE,
        meta_description=META,
        focus_keyphrase="data de estreia",
        slug_suggestion="nova-serie-data-de-estreia",
        content_type="news",
        lead_text="O estudio confirmou a data de estreia em comunicado.",
    )
    kwargs.update(overrides)
    return build_seo_proposal(**kwargs)


# ===========================================================================
# Migracao do legado Yoast
# ===========================================================================


def test_yoast_values_survive_as_neutral_fields():
    """Os VALORES nao se perdem; o formato do CMS de terceiro e que sai."""
    neutral = migrate_legacy_seo(
        {
            "yoast_meta": {
                "_yoast_wpseo_opengraph-title": "Titulo social",
                "_yoast_wpseo_twitter-description": "Descricao do Twitter",
                "_yoast_news_keywords": "kw1, kw2",
            }
        }
    )
    assert neutral["openGraphTitleSuggestion"] == "Titulo social"
    assert neutral["twitterDescriptionSuggestion"] == "Descricao do Twitter"


def test_every_useful_yoast_field_has_a_neutral_home():
    assert set(LEGACY_SEO_MIGRATION) == {
        "_yoast_wpseo_title",
        "_yoast_wpseo_metadesc",
        "_yoast_wpseo_focuskw",
        "_yoast_news_keywords",
        "_yoast_wpseo_opengraph-title",
        "_yoast_wpseo_opengraph-description",
        "_yoast_wpseo_twitter-title",
        "_yoast_wpseo_twitter-description",
    }


def test_neutral_field_wins_over_legacy():
    """Campo neutro presente = execucao nova. O yoast remanescente e residuo."""
    neutral = migrate_legacy_seo(
        {
            "yoast_meta": {"_yoast_wpseo_opengraph-title": "antigo"},
            "openGraphTitleSuggestion": "novo",
        }
    )
    assert neutral["openGraphTitleSuggestion"] == "novo"


def test_news_keywords_string_becomes_a_list():
    built = proposal(extra={"yoast_meta": {"_yoast_news_keywords": "serie, estreia , streaming"}})
    assert built.editorial_keywords == ["serie", "estreia", "streaming"]


def test_contract_output_has_no_yoast_key():
    payload = proposal(
        extra={"yoast_meta": {"_yoast_wpseo_title": "x", "_yoast_wpseo_canonical": "https://x"}}
    ).to_contract()
    flat = str(payload).lower()
    for forbidden in ("yoast", "post_status", "canonical", "robots", "jsonld"):
        assert forbidden not in flat


def test_contract_output_uses_exactly_the_schema_names():
    payload = proposal().to_contract()
    assert set(payload) <= {
        "version",
        "title",
        "metaDescription",
        "slugSuggestion",
        "focusKeyphrase",
        "relatedKeyphrases",
        "editorialKeywords",
        "schemaTypeRecommendation",
        "articleSectionSuggestion",
        "openGraphTitleSuggestion",
        "openGraphDescriptionSuggestion",
        "twitterTitleSuggestion",
        "twitterDescriptionSuggestion",
        "imageAltSuggestions",
        "internalLinkSuggestions",
    }


# ===========================================================================
# Transporte x autopublicacao
# ===========================================================================


def test_short_title_is_completed_instead_of_blocked():
    """Titulo curto demais para transporte e COMPLETADO, nao recusado.

    "Duna 3" e um titulo legitimo que o contrato nao transporta sozinho (minimo
    de 15 caracteres, regra de `superRefine` do Cinerie). Bloquear ali custava a
    materia inteira por uma questao de tamanho; compor com o material que a
    propria materia ja tem resolve sem afirmar nada novo.
    """
    built = proposal(title="Curto", subtitle="o estudio confirmou a data de estreia")
    assert not built.blocking
    assert len(built.title) >= 15
    assert any(item.code == "CAMPO_DERIVADO_POR_FALLBACK" for item in built.findings), (
        "o remendo precisa aparecer: um campo consertado em silencio esconde o defeito"
    )


def test_short_title_without_material_to_complete_still_blocks():
    """Sem nada de onde derivar, a recusa continua — e continua honesta."""
    built = proposal(title="Curto", subtitle="", summary="", lead_text="")
    assert any(item.severity == BLOCKING for item in built.findings)


def test_long_but_transportable_title_is_only_ineligible():
    """100 caracteres: transportavel, nao publicavel sozinho."""
    built = proposal(title="E" * 100)
    assert not built.blocking
    assert built.auto_publish_blockers
    assert not built.auto_publishable


def test_editorial_range_is_a_warning_not_a_block():
    built = proposal(meta_description="M" * 130)
    assert not built.blocking
    assert not built.auto_publish_blockers
    assert built.warnings
    assert built.auto_publishable


def test_meta_inside_the_editorial_range_is_clean():
    built = proposal(meta_description="M" * 148)
    assert not built.findings or all(item.severity == WARNING for item in built.findings)
    assert built.auto_publishable


# ===========================================================================
# Focus keyphrase
# ===========================================================================


def test_missing_focus_keyphrase_is_derived_from_the_title():
    """A IA omitir a frase-chave nao pode custar a materia.

    Este e o defeito que matou uma materia de gate limpo em producao: a IA
    devolveu `None` e o pedido saiu com o campo vazio, para morrer em
    CONTRACT_INVALID no proprio validador local. O campo agora e derivado do
    titulo — texto que a materia ja tem, nunca uma frase inventada.
    """
    built = proposal(focus_keyphrase="")
    assert built.focus_keyphrase, "focusKeyphrase e obrigatorio no contrato"
    assert 2 <= len(built.focus_keyphrase) <= 80
    assert not built.blocking
    assert any(
        item.code == "CAMPO_DERIVADO_POR_FALLBACK" and item.field == "seo.focusKeyphrase"
        for item in built.findings
    )


@pytest.mark.parametrize("value", [None, "", "   ", "<script>x</script>"])
def test_focus_keyphrase_never_leaves_empty(value):
    """Nulo, vazio, so espacos ou com markup: nenhum deles sai no pedido."""
    built = proposal(focus_keyphrase=value)
    assert built.focus_keyphrase
    assert len(built.focus_keyphrase) <= 80


def test_derived_focus_keyphrase_does_not_start_with_a_preposition():
    built = proposal(focus_keyphrase="", title="De volta ao futuro ganha nova edicao")
    assert not built.focus_keyphrase.lower().startswith("de ")


def test_generic_focus_keyphrase_is_not_auto_publishable():
    built = proposal(focus_keyphrase="serie", title="Serie ganha nova temporada em julho")
    assert any(item.code == "FOCUS_KEYPHRASE_GENERICA" for item in built.findings)
    assert not built.auto_publishable


def test_focus_absent_from_the_lead_is_a_warning():
    built = proposal(lead_text="O comunicado chegou a imprensa nesta terca-feira.")
    codes = [item.code for item in built.findings]
    assert "FOCUS_KEYPHRASE_AUSENTE_NO_LEAD" in codes
    assert built.auto_publishable, "presenca no lead e sinal editorial, nao regra de forma"


def test_focus_present_everywhere_produces_no_finding():
    """Controle NEGATIVO: sem ele a checagem poderia reclamar sempre."""
    built = proposal()
    codes = [item.code for item in built.findings]
    assert "FOCUS_KEYPHRASE_AUSENTE_NO_TITULO" not in codes
    assert "FOCUS_KEYPHRASE_AUSENTE_NA_META" not in codes
    assert "FOCUS_KEYPHRASE_AUSENTE_NO_LEAD" not in codes


def test_related_repeating_the_focus_is_removed_and_reported():
    """A lista e CONSERTADA, e o achado registra que ela veio redundante.

    Recusar o pedido inteiro por causa de uma keyphrase repetida seria
    desproporcional: o conteudo esta certo, a lista e que veio suja. Mas
    consertar em silencio deixaria o defeito de geracao invisivel para sempre.
    """
    built = proposal(related_keyphrases=["Data De Estreia", "catalogo de julho"])
    assert built.related_keyphrases == ["catalogo de julho"]
    assert any(item.code == "RELATED_DUPLICADA_REMOVIDA" for item in built.findings)
    assert built.auto_publishable


def test_trivial_plural_variation_is_caught():
    built = proposal(focus_keyphrase="nova serie", related_keyphrases=["novas series"])
    assert built.related_keyphrases == []
    assert any(item.code == "RELATED_DUPLICADA_REMOVIDA" for item in built.findings)


def test_distinct_related_keyphrases_are_all_kept():
    """Controle NEGATIVO: sem ele a deduplicacao poderia esvaziar tudo."""
    built = proposal(related_keyphrases=["nova serie", "catalogo de julho", "oito episodios"])
    assert len(built.related_keyphrases) == 3
    assert all(item.code != "RELATED_DUPLICADA_REMOVIDA" for item in built.findings)


def test_keyword_stuffing_is_not_auto_publishable():
    built = proposal(
        title="Data de estreia: data de estreia da data de estreia",
        meta_description="Data de estreia " * 12,
        extra={
            "openGraphTitleSuggestion": "Data de estreia confirmada",
            "twitterTitleSuggestion": "Data de estreia agora",
        },
    )
    assert any(item.code == "KEYWORD_STUFFING" for item in built.findings)
    assert not built.auto_publishable


def test_normal_repetition_is_not_stuffing():
    assert all(item.code != "KEYWORD_STUFFING" for item in proposal().findings)


def test_redundant_social_suggestion_is_dropped_instead_of_shipped():
    """O caso real que voltou 422 do Cinerie: og e twitter copiando o titulo.

    O MNScr MEDIA as 4 repeticoes contra o maximo de 3 e mandava assim mesmo,
    porque o achado era so um aviso. O Cinerie respondeu
    `BLOCKED / SEO_KEYWORD_STUFFING` e a materia morreu. Saber do defeito e
    enviar mesmo assim gasta a tentativa e ainda registra a recusa la.

    A repeticao era inteiramente duplicata: as duas sugestoes sociais eram o
    titulo copiado. Sao campos OPCIONAIS — sem eles o CMS usa `seo.title`, que
    continua no pedido —, entao descartar nao perde nada que o leitor veria.
    """
    title = "Spider-Man: Brand New Day quebra recorde de bilheteria nos EUA"
    built = proposal(
        title=title,
        focus_keyphrase="Spider-Man: Brand New Day",
        meta_description=(
            "Spider-Man: Brand New Day quebra recorde de bilheteria ao atingir "
            "US$ 500 milhoes nos EUA em apenas sete dias de exibicao."
        ),
        extra={"openGraphTitleSuggestion": title, "twitterTitleSuggestion": title},
    )
    assert all(item.code != "KEYWORD_STUFFING" for item in built.findings)
    assert any(item.code == "SUGESTAO_SOCIAL_DESCARTADA" for item in built.findings), (
        "o descarte precisa aparecer: um campo removido em silencio esconde o defeito"
    )
    assert "twitterTitleSuggestion" not in built.to_contract()
    # O campo obrigatorio que o CMS usa como fallback continua no pedido.
    assert built.to_contract()["title"] == title


def test_stuffing_that_survives_the_drop_is_still_reported():
    """Quando nao sobra sugestao opcional para descartar, o achado permanece.

    Reescrever titulo ou meta description para caber no limite seria mexer em
    texto editorial, e isso nao e trabalho desta camada.
    """
    built = proposal(
        title="Data de estreia: data de estreia da data de estreia",
        meta_description="Data de estreia " * 12,
        extra={},
    )
    assert any(item.code == "KEYWORD_STUFFING" for item in built.findings)
    assert not built.auto_publishable


# ===========================================================================
# Related keyphrases e keywords
# ===========================================================================


def test_dedupe_ignores_case_accents_and_punctuation():
    assert dedupe_keyphrases(["Estreia", "estréia", "ESTREIA!"]) == ["Estreia"]


def test_dedupe_preserves_the_first_spelling():
    assert dedupe_keyphrases(["Estréia", "estreia"]) == ["Estréia"]


def test_dedupe_drops_empty_and_markup():
    assert dedupe_keyphrases(["", "   ", "<b>x</b>", "ok"]) == ["ok"]


def test_lists_are_capped_by_the_contract():
    built = proposal(
        related_keyphrases=[f"frase {index}" for index in range(30)],
        editorial_keywords=[f"tag{index}" for index in range(50)],
    )
    assert len(built.related_keyphrases) == 10
    assert len(built.editorial_keywords) == 20


def test_oversized_item_is_dropped_not_truncated():
    """Truncar uma keyphrase produziria uma frase que ninguem escreveu."""
    assert dedupe_keyphrases(["x" * 200], item_max_length=80) == []


def test_no_meta_keywords_are_produced():
    assert "keywords" not in str(proposal().to_contract()).lower().replace("editorialkeywords", "")


# ===========================================================================
# Slug
# ===========================================================================


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Estúdio confirma a DATA de estreia!!", "estudio-confirma-a-data-de-estreia"),
        ("  espaços   demais  ", "espacos-demais"),
        ("acentuação-çãõ", "acentuacao-cao"),
        ("---hifens---duplos---", "hifens-duplos"),
        ("Ação & Aventura", "acao-aventura"),
    ],
)
def test_slug_normalization_is_deterministic(raw, expected):
    assert normalize_slug(raw) == expected
    assert normalize_slug(raw) == normalize_slug(raw)


def test_empty_slug_is_none_not_empty_string():
    """`""` pediria ao Payload que adivinhasse. `None` diz que nao ha sugestao."""
    assert normalize_slug("!!!") is None
    assert normalize_slug("") is None


@pytest.mark.parametrize("reserved", sorted(RESERVED_SLUGS)[:5])
def test_reserved_slug_is_refused(reserved):
    assert normalize_slug(reserved) is None


def test_slug_respects_the_word_limit():
    slug = normalize_slug("uma frase muito comprida com um monte de palavras seguidas aqui")
    assert len(slug.split("-")) <= 8


def test_slug_stays_inside_the_schema_length():
    slug = normalize_slug(" ".join(["palavra"] * 60))
    assert is_valid_slug(slug)
    assert len(slug) <= 200


def test_invalid_slug_falls_back_to_the_cluster_id():
    """Slug inutilizavel vira a do agrupamento, que ja e estavel e ja esta no pedido.

    A slug e SUGESTAO: quem decide a canonica, a colisao e o redirect e o
    Cinerie. Recusar o pedido inteiro por causa de uma sugestao seria caro por
    nada — desde que o remendo apareca no aviso.
    """
    built = proposal(slug_suggestion="!!!", title="!!! ???", cluster_id="cluster.abc123")
    assert not built.blocking
    assert built.slug_suggestion
    assert is_valid_slug(built.slug_suggestion)
    assert any(
        item.code == "CAMPO_DERIVADO_POR_FALLBACK" and item.field == "seo.slugSuggestion"
        for item in built.findings
    )


def test_slug_with_nothing_to_derive_from_still_blocks():
    """Sem titulo, sem frase-chave e sem agrupamento nao ha o que derivar.

    A cadeia e longa de proposito — titulo, frase-chave, agrupamento — e so
    quando ela inteira falha o achado bloqueante volta. Um pedido sem nada
    disso nao e uma materia.
    """
    built = proposal(
        slug_suggestion="!!!",
        title="!!! ???",
        focus_keyphrase="!!!",
        meta_description="!!! ???",
        lead_text="",
        cluster_id="",
    )
    assert any(item.severity == BLOCKING for item in built.findings)


@pytest.mark.parametrize(
    "value,code",
    [(None, "SLUG_AUSENTE"), ("Com Maiuscula", "SLUG_FORA_DO_FORMATO"), ("admin", "SLUG_RESERVADA")],
)
def test_slug_issue_names_the_problem(value, code):
    assert slug_issue(value).startswith(code)


def test_valid_slug_has_no_issue():
    assert slug_issue("nova-serie-data-de-estreia") is None


# ===========================================================================
# Matriz de Schema.org
# ===========================================================================


@pytest.mark.parametrize(
    "content_type,expected",
    [
        ("news", "NewsArticle"),
        ("feature", "Article"),
        ("review", "Article"),
        ("guide", "Article"),
        ("list", "Article"),
        ("interview", "Article"),
        ("evergreen", "Article"),
    ],
)
def test_default_never_promises_structure_it_cannot_prove(content_type, expected):
    resolved, findings = resolve_schema_type(
        content_type, requested=None, has_steps=False, has_list=False, has_rating=False
    )
    assert resolved == expected
    assert findings == [], "nao pedir HowTo nao deveria gerar achado"


def test_requested_type_outside_the_matrix_falls_back_with_a_warning():
    resolved, findings = resolve_schema_type(
        "news", requested="Review", has_steps=False, has_list=False, has_rating=True
    )
    assert resolved == "Article"
    assert findings[0].code == "SCHEMA_TYPE_FORA_DA_MATRIZ"
    assert findings[0].severity == WARNING


def test_review_without_a_real_rating_is_false_structured_data():
    resolved, findings = resolve_schema_type(
        "review", requested="Review", has_steps=False, has_list=False, has_rating=False
    )
    assert resolved == "Article"
    assert findings[0].code == "SCHEMA_TYPE_SEM_ESTRUTURA"
    assert findings[0].severity == AUTO_PUBLISH_INELIGIBLE


def test_howto_without_steps_is_refused():
    resolved, findings = resolve_schema_type(
        "guide", requested="HowTo", has_steps=False, has_list=False, has_rating=False
    )
    assert resolved == "Article"
    assert findings[0].code == "SCHEMA_TYPE_SEM_ESTRUTURA"


def test_review_with_a_rating_is_accepted():
    """Controle POSITIVO: a matriz aceita quando a estrutura existe."""
    resolved, findings = resolve_schema_type(
        "review", requested="Review", has_steps=False, has_list=False, has_rating=True
    )
    assert resolved == "Review"
    assert findings == []


def test_unknown_schema_type_is_not_forwarded():
    resolved, findings = resolve_schema_type(
        "news", requested="BlogPosting", has_steps=False, has_list=False, has_rating=False
    )
    assert resolved == "Article"
    assert findings[0].code == "SCHEMA_TYPE_DESCONHECIDO"


def test_mnscr_never_emits_json_ld():
    payload = proposal().to_contract()
    for key in ("@context", "@type", "publisher", "datePublished", "dateModified", "canonical"):
        assert key not in payload


def test_unknown_content_type_falls_back_to_news():
    assert resolve_content_type("podcast") == "news"
    assert resolve_content_type("review") == "review"


# ===========================================================================
# Alt text
# ===========================================================================


def test_alt_is_kept_when_the_media_is_in_the_request():
    """A capacidade existe: alt gerado NAO e mais descartado."""
    result = build_alt_suggestions({"media-1": "Cartaz oficial da serie"}, media_ids=["media-1"])
    assert result == [{"mediaRef": "media-1", "alt": "Cartaz oficial da serie"}]


def test_alt_for_unknown_media_is_dropped():
    """Alt sem midia e texto solto que nunca chega a imagem nenhuma."""
    assert build_alt_suggestions({"media-9": "Cartaz"}, media_ids=["media-1"]) == []


def test_too_short_alt_is_dropped():
    assert build_alt_suggestions({"media-1": "ab"}, media_ids=["media-1"]) == []


def test_duplicate_media_ref_keeps_only_the_first():
    result = build_alt_suggestions(
        {"media-1": "Primeiro alt valido"}, media_ids=["media-1", "media-1"]
    )
    assert len(result) == 1


def test_alt_without_media_produces_an_explicit_warning():
    built = proposal(alt_texts={"media-1": "Cartaz oficial"}, media_ids=[])
    assert any(item.code == "ALT_SEM_MIDIA_CORRESPONDENTE" for item in built.findings)


# ===========================================================================
# Links internos
# ===========================================================================


def test_internal_link_is_structured_not_a_url():
    result = build_internal_links(
        [
            {
                "targetType": "tv_show",
                "targetId": "tv-1",
                "anchorText": "a nova serie",
                "reason": "entidade principal",
                "confidence": 0.9,
            }
        ]
    )
    assert result == [
        {
            "targetType": "tv_show",
            "anchorText": "a nova serie",
            "targetId": "tv-1",
            "reason": "entidade principal",
            "confidence": 0.9,
        }
    ]


def test_absolute_url_in_target_path_is_refused():
    """Uma URL absoluta ali seria link externo disfarcado de interno."""
    assert build_internal_links(
        [{"targetType": "article", "targetPath": "https://externo.example/x", "anchorText": "veja"}]
    ) == []


def test_link_without_a_target_is_dropped():
    assert build_internal_links([{"targetType": "article", "anchorText": "veja"}]) == []


def test_unknown_target_type_is_dropped():
    assert build_internal_links(
        [{"targetType": "podcast", "targetId": "p-1", "anchorText": "veja"}]
    ) == []


def test_duplicate_links_are_collapsed():
    link = {"targetType": "movie", "targetId": "m-1", "anchorText": "o filme"}
    assert len(build_internal_links([link, dict(link)])) == 1


def test_links_are_capped():
    links = [
        {"targetType": "movie", "targetId": f"m-{index}", "anchorText": f"filme {index}"}
        for index in range(40)
    ]
    assert len(build_internal_links(links)) == 20


def test_invalid_confidence_is_dropped_not_clamped():
    result = build_internal_links(
        [{"targetType": "movie", "targetId": "m-1", "anchorText": "o filme", "confidence": 5}]
    )
    assert "confidence" not in result[0]


# ===========================================================================
# Blocos
# ===========================================================================


def test_html_becomes_discriminated_blocks():
    conversion = html_to_blocks(
        "<h2>Titulo</h2><p>Paragrafo.</p><blockquote>Citacao.<cite>Fonte</cite></blockquote><hr/>"
    )
    types = [block["type"] for block in conversion.blocks]
    assert types == [BLOCK_HEADING, BLOCK_PARAGRAPH, BLOCK_QUOTE, BLOCK_DIVIDER]


def test_block_ids_are_deterministic_and_unique():
    conversion = html_to_blocks("<p>um</p><p>dois</p><p>tres</p>")
    ids = [block["id"] for block in conversion.blocks]
    assert ids == ["b1", "b2", "b3"]
    assert len(set(ids)) == len(ids)


def test_inline_markup_becomes_plain_text():
    """`plainText` recusa qualquer tag; o link reaparece em internalLinkSuggestions."""
    conversion = html_to_blocks('<p>Veja <strong>isto</strong> e <a href="/x">aquilo</a>.</p>')
    text = conversion.blocks[0]["text"]
    assert find_forbidden_markup(text) is None
    assert "isto" in text and "aquilo" in text


def test_h1_is_demoted_to_h2():
    """`h1` e do titulo da pagina; o corpo comeca em h2."""
    assert html_to_blocks("<h1>Titulo</h1>").blocks[0]["level"] == 2


def test_list_items_become_paragraphs():
    conversion = html_to_blocks("<ul><li>um</li><li>dois</li></ul>")
    assert [block["type"] for block in conversion.blocks] == [BLOCK_PARAGRAPH, BLOCK_PARAGRAPH]


def test_image_is_dropped_with_an_explicit_warning():
    """Sem mediaRef de midia aprovada no CMS, emitir um bloco de imagem seria
    pedir ao Cinerie que publicasse apontando para nada."""
    conversion = html_to_blocks('<p>texto</p><figure><img src="https://cdn.example/a.jpg"/></figure>')
    assert all(block["type"] != "image" for block in conversion.blocks)
    assert conversion.dropped_images == ["https://cdn.example/a.jpg"]
    assert any("IMAGENS_NAO_ENVIADAS" in warning for warning in conversion.warnings)


def test_script_never_becomes_a_block():
    conversion = html_to_blocks("<p>ok</p><script>alert(1)</script>")
    assert len(conversion.blocks) == 1
    assert any("BLOCO_DESCARTADO:script" in warning for warning in conversion.warnings)


def test_quote_attribution_is_extracted():
    conversion = html_to_blocks("<blockquote>Frase.<cite>Variety</cite></blockquote>")
    assert conversion.blocks[0]["attribution"] == "Variety"


def test_bare_text_is_not_lost():
    assert html_to_blocks("texto solto sem tag").blocks[0]["text"] == "texto solto sem tag"


def test_empty_body_produces_no_block():
    assert html_to_blocks("").is_empty


def test_oversized_body_is_flagged_never_silently_truncated():
    conversion = html_to_blocks("<p>x</p>" * (max_blocks() + 5))
    assert conversion.truncated
    assert any("CORPO_ACIMA_DO_LIMITE" in warning for warning in conversion.warnings)


# ===========================================================================
# Identidade
# ===========================================================================


def test_the_four_identifiers_are_distinct():
    identity = build_identity(event_key="cluster-4711", revision=3, payload_hash="a" * 64)
    values = {
        identity.request_id,
        identity.idempotency_key,
        identity.source_cluster_id,
        identity.source_payload_hash,
    }
    assert len(values) == 4, "derivar tudo de um valor so e o erro classico"


def test_idempotency_key_is_stable_across_runs():
    first = build_identity(event_key="cluster-1", revision=2, payload_hash="b" * 64)
    second = build_identity(event_key="cluster-1", revision=2, payload_hash="b" * 64)
    assert first.idempotency_key == second.idempotency_key
    assert first.request_id == second.request_id


def test_request_id_changes_only_when_the_content_changes():
    """Retry de rede repete o requestId; conteudo novo declara tentativa nova."""
    base = build_identity(event_key="cluster-1", revision=2, payload_hash="b" * 64)
    same_work_new_content = build_identity(
        event_key="cluster-1", revision=2, payload_hash="c" * 64
    )
    assert base.idempotency_key == same_work_new_content.idempotency_key
    assert base.request_id != same_work_new_content.request_id


def test_new_revision_is_a_new_work():
    first = build_identity(event_key="cluster-1", revision=1, payload_hash="b" * 64)
    second = build_identity(event_key="cluster-1", revision=2, payload_hash="b" * 64)
    assert first.idempotency_key != second.idempotency_key


def test_clean_event_key_passes_through_readable():
    assert normalize_cluster_id("cluster-4711") == "cluster-4711"


def test_dirty_event_key_becomes_a_digest_not_a_scrub():
    """Limpar caractere a caractere colidiria duas materias distintas."""
    first = normalize_cluster_id("evento com espacos!")
    second = normalize_cluster_id("evento/com/espacos?")
    assert first != second
    assert is_stable_id(first) and is_stable_id(second)


def test_source_id_follows_the_url_not_the_position():
    assert build_source_id("https://variety.example/a", index=0) == build_source_id(
        "https://variety.example/a", index=7
    )


def test_idempotency_key_matches_the_cinerie_shape():
    assert build_idempotency_key(cluster_id="cluster-4711", revision=3) == "mnscr:cluster-4711:rev-3"


def test_request_id_needs_both_inputs():
    from app.cinerie.errors import RequestBuildError

    with pytest.raises(RequestBuildError):
        build_request_id(idempotency_key="", payload_hash="a" * 64)


# ===========================================================================
# Texto
# ===========================================================================


@pytest.mark.parametrize(
    "value,label",
    [
        ("<script>x</script>", "tag <script>"),
        ("<iframe src=x>", "tag <iframe>"),
        ("<b>x</b>", "markup HTML"),
        ("javascript:alert(1)", "esquema javascript:"),
        ('onclick="x"', "handler inline (onX=)"),
    ],
)
def test_forbidden_markup_is_labelled(value, label):
    assert find_forbidden_markup(value) == label


def test_clean_text_has_no_markup_finding():
    assert find_forbidden_markup("Um texto editorial normal, com virgula e ponto.") is None


def test_less_than_sign_alone_is_not_markup():
    assert find_forbidden_markup("2 < 3 e verdadeiro") is None


def test_normalize_keyphrase_strips_accents_and_punctuation():
    assert normalize_keyphrase("Estréia, Oficial!") == "estreia oficial"


def test_occurrences_are_counted_across_fields_not_per_field():
    assert count_occurrences("estreia", ["A estreia", "outra estreia", None, 5]) == 2


def test_plain_text_truncates_on_a_word_boundary():
    assert plain_text("palavra " * 20, max_length=30).endswith("palavra")


def test_collapse_normalizes_whitespace():
    assert collapse("  a \n b \t c ") == "a b c"
