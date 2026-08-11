"""O terceiro passo da foto: `PATCH /api/internal/editorial-media/:mediaId/hero`.

Ate 07/08 o log de producao dizia, toda rodada:

    mediaId=14 obtido para article_id=19, mas a capa NAO foi apontada

A foto entrava no acervo, ganhava id, e nenhuma materia tinha capa. A causa era
de ORDEM: a foto so pode ser ingerida depois de a materia existir, e a materia
so poderia declarar `media[]` antes de a foto existir.

O que este arquivo cobre e menos o caminho feliz e mais as RECUSAS, porque e la
que estao as duas coisas que podem custar caro:

1. **nenhuma recusa pode derrubar a materia.** A esta altura o Cinerie ja
   aceitou o texto e devolveu um `article_id` real. Uma excecao vazando daqui
   seria lida como "a publicacao falhou" e o id seria descartado;
2. **os quatro `409` sao de ESTADO, e nao de id errado.** Quem le
   `article_not_automation_draft` e conclui "mediaId invalido" vai procurar
   defeito num id que estava certo. O desfecho e tratado pelo CODIGO, nunca
   pelo texto da mensagem.
"""

import pytest

from app.cinerie.client import CinerieClient, CinerieConfig
from app.cinerie.errors import MediaHeroError
from app.cinerie.media_client import HERO_STATE_CONFLICTS, media_hero_path
from app.cinerie.media_selection import point_hero_media


class _FakeResponse:
    def __init__(self, status_code, body=b"", headers=None):
        self.status_code = status_code
        self.content = body
        self.headers = headers or {}


