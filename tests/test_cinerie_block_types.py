"""Os tipos de bloco que o contrato aceita e o MNScr passou a emitir.

O contrato define dez tipos e o MNScr mandava dois — ``paragraph`` e
``heading``. Este arquivo cobre os que NAO dependem de ``mediaRef``: ``video``,
``quote`` e ``sourceList``. ``image`` continua fora por construcao (exige um
``mediaId`` que so existe depois da materia existir).

Cada tipo aparece aqui em par: o caso que EMITE e o caso que NAO emite. O
segundo e o que importa, porque o modo de falhar destes blocos nao e o pedido
ser recusado — e ele ser aceito apontando para o lugar errado: um player de um
video que nao existe, uma citacao com o autor trocado, uma lista de fontes com
referencia orfa. Nada disso o JSON Schema pega.

O texto real usado nos casos veio das materias publicadas em 07/08 — inclusive
o ``"..." , afirmou o executivo`` que NAO pode virar bloco.
"""

import hashlib
from types import SimpleNamespace

import pytest

from app.cinerie.blocks import (
    BLOCK_HEADING,
    BLOCK_PARAGRAPH,
    BLOCK_QUOTE,
    BLOCK_SOURCE_LIST,
    BLOCK_VIDEO,
    SOURCE_LIST_BLOCK_ID,
    attach_source_list,
    html_to_blocks,
    node_text,
    split_promotable_quote,
    youtube_video_id,
)
from app.cinerie.contract import local_identity
from app.cinerie.request import build_publication_request
from app.cinerie.validation import validate_request
from app.editorial.models import DraftContent, DraftProvenance, EditorialDraft, SourceReference

CREDIT_PARAGRAPH = (
    '<p class="mn-source-credit-line"><span>Fonte:</span> '
    '<a href="https://variety.com/2026/x" rel="nofollow">Variety</a></p>'
)


def types_of(blocks):
    return [block["type"] for block in blocks]


# ===========================================================================
# video
# ===========================================================================


def test_youtube_id_is_read_from_every_url_shape_that_reaches_the_converter():
    assert youtube_video_id("https://www.youtube.com/watch?v=n1Uv1kF-SBU") == "n1Uv1kF-SBU"
    assert youtube_video_id("https://youtu.be/fJqY-iTaafo") == "fJqY-iTaafo"
    assert youtube_video_id("https://www.youtube.com/embed/fJqY-iTaafo?rel=0") == "fJqY-iTaafo"
    assert youtube_video_id("https://www.youtube.com/shorts/fJqY-iTaafo") == "fJqY-iTaafo"


def test_video_paragraph_becomes_a_video_block_after_the_first_h2():
    """A regra de posicao: logo depois do primeiro intertitulo.

    E o unico ponto DEDUZIVEL do corpo em que o leitor ja sabe do que a materia
    trata e ainda nao chegou ao fim. Um indice fixo escolhido a mao seria
    desmentido pela proxima materia com outra quantidade de paragrafos.
    """
    conversion = html_to_blocks(
        "<p>Abertura.</p>"
        "<h2>Intertitulo</h2>"
        "<p>https://www.youtube.com/watch?v=n1Uv1kF-SBU</p>"
        "<p>Sequencia.</p>"
    )
    assert types_of(conversion.blocks) == [
        BLOCK_PARAGRAPH,
        BLOCK_HEADING,
        BLOCK_VIDEO,
        BLOCK_PARAGRAPH,
    ]
    video = conversion.blocks[2]
    assert video["provider"] == "youtube"
    assert video["url"] == "https://www.youtube.com/watch?v=n1Uv1kF-SBU"
    assert video["externalId"] == "n1Uv1kF-SBU"


def test_video_goes_to_the_end_when_the_body_has_no_h2():
    conversion = html_to_blocks(
        "<p>https://www.youtube.com/watch?v=n1Uv1kF-SBU</p><p>Unico paragrafo.</p>"
    )
    assert types_of(conversion.blocks) == [BLOCK_PARAGRAPH, BLOCK_VIDEO]


def test_invalid_video_is_discarded_with_a_warning_never_emitted_broken():
    """Id fora da forma do YouTube nao vira bloco.

    Um ``video`` com id invalido passa no JSON Schema — ``externalId`` so
    cobra formato de ``stableId`` — e o pedido e aceito. O defeito so aparece na
    pagina publicada, como um player que nao carrega.
    """
    conversion = html_to_blocks("<p>https://www.youtube.com/watch?v=curto</p><p>Texto.</p>")
    assert BLOCK_VIDEO not in types_of(conversion.blocks)
    assert any(warning.startswith("VIDEO_DESCARTADO") for warning in conversion.warnings)


