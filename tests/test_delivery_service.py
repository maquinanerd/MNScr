"""Servico de entrega: ordem, idempotencia, retry, persistencia e end-to-end.

Os testes que exercitam o transporte usam o servidor local falso e por isso nao
podem usar `_no_network` (que bloqueia socket por completo); a garantia neles e
`assert_loopback_only()`. Os testes de dominio puro usam um destino falso em
memoria e nao abrem socket nenhum.
"""

import json

import pytest

from app.delivery import states as S
from app.delivery.contract import EditorialDeliveryPayload
from app.delivery.destination import RemoteDraft
from app.delivery.errors import (
    DeliveryConflictError,
    DeliveryRateLimitedError,
    DeliveryTimeoutError,
    InvalidRemoteResponseError,
    error_for_status,
)
from app.delivery.payload_cms import PayloadCmsConfig, PayloadCmsDestination
from app.delivery.retry import FakeClock, FakeSleeper, RetryPolicy
from app.delivery_service import DeliveryService
from app.delivery_store import DeliveryStore
from app.editorial import DraftContent, DraftProvenance, EditorialDraft, SourceReference
from app.editorial_gate import EditorialGate
from app.factual_builder import build_factual_assessment
from tests.fake_payload_server import FakePayloadServer, ScriptedResponse

FAKE_TOKEN = "fake-payload-token-for-tests-0000"
FILLER = " ".join(["palavra"] * 260)

SOURCES = [
    {
        "url": "https://variety.example/a", "domain": "variety.example",
        "content": (
            "O estudio confirmou a estreia para 29 de julho de 2026 conforme anunciado. "
            "A serie foi renovada para uma segunda temporada."
        ),
    },
    {
        "url": "https://deadline.example/b", "domain": "deadline.example",
        "content": (
            "A estreia acontece em 29 de julho de 2026 segundo o estudio. "
            "A renovacao para a segunda temporada foi confirmada."
        ),
    },
]

CONFLICTING_SOURCES = [
    {
        "url": "https://variety.example/a", "domain": "variety.example",
        "content": "O estudio confirmou a estreia para 29 de julho de 2026 conforme anunciado.",
    },
    {
        "url": "https://deadline.example/b", "domain": "deadline.example",
        "content": "A estreia foi remarcada e acontece agora em 05 de agosto de 2026.",
    },
]


def make_draft(draft_id="draft-svc", title=None, body=None) -> EditorialDraft:
    return EditorialDraft(
        draft_id=draft_id, article_id=1,
        content=DraftContent(
            title=title or "Estudio confirma estreia em 29 de julho de 2026",
            body_html=body or f"<p>A serie foi renovada para uma segunda temporada.</p><p>{FILLER}</p>",
            subtitle="Resumo editorial distinto do titulo e da meta description.",
            meta_description="Meta description com o fato principal do acontecimento.",
            category_suggestions=["Filmes"],
        ),
        provenance=DraftProvenance(
            input_hash="a" * 64, output_hash="b" * 64,
            input_contract_version="rss-prime-event-v1",
            input_event_key="evt-svc", input_revision=1, prompt_version="p1",
        ),
        event_key="evt-svc", revision=1,
        sources=[
            SourceReference(url="https://variety.example/a", domain="variety.example",
                            is_primary=True, extraction_status="OK"),
            SourceReference(url="https://deadline.example/b", domain="deadline.example",
                            extraction_status="OK"),
        ],
    )


def assessed(draft, sources=SOURCES):
    """Run the real MS-4 and MS-3 stages over a draft."""
    draft.factual_assessment = build_factual_assessment(draft, sources, mode="deterministic")
    draft.editorial_gate = EditorialGate().evaluate(
        draft, context={"output_mode": "local", "factual_assessment": draft.factual_assessment}
    )
    return draft


class RecordingDestination:
    """An in-memory destination. No socket, scriptable failures."""

    name = "payload_cms"

    def __init__(self, script=None):
        self.script = list(script or [])
        self.calls = []

    def submit_draft(self, payload: EditorialDeliveryPayload) -> RemoteDraft:
        self.calls.append(payload)
        if self.script:
            outcome = self.script.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        return RemoteDraft(remote_id=f"remote-{len(self.calls)}", url="https://cms.example/x")

    def find_existing_draft(self, delivery_id):
        raise NotImplementedError


