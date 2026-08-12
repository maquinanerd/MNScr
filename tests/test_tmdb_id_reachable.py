"""O `tmdb_id` deixou de ser inalcancavel — e o preco de erra-lo nao mudou.

Duas coisas mantinham o casamento de confianca 1.0 fora de alcance, e as duas
foram medidas ao vivo antes deste arquivo existir:

1. o item enviado a rota carregava so `kind` + `name`/`title`+`year`;
2. a credencial da TMDB no `.env` era um token **v4** guardado numa variavel
   chamada `TMDB_API_KEY`, e o endpoint v3 responde 401 com ela.

Consertar so uma das duas nao resolveria nada, e por isso este arquivo cobre as
duas pontas. Mas o que ele mede COM MAIS INSISTENCIA e a terceira coisa, que so
apareceu na sonda: **a rota nao volta ao nome quando o `tmdbId` nao esta no
catalogo**. Um id errado nao produz "casamento pior" — produz `null` onde antes
havia vinculo. Logo, todo teste aqui que parece paranoico esta medindo a mesma
propriedade: *ligar isto nunca pode publicar menos do que ontem.*
"""

import pytest

from app.cinerie.entity_resolve import (
    TMDB_ID_NOT_IN_CATALOG,
    EntityCandidate,
    enrich_with_tmdb_ids,
    parse_resolution,
    resolve_in_two_passes,
)
from app.config import TMDB_AUTH_BEARER, TMDB_AUTH_QUERY, tmdb_auth_mode, tmdb_credential
from app.tmdb_lookup import TmdbCredential, TmdbLookup

TODOS_OS_CASAMENTOS = ("tmdb_id", "exact_title_year", "exact_name")


class FakeResposta:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        if self._payload is None:
            raise ValueError("nao e JSON")
        return self._payload


class FakeSession:
    """Guarda cada chamada para que o teste possa contar — e conferir o cabecalho."""

    def __init__(self, *respostas):
        self.respostas = list(respostas)
        self.chamadas = []

    def request(self, method, url, *, headers=None, params=None, timeout=None):
        self.chamadas.append({"url": url, "headers": dict(headers or {}), "params": dict(params or {})})
        if not self.respostas:
            raise AssertionError("chamada a mais para a TMDB")
        return self.respostas.pop(0)


def busca(*respostas, **kwargs):
    sessao = FakeSession(*respostas)
    lookup = TmdbLookup(
        TmdbCredential(value="eyJhbGc.payload.assinatura", mode=TMDB_AUTH_BEARER),
        session=sessao,
        cache={},
        **kwargs,
    )
    return lookup, sessao


# ===========================================================================
# A credencial: o modo vem da FORMA, nunca do nome da variavel
# ===========================================================================


def test_a_jwt_v4_is_recognized_as_a_bearer_token():
    """Tres segmentos comecando em `eyJ` — o formato que o `.env` guardava.

    Foi este valor, mandado como query param `api_key`, que respondeu 401 e
    manteve a TMDB na lista de pendencias.
    """
    assert tmdb_auth_mode("eyJhbGciOiJIUzI1NiJ9.eyJhdWQiOiJ4In0.assinatura") == TMDB_AUTH_BEARER


def test_a_v3_key_is_recognized_as_a_query_param():
    assert tmdb_auth_mode("0123456789abcdef0123456789abcdef") == TMDB_AUTH_QUERY


@pytest.mark.parametrize("valor", ["", "   ", "chave-que-alguem-inventou", "eyJsemponto"])
def test_an_unknown_shape_is_refused_instead_of_being_tried_blindly(valor):
    """Tentar assim mesmo volta 401, e 401 le-se como "credencial errada".

    O defeito seria a credencial estar na FORMA errada — outra conversa, e a
    unica das duas que tem conserto sem pedir chave nova a ninguem.
    """
    assert tmdb_auth_mode(valor) == ""