def test_non_youtube_iframe_is_still_discarded():
    conversion = html_to_blocks('<p>Texto.</p><iframe src="https://player.vimeo.com/1"></iframe>')
    assert BLOCK_VIDEO not in types_of(conversion.blocks)
    assert "BLOCO_DESCARTADO:iframe" in conversion.warnings


def test_youtube_id_starting_with_a_dash_drops_externalId_not_the_block():
    """``stableId`` exige primeiro caractere alfanumerico; o YouTube nao.

    Mandar ``-bcdefghij_`` em ``externalId`` reprovaria o pedido INTEIRO por
    causa de um campo opcional. A ``url``, que nao tem esse formato, carrega o
    video sozinha.
    """
    conversion = html_to_blocks("<p>https://www.youtube.com/watch?v=-bcdefghij_</p>")
    video = conversion.blocks[0]
    assert video["type"] == BLOCK_VIDEO
    assert "externalId" not in video
    assert video["url"].endswith("-bcdefghij_")


def test_underscore_inside_the_youtube_id_still_goes_to_externalId():
    conversion = html_to_blocks("<p>https://www.youtube.com/watch?v=a_bcdefghij</p>")
    assert conversion.blocks[0]["externalId"] == "a_bcdefghij"


def test_only_the_first_video_is_published_and_the_rest_is_recorded():
    conversion = html_to_blocks(
        "<p>https://www.youtube.com/watch?v=n1Uv1kF-SBU</p>"
        "<p>https://www.youtube.com/watch?v=f4P-DaP69hw</p>"
    )
    videos = [block for block in conversion.blocks if block["type"] == BLOCK_VIDEO]
    assert len(videos) == 1
    assert videos[0]["externalId"] == "n1Uv1kF-SBU"
    assert "VIDEOS_EXCEDENTES:1" in conversion.warnings


def test_a_paragraph_that_merely_mentions_youtube_stays_a_paragraph():
    conversion = html_to_blocks("<p>O trailer foi ao ar no YouTube nesta terca-feira.</p>")
    assert types_of(conversion.blocks) == [BLOCK_PARAGRAPH]


# ===========================================================================
# sourceList
# ===========================================================================


def test_source_list_replaces_the_credit_paragraph_and_closes_the_body():
    conversion = html_to_blocks("<p>Materia.</p>" + CREDIT_PARAGRAPH)
    assert types_of(conversion.blocks) == [BLOCK_PARAGRAPH, BLOCK_PARAGRAPH]

    assert attach_source_list(conversion, ["variety.com"]) == SOURCE_LIST_BLOCK_ID
    assert types_of(conversion.blocks) == [BLOCK_PARAGRAPH, BLOCK_SOURCE_LIST]
    assert conversion.blocks[-1]["sourceRefs"] == ["variety.com"]
    # A linha "Fonte: Variety" em texto morto nao pode sobrar ao lado do bloco.
    assert all("Fonte:" not in (block.get("text") or "") for block in conversion.blocks)


def test_credit_paragraph_survives_when_there_is_no_referenceable_source():
    """Sem o bloco, o texto fica. Perder a atribuicao seria pior do que exibi-la
    sem link, que e exatamente o estado que este bloco veio corrigir."""
    conversion = html_to_blocks("<p>Materia.</p>" + CREDIT_PARAGRAPH)
    assert attach_source_list(conversion, []) is None
    assert BLOCK_SOURCE_LIST not in types_of(conversion.blocks)
    assert any("Fonte:" in (block.get("text") or "") for block in conversion.blocks)
    assert "FONTES_SEM_ID_ESTAVEL" in conversion.warnings


def test_source_ref_outside_the_stable_id_format_never_reaches_the_block():
    conversion = html_to_blocks("<p>Materia.</p>")
    attach_source_list(conversion, ["variety.com", "nao vale como id", "", "variety.com"])
    assert conversion.blocks[-1]["sourceRefs"] == ["variety.com"]


def test_every_source_ref_exists_in_external_sources():
    """A guarda que o JSON Schema nao tem: ele conhece o FORMATO do id, nao a
    lista. Um ``sourceRefs`` orfao e um link quebrado publicado com 201."""
    built = build_request(_draft_with(body="<p>" + "Corpo da materia. " * 12 + "</p>" + CREDIT_PARAGRAPH))
    ids = {source["id"] for source in built.payload["externalSources"]}
    listas = [b for b in built.payload["blocks"] if b["type"] == BLOCK_SOURCE_LIST]
    assert listas, "a materia com fonte tem de fechar com sourceList"
    for bloco in listas:
        assert set(bloco["sourceRefs"]) <= ids