@pytest.fixture
def store(tmp_path) -> DeliveryStore:
    delivery_store = DeliveryStore(db_path=str(tmp_path / "app.db"))
    yield delivery_store
    delivery_store.close()


def make_service(store, destination, **overrides) -> DeliveryService:
    kwargs = dict(
        store=store,
        destination=destination,
        retry_policy=RetryPolicy(max_attempts=3, base_seconds=1),
        sleeper=FakeSleeper(),
        clock=FakeClock(),
    )
    kwargs.update(overrides)
    return DeliveryService(**kwargs)


# ===========================================================================
# Persistencia
# ===========================================================================


def test_schema_is_created(store):
    cursor = store.conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row["name"] for row in cursor.fetchall()}
    assert {"editorial_deliveries", "delivery_attempts"} <= tables


def test_migration_is_idempotent(tmp_path):
    path = str(tmp_path / "app.db")
    for _ in range(3):
        DeliveryStore(db_path=path).close()
    store = DeliveryStore(db_path=path)
    cursor = store.conn.cursor()
    cursor.execute("PRAGMA table_info(editorial_deliveries)")
    names = [row["name"] for row in cursor.fetchall()]
    assert len(names) == len(set(names))
    store.close()


def test_migration_over_an_existing_database_preserves_rows(tmp_path):
    path = str(tmp_path / "app.db")
    first = DeliveryStore(db_path=path)
    first.create_delivery(
        delivery_id="dlv-1", idempotency_key="key-1", draft_id="d1",
        content_hash="h", destination="payload_cms", schema_version="1",
    )
    first.close()

    second = DeliveryStore(db_path=path)
    assert second.get_by_delivery_id("dlv-1") is not None
    second.close()


def test_migration_does_not_touch_earlier_phase_tables(tmp_path):
    """A MS-5 nao mexe em ingestao, gate nem camada factual."""
    store = DeliveryStore(db_path=str(tmp_path / "app.db"))
    cursor = store.conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row["name"] for row in cursor.fetchall()}
    for foreign in ("rssprime_events", "ingestion_cursors", "editorial_gate_results",
                    "factual_assessments", "seen_articles"):
        assert foreign not in tables
    store.close()


def test_duplicate_idempotency_key_is_refused_by_the_database(store):
    args = dict(
        delivery_id="dlv-1", idempotency_key="key-1", draft_id="d1",
        content_hash="h", destination="payload_cms", schema_version="1",
    )
    assert store.create_delivery(**args) is not None
    assert store.create_delivery(**{**args, "delivery_id": "dlv-2"}) is None


def test_invalid_transition_is_refused_by_the_store(store):
    from app.delivery.states import InvalidDeliveryTransitionError

    store.create_delivery(
        delivery_id="dlv-1", idempotency_key="key-1", draft_id="d1",
        content_hash="h", destination="payload_cms", schema_version="1",
    )
    with pytest.raises(InvalidDeliveryTransitionError):
        store.update_status("dlv-1", S.DELIVERED)


def test_attempts_are_appended_never_overwritten(store):
    store.create_delivery(
        delivery_id="dlv-1", idempotency_key="key-1", draft_id="d1",
        content_hash="h", destination="payload_cms", schema_version="1",
    )
    store.record_attempt("dlv-1", attempt=1, status=S.DELIVERY_FAILED_RETRYABLE, http_status=503)
    store.record_attempt("dlv-1", attempt=2, status=S.DELIVERED)
    attempts = store.list_attempts("dlv-1")
    assert [a["attempt"] for a in attempts] == [1, 2]
    assert attempts[0]["http_status"] == 503


