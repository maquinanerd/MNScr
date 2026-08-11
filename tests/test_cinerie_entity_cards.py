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
    MAX_RESOLVE_ITEMS,
    EntityCandidate,
    EntityResolution,
    ResolvedEntity,
    attach_entity_cards,
    collect_entity_candidates,
    fold,
    parse_resolution,
    relation_for,
    select_entity_cards,
    to_entity_links,
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


# ===========================================================================
# `entityLinks[]` — o que alimenta "Destaques de hoje"
# ===========================================================================


def _resolver_de(por_nome):
    """Resolvedor que so conhece os nomes do dicionario; o resto e `not_found`.

    A chave e o `name`/`title` do item enviado, e o valor e a linha da rota.
    """

    def resolver(items):
        linhas = []
        for indice, item in enumerate(items):
            chave = item.get("name") or item.get("title")
            linha = por_nome.get(chave)
            if linha is None or linha.get("kind_esperado", item.get("kind")) != item.get("kind"):
                linhas.append(
                    {"index": indice, "entityId": None, "matchedBy": None, "reason": "not_found"}
                )
                continue
            linhas.append({**{k: v for k, v in linha.items() if k != "kind_esperado"}, "index": indice})
        return linhas

    return resolver


def test_a_resolved_entity_becomes_an_entity_link_with_the_exact_confidence():
    """O vinculo sai com a confianca que a ROTA calculou, sem arredondar.

    Do outro lado o corte e 0.9: `exact_title_year` (0.90) nasce verificado,
    `exact_name` (0.85) espera um humano. Empurrar 0.85 para 0.9 faria o Cinerie
    auto-verificar exatamente o vinculo que a regra dele segura de proposito —
    `exact_name` e o unico casamento sem um segundo campo confirmando identidade.
    """
    built = _build(
        resolver=_resolver_de(
            {
                "Morgan Freeman": {
                    "entityKind": "person",
                    "entityId": "4210",
                    "matchedBy": "exact_name",
                    "confidence": 0.85,
                    "kind_esperado": "person",
                }
            }
        )
    )
    report = validate_request(built.payload, identity=built.contract)
    assert report.ok, [f"{i.path}: {i.message}" for i in report.issues]

    links = built.payload["entityLinks"]
    assert len(links) == 1
    assert links[0]["entityKind"] == "person"
    assert links[0]["entityId"] == "4210"
    assert links[0]["confidence"] == 0.85
    assert links[0]["confidence"] < 0.9


def test_the_relation_comes_from_where_the_text_cites_the_entity():
    """Titulo -> `primary_subject`; lead -> `secondary_subject`; resto -> `mentioned`.

    Nao ha heuristica nova: e a mesma `prominence` que a coleta ja apurou contra
    o texto, lida agora como papel.
    """
    assert relation_for(0) == "primary_subject"
    assert relation_for(1) == "secondary_subject"
    assert relation_for(2) == "mentioned"

    candidatos = collect_entity_candidates(
        title="Christopher Nolan volta a dirigir",
        lead="O anuncio foi feito por Emma Thomas nesta terca.",
        blocks=[{"type": "paragraph", "text": "Cillian Murphy tambem esta no elenco."}],
    )
    por_nome = {c.name: c for c in candidatos}
    resolucao = EntityResolution(
        resolved=[
            ResolvedEntity(
                candidate=por_nome[nome],
                entity_kind="person",
                entity_id=identificador,
                matched_by="exact_name",
                confidence=0.85,
            )
            for nome, identificador in (
                ("Christopher Nolan", "11"),
                ("Emma Thomas", "22"),
                ("Cillian Murphy", "33"),
            )
        ]
    )
    papeis = {link["entityId"]: link["relation"] for link in to_entity_links(resolucao)}
    assert papeis == {
        "11": "primary_subject",
        "22": "secondary_subject",
        "33": "mentioned",
    }