# ===========================================================================
# quote
# ===========================================================================


def test_quote_with_a_named_speaker_is_promoted_and_the_paragraph_is_split():
    """Promover e PARTIR, nao duplicar: o que sobra do paragrafo continua nele.

    Repetir a fala no paragrafo e no bloco publicaria a mesma frase duas vezes
    na mesma pagina.
    """
    corpo = (
        "<p>“Eles sao populares porque sao talentosos e merecem essa popularidade. "
        "Sao pessoas decentes, com valores solidos”, afirmou <b>Rothman</b>. "
        "A direcao trouxe outra abordagem.</p>"
    )
    blocos = html_to_blocks(corpo).blocks
    assert types_of(blocos) == [BLOCK_QUOTE, BLOCK_PARAGRAPH]
    assert blocos[0]["attribution"] == "Rothman"
    assert blocos[0]["text"].startswith("Eles sao populares")
    assert "afirmou" not in blocos[0]["text"]
    assert blocos[1]["text"] == "A direcao trouxe outra abordagem."


def test_quote_without_a_speech_verb_stays_in_the_paragraph():
    """``a frase traduzida e "Hair today, Gorn tomorrow"`` nao e fala de ninguem.

    Texto real da materia de Star Trek publicada em 07/08.
    """
    corpo = (
        "<p>A frase traduzida e \"Hair today, Gorn tomorrow\", uma brincadeira que faz "
        "referencia a uma especie da serie e ao cabelo do capitao.</p>"
    )
    assert types_of(html_to_blocks(corpo).blocks) == [BLOCK_PARAGRAPH]


def test_quote_attributed_to_a_role_instead_of_a_name_stays_in_the_paragraph():
    """``afirmou o executivo`` nao diz QUEM falou.

    Texto real da materia da Lionsgate publicada em 07/08. Promover aqui
    exigiria ir buscar o nome na frase anterior — inventar a atribuicao.
    """
    corpo = (
        "<p>Segundo Fogelson, parte do material cortado podera ser aproveitado. "
        "\"Temos varias sequencias, especialmente grandes momentos musicais que foram "
        "filmados anteriormente\", afirmou o executivo.</p>"
    )
    assert types_of(html_to_blocks(corpo).blocks) == [BLOCK_PARAGRAPH]


def test_short_quoted_expression_is_not_a_citation():
    corpo = "<p>O compromisso da producao e \"ousar ir onde ninguem esteve\", disse Goldsman.</p>"
    assert types_of(html_to_blocks(corpo).blocks) == [BLOCK_PARAGRAPH]


def test_the_article_is_never_sliced_into_more_than_two_quotes():
    paragrafo = (
        "<p>\"Esta e uma declaracao suficientemente longa para valer como citacao "
        "de verdade, com corpo de frase\", afirmou Winderbaum. Sequencia.</p>"
    )
    blocos = html_to_blocks(paragrafo * 4).blocks
    assert types_of(blocos).count(BLOCK_QUOTE) == 2


def test_promoted_quote_ids_do_not_move_when_a_paragraph_is_added_before_it():
    """Id estavel entre revisoes: um paragrafo a mais nao pode reembaralhar a
    materia inteira do lado do CMS."""
    citacao = (
        "<p>\"Esta e uma declaracao suficientemente longa para valer como citacao "
        "de verdade, com corpo de frase\", afirmou Winderbaum.</p>"
    )
    antes = html_to_blocks(citacao).blocks
    depois = html_to_blocks("<p>Paragrafo novo da revisao seguinte.</p>" + citacao).blocks
    assert [b["id"] for b in antes if b["type"] == BLOCK_QUOTE] == ["q1"]
    assert [b["id"] for b in depois if b["type"] == BLOCK_QUOTE] == ["q1"]


def test_blockquote_attribution_leaves_the_quote_text():
    """O ``<cite>`` vira ``attribution`` e SAI do corpo da citacao; senao o nome
    aparece duas vezes, dentro do texto e embaixo dele."""
    conversion = html_to_blocks("<blockquote>Frase de alguem.<cite>Variety</cite></blockquote>")
    bloco = conversion.blocks[0]
    assert bloco["attribution"] == "Variety"
    assert "Variety" not in bloco["text"]