def test_state_survives_a_reopen(tmp_path):
    """Reinicializacao preserva o estado da entrega."""
    path = str(tmp_path / "app.db")
    first = DeliveryStore(db_path=path)
    first.create_delivery(
        delivery_id="dlv-1", idempotency_key="key-1", draft_id="d1",
        content_hash="h", destination="payload_cms", schema_version="1",
    )
    first.update_status("dlv-1", S.DELIVERY_IN_PROGRESS)
    first.update_status("dlv-1", S.DELIVERED, remote_draft_id="remote-9")
    first.close()

    second = DeliveryStore(db_path=path)
    record = second.get_by_delivery_id("dlv-1")
    assert record.status == S.DELIVERED
    assert record.remote_draft_id == "remote-9"
    assert record.delivered_at
    second.close()


# ===========================================================================
# Bloqueio — sem rede
# ===========================================================================


def test_blocked_draft_never_reaches_the_destination(store):
    destination = RecordingDestination()
    draft = assessed(make_draft(title=""), CONFLICTING_SOURCES)
    outcome = make_service(store, destination).deliver_draft(draft, topic="movies")
    assert outcome.result.status == S.DELIVERY_BLOCKED
    assert destination.calls == []


def test_draft_without_assessment_is_blocked(store):
    destination = RecordingDestination()
    outcome = make_service(store, destination).deliver_draft(make_draft(), topic="movies")
    assert outcome.result.status == S.DELIVERY_BLOCKED
    assert "AVALIACAO_FACTUAL_AUSENTE" in outcome.blocked_reasons
    assert destination.calls == []


def test_draft_without_gate_is_blocked(store):
    destination = RecordingDestination()
    draft = make_draft()
    draft.factual_assessment = build_factual_assessment(draft, SOURCES, mode="deterministic")
    outcome = make_service(store, destination).deliver_draft(draft, topic="movies")
    assert "EDITORIAL_GATE_AUSENTE" in outcome.blocked_reasons
    assert destination.calls == []


def test_critical_conflict_is_blocked(store):
    destination = RecordingDestination()
    draft = assessed(make_draft(), CONFLICTING_SOURCES)
    outcome = make_service(store, destination).deliver_draft(draft, topic="movies")
    assert outcome.result.status == S.DELIVERY_BLOCKED
    assert destination.calls == []


def test_unknown_category_is_blocked(store):
    destination = RecordingDestination()
    draft = assessed(make_draft())
    draft.content.category_suggestions = []
    outcome = make_service(store, destination).deliver_draft(draft, topic="culinaria")
    assert outcome.result.status == S.DELIVERY_BLOCKED
    assert destination.calls == []


def test_blocked_delivery_is_persisted(store):
    draft = assessed(make_draft(title=""), CONFLICTING_SOURCES)
    outcome = make_service(store, RecordingDestination()).deliver_draft(draft, topic="movies")
    assert outcome.record is not None
    assert store.get_by_delivery_id(outcome.record.delivery_id).status == S.DELIVERY_BLOCKED


# ===========================================================================
# Sucesso e idempotencia
# ===========================================================================


def test_approved_draft_is_delivered(store):
    destination = RecordingDestination()
    outcome = make_service(store, destination).deliver_draft(assessed(make_draft()), topic="movies")
    assert outcome.delivered
    assert outcome.result.remote_draft_id == "remote-1"
    assert len(destination.calls) == 1


def test_success_is_persisted(store):
    service = make_service(store, RecordingDestination())
    outcome = service.deliver_draft(assessed(make_draft()), topic="movies")
    record = store.get_by_delivery_id(outcome.result.delivery_id)
    assert record.status == S.DELIVERED
    assert record.remote_draft_id == "remote-1"
    assert record.delivered_at


def test_replaying_the_same_content_creates_no_second_draft(store):
    destination = RecordingDestination()
    service = make_service(store, destination)
    draft = assessed(make_draft())

    first = service.deliver_draft(draft, topic="movies")
    second = service.deliver_draft(draft, topic="movies")

    assert len(destination.calls) == 1, "retry criou um segundo draft remoto"
    assert second.result.idempotent_replay is True
    assert second.result.remote_draft_id == first.result.remote_draft_id


def test_replay_returns_the_same_delivery_id(store):
    service = make_service(store, RecordingDestination())
    draft = assessed(make_draft())
    assert (
        service.deliver_draft(draft, topic="movies").result.delivery_id
        == service.deliver_draft(draft, topic="movies").result.delivery_id
    )


