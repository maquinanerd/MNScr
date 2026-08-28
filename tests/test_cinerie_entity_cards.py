"""`entityCard`: o unico bloco que aponta para fora sem que o Cinerie confira.

O `entityId` e um id INTERNO do catalogo. O CMS nao alcanca aquele banco e
aceita qualquer inteiro com `201`; quem confere e a renderizacao, e ela descarta
em silencio o que nao acha. Isso produz dois modos de falha com precos bem
diferentes:

    id inexistente        -> o bloco some da pagina, e ninguem e avisado
    id existente e ERRADO -> a pagina mostra a OBRA ERRADA com cara de certa

O segundo e o caro, e e ele que organiza este arquivo. A esmagadora maioria dos
casos aqui mede RECUSA — `null` que nao vira bloco, casamento abaixo do limiar,
resposta desalinhada, id fora de forma. O caminho feliz cabe em tres testes.

**Erre para menos, sempre.**
"""

import hashlib
from types import SimpleNamespace

import pytest

from app.cinerie.blocks import BLOCK_PARAGRAPH, BLOCK_SOURCE_LIST
from app.cinerie.contract import local_identity
from app.cinerie.entity_resolve import (
    BLOCK_ENTITY_CARD,
    MAX_DECLARED_WORKS,
    MAX_RESOLVE_ITEMS,
    EntityCandidate,
    EntityResolution,
    ResolvedEntity,
    attach_entity_cards,
    collect_entity_candidates,
    entity_links_from,
    fold,
    parse_resolution,
    select_entity_cards,
)
from app.cinerie.request import build_publication_request
from app.cinerie.validation import validate_request
from app.editorial.models import DraftContent, DraftProvenance, EditorialDraft, SourceReference

TODOS_OS_CASAMENTOS = ("tmdb_id", "exact_title_year", "exact_name")


def types_of(blocks):
    return [block["type"] for block in blocks]


# ===========================================================================
# A dobra
# ===========================================================================


def test_the_fold_matches_the_one_the_route_applies():
    """NFD, sem acento, minusculo, espacos colapsados — a MESMA dos dois lados.

    Divergir daqui nao quebra nada ruidosamente: so faz o casamento exato
    deixar de casar, e a rota responder `not_found` para titulos que existem.
    """
    assert fold("  clube DA   luta ") == fold("Clube da Luta")
    assert fold("Ruptura") == "ruptura"
    assert fold("Amélie") == "amelie"
    # "Clube de Luta" NAO e o mesmo termo — a rota nao faz aproximacao.
    assert fold("Clube de Luta") != fold("Clube da Luta")


# ===========================================================================
# Candidatos
# ===========================================================================


def test_title_and_lead_come_before_the_body():
    """A prioridade e do enunciado e e a certa: e no titulo e no lead que a
    materia declara do que trata."""
    candidatos = collect_entity_candidates(
        title="Christopher Nolan volta a dirigir",
        lead="O anuncio foi feito por Emma Thomas nesta terca.",
        blocks=[{"type": "paragraph", "text": "Cillian Murphy tambem esta no elenco."}],
    )
    por_nome = {c.name: c.prominence for c in candidatos}
    assert por_nome["Christopher Nolan"] == 0
    assert por_nome["Emma Thomas"] == 1
    assert por_nome["Cillian Murphy"] == 2


def test_a_work_only_travels_with_a_year_the_text_itself_declared():
    """Sem ano a rota responde `title_requires_year` — e nao ha ano para inventar.

    Colar o primeiro ano do texto num titulo qualquer produziria
    `exact_title_year` (o casamento MAIS confiante da rota) ancorado num chute.
    So entra o ano que o texto declara COLADO ao titulo.
    """
    com_ano = collect_entity_candidates(
        title="A estreia de Ruptura (2022) mudou o streaming", lead=""
    )
    obras = {(c.name, c.kind, c.year) for c in com_ano if c.kind in ("movie", "tv")}
    assert ("Ruptura", "tv", 2022) in obras
    assert ("Ruptura", "movie", 2022) in obras

    # O mesmo titulo, com o ano tres frases adiante, nao produz obra nenhuma.
    sem_ano = collect_entity_candidates(
        title="A estreia de Ruptura mudou o streaming",
        lead="A serie foi lancada. Isso aconteceu em 2022, segundo o estudio.",
    )
    assert [c for c in sem_ano if c.kind in ("movie", "tv")] == []