def test_the_clause_that_introduces_the_quote_does_not_become_a_stub_paragraph():
    """``Em comunicado oficial, a Universal declarou: "..."``.

    A materia 21 saiu publicada com o paragrafo ``Em comunicado oficial, a`` —
    uma frase cortada no artigo. O pedaco entre o ultimo ponto final e a citacao
    apresenta a fala, e quem falou ja esta em ``attribution``.
    """
    corpo = (
        "<p>O diretor deixou o projeto. Em comunicado oficial, a <b>Universal</b> declarou: "
        "“Tivemos uma experiencia incrivel trabalhando com Gareth em Jurassic World Rebirth”. "
        "O estudio agora busca um novo nome.</p>"
    )
    blocos = html_to_blocks(corpo).blocks
    assert types_of(blocos) == [BLOCK_PARAGRAPH, BLOCK_QUOTE, BLOCK_PARAGRAPH]
    assert blocos[0]["text"] == "O diretor deixou o projeto."
    assert blocos[1]["attribution"] == "Universal"
    assert blocos[2]["text"] == "O estudio agora busca um novo nome."
    assert all("comunicado oficial" not in (b.get("text") or "") for b in blocos)


def test_a_long_run_of_text_before_the_quote_blocks_the_promotion():
    """Se o "resto" antes da citacao e grande demais para ser uma clausula de
    apresentacao, entao ele e CONTEUDO — e corta-lo perderia texto da materia.
    Sem ponto final onde cortar, a citacao fica onde esta."""
    corpo = (
        "<p>" + "um texto corrido bem longo sem nenhuma pontuacao final " * 4
        + "a Universal declarou: “Tivemos uma experiencia incrivel trabalhando com "
        "Gareth em Jurassic World Rebirth”.</p>"
    )
    assert types_of(html_to_blocks(corpo).blocks) == [BLOCK_PARAGRAPH]


def test_split_returns_nothing_when_there_is_no_quote_at_all():
    assert split_promotable_quote("Um paragrafo comum, sem aspas nenhuma.") is None


# ===========================================================================
# Palavras coladas (item 6)
# ===========================================================================


def test_adjacent_inline_elements_do_not_glue_words():
    """``<span>Season 2</span><span>Disney/Hulu</span>`` -> ``Season 2 Disney/Hulu``.

    ``get_text()`` sem separador emenda os pedacos; foi assim que
    ``Season 2Disney/Hulu`` apareceu no log de 07/08.
    """
    texto = node_text(_soup("<p><span>Season 2</span> <span>Disney/Hulu</span></p>"))
    assert "2Disney" not in texto
    assert "Season 2 Disney/Hulu" in " ".join(texto.split())


def test_inline_markup_does_not_add_a_space_before_punctuation():
    """``get_text(" ")`` conserta a emenda e cria o defeito inverso: ``Rothman .``"""
    conversion = html_to_blocks("<p>Segundo <b>Fogelson</b>, o estudio decidiu.</p>")
    assert conversion.blocks[0]["text"] == "Segundo Fogelson, o estudio decidiu."


def test_a_word_split_across_inline_tags_is_not_broken_apart():
    conversion = html_to_blocks("<p>super<b>heroi</b> da vez</p>")
    assert conversion.blocks[0]["text"] == "superheroi da vez"


def test_html_comment_never_becomes_published_text():
    conversion = html_to_blocks("<p>Texto visivel.<!-- rascunho interno --></p>")
    assert conversion.blocks[0]["text"] == "Texto visivel."


# ===========================================================================
# O pedido inteiro, contra o schema real
# ===========================================================================


def _soup(html):
    from bs4 import BeautifulSoup

    return BeautifulSoup(html, "html.parser")


def _draft_with(*, body):
    output_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
    draft = EditorialDraft(
        draft_id="draft-teste-tipos-de-bloco",
        article_id=1,
        event_key="evento-teste-tipos-de-bloco",
        revision=1,
        content=DraftContent(
            title="Estudio confirma data de estreia da nova serie derivada",
            subtitle="O anuncio veio acompanhado do primeiro trailer completo da temporada.",
            body_html=body,
            summary=(
                "O estudio confirmou a data de estreia da nova serie derivada e divulgou "
                "o primeiro trailer completo, com o elenco principal ja confirmado."
            ),
            meta_description=(
                "O estudio confirmou a data de estreia da nova serie derivada e divulgou "
                "o primeiro trailer completo da temporada."
            ),
            focus_keyphrase="nova serie derivada",
            slug_suggestion="nova-serie-derivada-data-de-estreia",
            category_suggestions=["news"],
        ),
        provenance=DraftProvenance(
            input_hash=hashlib.sha256(b"entrada").hexdigest(),
            output_hash=output_hash,
            input_event_key="evento-teste-tipos-de-bloco",
            input_revision=1,
            prompt_version="teste@1",
            created_at="2026-08-07T12:00:00+00:00",
        ),
        sources=[
            SourceReference(
                url="https://variety.com/2026/tv/news/exemplo-1234",
                domain="variety.com",
                name="Variety",
                is_primary=True,
            ),
            SourceReference(
                url="https://www.hollywoodreporter.com/tv/exemplo-5678",
                domain="hollywoodreporter.com",
                name="Hollywood Reporter",
            ),
        ],
    )
    draft.editorial_gate = SimpleNamespace(outcome="GATE_CLEAR")
    draft.factual_assessment = SimpleNamespace(critical_conflicts=[])
    return draft