def test_only_one_delivery_row_per_content(store):
    service = make_service(store, RecordingDestination())
    draft = assessed(make_draft())
    service.deliver_draft(draft, topic="movies")
    service.deliver_draft(draft, topic="movies")
    assert len(store.list_for_draft(draft.draft_id)) == 1


def test_changed_content_creates_a_new_versioned_delivery(store):
    """Politica escolhida: nova entrega versionada, sem tocar no draft remoto."""
    destination = RecordingDestination()
    service = make_service(store, destination)

    draft = assessed(make_draft())
    service.deliver_draft(draft, topic="movies")

    changed = assessed(make_draft(body=f"<p>Texto revisado pela redacao.</p><p>{FILLER}</p>"))
    changed.provenance.output_hash = "c" * 64
    outcome = service.deliver_draft(changed, topic="movies")

    assert outcome.result.status == S.DELIVERY_REQUIRES_MANUAL_REVIEW
    assert len(destination.calls) == 1, "conteudo alterado nao pode sobrescrever o draft remoto"
    assert len(store.list_for_draft(draft.draft_id)) == 2


def test_the_previous_delivery_is_preserved_in_history(store):
    service = make_service(store, RecordingDestination())
    draft = assessed(make_draft())
    first = service.deliver_draft(draft, topic="movies")

    changed = assessed(make_draft())
    changed.provenance.output_hash = "c" * 64
    service.deliver_draft(changed, topic="movies")

    history = store.list_for_draft(draft.draft_id)
    assert history[0].delivery_id == first.result.delivery_id
    assert history[0].status == S.DELIVERED
    assert history[1].supersedes_delivery_id == first.result.delivery_id


# ===========================================================================
# Retry
# ===========================================================================


def test_transient_failure_is_retried_then_succeeds(store):
    destination = RecordingDestination(
        script=[error_for_status(503, "indisponivel"), RemoteDraft(remote_id="remote-ok")]
    )
    outcome = make_service(store, destination).deliver_draft(assessed(make_draft()), topic="movies")
    assert outcome.delivered
    assert outcome.result.attempts == 2
    assert len(destination.calls) == 2


def test_retry_produces_one_logical_delivery(store):
    destination = RecordingDestination(
        script=[error_for_status(503, "x"), RemoteDraft(remote_id="remote-ok")]
    )
    service = make_service(store, destination)
    draft = assessed(make_draft())
    service.deliver_draft(draft, topic="movies")
    assert len(store.list_for_draft(draft.draft_id)) == 1


def test_every_attempt_is_persisted(store):
    destination = RecordingDestination(
        script=[error_for_status(503, "x"), RemoteDraft(remote_id="remote-ok")]
    )
    service = make_service(store, destination)
    outcome = service.deliver_draft(assessed(make_draft()), topic="movies")
    attempts = service.attempts_for(outcome.result.delivery_id)
    assert [a["attempt"] for a in attempts] == [1, 2]
    assert attempts[0]["http_status"] == 503
    assert attempts[0]["error_class"] == "SERVER"


def test_permanent_failure_is_not_retried(store):
    destination = RecordingDestination(script=[error_for_status(401, "sem permissao")])
    outcome = make_service(store, destination).deliver_draft(assessed(make_draft()), topic="movies")
    assert outcome.result.status == S.DELIVERY_FAILED_PERMANENT
    assert len(destination.calls) == 1, "erro permanente nao pode ser repetido"


def test_attempt_limit_is_respected(store):
    destination = RecordingDestination(script=[error_for_status(503, "x") for _ in range(10)])
    service = make_service(
        store, destination, retry_policy=RetryPolicy(max_attempts=3, base_seconds=1)
    )
    outcome = service.deliver_draft(assessed(make_draft()), topic="movies")
    assert len(destination.calls) == 3
    assert outcome.result.attempts == 3