def test_a_declared_work_travels_without_a_year_because_the_route_checks_uniqueness():
    """O caminho que faz filme existir: `obras_citadas`.

    Medido em 27/08/2026: cinco materias publicadas, 93 candidatos, ZERO obras —
    nenhuma delas escrevia `Titulo (ano)`, que era a unica forma que o extrator
    por texto enxergava. A obra declarada entra sem ano e quem confere unicidade
    e a rota, do lado que tem o catalogo.
    """
    candidatos = collect_entity_candidates(
        title="Filme ganha data de estreia",
        lead="O longa chega em setembro.",
        declared_works=[{"titulo": "Signal One"}],
    )
    obras = {(c.name, c.kind, c.year) for c in candidatos if c.kind in ("movie", "tv")}
    assert obras == {("Signal One", "movie", None), ("Signal One", "tv", None)}


def test_a_declared_year_is_a_second_probe_and_never_the_only_one():
    """Ano declarado pela IA nao pode ser a unica forma de perguntar.

    `Superman` com 1978 numa materia sobre o filme de 2025 casaria
    `exact_title_year` — o casamento mais confiante da rota — sobre o filme
    ERRADO. Mandando as duas formas, titulo repetido no catalogo volta
    `ambiguous_title` em vez de virar bloco.
    """
    candidatos = collect_entity_candidates(
        title="Superman ganha continuacao",
        lead="",
        declared_works=[{"titulo": "Superman", "ano": 2025}],
    )
    anos = {c.year for c in candidatos if c.name == "Superman" and c.kind == "movie"}
    assert anos == {None, 2025}


def test_an_implausible_declared_year_is_dropped_and_the_title_survives():
    """Ano fora da janela da rota nao derruba a obra: ele so nao viaja."""
    candidatos = collect_entity_candidates(
        title="Filme antigo",
        lead="",
        declared_works=[{"titulo": "Metropolis", "ano": 12}],
    )
    anos = {c.year for c in candidatos if c.name == "Metropolis" and c.kind == "movie"}
    assert anos == {None}


def test_declared_works_are_capped_before_the_batch_is_built():
    """Uma lista longa demais e sinal de lista errada, e cada obra custa ate
    quatro itens do lote de 50."""
    declaradas = [{"titulo": f"Obra Numero {indice}"} for indice in range(30)]
    candidatos = collect_entity_candidates(
        title="Retrospectiva", lead="", declared_works=declaradas
    )
    titulos = {c.name for c in candidatos if c.kind == "movie"}
    assert len(titulos) == MAX_DECLARED_WORKS


def test_a_work_is_sent_as_both_kinds_because_the_text_does_not_say_which():
    """Escolher `movie` ou `tv` por heuristica seria decidir "isto e serie" sem
    nada no texto dizendo isso. Mandar os dois custa um item do lote e deixa a
    ROTA responder `null` para o que nao existe.

    O mesmo texto tambem vira candidato a `person`, e isso e DELIBERADO: um
    titulo com forma de nome (`Steve Jobs (2015)`) e as duas coisas ao mesmo
    tempo, e as duas sao respostas certas. Filtrar aqui exigiria decidir qual
    delas a materia trata — exatamente a decisao que este modulo nao toma.
    """
    candidatos = collect_entity_candidates(title="Clube da Luta (1999) faz aniversario", lead="")
    kinds = {c.kind for c in candidatos if c.name == "Clube da Luta"}
    assert {"movie", "tv"} <= kinds


def test_the_same_name_repeated_is_one_candidate_not_ten():
    candidatos = collect_entity_candidates(
        title="Jon Hamm volta",
        lead="Jon Hamm assina o contrato.",
        blocks=[{"type": "paragraph", "text": "Para Jon Hamm, o papel e novo."}],
    )
    assert [c.name for c in candidatos].count("Jon Hamm") == 1


def test_the_batch_is_cut_locally_and_never_sent_over_the_route_ceiling():
    """Acima do teto a rota recusa o pedido INTEIRO (`422 too_many_items`) — nunca
    trunca, porque truncar alinharia os resultados errados aos itens errados."""
    lead = " ".join(f"Nome Sobrenome{indice}" for indice in range(80))
    candidatos = collect_entity_candidates(title="", lead=lead)
    assert len(candidatos) <= MAX_RESOLVE_ITEMS


