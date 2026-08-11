from __future__ import annotations

import pytest

from app.cinerie import outcomes as O
from app.cinerie.outcomes import PublicationResult, parse_result
from app.cinerie_canary import execute_canary, synthetic_payload, validate_canary_environment


class _Client:
    def __init__(self, results):
        self.results = list(results)
        self.payloads = []

    def submit_publication(self, payload):
        self.payloads.append(payload)
        return self.results.pop(0)


def _result(*, idempotent=False, outcome=O.ROUTED_TO_REVIEW):
    return PublicationResult(
        outcome=outcome,
        http_status=202,
        request_id="req-canary",
        idempotency_key="mnscr:canary-mnscr-20260806:rev-1",
        article_id="article-88",
        idempotent=idempotent,
    )


def test_canario_exige_autorizacao_e_draft_explicitos():
    with pytest.raises(RuntimeError, match="MNSCR_CANARY_AUTHORIZED"):
        validate_canary_environment({"MNSCR_DELIVERY_MODE": "DRAFT"})
    with pytest.raises(RuntimeError, match="MNSCR_DELIVERY_MODE=DRAFT"):
        validate_canary_environment(
            {"MNSCR_CANARY_AUTHORIZED": "YES", "MNSCR_DELIVERY_MODE": "AUTO_PUBLISH"}
        )


def test_canario_repete_exatamente_o_mesmo_pedido_e_exige_idempotencia():
    client = _Client([_result(), _result(idempotent=True)])
    payload = {
        "requestId": "req-canary",
        "idempotencyKey": "mnscr:canary-mnscr-20260806:rev-1",
    }

    report = execute_canary(client, payload)

    assert client.payloads == [payload, payload]
    assert report["outcome"] == O.ROUTED_TO_REVIEW
    assert report["idempotent"] is True
    assert report["requestId"] == "req-canary"
    assert report["articleId"] == "article-88"


def test_canario_recusa_replay_que_aponta_para_outro_artigo():
    second = _result(idempotent=True)
    second.article_id = "article-99"
    client = _Client([_result(), second])

    with pytest.raises(RuntimeError, match="articleId"):
        execute_canary(
            client,
            {"requestId": "req-canary", "idempotencyKey": "mnscr:canary-mnscr-20260806:rev-1"},
        )


def test_canario_exige_ids_remotos_para_registrar_desfecho():
    first = _result()
    first.article_id = None
    second = _result(idempotent=True)
    second.article_id = None

    with pytest.raises(RuntimeError, match="articleId"):
        execute_canary(
            _Client([first, second]),
            {"requestId": "req-canary", "idempotencyKey": "mnscr:canary-mnscr-20260806:rev-1"},
        )


def test_canario_para_se_destino_publicar_mesmo_com_modo_draft():
    client = _Client([_result(outcome=O.PUBLISHED), _result(idempotent=True)])

    with pytest.raises(RuntimeError, match="PUBLISHED"):
        execute_canary(client, {"requestId": "req", "idempotencyKey": "mnscr:x:rev-1"})

    assert len(client.payloads) == 1


def test_payload_sintetico_e_deterministico_e_claramente_marcado():
    first = synthetic_payload(public_author_id="12", day="20260806")
    second = synthetic_payload(public_author_id="12", day="20260806")

    assert first == second
    assert first["idempotencyKey"] == "mnscr:canary-mnscr-20260806:rev-1"
    assert "CANÁRIO SINTÉTICO" in first["title"]
    assert first["qa"]["passed"] is False


def test_teto_restante_aceita_so_dimensoes_curtas_e_numericas():
    result = parse_result(
        {
            "outcome": O.ROUTED_TO_REVIEW,
            "quotaRemaining": {
                "author": 4,
                "forged\nlog": "segredo",
                "nested": {"payload": "nao registrar"},
            },
        },
        202,
    )

    assert result is not None
    assert result.quota_remaining == {"author": 4}
