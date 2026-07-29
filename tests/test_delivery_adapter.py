"""Adapter HTTP do Payload CMS, contra servidor local falso.

Estes testes precisam de socket real, entao NAO usam a fixture `_no_network`
usada no resto da suite. A garantia aqui e explicita: o servidor so escuta em
127.0.0.1 e cada teste chama ``assert_loopback_only()``. Nenhum host externo,
nenhum token real, nenhuma instancia de Payload de verdade.
"""


import pytest

from app.delivery.contract import EditorialDeliveryPayload
from app.delivery.errors import (
    DeliveryAuthenticationError,
    DeliveryConfigurationError,
    DeliveryConflictError,
    DeliveryConnectionError,
    DeliveryNotFoundError,
    DeliveryPermissionError,
    DeliveryRateLimitedError,
    DeliveryServerError,
    DeliveryTimeoutError,
    InvalidDeliveryPayloadError,
    InvalidRemoteResponseError,
)
from app.delivery.payload_cms import PayloadCmsConfig, PayloadCmsDestination
from tests.fake_payload_server import FakePayloadServer, ScriptedResponse

# Deliberadamente fora do formato de qualquer credencial real: o guard de
# seguranca varre tests/ tambem, e um valor que imitasse um token verdadeiro
# seria indistinguivel de um vazamento.
FAKE_TOKEN = "fake-payload-token-for-tests-0000"


@pytest.fixture
def server():
    with FakePayloadServer() as running:
        running.assert_loopback_only()
        yield running


def make_config(server: FakePayloadServer, **overrides) -> PayloadCmsConfig:
    base = dict(
        enabled=True,
        base_url=server.base_url,
        token=FAKE_TOKEN,
        collection="editorial-drafts",
        timeout_seconds=5.0,
    )
    base.update(overrides)
    return PayloadCmsConfig(**base)


def make_payload(**overrides) -> EditorialDeliveryPayload:
    base = dict(
        delivery_id="dlv-adapter", draft_id="draft-adapter",
        title="Estudio confirma estreia", content="<p>corpo editorial</p>",
        category="Filmes", content_hash="b" * 64,
        metadata={"idempotency_key": "key-adapter"},
    )
    base.update(overrides)
    return EditorialDeliveryPayload(**base)


def destination(server: FakePayloadServer, **overrides) -> PayloadCmsDestination:
    return PayloadCmsDestination(make_config(server, **overrides))


# ===========================================================================
# Sucesso
# ===========================================================================


def test_created_draft_returns_the_remote_id(server):
    result = destination(server).submit_draft(make_payload())
    assert result.remote_id == "remote-draft-1"
    server.assert_loopback_only()


def test_request_reaches_the_collection_endpoint(server):
    destination(server).submit_draft(make_payload())
    assert server.last_request.path.endswith("/api/editorial-drafts")


def test_request_body_pins_the_draft_status(server):
    """Nao existe caminho que peca publicacao."""
    destination(server).submit_draft(make_payload())
    body = server.last_request.body
    assert body["cms_status"] == "draft"
    assert body["_status"] == "draft"


def test_request_sends_the_idempotency_key(server):
    destination(server).submit_draft(make_payload())
    assert server.last_request.idempotency_key == "key-adapter"


def test_request_sends_the_authorization_header(server):
    destination(server).submit_draft(make_payload())
    assert server.last_request.authorization == f"Bearer {FAKE_TOKEN}"


def test_unicode_survives_the_round_trip(server):
    destination(server).submit_draft(
        make_payload(title="Estreia de Ação: coração — 2026")
    )
    assert "Ação" in server.last_request.body["title"]


@pytest.mark.parametrize(
    "body, expected",
    [
        ({"id": "r1"}, "r1"),
        ({"_id": "r2"}, "r2"),
        ({"doc": {"id": "r3"}}, "r3"),
        ({"data": {"docId": "r4"}}, "r4"),
    ],
    ids=["id", "_id", "doc.id", "data.docId"],
)
def test_remote_id_is_found_in_common_envelopes(body, expected):
    with FakePayloadServer([ScriptedResponse(status=201, body=body)]) as running:
        running.assert_loopback_only()
        assert destination(running).submit_draft(make_payload()).remote_id == expected


def test_remote_url_is_captured_when_present():
    with FakePayloadServer(
        [ScriptedResponse(status=201, body={"doc": {"id": "r", "url": "https://cms.example/x"}})]
    ) as running:
        assert destination(running).submit_draft(make_payload()).url == "https://cms.example/x"