def test_a_streaming_brand_is_not_treated_as_a_person():
    candidatos = collect_entity_candidates(title="Prime Video anuncia a nova temporada", lead="")
    assert "Prime Video" not in {c.name for c in candidatos}


def test_the_resolve_item_uses_the_field_name_the_kind_makes_readable():
    pessoa, *_ = collect_entity_candidates(title="Morgan Freeman narra o filme", lead="")
    assert pessoa.as_resolve_item() == {"kind": "person", "name": "Morgan Freeman"}

    obra = next(
        c
        for c in collect_entity_candidates(title="Ruptura (2022) volta", lead="")
        if c.kind == "tv"
    )
    assert obra.as_resolve_item() == {"kind": "tv", "title": "Ruptura", "year": 2022}


# ===========================================================================
# A resposta — e o que ela NAO pode produzir
# ===========================================================================


def _um_candidato():
    return list(collect_entity_candidates(title="Morgan Freeman esta no elenco", lead=""))


@pytest.mark.parametrize(
    "reason",
    [
        "not_found",
        "ambiguous_title",
        "ambiguous_name",
        "title_requires_year",
        "no_canonical_slug",
        "tmdb_id_not_in_catalog",
        "unsupported_kind",
        "no_input",
    ],
)
def test_a_null_result_never_becomes_a_block(reason):
    """Resultado nulo = nao emite. Ponto. Sem plano B, sem aproximacao.

    `no_canonical_slug` e o caso que parece excecao e nao e: a entidade EXISTE e
    ainda assim nao serve, porque sem slug canonico pt-BR ela nao tem pagina — e
    o card sumiria do corpo exatamente como sumiria um id inexistente.
    """
    candidatos = _um_candidato()
    resolucao = parse_resolution(
        [{"index": 0, "entityId": None, "matchedBy": None, "confidence": 0, "reason": reason}],
        candidatos,
        accepted_match_kinds=TODOS_OS_CASAMENTOS,
    )
    assert resolucao.resolved == []
    assert resolucao.reasons == {reason: 1}


def test_a_match_below_the_configured_threshold_never_becomes_a_block():
    """O limiar e configuravel, e apertar ele precisa realmente apertar."""
    candidatos = _um_candidato()
    linha = {
        "index": 0,
        "entityKind": "person",
        "entityId": "4210",
        "matchedBy": "exact_name",
        "confidence": 0.85,
    }
    aceito = parse_resolution([linha], candidatos, accepted_match_kinds=TODOS_OS_CASAMENTOS)
    assert len(aceito.resolved) == 1

    recusado = parse_resolution(
        [linha], candidatos, accepted_match_kinds=("tmdb_id", "exact_title_year")
    )
    assert recusado.resolved == []
    assert any("ABAIXO_DO_LIMIAR" in aviso for aviso in recusado.warnings)


def test_an_id_outside_the_internal_form_never_becomes_a_block():
    """`tmdb:550` e `tt0111161` passariam no bloco e seriam recusados no CMS.

    Recusar aqui troca um pedido INTEIRO reprovado por uma ficha a menos.
    """
    candidatos = _um_candidato()
    resolucao = parse_resolution(
        [
            {
                "index": 0,
                "entityKind": "person",
                "entityId": ":550",
                "matchedBy": "exact_name",
                "confidence": 0.85,
            }
        ],
        candidatos,
        accepted_match_kinds=TODOS_OS_CASAMENTOS,
    )
    assert resolucao.resolved == []


def test_a_result_index_outside_the_batch_is_refused_instead_of_guessed():
    """Alinhar errado publicaria a ficha de uma entidade sob o nome de outra —
    a falha exata que este modulo existe para impedir."""
    candidatos = _um_candidato()
    resolucao = parse_resolution(
        [
            {
                "index": 99,
                "entityKind": "person",
                "entityId": "4210",
                "matchedBy": "exact_name",
                "confidence": 0.85,
            }
        ],
        candidatos,
        accepted_match_kinds=TODOS_OS_CASAMENTOS,
    )
    assert resolucao.resolved == []
    assert "ENTITY_RESOLVE_INDICE_FORA_DA_FAIXA" in resolucao.warnings