def test_backoff_grows_without_waiting(store):
    sleeper = FakeSleeper()
    destination = RecordingDestination(script=[error_for_status(503, "x") for _ in range(5)])
    service = make_service(
        store, destination, sleeper=sleeper,
        retry_policy=RetryPolicy(max_attempts=3, base_seconds=2, jitter_ratio=0.0),
    )
    service.deliver_draft(assessed(make_draft()), topic="movies")
    waits = [s for s in sleeper.slept if s > 0]
    assert waits == sorted(waits) and len(waits) == 2
    assert sleeper.total_slept > 0  # calculado, mas nunca esperado de verdade


def test_retry_after_is_honoured(store):
    sleeper = FakeSleeper()
    destination = RecordingDestination(
        script=[
            DeliveryRateLimitedError("limitado", http_status=429, retry_after_seconds=9),
            RemoteDraft(remote_id="remote-ok"),
        ]
    )
    service = make_service(store, destination, sleeper=sleeper)
    service.deliver_draft(assessed(make_draft()), topic="movies")
    assert 9 in sleeper.slept


def test_exhausted_timeout_needs_reconciliation(store):
    """Timeout de leitura deixa o resultado desconhecido; nao e falha limpa."""
    destination = RecordingDestination(
        script=[DeliveryTimeoutError("timeout") for _ in range(5)]
    )
    outcome = make_service(store, destination).deliver_draft(assessed(make_draft()), topic="movies")
    assert outcome.result.status == S.DELIVERY_NEEDS_RECONCILIATION


def test_conflict_needs_reconciliation_not_recreation(store):
    """409 sugere que uma tentativa anterior chegou; recriar duplicaria."""
    destination = RecordingDestination(script=[DeliveryConflictError("ja existe", http_status=409)])
    outcome = make_service(store, destination).deliver_draft(assessed(make_draft()), topic="movies")
    assert outcome.result.status == S.DELIVERY_NEEDS_RECONCILIATION
    assert len(destination.calls) == 1


def test_invalid_remote_response_needs_reconciliation(store):
    destination = RecordingDestination(script=[InvalidRemoteResponseError("sem id")])
    outcome = make_service(store, destination).deliver_draft(assessed(make_draft()), topic="movies")
    assert outcome.result.status == S.DELIVERY_NEEDS_RECONCILIATION


def test_unexpected_destination_error_is_not_swallowed(store):
    destination = RecordingDestination(script=[RuntimeError("boom inesperado")])
    outcome = make_service(store, destination).deliver_draft(assessed(make_draft()), topic="movies")
    assert outcome.result.success is False
    assert outcome.result.error_class == "INVALID_RESPONSE"


def test_error_is_persisted(store):
    destination = RecordingDestination(script=[error_for_status(401, "sem permissao")])
    service = make_service(store, destination)
    outcome = service.deliver_draft(assessed(make_draft()), topic="movies")
    record = store.get_by_delivery_id(outcome.result.delivery_id)
    assert record.error_class == "AUTHENTICATION"
    assert record.http_status == 401
    assert record.last_error


# ===========================================================================
# End-to-end contra servidor local falso
# ===========================================================================


def test_end_to_end_event_to_remote_draft(tmp_path):
    """Evento sintetico → draft → fatos → gate → contrato → CMS falso → DELIVERED."""
    with FakePayloadServer([ScriptedResponse(status=201, body={"doc": {"id": "remote-e2e"}})]) as server:
        server.assert_loopback_only()
        store = DeliveryStore(db_path=str(tmp_path / "app.db"))
        destination = PayloadCmsDestination(
            PayloadCmsConfig(enabled=True, base_url=server.base_url, token=FAKE_TOKEN,
                             timeout_seconds=5)
        )
        service = make_service(store, destination)

        draft = assessed(make_draft("draft-e2e"))
        assert draft.editorial_gate.outcome == "GATE_CLEAR"

        outcome = service.deliver_draft(draft, topic="movies")

        assert outcome.delivered
        assert outcome.result.remote_draft_id == "remote-e2e"
        assert store.get_by_delivery_id(outcome.result.delivery_id).status == S.DELIVERED
        assert server.last_request.body["cms_status"] == "draft"
        store.close()