def build_request(draft):
    return build_publication_request(
        draft,
        public_author_id="1",
        attribution_mode="newsroom",
        contract=local_identity(),
    )


BODY_WITH_EVERY_NEW_TYPE = (
    "<p>O estudio confirmou nesta terca-feira a data de estreia da nova serie derivada, "
    "encerrando meses de especulacao entre os fas da franquia original.</p>"
    "<h2>O que o trailer mostra</h2>"
    "<p>https://www.youtube.com/watch?v=n1Uv1kF-SBU</p>"
    "<p>\"Esta temporada foi a mais dificil de filmar de toda a franquia, e o "
    "resultado esta na tela\", afirmou <b>Winderbaum</b>. A equipe levou dois anos "
    "na producao.</p>"
    "<hr/>"
    "<p>O elenco principal foi mantido integralmente, segundo o comunicado oficial "
    "divulgado pelo estudio.</p>" + CREDIT_PARAGRAPH
)


def test_a_payload_with_video_quote_divider_and_source_list_is_valid():
    built = build_request(_draft_with(body=BODY_WITH_EVERY_NEW_TYPE))
    report = validate_request(built.payload, identity=built.contract)
    assert report.ok, [f"{issue.path}: {issue.message}" for issue in report.issues]

    tipos = types_of(built.payload["blocks"])
    assert BLOCK_VIDEO in tipos
    assert BLOCK_QUOTE in tipos
    assert BLOCK_SOURCE_LIST in tipos
    assert "divider" in tipos
    # `sourceList` fecha a materia.
    assert tipos[-1] == BLOCK_SOURCE_LIST


def test_block_ids_stay_unique_once_the_new_types_join():
    built = build_request(_draft_with(body=BODY_WITH_EVERY_NEW_TYPE))
    ids = [block["id"] for block in built.payload["blocks"]]
    assert len(set(ids)) == len(ids)


def test_the_baseline_body_without_any_new_type_is_unchanged():
    """A materia que so tem paragrafo e heading continua saindo do mesmo jeito —
    menos o paragrafo de credito, que virou o bloco de fontes."""
    body = (
        "<p>Primeiro paragrafo com corpo suficiente para valer como lead da materia.</p>"
        "<h2>Intertitulo</h2>"
        "<p>Segundo paragrafo, sem video, sem citacao atribuida e sem divisor.</p>"
        + CREDIT_PARAGRAPH
    )
    built = build_request(_draft_with(body=body))
    assert validate_request(built.payload, identity=built.contract).ok
    assert types_of(built.payload["blocks"]) == [
        BLOCK_PARAGRAPH,
        BLOCK_HEADING,
        BLOCK_PARAGRAPH,
        BLOCK_SOURCE_LIST,
    ]


def test_qa_still_passes_with_the_new_blocks():
    """O desfecho de 07/08 foi ``201 PUBLISHED`` com ``qa.passed=true``. Bloco
    novo e chance nova de reprovar o QA — este teste e o que segura isso."""
    built = build_request(_draft_with(body=BODY_WITH_EVERY_NEW_TYPE))
    assert built.payload["qa"]["passed"] is True
    assert built.payload["qa"]["blockingErrors"] == []


@pytest.mark.parametrize("tipo", ["image", "entityCard", "factBox", "relatedContent"])
def test_types_that_depend_on_ids_the_mnscr_does_not_have_are_never_emitted(tipo):
    """``image`` exige ``mediaRef``; ``entityCard`` e ``relatedContent`` exigem id
    INTERNO do catalogo do Cinerie, que o MNScr nao resolve. ``factBox`` exigiria
    dado estruturado que o cluster nao carrega — uma caixa preenchida por
    heuristica seria fato inventado."""
    built = build_request(_draft_with(body=BODY_WITH_EVERY_NEW_TYPE))
    assert tipo not in types_of(built.payload["blocks"])