def test_a_resolved_result_carries_the_origin_name_and_the_public_path():
    """O par (nome de origem, id resolvido) e o que uma auditoria posterior
    precisa para conferir se a pagina mostrou a obra certa."""
    candidatos = _um_candidato()
    resolucao = parse_resolution(
        [
            {
                "index": 0,
                "entityKind": "person",
                "entityId": "4210",
                "matchedBy": "exact_name",
                "confidence": 0.85,
                "canonicalTitle": "Morgan Freeman",
                "path": "/pt/pessoas/morgan-freeman/",
            }
        ],
        candidatos,
        accepted_match_kinds=TODOS_OS_CASAMENTOS,
    )
    (resolvido,) = resolucao.resolved
    assert resolvido.candidate.name == "Morgan Freeman"
    assert resolvido.entity_id == "4210"
    assert resolvido.public_link == "/pt/pessoas/morgan-freeman/"


# ===========================================================================
# Selecao e posicao
# ===========================================================================


def _resolvido(nome, entity_id, confidence, matched_by="exact_name", kind="person"):
    from app.cinerie.entity_resolve import EntityCandidate, ResolvedEntity

    return ResolvedEntity(
        candidate=EntityCandidate(name=nome, kind=kind, prominence=1),
        entity_kind=kind,
        entity_id=entity_id,
        matched_by=matched_by,
        confidence=confidence,
    )


def test_at_most_three_cards_and_they_are_the_most_confident():
    """Confianca decide; o desempate por NOME e o que torna a escolha estavel.

    Um desempate instavel faria a revisao seguinte trocar as fichas sem que nada
    tivesse mudado no texto — e o CMS leria isso como blocos diferentes.
    """
    from app.cinerie.entity_resolve import EntityResolution

    resolucao = EntityResolution(
        resolved=[
            _resolvido("Nome Um", "1", 0.85),
            _resolvido("Nome Dois", "2", 1.0, matched_by="tmdb_id"),
            _resolvido("Nome Tres", "3", 0.9, matched_by="exact_title_year"),
            _resolvido("Nome Quatro", "4", 0.85),
        ]
    )
    escolhidas = select_entity_cards(resolucao, maximum=3)
    # 1.0, depois 0.9, e o empate em 0.85 resolvido por nome ("Quatro" < "Um").
    assert [c.entity_id for c in escolhidas] == ["2", "3", "4"]
    assert select_entity_cards(resolucao, maximum=3) == escolhidas


def test_the_same_entity_resolved_twice_enters_only_once():
    """O mesmo titulo casa como filme e como serie; o mesmo nome aparece em duas
    formas. O que decide e o par (tipo, id)."""
    from app.cinerie.entity_resolve import EntityResolution

    resolucao = EntityResolution(
        resolved=[_resolvido("Nome", "7", 0.9), _resolvido("Nome completo", "7", 0.85)]
    )
    assert len(select_entity_cards(resolucao, maximum=3)) == 1


def test_the_card_goes_right_after_the_paragraph_that_first_mentions_the_entity():
    """A REGRA DE POSICAO, e ela e deduzivel do texto — nao um indice a mao."""
    blocks = [
        {"id": "b1", "type": BLOCK_PARAGRAPH, "text": "Abertura sem nome nenhum."},
        {"id": "b2", "type": BLOCK_PARAGRAPH, "text": "Morgan Freeman assinou o contrato."},
        {"id": "b3", "type": BLOCK_PARAGRAPH, "text": "O estudio confirmou depois."},
    ]
    attach_entity_cards(
        blocks, [_resolvido("Morgan Freeman", "4210", 0.85)], max_total_blocks=200
    )
    assert [b["id"] for b in blocks] == ["b1", "b2", "ec1", "b3"]


def test_a_name_only_in_the_headline_lands_after_the_first_paragraph():
    blocks = [
        {"id": "b1", "type": BLOCK_PARAGRAPH, "text": "O ator assinou o contrato."},
        {"id": "b2", "type": BLOCK_PARAGRAPH, "text": "O estudio confirmou depois."},
    ]
    attach_entity_cards(
        blocks, [_resolvido("Morgan Freeman", "4210", 0.85)], max_total_blocks=200
    )
    assert [b["id"] for b in blocks] == ["b1", "ec1", "b2"]