class _FakeSession:
    """Grava o que foi enviado e devolve o que o teste mandar."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        return self._responses.pop(0)


def _client(session):
    return CinerieClient(
        CinerieConfig(base_url="https://cms.cinerie.com", api_key="chave-de-publicacao"),
        session=session,
    )


def _json(payload):
    import json

    return json.dumps(payload).encode("utf-8")


# ===========================================================================
# O pedido
# ===========================================================================


def test_media_id_goes_in_the_path_and_article_id_in_the_body():
    """A assimetria e do contrato e nao e enfeite.

    A foto e o recurso ALTERADO — por isso identifica a URL. A materia e a
    afirmacao que sera CONFERIDA contra `media.ingestedForArticle`.
    """
    assert media_hero_path("14") == "/api/internal/editorial-media/14/hero"

    session = _FakeSession(
        [_FakeResponse(200, _json({"outcome": "linked", "articleId": "19", "mediaId": "14"}))]
    )
    resultado = _client(session).point_media_as_hero(
        media_id="14", article_id="19", api_key="chave-de-midia"
    )

    chamada = session.calls[0]
    assert chamada["method"] == "PATCH"
    assert chamada["url"] == "https://cms.cinerie.com/api/internal/editorial-media/14/hero"
    assert chamada["data"] == b'{"articleId": "19"}'
    assert resultado.outcome == "linked"


def test_the_hero_step_uses_the_same_credential_as_the_ingest_step():
    """Mesmo escopo (`editorial_media_ingest`), nenhuma variavel de ambiente nova.

    Quem ja pode POR a foto no acervo daquela materia nao ganha poder novo ao
    dizer qual delas e a capa. Um terceiro escopo sugeriria uma fronteira que
    nao existe e daria mais uma chave para rotacionar.
    """
    session = _FakeSession(
        [_FakeResponse(200, _json({"outcome": "linked", "articleId": "19", "mediaId": "14"}))]
    )
    _client(session).point_media_as_hero(
        media_id="14", article_id="19", api_key="chave-de-midia"
    )
    autorizacao = session.calls[0]["headers"]["Authorization"]
    assert autorizacao.endswith("chave-de-midia")
    assert "chave-de-publicacao" not in autorizacao


# ===========================================================================
# Os desfechos de sucesso
# ===========================================================================


@pytest.mark.parametrize("outcome", ["linked", "unchanged", "replaced"])
def test_the_three_named_outcomes_come_back_as_results(outcome):
    session = _FakeSession(
        [
            _FakeResponse(
                200,
                _json(
                    {
                        "outcome": outcome,
                        "articleId": "19",
                        "mediaId": "14",
                        "previousMediaId": "13" if outcome == "replaced" else None,
                    }
                ),
            )
        ]
    )
    resultado = _client(session).point_media_as_hero(
        media_id="14", article_id="19", api_key="k"
    )
    assert resultado.outcome == outcome
    assert resultado.article_id == "19"
    assert resultado.previous_media_id == ("13" if outcome == "replaced" else None)


def test_unchanged_reports_that_nothing_was_written():
    """`unchanged` nao escrever nao e otimizacao.

    Cada `update` cria uma linha de versao, e um pipeline que reenvia a cada
    revisao encheria o historico da materia com versoes que nao mudaram nada.
    """
    session = _FakeSession(
        [_FakeResponse(200, _json({"outcome": "unchanged", "articleId": "19", "mediaId": "14"}))]
    )
    resultado = _client(session).point_media_as_hero(media_id="14", article_id="19", api_key="k")
    assert resultado.wrote is False


def test_a_200_with_an_unknown_outcome_is_not_taken_for_success():
    """Um desfecho fora dos tres pode ter escrito ou nao.

    Presumir sucesso registraria uma capa que talvez nao exista, e o log diria
    que a materia tem foto quando ninguem sabe se tem.
    """
    session = _FakeSession([_FakeResponse(200, _json({"outcome": "talvez"}))])
    with pytest.raises(MediaHeroError) as excecao:
        _client(session).point_media_as_hero(media_id="14", article_id="19", api_key="k")
    assert excecao.value.remote_code == "invalid_response"


# ===========================================================================
# As recusas — o `409` e o resto
# ===========================================================================


@pytest.mark.parametrize("codigo", sorted(HERO_STATE_CONFLICTS))
def test_every_409_is_a_state_conflict_and_never_a_wrong_media_id(codigo):
    """Os quatro `409` dizem que a CAPA nao pode ser apontada agora.

    Nenhum deles diz que o `mediaId` estava errado — e a distincao e o que
    separa "procure o defeito no id" de "a materia saiu do alcance da
    automacao, e dali em diante a capa e de quem edita".
    """
    session = _FakeSession([_FakeResponse(409, _json({"error": codigo}))])
    with pytest.raises(MediaHeroError) as excecao:
        _client(session).point_media_as_hero(media_id="14", article_id="19", api_key="k")
    assert excecao.value.is_state_conflict is True
    assert excecao.value.remote_code == codigo
    # A explicacao do contrato acompanha, para o log nao precisar adivinhar.
    assert HERO_STATE_CONFLICTS[codigo] in str(excecao.value)


@pytest.mark.parametrize(
    "status,codigo",
    [
        (401, "unauthenticated"),
        (403, "forbidden_scope"),
        (404, "media_not_found"),
        (404, "article_not_found"),
        (422, "validation_failed"),
        (400, "invalid_json"),
        (500, "hero_write_failed"),
    ],
)
def test_refusals_outside_409_are_not_state_conflicts(status, codigo):
    session = _FakeSession([_FakeResponse(status, _json({"error": codigo}))])
    with pytest.raises(MediaHeroError) as excecao:
        _client(session).point_media_as_hero(media_id="14", article_id="19", api_key="k")
    assert excecao.value.is_state_conflict is False
    assert excecao.value.remote_code == codigo


def test_the_outcome_is_classified_by_code_and_never_by_message_text():
    """O texto da mensagem nao decide nada.

    Um `409` com mensagem em ingles, em portugues ou vazia continua sendo o
    mesmo desfecho — o que decide e o campo `error`.
    """
    session = _FakeSession(
        [
            _FakeResponse(
                409,
                _json(
                    {
                        "error": "hero_not_owned_by_automation",
                        "message": "linked successfully",
                    }
                ),
            )
        ]
    )
    with pytest.raises(MediaHeroError) as excecao:
        _client(session).point_media_as_hero(media_id="14", article_id="19", api_key="k")
    assert excecao.value.is_state_conflict is True


def test_a_missing_media_credential_never_reaches_the_network():
    """Sem chave nenhuma o passo falha LOCAL, e nao com um `401` do outro lado."""
    session = _FakeSession([])
    cliente = _client(session)
    # `CinerieConfig` ja recusa `api_key` vazia na construcao; o estado real que
    # se quer cobrir e "a de publicacao existe, a de MIDIA nao".
    cliente.config.api_key = ""
    with pytest.raises(MediaHeroError) as excecao:
        cliente.point_media_as_hero(media_id="14", article_id="19", api_key="")
    assert excecao.value.remote_code == "missing_media_api_key"
    assert session.calls == []


@pytest.mark.parametrize("media_id,article_id", [("", "19"), ("14", ""), ("", "")])
def test_an_empty_id_never_reaches_the_network(media_id, article_id):
    session = _FakeSession([])
    with pytest.raises(MediaHeroError) as excecao:
        _client(session).point_media_as_hero(
            media_id=media_id, article_id=article_id, api_key="k"
        )
    assert excecao.value.remote_code == "validation_failed"
    assert session.calls == []


# ===========================================================================
# A promessa que nao pode ser quebrada
# ===========================================================================


class _ExplodingClient:
    def point_media_as_hero(self, **_kwargs):
        raise RuntimeError("qualquer coisa inesperada")


def test_the_orchestrator_never_raises_whatever_happens():
    """Falha da capa NUNCA derruba a materia — mesma regra da ingestao.

    A materia ja foi aceita e ja tem `article_id`. O unico desfecho aceitavel
    aqui e WARNING e `None`.
    """
    assert (
        point_hero_media(
            article_id="19", media_id="14", client=_ExplodingClient(), media_api_key="k"
        )
        is None
    )


def test_the_orchestrator_swallows_a_state_conflict_as_a_warning(caplog):
    class _Recusa:
        def point_media_as_hero(self, **_kwargs):
            raise MediaHeroError(
                "recusado", remote_code="article_not_automation_draft", state_conflict=True
            )

    with caplog.at_level("WARNING"):
        assert (
            point_hero_media(
                article_id="19", media_id="14", client=_Recusa(), media_api_key="k"
            )
            is None
        )
    registro = "\n".join(record.getMessage() for record in caplog.records)
    # O log precisa dizer que o mediaId CONTINUA valido: e isso que impede
    # alguem de ir consertar um id que nunca esteve errado.
    assert "conflito de ESTADO" in registro
    assert "continua valido" in registro


def test_without_a_media_credential_the_step_is_skipped_and_not_attempted():
    assert point_hero_media(article_id="19", media_id="14", client=None, media_api_key="k") is None
    assert (
        point_hero_media(article_id="19", media_id="14", client=object(), media_api_key="")
        is None
    )