def test_the_canonical_variable_wins_over_the_legacy_one(monkeypatch):
    monkeypatch.setenv("TMDB_ACCESS_TOKEN", "eyJhbGc.canonico.assinatura")
    monkeypatch.setenv("TMDB_API_KEY", "0123456789abcdef0123456789abcdef")
    valor, modo = tmdb_credential()
    assert (valor, modo) == ("eyJhbGc.canonico.assinatura", TMDB_AUTH_BEARER)


def test_the_legacy_variable_still_works_because_that_is_where_the_token_lives(monkeypatch):
    """Renomear nao pode quebrar a maquina que ja rodava.

    `TMDB_API_KEY` continua aceita — com WARNING, porque um nome que mente
    sobre o conteudo e armadilha para o proximo.
    """
    monkeypatch.delenv("TMDB_ACCESS_TOKEN", raising=False)
    monkeypatch.setenv("TMDB_API_KEY", "eyJhbGc.legado.assinatura")
    assert tmdb_credential() == ("eyJhbGc.legado.assinatura", TMDB_AUTH_BEARER)


def test_no_credential_at_all_is_not_an_error(monkeypatch):
    monkeypatch.delenv("TMDB_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("TMDB_API_KEY", raising=False)
    assert tmdb_credential() == ("", "")


def test_the_credential_never_shows_up_in_a_repr():
    """`repr` de objeto vaza em log de excecao. O valor nao pode estar la."""
    texto = repr(TmdbCredential(value="eyJhbGc.segredo.assinatura", mode=TMDB_AUTH_BEARER))
    assert "segredo" not in texto
    assert TMDB_AUTH_BEARER in texto


def test_the_v4_token_travels_as_a_header_and_never_as_a_query_param():
    lookup, sessao = busca(FakeResposta({"results": [{"id": 2037, "name": "Cillian Murphy"}]}))
    assert lookup.find("person", "Cillian Murphy") == 2037
    chamada = sessao.chamadas[0]
    assert chamada["headers"]["Authorization"].startswith("Bearer ")
    assert "api_key" not in chamada["params"]


def test_the_v3_key_travels_as_a_query_param_and_never_as_a_header():
    sessao = FakeSession(FakeResposta({"results": [{"id": 2037, "name": "Cillian Murphy"}]}))
    lookup = TmdbLookup(
        TmdbCredential(value="0123456789abcdef0123456789abcdef", mode=TMDB_AUTH_QUERY),
        session=sessao,
        cache={},
    )
    assert lookup.find("person", "Cillian Murphy") == 2037
    chamada = sessao.chamadas[0]
    assert chamada["params"]["api_key"] == "0123456789abcdef0123456789abcdef"
    assert "Authorization" not in chamada["headers"]


# ===========================================================================
# A busca: igualdade exata e unicidade, ou nada
# ===========================================================================


def test_a_single_exact_match_gives_the_id():
    lookup, _ = busca(FakeResposta({"results": [{"id": 872585, "title": "Oppenheimer"}]}))
    assert lookup.find("movie", "Oppenheimer", 2023) == 872585


def test_the_accent_folds_on_both_sides():
    """"Chloe Zhao" e "Chloé Zhao" sao o MESMO nome — e precisam ser, senao o
    id encontrado aqui nao corresponderia ao nome enviado a rota."""
    lookup, _ = busca(FakeResposta({"results": [{"id": 1223787, "name": "Chloé Zhao"}]}))
    assert lookup.find("person", "Chloe Zhao") == 1223787


def test_two_people_with_the_same_exact_name_give_nothing():
    """O caso "Chris Evans" — o ator e o apresentador britanico.

    E exatamente o homonimo que a ADR usa para recusar baixar o corte para
    0.85. Desempatar por popularidade aqui publicaria "o mais famoso parecido",
    que e a obra errada com cara de certa. Sem id, o vinculo cai no caminho
    antigo e a unicidade no catalogo do Cinerie continua sendo quem o segura.
    """
    lookup, _ = busca(
        FakeResposta(
            {"results": [
                {"id": 16828, "name": "Chris Evans", "popularity": 90.0},
                {"id": 84287, "name": "Chris Evans", "popularity": 1.0},
            ]}
        )
    )
    assert lookup.find("person", "Chris Evans") is None


def test_the_first_result_is_not_the_answer_when_nothing_matches_exactly():
    """A TMDB ordena por popularidade e responde a aproximacoes."""
    lookup, _ = busca(
        FakeResposta({"results": [{"id": 1, "title": "Oppenheimer After Trinity"}]})
    )
    assert lookup.find("movie", "Oppenheimer", 2023) is None


def test_the_original_title_also_counts_as_an_exact_match():
    """Materia em portugues cita ora o titulo traduzido, ora o de origem."""
    lookup, _ = busca(
        FakeResposta({"results": [{"id": 95396, "name": "Ruptura", "original_name": "Severance"}]})
    )
    assert lookup.find("tv", "Severance", 2022) == 95396


def test_the_year_travels_with_the_parameter_each_kind_expects():
    lookup, sessao = busca(
        FakeResposta({"results": [{"id": 1, "title": "X"}]}),
        FakeResposta({"results": [{"id": 2, "name": "X"}]}),
    )
    lookup.find("movie", "X", 2023)
    lookup.find("tv", "X", 2022)
    assert sessao.chamadas[0]["params"]["primary_release_year"] == 2023
    assert sessao.chamadas[1]["params"]["first_air_date_year"] == 2022


def test_a_person_search_carries_no_year_at_all():
    """Pessoa nao tem ano de estreia, e mandar um so estreitaria a busca a toa."""
    lookup, sessao = busca(FakeResposta({"results": [{"id": 525, "name": "Christopher Nolan"}]}))
    lookup.find("person", "Christopher Nolan", 2023)
    assert "primary_release_year" not in sessao.chamadas[0]["params"]
    assert "first_air_date_year" not in sessao.chamadas[0]["params"]


@pytest.mark.parametrize("kind", ["season", "episode", "franchise", "character", ""])
def test_a_kind_the_route_cannot_resolve_is_never_searched(kind):
    """`season`/`episode` respondem `unsupported_kind` na rota, verificado ao
    vivo. Gastar uma chamada de TMDB por eles seria quota jogada fora."""
    lookup, sessao = busca()
    assert lookup.find(kind, "Alguma Coisa", 2023) is None
    assert sessao.chamadas == []


# ===========================================================================
# Falha: sempre `None`, nunca excecao, nunca palpite
# ===========================================================================


@pytest.mark.parametrize("status", [401, 404, 429, 500])
def test_any_refusal_becomes_none(status):
    lookup, _ = busca(FakeResposta({"status_message": "nao"}, status_code=status))
    assert lookup.find("person", "Cillian Murphy") is None


def test_a_broken_body_becomes_none():
    lookup, _ = busca(FakeResposta(None))
    assert lookup.find("person", "Cillian Murphy") is None


def test_the_transport_blowing_up_becomes_none():
    class Explode:
        def request(self, *args, **kwargs):
            raise ConnectionError("sem rede")

    lookup = TmdbLookup(
        TmdbCredential(value="eyJa.b.c", mode=TMDB_AUTH_BEARER), session=Explode(), cache={}
    )
    assert lookup.find("person", "Cillian Murphy") is None


def test_without_a_usable_credential_nothing_is_even_asked():
    lookup, sessao = busca()
    lookup.credential = TmdbCredential(value="", mode="")
    assert lookup.find("person", "Cillian Murphy") is None
    assert sessao.chamadas == []


# ===========================================================================
# Cache: a mesma entidade nao e perguntada duas vezes
# ===========================================================================


def test_the_same_entity_is_asked_once_per_process():
    lookup, sessao = busca(FakeResposta({"results": [{"id": 2037, "name": "Cillian Murphy"}]}))
    assert lookup.find("person", "Cillian Murphy") == 2037
    assert lookup.find("person", "Cillian Murphy") == 2037
    assert lookup.find("person", "cillian  murphy") == 2037  # a dobra e a chave
    assert len(sessao.chamadas) == 1


def test_the_negative_answer_is_cached_too():
    """O que MAIS aparece e ruido com forma de nome proprio. Sem cache negativo,
    ele seria perguntado de novo em cada materia do mesmo cluster."""
    lookup, sessao = busca(FakeResposta({"results": []}))
    assert lookup.find("person", "Nome Que Nao Existe") is None
    assert lookup.find("person", "Nome Que Nao Existe") is None
    assert len(sessao.chamadas) == 1


def test_the_cache_is_shared_across_articles_of_the_same_run():
    compartilhado = {}
    primeira = TmdbLookup(
        TmdbCredential(value="eyJa.b.c", mode=TMDB_AUTH_BEARER),
        session=FakeSession(FakeResposta({"results": [{"id": 2037, "name": "Cillian Murphy"}]})),
        cache=compartilhado,
    )
    assert primeira.find("person", "Cillian Murphy") == 2037

    sessao_seguinte = FakeSession()  # qualquer chamada aqui e uma chamada a mais
    segunda = TmdbLookup(
        TmdbCredential(value="eyJa.b.c", mode=TMDB_AUTH_BEARER),
        session=sessao_seguinte,
        cache=compartilhado,
    )
    assert segunda.find("person", "Cillian Murphy") == 2037
    assert sessao_seguinte.chamadas == []


# ===========================================================================
# O item: o id so entra quando existe
# ===========================================================================


def test_the_item_carries_the_tmdb_id_when_the_search_resolved():
    candidato = EntityCandidate(name="Cillian Murphy", kind="person", tmdb_id=2037)
    assert candidato.as_resolve_item() == {
        "kind": "person", "name": "Cillian Murphy", "tmdbId": 2037,
    }


def test_the_item_looks_exactly_like_before_when_there_is_no_id():
    """A garantia de que ligar isto nao muda o pedido de quem nao resolve."""
    candidato = EntityCandidate(name="Oppenheimer", kind="movie", year=2023)
    assert candidato.as_resolve_item() == {"kind": "movie", "title": "Oppenheimer", "year": 2023}


def test_the_same_item_can_be_asked_again_without_the_id():
    candidato = EntityCandidate(name="Cillian Murphy", kind="person", tmdb_id=2037)
    assert candidato.as_resolve_item(with_tmdb_id=False) == {
        "kind": "person", "name": "Cillian Murphy",
    }


def test_enrichment_without_a_lookup_returns_the_very_same_candidates():
    candidatos = [EntityCandidate(name="Cillian Murphy", kind="person")]
    assert enrich_with_tmdb_ids(candidatos, None) == candidatos


def test_a_lookup_that_blows_up_costs_the_id_and_nothing_else():
    def explode(kind, nome, ano):
        raise RuntimeError("a TMDB caiu")

    candidatos = [EntityCandidate(name="Cillian Murphy", kind="person")]
    assert enrich_with_tmdb_ids(candidatos, explode) == candidatos


def test_enrichment_asks_the_lookup_with_kind_name_and_year():
    perguntas = []

    def lookup(kind, nome, ano):
        perguntas.append((kind, nome, ano))
        return 872585 if kind == "movie" else None

    candidatos = [
        EntityCandidate(name="Oppenheimer", kind="movie", year=2023),
        EntityCandidate(name="Nome Solto", kind="person"),
    ]
    enriquecidos = enrich_with_tmdb_ids(candidatos, lookup)
    assert perguntas == [("movie", "Oppenheimer", 2023), ("person", "Nome Solto", None)]
    assert enriquecidos[0].tmdb_id == 872585
    assert enriquecidos[1].tmdb_id is None


# ===========================================================================
# As duas passadas — o achado que organiza tudo
# ===========================================================================


class ResolvedorGravado:
    """Devolve respostas em ordem e guarda os lotes que recebeu."""

    def __init__(self, *respostas):
        self.respostas = list(respostas)
        self.lotes = []

    def __call__(self, itens):
        self.lotes.append([dict(item) for item in itens])
        return self.respostas.pop(0) if self.respostas else None


def test_an_id_the_catalog_knows_matches_at_full_confidence():
    candidatos = [EntityCandidate(name="Cillian Murphy", kind="person", tmdb_id=2037)]
    resolvedor = ResolvedorGravado(
        [{"index": 0, "entityId": "7", "entityKind": "person", "matchedBy": "tmdb_id",
          "confidence": 1, "canonicalTitle": "Cillian Murphy"}]
    )
    resultados = resolve_in_two_passes(candidatos, resolvedor)
    resolucao = parse_resolution(resultados, candidatos, accepted_match_kinds=TODOS_OS_CASAMENTOS)
    assert len(resolvedor.lotes) == 1
    assert resolucao.resolved[0].matched_by == "tmdb_id"
    assert resolucao.resolved[0].confidence == 1.0


def test_an_id_the_catalog_does_not_know_falls_back_to_the_name():
    """**O teste que justifica a segunda passada.**

    Medido ao vivo: a rota NAO volta ao nome sozinha. Sem esta passada, mandar
    o id seria uma aposta — ganharia 1.0 onde o catalogo indexa por TMDB e
    perderia o vinculo INTEIRO onde nao indexa.
    """
    candidatos = [EntityCandidate(name="Cillian Murphy", kind="person", tmdb_id=999999999)]
    resolvedor = ResolvedorGravado(
        [{"index": 0, "entityId": None, "matchedBy": None, "confidence": 0,
          "reason": TMDB_ID_NOT_IN_CATALOG}],
        [{"index": 0, "entityId": "7", "entityKind": "person", "matchedBy": "exact_name",
          "confidence": 0.85, "canonicalTitle": "Cillian Murphy"}],
    )
    resultados = resolve_in_two_passes(candidatos, resolvedor)
    resolucao = parse_resolution(resultados, candidatos, accepted_match_kinds=TODOS_OS_CASAMENTOS)

    assert "tmdbId" in resolvedor.lotes[0][0]
    assert "tmdbId" not in resolvedor.lotes[1][0]
    assert resolucao.resolved[0].entity_id == "7"
    assert resolucao.resolved[0].confidence == 0.85


def test_the_second_pass_only_carries_what_the_first_one_dropped():
    """E o `index` volta a apontar para o candidato ORIGINAL.

    Alinhar errado publicaria a ficha de uma entidade sob o nome de outra — a
    falha exata que o modulo inteiro existe para impedir.
    """
    candidatos = [
        EntityCandidate(name="Cillian Murphy", kind="person", tmdb_id=2037, prominence=0),
        EntityCandidate(name="Emily Blunt", kind="person", tmdb_id=999999999, prominence=1),
        EntityCandidate(name="Robert Downey Jr.", kind="person", prominence=2),
    ]
    resolvedor = ResolvedorGravado(
        [
            {"index": 0, "entityId": "7", "entityKind": "person", "matchedBy": "tmdb_id", "confidence": 1},
            {"index": 1, "entityId": None, "matchedBy": None, "confidence": 0,
             "reason": TMDB_ID_NOT_IN_CATALOG},
            {"index": 2, "entityId": "1599", "entityKind": "person", "matchedBy": "exact_name",
             "confidence": 0.85},
        ],
        [
            {"index": 0, "entityId": "1597", "entityKind": "person", "matchedBy": "exact_name",
             "confidence": 0.85},
        ],
    )
    resultados = resolve_in_two_passes(candidatos, resolvedor)
    resolucao = parse_resolution(resultados, candidatos, accepted_match_kinds=TODOS_OS_CASAMENTOS)

    assert [item["name"] for item in resolvedor.lotes[1]] == ["Emily Blunt"]
    por_nome = {item.candidate.name: item for item in resolucao.resolved}
    assert por_nome["Cillian Murphy"].entity_id == "7"
    assert por_nome["Emily Blunt"].entity_id == "1597"
    assert por_nome["Robert Downey Jr."].entity_id == "1599"


def test_nothing_dropped_means_a_single_call():
    """O teto da rota e 60 por minuto por credencial. A segunda pergunta so
    existe quando ha o que reperguntar."""
    candidatos = [EntityCandidate(name="Cillian Murphy", kind="person", tmdb_id=2037)]
    resolvedor = ResolvedorGravado(
        [{"index": 0, "entityId": "7", "entityKind": "person", "matchedBy": "tmdb_id", "confidence": 1}]
    )
    resolve_in_two_passes(candidatos, resolvedor)
    assert len(resolvedor.lotes) == 1


def test_a_not_found_without_an_id_is_not_asked_twice():
    """`not_found` e a resposta final: o nome nao esta no catalogo, e repetir a
    MESMA pergunta devolveria a MESMA resposta."""
    candidatos = [EntityCandidate(name="Nome Solto", kind="person")]
    resolvedor = ResolvedorGravado(
        [{"index": 0, "entityId": None, "matchedBy": None, "confidence": 0, "reason": "not_found"}]
    )
    resolve_in_two_passes(candidatos, resolvedor)
    assert len(resolvedor.lotes) == 1


def test_a_failing_second_pass_keeps_the_first_answer():
    """Falha de leitura nao pode virar excecao: o resto da materia ja resolveu."""
    candidatos = [
        EntityCandidate(name="Cillian Murphy", kind="person", tmdb_id=2037),
        EntityCandidate(name="Emily Blunt", kind="person", tmdb_id=999999999),
    ]
    resolvedor = ResolvedorGravado(
        [
            {"index": 0, "entityId": "7", "entityKind": "person", "matchedBy": "tmdb_id", "confidence": 1},
            {"index": 1, "entityId": None, "matchedBy": None, "confidence": 0,
             "reason": TMDB_ID_NOT_IN_CATALOG},
        ],
        None,
    )
    resultados = resolve_in_two_passes(candidatos, resolvedor)
    resolucao = parse_resolution(resultados, candidatos, accepted_match_kinds=TODOS_OS_CASAMENTOS)
    assert [item.entity_id for item in resolucao.resolved] == ["7"]


def test_a_resolver_that_is_unavailable_stays_unavailable():
    """`None` e "nao sei", e "nao sei" nunca vira bloco nem vinculo."""
    candidatos = [EntityCandidate(name="Cillian Murphy", kind="person", tmdb_id=2037)]
    assert resolve_in_two_passes(candidatos, ResolvedorGravado(None)) is None


# ===========================================================================
# A ponta que importa: o vinculo nasce verificado
# ===========================================================================


def test_the_confidence_the_route_gave_is_the_confidence_that_travels():
    """1.0 vai como 1.0 e 0.85 vai como 0.85 — o corte do Cinerie e 0.9.

    Empurrar 0.85 para cima faria o Cinerie auto-verificar exatamente o vinculo
    que a regra dele segura de proposito; arredondar 1.0 para baixo desperdicaria
    o unico casamento que nasce verificado.
    """
    from app.cinerie.entity_resolve import CINERIE_VERIFIED_CUTOFF, to_entity_links

    candidatos = [
        EntityCandidate(name="Cillian Murphy", kind="person", tmdb_id=2037, prominence=0),
        EntityCandidate(name="Robert Downey Jr.", kind="person", prominence=2),
    ]
    resolucao = parse_resolution(
        [
            {"index": 0, "entityId": "7", "entityKind": "person", "matchedBy": "tmdb_id", "confidence": 1},
            {"index": 1, "entityId": "1599", "entityKind": "person", "matchedBy": "exact_name",
             "confidence": 0.85},
        ],
        candidatos,
        accepted_match_kinds=TODOS_OS_CASAMENTOS,
    )
    links = to_entity_links(resolucao)
    assert links[0] == {
        "entityKind": "person", "entityId": "7",
        "relation": "primary_subject", "confidence": 1.0,
    }
    assert links[0]["confidence"] >= CINERIE_VERIFIED_CUTOFF   # nasce verificado
    assert links[1]["confidence"] == 0.85
    assert links[1]["confidence"] < CINERIE_VERIFIED_CUTOFF    # espera um humano