def test_the_match_respects_word_boundaries():
    """Sem fronteira de palavra, "Ana" casaria dentro de "Anakin" e a ficha
    entraria depois do paragrafo errado."""
    blocks = [
        {"id": "b1", "type": BLOCK_PARAGRAPH, "text": "Anakin Skywalker aparece no flashback."},
        {"id": "b2", "type": BLOCK_PARAGRAPH, "text": "Ana Torrent entrou no elenco."},
    ]
    attach_entity_cards(blocks, [_resolvido("Ana Torrent", "99", 0.85)], max_total_blocks=200)
    assert [b["id"] for b in blocks] == ["b1", "b2", "ec1"]


def test_several_cards_keep_each_ones_own_paragraph():
    """Inserir da frente para tras deslocaria os indices ainda nao usados, e a
    segunda ficha cairia um bloco adiante do paragrafo dela."""
    blocks = [
        {"id": "b1", "type": BLOCK_PARAGRAPH, "text": "Morgan Freeman abre a materia."},
        {"id": "b2", "type": BLOCK_PARAGRAPH, "text": "Nada aqui."},
        {"id": "b3", "type": BLOCK_PARAGRAPH, "text": "Jon Hamm entra depois."},
    ]
    attach_entity_cards(
        blocks,
        [_resolvido("Morgan Freeman", "1", 0.85), _resolvido("Jon Hamm", "2", 0.85)],
        max_total_blocks=200,
    )
    assert [b["id"] for b in blocks] == ["b1", "ec1", "b2", "b3", "ec2"]


def test_the_note_only_carries_a_canonical_title_that_differs_from_the_text():
    from app.cinerie.entity_resolve import EntityCandidate, ResolvedEntity

    igual = ResolvedEntity(
        candidate=EntityCandidate(name="Clube da Luta", kind="movie", year=1999),
        entity_kind="movie",
        entity_id="1",
        matched_by="exact_title_year",
        confidence=0.9,
        canonical_title="Clube da Luta",
    )
    diferente = ResolvedEntity(
        candidate=EntityCandidate(name="Fight Club", kind="movie", year=1999),
        entity_kind="movie",
        entity_id="2",
        matched_by="exact_title_year",
        confidence=0.9,
        canonical_title="Clube da Luta",
    )
    blocks = [{"id": "b1", "type": BLOCK_PARAGRAPH, "text": "Clube da Luta e Fight Club."}]
    attach_entity_cards(blocks, [igual, diferente], max_total_blocks=200)
    cards = [b for b in blocks if b["type"] == BLOCK_ENTITY_CARD]
    assert "note" not in cards[0]
    assert cards[1]["note"] == "Clube da Luta"


def test_cards_never_push_the_body_past_the_contract_ceiling():
    blocks = [{"id": f"b{i}", "type": BLOCK_PARAGRAPH, "text": "Texto."} for i in range(10)]
    avisos = attach_entity_cards(
        blocks,
        [_resolvido("Um", "1", 0.9), _resolvido("Dois", "2", 0.9)],
        max_total_blocks=11,
    )
    assert len(blocks) == 11
    assert any("ENTITY_CARDS_NAO_COUBERAM:1" == aviso for aviso in avisos)


# ===========================================================================
# Ponta a ponta, dentro do pedido
# ===========================================================================


CORPO = (
    "<p>O estudio confirmou que Morgan Freeman assinou para narrar o documentario, "
    "encerrando semanas de especulacao sobre quem assumiria o papel.</p>"
    "<h2>O que muda</h2>"
    "<p>A producao comeca no proximo trimestre, com estreia prevista para o fim do ano.</p>"
)


def _draft():
    output_hash = hashlib.sha256(CORPO.encode("utf-8")).hexdigest()
    draft = EditorialDraft(
        draft_id="draft-teste-entity-card",
        article_id=1,
        event_key="evento-teste-entity-card",
        revision=1,
        content=DraftContent(
            title="Morgan Freeman assina para narrar o documentario do estudio",
            subtitle="O anuncio encerra semanas de especulacao sobre o narrador.",
            body_html=CORPO,
            summary=(
                "O estudio confirmou que Morgan Freeman vai narrar o documentario, "
                "com producao marcada para o proximo trimestre."
            ),
            meta_description=(
                "O estudio confirmou que Morgan Freeman vai narrar o documentario "
                "que estreia no fim do ano."
            ),
            focus_keyphrase="Morgan Freeman documentario",
            slug_suggestion="morgan-freeman-narra-documentario",
            category_suggestions=["news"],
        ),
        provenance=DraftProvenance(
            input_hash=hashlib.sha256(b"entrada").hexdigest(),
            output_hash=output_hash,
            input_event_key="evento-teste-entity-card",
            input_revision=1,
            prompt_version="teste@1",
            created_at="2026-08-08T12:00:00+00:00",
        ),
        sources=[
            SourceReference(
                url="https://variety.com/2026/film/news/exemplo-1234",
                domain="variety.com",
                name="Variety",
                is_primary=True,
            )
        ],
    )
    draft.editorial_gate = SimpleNamespace(outcome="GATE_CLEAR")
    draft.factual_assessment = SimpleNamespace(critical_conflicts=[])
    return draft