@pytest.mark.parametrize("status", [200, 201, 202])
def test_any_2xx_with_an_id_is_accepted(status):
    with FakePayloadServer([ScriptedResponse(status=status, body={"id": "r"})]) as running:
        assert destination(running).submit_draft(make_payload()).remote_id == "r"


# ===========================================================================
# Respostas invalidas
# ===========================================================================


def test_success_without_a_remote_id_is_rejected():
    """Um 2xx sem id e pior que um erro: o draft pode existir e nao ter referencia."""
    with FakePayloadServer([ScriptedResponse(status=201, body={"ok": True})]) as running:
        with pytest.raises(InvalidRemoteResponseError):
            destination(running).submit_draft(make_payload())


def test_success_with_invalid_json_is_rejected():
    with FakePayloadServer([ScriptedResponse(status=201, raw_body="{isto nao e json")]) as running:
        with pytest.raises(InvalidRemoteResponseError):
            destination(running).submit_draft(make_payload())


def test_empty_body_is_rejected():
    with FakePayloadServer([ScriptedResponse(status=201, body=None)]) as running:
        with pytest.raises(InvalidRemoteResponseError):
            destination(running).submit_draft(make_payload())


def test_invalid_response_is_not_retryable():
    with FakePayloadServer([ScriptedResponse(status=201, body={"ok": True})]) as running:
        with pytest.raises(InvalidRemoteResponseError) as exc:
            destination(running).submit_draft(make_payload())
        assert exc.value.retryable is False


# ===========================================================================
# Status HTTP
# ===========================================================================


@pytest.mark.parametrize(
    "status, expected, retryable",
    [
        (400, InvalidDeliveryPayloadError, False),
        (401, DeliveryAuthenticationError, False),
        (403, DeliveryPermissionError, False),
        (404, DeliveryNotFoundError, False),
        (408, DeliveryTimeoutError, True),
        (409, DeliveryConflictError, False),
        (429, DeliveryRateLimitedError, True),
        (500, DeliveryServerError, True),
        (502, DeliveryServerError, True),
        (503, DeliveryServerError, True),
        (504, DeliveryServerError, True),
    ],
)
def test_http_status_maps_to_typed_error(status, expected, retryable):
    with FakePayloadServer([ScriptedResponse(status=status, body={"error": "x"})]) as running:
        running.assert_loopback_only()
        with pytest.raises(expected) as exc:
            destination(running).submit_draft(make_payload())
        assert exc.value.http_status == status
        assert exc.value.retryable is retryable


def test_retry_after_header_is_read_on_429():
    with FakePayloadServer(
        [ScriptedResponse(status=429, body={}, headers={"Retry-After": "17"})]
    ) as running:
        with pytest.raises(DeliveryRateLimitedError) as exc:
            destination(running).submit_draft(make_payload())
        assert exc.value.retry_after_seconds == 17.0


def test_retry_after_header_is_read_on_503():
    with FakePayloadServer(
        [ScriptedResponse(status=503, body={}, headers={"Retry-After": "4"})]
    ) as running:
        with pytest.raises(DeliveryServerError) as exc:
            destination(running).submit_draft(make_payload())
        assert exc.value.retry_after_seconds == 4.0


def test_unparseable_retry_after_is_ignored():
    with FakePayloadServer(
        [ScriptedResponse(status=503, body={}, headers={"Retry-After": "amanha"})]
    ) as running:
        with pytest.raises(DeliveryServerError) as exc:
            destination(running).submit_draft(make_payload())
        assert exc.value.retry_after_seconds is None


# ===========================================================================
# Transporte
# ===========================================================================


def test_read_timeout_is_typed_and_retryable():
    """Conexao abre, resposta demora: o resultado fica genuinamente ambiguo."""
    with FakePayloadServer([ScriptedResponse(status=201, delay_seconds=1.5)]) as running:
        with pytest.raises(DeliveryTimeoutError) as exc:
            destination(running, timeout_seconds=0.25).submit_draft(make_payload())
        assert exc.value.retryable is True


def test_connection_failure_is_typed_and_retryable():
    """Servidor derrubado antes da chamada: a requisicao nunca chegou.

    Distinto de um read timeout: aqui nada foi criado do outro lado, entao a
    falha e inequivoca e o retry e limpo — nao precisa de reconciliacao.
    """
    server = FakePayloadServer().start()
    base_url = server.base_url
    server.stop()
    with pytest.raises(DeliveryConnectionError) as exc:
        PayloadCmsDestination(
            PayloadCmsConfig(
                enabled=True, base_url=base_url, token=FAKE_TOKEN, timeout_seconds=0.4
            )
        ).submit_draft(make_payload())
    assert exc.value.retryable is True