def test_the_same_entity_never_produces_two_links():
    """Uma entidade, um vinculo — e vale o papel MAIS FORTE.

    O Cinerie projeta `(entityKind, entityId)` em `entity_news_links`: a mesma
    materia repetida na mesma entidade nao significa nada la.
    """
    citado_duas_vezes = collect_entity_candidates(
        title="Morgan Freeman assina novo projeto",
        lead="",
        blocks=[{"type": "paragraph", "text": "Morgan Freeman ja trabalhou com o estudio."}],
    )
    candidato = next(c for c in citado_duas_vezes if c.name == "Morgan Freeman")
    # A coleta ja guardou a posicao mais forte: titulo vence corpo.
    assert candidato.prominence == 0

    resolucao = EntityResolution(
        resolved=[
            ResolvedEntity(
                candidate=candidato,
                entity_kind="person",
                entity_id="4210",
                matched_by="exact_name",
                confidence=0.85,
            ),
            # O mesmo id alcancado por outro candidato — acontece quando o nome
            # aparece em duas formas.
            ResolvedEntity(
                candidate=EntityCandidate(name="Freeman", kind="person", prominence=2),
                entity_kind="person",
                entity_id="4210",
                matched_by="exact_name",
                confidence=0.85,
            ),
        ]
    )
    links = to_entity_links(resolucao)
    assert len(links) == 1
    assert links[0]["relation"] == "primary_subject"


def test_an_unresolved_entity_never_becomes_a_link():
    """`null` e recusa da rota, e recusa nao vira palpite de baixa confianca.

    Vinculo errado mostra a obra errada com cara de certa — e o `entityLinks`
    ainda alimenta a Home, onde o erro fica visivel fora da materia.
    """
    built = _build(resolver=_resolver_de({}))
    assert built.payload["entityLinks"] == []


def test_a_match_below_the_threshold_never_becomes_a_link():
    """O limiar (`MNSCR_CINERIE_ENTITY_MATCH_KINDS`) vale para os dois destinos.

    Se valesse so para a ficha, o casamento recusado no corpo continuaria
    entrando na Home pela porta de tras.
    """
    built = _build(
        resolver=_resolver_de(
            {
                "Morgan Freeman": {
                    "entityKind": "person",
                    "entityId": "4210",
                    "matchedBy": "exact_name",
                    "confidence": 0.85,
                    "kind_esperado": "person",
                }
            }
        ),
        match_kinds=("tmdb_id", "exact_title_year"),
    )
    assert built.payload["entityLinks"] == []


def test_human_curation_is_never_overwritten_by_the_resolver():
    """Quem chama vem primeiro; a resolucao preenche o que falta.

    `entityLinks` carrega `verified`, que e decisao de gente, e o CMS nem
    reaplica o campo em `update` (`ENTITY_LINK_NOT_REAPPLIED`). Um vinculo vindo
    de fora e curadoria: a dobra mantem o primeiro de cada entidade.
    """
    built = build_publication_request(
        _draft(),
        public_author_id="1",
        attribution_mode="newsroom",
        contract=local_identity(),
        entity_links=[
            {
                "entityKind": "person",
                "entityId": "4210",
                "relation": "reviewed",
                "confidence": 1.0,
            }
        ],
        entity_resolver=_resolver_de(
            {
                "Morgan Freeman": {
                    "entityKind": "person",
                    "entityId": "4210",
                    "matchedBy": "exact_name",
                    "confidence": 0.85,
                    "kind_esperado": "person",
                }
            }
        ),
        entity_card_max=3,
        entity_match_kinds=TODOS_OS_CASAMENTOS,
    )
    links = built.payload["entityLinks"]
    assert len(links) == 1
    assert links[0]["relation"] == "reviewed"
    assert links[0]["confidence"] == 1.0


def test_a_card_ceiling_of_zero_silences_the_body_and_not_the_home():
    """`MNSCR_CINERIE_ENTITY_CARD_MAX` decide o CORPO, nao a Home.

    Se ele calasse tambem os `entityLinks`, "Destaques de hoje" ficaria vazia por
    causa de uma variavel que nao diz nada sobre ela — e sem nada no log ligando
    uma coisa a outra.
    """
    built = _build(
        resolver=_resolver_de(
            {
                "Morgan Freeman": {
                    "entityKind": "person",
                    "entityId": "4210",
                    "matchedBy": "exact_name",
                    "confidence": 0.85,
                    "kind_esperado": "person",
                }
            }
        ),
        maximum=0,
    )
    assert BLOCK_ENTITY_CARD not in types_of(built.payload["blocks"])
    assert built.entity_cards == []
    assert [link["entityId"] for link in built.payload["entityLinks"]] == ["4210"]


def test_a_resolver_that_fails_sends_no_links_either():
    """Enriquecimento nao derruba materia — e falha nao vira vinculo."""
    def resolver(_items):
        raise RuntimeError("catalogo fora do ar")

    built = _build(resolver=resolver)
    assert built.payload["entityLinks"] == []
    assert built.payload["qa"]["passed"] is True