def _build(resolver=None, maximum=3, match_kinds=TODOS_OS_CASAMENTOS):
    return build_publication_request(
        _draft(),
        public_author_id="1",
        attribution_mode="newsroom",
        contract=local_identity(),
        entity_resolver=resolver,
        entity_card_max=maximum,
        entity_match_kinds=match_kinds,
    )


def test_without_a_resolver_the_body_is_exactly_what_it_was_before():
    """A regressao que mais importa: sem resolvedor, nada muda.

    E o que mantem replay, teste e execucao sem credencial de catalogo
    produzindo o MESMO corpo de antes.
    """
    built = _build(resolver=None)
    assert BLOCK_ENTITY_CARD not in types_of(built.payload["blocks"])
    assert built.entity_cards == []
    assert types_of(built.payload["blocks"])[-1] == BLOCK_SOURCE_LIST


def test_a_resolved_entity_becomes_a_valid_entity_card_inside_a_valid_payload():
    chamadas = []

    def resolver(items):
        chamadas.append(list(items))
        return [
            (
                {
                    "index": indice,
                    "entityKind": "person",
                    "entityId": "4210",
                    "matchedBy": "exact_name",
                    "confidence": 0.85,
                    "canonicalTitle": "Morgan Freeman",
                    "path": "/pt/pessoas/morgan-freeman/",
                }
                if item.get("name") == "Morgan Freeman"
                else {
                    "index": indice,
                    "entityId": None,
                    "matchedBy": None,
                    "confidence": 0,
                    "reason": "not_found",
                }
            )
            for indice, item in enumerate(items)
        ]

    built = _build(resolver=resolver)
    report = validate_request(built.payload, identity=built.contract)
    assert report.ok, [f"{i.path}: {i.message}" for i in report.issues]

    # UMA chamada por materia, nunca uma por entidade.
    assert len(chamadas) == 1

    cards = [b for b in built.payload["blocks"] if b["type"] == BLOCK_ENTITY_CARD]
    assert len(cards) == 1
    assert cards[0]["entityKind"] == "person"
    assert cards[0]["entityId"] == "4210"
    # `sourceList` continua sendo o ultimo bloco.
    assert types_of(built.payload["blocks"])[-1] == BLOCK_SOURCE_LIST


def test_a_resolver_that_fails_never_costs_the_article():
    def resolver(_items):
        raise RuntimeError("catalogo fora do ar")

    built = _build(resolver=resolver)
    report = validate_request(built.payload, identity=built.contract)
    assert report.ok
    assert BLOCK_ENTITY_CARD not in types_of(built.payload["blocks"])
    assert built.payload["qa"]["passed"] is True
    assert "ENTITY_RESOLVE_FALHOU" in built.payload["qa"]["warnings"]


def test_a_resolver_that_returns_none_produces_no_card_and_a_warning():
    """`None` e "nao sei", e "nao sei" nunca vira bloco — em especial porque
    `503 resolve_failed` existe justamente para nao virar `not_found`."""
    built = _build(resolver=lambda _items: None)
    assert BLOCK_ENTITY_CARD not in types_of(built.payload["blocks"])
    assert "ENTITY_RESOLVE_INDISPONIVEL" in built.payload["qa"]["warnings"]
    assert built.payload["qa"]["passed"] is True


def _resolver_fixo(entity_id="4210", kind="person", matched_by="exact_name", confidence=0.85):
    def resolver(items):
        return [
            {
                "index": indice,
                "entityKind": kind,
                "entityId": entity_id,
                "matchedBy": matched_by,
                "confidence": confidence,
            }
            for indice, _item in enumerate(items)
        ]

    return resolver