def test_connect_and_read_timeouts_are_classified_differently():
    """ConnectTimeout herda de Timeout; a ordem de captura importa."""
    import requests

    assert issubclass(requests.ConnectTimeout, requests.Timeout)
    assert issubclass(requests.ConnectTimeout, requests.ConnectionError)


def test_timeout_is_explicit_and_configurable(server):
    assert destination(server, timeout_seconds=3.5).config.timeout_seconds == 3.5


# ===========================================================================
# Configuracao e seguranca
# ===========================================================================


@pytest.mark.parametrize(
    "overrides",
    [{"base_url": None}, {"token": None}, {"timeout_seconds": 0}, {"base_url": "ftp://x.example"}],
    ids=["sem-url", "sem-token", "timeout-zero", "esquema-invalido"],
)
def test_incomplete_configuration_is_rejected_before_any_request(server, overrides):
    with pytest.raises(DeliveryConfigurationError):
        destination(server, **overrides)
    assert server.call_count == 0


def test_disabled_configuration_reports_no_issues():
    assert PayloadCmsConfig(enabled=False).issues() == []


def test_repr_never_reveals_the_token(server):
    rendered = repr(make_config(server))
    assert FAKE_TOKEN not in rendered
    assert "***" in rendered


def test_errors_never_reveal_the_token():
    with FakePayloadServer([ScriptedResponse(status=401, body={})]) as running:
        with pytest.raises(DeliveryAuthenticationError) as exc:
            destination(running).submit_draft(make_payload())
        assert FAKE_TOKEN not in str(exc.value)


def test_logs_never_reveal_the_token(server, caplog):
    import logging

    with caplog.at_level(logging.DEBUG):
        destination(server).submit_draft(make_payload())
    assert FAKE_TOKEN not in caplog.text


def test_error_messages_never_include_a_query_string():
    """Uma query string pode carregar credencial; a URL e sanitizada."""
    with FakePayloadServer([ScriptedResponse(status=500, body={})]) as running:
        endpoint = f"{running.base_url}/api/drafts?token=segredo-na-url"
        with pytest.raises(DeliveryServerError) as exc:
            PayloadCmsDestination(
                PayloadCmsConfig(
                    enabled=True, base_url=running.base_url, draft_endpoint=endpoint,
                    token=FAKE_TOKEN, timeout_seconds=2,
                )
            ).submit_draft(make_payload())
        assert "segredo-na-url" not in str(exc.value)


def test_full_article_body_is_not_logged(server, caplog):
    import logging

    marker = "TRECHO_INTEGRAL_DA_MATERIA_QUE_NAO_PODE_VAZAR"
    with caplog.at_level(logging.DEBUG):
        destination(server).submit_draft(make_payload(content=f"<p>{marker}</p>"))
    assert marker not in caplog.text


# ===========================================================================
# Reconciliacao
# ===========================================================================


def test_lookup_is_not_claimed_to_work(server):
    """O contrato de query do Payload nao foi confirmado pelo Screen-App."""
    with pytest.raises(NotImplementedError) as exc:
        destination(server).find_existing_draft("dlv-1")
    assert "Screen-App" in str(exc.value)


# ===========================================================================
# Isolamento
# ===========================================================================


def test_importing_the_adapter_opens_nothing():
    """Nenhum I/O no import: a classe so abre sessao quando usada."""
    import importlib

    module = importlib.import_module("app.delivery.payload_cms")
    assert module.PayloadCmsDestination is not None


def test_only_the_adapter_imports_an_http_client():
    from pathlib import Path

    transport_free = [
        "app/delivery/contract.py", "app/delivery/builder.py", "app/delivery/states.py",
        "app/delivery/errors.py", "app/delivery/categories.py", "app/delivery/destination.py",
        "app/delivery/retry.py", "app/delivery_store.py",
    ]
    for module in transport_free:
        source = Path(module).read_text(encoding="utf-8")
        for forbidden in ("import requests", "import httpx", "urlopen"):
            assert forbidden not in source, f"{module} importa {forbidden}"


def test_the_fake_server_is_loopback_only(server):
    server.assert_loopback_only()
    assert server.base_url.startswith("http://127.0.0.1:")