def test_end_to_end_transient_failure_then_success(tmp_path):
    with FakePayloadServer(
        [
            ScriptedResponse(status=503, body={"error": "indisponivel"}),
            ScriptedResponse(status=201, body={"doc": {"id": "remote-retry"}}),
        ]
    ) as server:
        store = DeliveryStore(db_path=str(tmp_path / "app.db"))
        destination = PayloadCmsDestination(
            PayloadCmsConfig(enabled=True, base_url=server.base_url, token=FAKE_TOKEN,
                             timeout_seconds=5)
        )
        service = make_service(store, destination)
        outcome = service.deliver_draft(assessed(make_draft("draft-retry-e2e")), topic="movies")

        assert outcome.delivered
        assert outcome.result.attempts == 2
        assert server.call_count == 2
        assert len(store.list_for_draft("draft-retry-e2e")) == 1
        store.close()


def test_end_to_end_replay_creates_no_second_remote_draft(tmp_path):
    with FakePayloadServer([ScriptedResponse(status=201, body={"doc": {"id": "remote-once"}})]) as server:
        store = DeliveryStore(db_path=str(tmp_path / "app.db"))
        destination = PayloadCmsDestination(
            PayloadCmsConfig(enabled=True, base_url=server.base_url, token=FAKE_TOKEN,
                             timeout_seconds=5)
        )
        service = make_service(store, destination)
        draft = assessed(make_draft("draft-replay-e2e"))

        service.deliver_draft(draft, topic="movies")
        second = service.deliver_draft(draft, topic="movies")

        assert server.call_count == 1, "reexecucao criou um segundo draft remoto"
        assert second.result.idempotent_replay is True
        store.close()


def test_end_to_end_blocked_draft_opens_no_socket(tmp_path):
    with FakePayloadServer() as server:
        store = DeliveryStore(db_path=str(tmp_path / "app.db"))
        destination = PayloadCmsDestination(
            PayloadCmsConfig(enabled=True, base_url=server.base_url, token=FAKE_TOKEN,
                             timeout_seconds=5)
        )
        service = make_service(store, destination)
        draft = assessed(make_draft("draft-blocked-e2e"), CONFLICTING_SOURCES)

        outcome = service.deliver_draft(draft, topic="movies")

        assert outcome.result.status == S.DELIVERY_BLOCKED
        assert server.call_count == 0, "um draft bloqueado nao pode alcancar o destino"
        store.close()


# ===========================================================================
# Consultas e seguranca
# ===========================================================================


def test_deliveries_can_be_listed_by_status(store):
    service = make_service(store, RecordingDestination())
    service.deliver_draft(assessed(make_draft("draft-a")), topic="movies")
    assert [d.draft_id for d in service.list_by_status(S.DELIVERED)] == ["draft-a"]


def test_result_serializes_without_leaking(store):
    service = make_service(store, RecordingDestination())
    outcome = service.deliver_draft(assessed(make_draft()), topic="movies")
    rendered = json.dumps(outcome.result.to_dict())
    assert FAKE_TOKEN not in rendered
    for forbidden in ("PUBLISHED", "APPROVED", "PUBLICATION_READY"):
        assert forbidden not in rendered


def test_the_full_article_is_not_logged(store, caplog):
    import logging

    marker = "TRECHO_INTEGRAL_QUE_NAO_PODE_VAZAR_NO_LOG"
    draft = assessed(make_draft(body=f"<p>{marker}</p><p>{FILLER}</p>"))
    with caplog.at_level(logging.DEBUG):
        make_service(store, RecordingDestination()).deliver_draft(draft, topic="movies")
    assert marker not in caplog.text


def test_service_does_not_import_an_http_client():
    from pathlib import Path

    source = Path("app/delivery_service.py").read_text(encoding="utf-8")
    for forbidden in ("import requests", "import httpx", "urlopen"):
        assert forbidden not in source


def test_destination_is_only_built_after_validation(store):
    """Um draft bloqueado nao chega nem a construir o transporte."""
    service = DeliveryService(
        store=store, destination=None,
        retry_policy=RetryPolicy(max_attempts=1), sleeper=FakeSleeper(), clock=FakeClock(),
    )
    outcome = service.deliver_draft(make_draft(), topic="movies")
    assert outcome.result.status == S.DELIVERY_BLOCKED
    assert service.destination is None, "o destino foi construido para um draft bloqueado"