def test_the_work_the_writer_declared_reaches_the_route_from_the_draft():
    """Ponta a ponta: `obras_citadas` do escritor -> item do lote da rota.

    O caminho inteiro so vale se o campo atravessar o draft. Ele nasce em
    `DraftContent.work_mentions`, sobrevive ao validador (`FIELD_PRESERVED`) e e
    lido aqui, na montagem do pedido.
    """
    enviados: list = []

    def resolver(items):
        enviados.extend(items)
        return [
            {"index": indice, "entityId": None, "matchedBy": None, "reason": "not_found"}
            for indice, _item in enumerate(items)
        ]

    draft = _draft()
    draft.content.work_mentions = [{"title": "Signal One"}]
    build_publication_request(
        draft,
        public_author_id="1",
        attribution_mode="newsroom",
        contract=local_identity(),
        entity_resolver=resolver,
        entity_card_max=3,
        entity_match_kinds=TODOS_OS_CASAMENTOS,
    )
    obras = [item for item in enviados if item.get("title") == "Signal One"]
    assert {item["kind"] for item in obras} == {"movie", "tv"}
    assert all("year" not in item for item in obras)


def test_what_the_route_resolved_becomes_entity_links_in_the_request():
    """O elo que estava cortado: resolver e nao vincular nao liga nada.

    `entityLinks` e a UNICA porta para `entity_news_links`, e e ele que leva a
    materia para a pagina do filme e da pessoa. Ate 27/08/2026 este array saia
    vazio em toda publicacao — ninguem passava o argumento —, e a ficha do filme
    ficava sem uma linha de noticia com a materia no ar.
    """
    built = _build(resolver=_resolver_fixo())
    assert built.payload["entityLinks"] == [
        {
            "entityKind": "person",
            "entityId": "4210",
            "relation": "primary_subject",
            "confidence": 0.85,
        }
    ]


def test_the_confidence_travels_untouched_because_the_cms_decides_with_it():
    """`confidence` decide se o vinculo nasce verificado no CMS (ADR 0019).

    Arredondar para cima aqui compraria auto-verificacao com numero que a rota
    nao devolveu.
    """
    built = _build(resolver=_resolver_fixo(matched_by="exact_title_year", kind="movie", confidence=0.9))
    assert [link["confidence"] for link in built.payload["entityLinks"]] == [0.9]


def test_a_link_declared_by_the_caller_is_not_downgraded_by_the_resolver():
    """Curadoria humana continua vencendo.

    O que muda e o array deixar de sair vazio; o que NAO muda e quem manda: uma
    relacao declarada de fora (`reviewed`) nao vira `mentioned` porque a rota
    resolveu o mesmo par (tipo, id).
    """
    curado = {
        "entityKind": "person",
        "entityId": "4210",
        "relation": "reviewed",
        "confidence": 1.0,
    }
    built = build_publication_request(
        _draft(),
        public_author_id="1",
        attribution_mode="newsroom",
        contract=local_identity(),
        entity_resolver=_resolver_fixo(),
        entity_card_max=3,
        entity_match_kinds=TODOS_OS_CASAMENTOS,
        entity_links=[curado],
    )
    assert built.payload["entityLinks"] == [curado]


def test_an_entity_that_did_not_become_a_card_still_becomes_a_link():
    """Ficha e espaco na pagina; vinculo e indice. Os tetos sao diferentes.

    Limitar o vinculo ao que virou ficha jogaria fora entidade corretamente
    resolvida — na materia da Saoirse Ronan foram 19 resolvidas e 3 fichas.
    """
    resolucao = EntityResolution(
        resolved=[
            ResolvedEntity(
                candidate=EntityCandidate(name=nome, kind="person", prominence=2),
                entity_kind="person",
                entity_id=str(4210 + indice),
                matched_by="exact_name",
                confidence=0.85,
            )
            for indice, nome in enumerate(["Ana Souza", "Bruno Lima", "Carla Dias"])
        ]
    )
    fichas = select_entity_cards(resolucao, maximum=1)
    vinculos = entity_links_from(resolucao.resolved)
    assert len(fichas) == 1
    assert [link["entityId"] for link in vinculos] == ["4210", "4211", "4212"]
    assert {link["relation"] for link in vinculos} == {"mentioned"}
