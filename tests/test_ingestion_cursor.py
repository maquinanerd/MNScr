"""Ingestao ponta a ponta: contrato -> revisao -> persistencia -> cursor.

Usa SQLite real em tmp_path. Nenhum acesso a rede, RSS Prime, Gemini ou Payload.
"""

import copy
import json
import socket
from pathlib import Path

import pytest

from app.contracts import errors as E
from app.contracts import states as S
from app.contracts.rssprime_event_v1 import RssPrimeEventV1
from app.event_store import CURSOR_MODE_UNAVAILABLE, EventStore
from app.ingestion import IngestionService

FIXTURES = Path(__file__).parent / "fixtures" / "rssprime"
SOURCE_ID = "rssprime_movies"


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _blocked(*args, **kwargs):
        raise AssertionError("Teste de ingestao tentou acessar a rede")

    monkeypatch.setattr(socket, "socket", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)


@pytest.fixture
def store(tmp_path) -> EventStore:
    event_store = EventStore(db_path=str(tmp_path / "app.db"))
    yield event_store
    event_store.close()


@pytest.fixture
def service(store) -> IngestionService:
    return IngestionService(store, accept_legacy=True, required_contract=None)


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def rehash(payload: dict) -> dict:
    payload = copy.deepcopy(payload)
    payload.pop("payload_hash", None)
    payload["payload_hash"] = RssPrimeEventV1.from_mapping(payload).calculate_payload_hash()
    return payload


@pytest.fixture
def valid() -> dict:
    return fixture("event_v1_valid.json")


# ---------------------------------------------------------------------------
# Esquema e migrations
# ---------------------------------------------------------------------------


def test_schema_is_created_on_first_use(store):
    cursor = store.conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row["name"] for row in cursor.fetchall()}
    assert {"rssprime_events", "ingestion_cursors", "event_processing_attempts"} <= tables


def test_migrations_are_idempotent(tmp_path):
    path = str(tmp_path / "app.db")
    for _ in range(3):
        EventStore(db_path=path).close()
    store = EventStore(db_path=path)
    cursor = store.conn.cursor()
    cursor.execute("PRAGMA table_info(rssprime_events)")
    names = [row["name"] for row in cursor.fetchall()]
    assert len(names) == len(set(names)), "migration duplicou coluna"
    store.close()


def test_unique_constraint_on_event_key_and_revision(store, valid):
    event = RssPrimeEventV1.from_mapping(valid)
    assert store.record_event(event, validation_status=S.VALIDATED) is not None
    assert store.record_event(event, validation_status=S.VALIDATED) is None


def test_required_indexes_exist(store):
    cursor = store.conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='index'")
    indexes = " ".join(row["name"] or "" for row in cursor.fetchall())
    for column in ("event_key", "payload_hash", "cursor", "processing_status", "received_at"):
        assert column in indexes


# ---------------------------------------------------------------------------
# Primeiro evento
# ---------------------------------------------------------------------------


def test_valid_event_is_accepted_and_persisted(service, store, valid):
    outcome = service.ingest(valid, source_id=SOURCE_ID)
    assert outcome.accepted
    assert outcome.validation_status == S.VALIDATED
    stored = store.get_event(valid["event_key"])
    assert stored is not None
    assert stored.revision == 1
    assert stored.processing_status == S.QUEUED


def test_accepted_event_stores_the_normalized_payload(service, store, valid):
    service.ingest(valid, source_id=SOURCE_ID)
    stored = store.get_event(valid["event_key"])
    assert stored.normalized_payload["event_key"] == valid["event_key"]


def test_accepted_event_stores_the_raw_payload_for_replay(service, store, valid):
    service.ingest(valid, source_id=SOURCE_ID)
    stored = store.get_event(valid["event_key"])
    assert stored.raw_payload["payload_hash"] == valid["payload_hash"]


def test_raw_payload_can_be_switched_off(store, valid):
    service = IngestionService(store, store_raw_payload=False)
    service.ingest(valid, source_id=SOURCE_ID)
    assert store.get_event(valid["event_key"]).raw_payload is None


# ---------------------------------------------------------------------------
# Cursor
# ---------------------------------------------------------------------------


def test_cursor_advances_after_a_valid_event_is_persisted(service, store, valid):
    outcome = service.ingest(valid, source_id=SOURCE_ID)
    assert outcome.cursor_advanced
    saved = store.get_cursor(SOURCE_ID, "rss-prime-event-v1")
    assert saved["cursor"] == valid["cursor"]
    assert saved["last_event_key"] == valid["event_key"]
    assert saved["last_revision"] == 1


def test_cursor_does_not_advance_on_an_invalid_payload(service, store, valid):
    outcome = service.ingest(rehash({**valid, "topic": "games"}), source_id=SOURCE_ID)
    assert not outcome.accepted
    assert not outcome.cursor_advanced
    assert store.get_cursor(SOURCE_ID, "rss-prime-event-v1") is None


def test_cursor_does_not_advance_on_a_revision_conflict(service, store, valid):
    service.ingest(valid, source_id=SOURCE_ID)
    before = store.get_cursor(SOURCE_ID, "rss-prime-event-v1")["cursor"]

    conflicting = rehash({**valid, "title": "Titulo divergente", "cursor": "cursor-novo"})
    outcome = service.ingest(conflicting, source_id=SOURCE_ID)

    assert outcome.validation_status == S.REVISION_HASH_CONFLICT
    assert not outcome.cursor_advanced
    assert store.get_cursor(SOURCE_ID, "rss-prime-event-v1")["cursor"] == before


def test_cursor_does_not_advance_on_a_stale_revision(service, store, valid):
    rev2 = rehash({**valid, "revision": 2, "title": "Rev 2", "cursor": "cursor-rev-2"})
    service.ingest(rev2, source_id=SOURCE_ID)
    before = store.get_cursor(SOURCE_ID, "rss-prime-event-v1")["cursor"]

    outcome = service.ingest(valid, source_id=SOURCE_ID)

    assert outcome.validation_status == S.STALE_REVISION
    assert not outcome.cursor_advanced
    assert store.get_cursor(SOURCE_ID, "rss-prime-event-v1")["cursor"] == before


def test_cursor_is_opaque_and_stored_verbatim(service, store, valid):
    weird = "nao-e-timestamp::{}::||"
    service.ingest(rehash({**valid, "cursor": weird}), source_id=SOURCE_ID)
    assert store.get_cursor(SOURCE_ID, "rss-prime-event-v1")["cursor"] == weird


def test_cursor_is_never_invented_when_the_feed_omits_it(service, store, valid):
    without = copy.deepcopy(valid)
    without.pop("cursor")
    outcome = service.ingest(rehash(without), source_id=SOURCE_ID)
    assert outcome.accepted
    assert not outcome.cursor_advanced
    saved = store.get_cursor(SOURCE_ID, "rss-prime-event-v1")
    assert saved["cursor"] is None
    assert saved["cursor_mode"] == CURSOR_MODE_UNAVAILABLE


def test_event_and_cursor_roll_back_together_on_failure(store, valid):
    """Se a gravacao do cursor falhar, o evento tambem nao pode sobrar.

    A falha e provocada removendo a tabela de cursores: o INSERT do evento ja
    aconteceu quando o INSERT do cursor estoura, entao so um rollback real
    deixa o banco consistente.
    """
    import sqlite3

    service = IngestionService(store)
    store.conn.execute("DROP TABLE ingestion_cursors")
    store.conn.commit()

    with pytest.raises(sqlite3.Error):
        service.ingest(valid, source_id=SOURCE_ID)

    assert store.get_event(valid["event_key"]) is None, "evento sobreviveu a um rollback"


def test_successful_ingest_leaves_event_and_cursor_consistent(service, store, valid):
    """O par oposto do teste de rollback: ou os dois existem, ou nenhum."""
    service.ingest(valid, source_id=SOURCE_ID)
    assert store.get_event(valid["event_key"]) is not None
    assert store.get_cursor(SOURCE_ID, "rss-prime-event-v1") is not None


# ---------------------------------------------------------------------------
# Revisoes no fluxo real
# ---------------------------------------------------------------------------


def test_identical_redelivery_does_not_create_a_second_row(service, store, valid):
    service.ingest(valid, source_id=SOURCE_ID)
    outcome = service.ingest(valid, source_id=SOURCE_ID)
    assert outcome.validation_status == S.DUPLICATE_DELIVERY
    assert len(store.list_event_revisions(valid["event_key"])) == 1


def test_new_revision_creates_a_second_row(service, store, valid):
    service.ingest(valid, source_id=SOURCE_ID)
    service.ingest(fixture("event_v1_revision_2.json"), source_id=SOURCE_ID)
    revisions = [item.revision for item in store.list_event_revisions(valid["event_key"])]
    assert revisions == [1, 2]


def test_conflicting_revision_is_persisted_with_its_reason(service, store, valid):
    service.ingest(valid, source_id=SOURCE_ID)
    conflicting = fixture("event_v1_hash_conflict.json")
    outcome = service.ingest(conflicting, source_id=SOURCE_ID)

    assert outcome.validation_status == S.REVISION_HASH_CONFLICT
    assert E.REVISION_HASH_CONFLICT in outcome.error_codes
    # A revisao 1 original permanece intacta.
    assert store.get_event(valid["event_key"], 1).payload_hash == valid["payload_hash"]


def test_invalid_event_is_persisted_with_structured_errors(service, store, valid):
    service.ingest(rehash({**valid, "topic": "games"}), source_id=SOURCE_ID)
    stored = store.get_event(valid["event_key"])
    assert stored.validation_status == S.INVALID
    assert any(err["code"] == E.BLOCKED_TOPIC for err in stored.validation_errors)


def test_warnings_are_persisted_alongside_the_event(service, store, valid):
    without_confidence = copy.deepcopy(valid)
    without_confidence.pop("cluster_confidence")
    service.ingest(rehash(without_confidence), source_id=SOURCE_ID)
    stored = store.get_event(valid["event_key"])
    codes = {w["code"] for w in stored.warnings}
    assert E.MISSING_CLUSTER_CONFIDENCE in codes


# ---------------------------------------------------------------------------
# Limite de tamanho
# ---------------------------------------------------------------------------


def test_oversized_payload_is_rejected_before_persistence(store, valid):
    service = IngestionService(store, max_payload_bytes=200)
    outcome = service.ingest(valid, source_id=SOURCE_ID)
    assert not outcome.accepted
    assert E.PAYLOAD_TOO_LARGE in outcome.error_codes
    assert store.get_event(valid["event_key"]) is None


def test_payload_within_the_limit_is_accepted(store, valid):
    service = IngestionService(store, max_payload_bytes=1048576)
    assert service.ingest(valid, source_id=SOURCE_ID).accepted


# ---------------------------------------------------------------------------
# Historico de tentativas
# ---------------------------------------------------------------------------


def test_attempts_are_appended_never_overwritten(store, valid):
    store.record_attempt("evt-1", 1, attempt_kind="ingest", status=S.DRAFT_GENERATED)
    store.record_attempt("evt-1", 1, attempt_kind="replay", status=S.REPLAY_COMPLETED)
    attempts = store.list_attempts("evt-1", 1)
    assert [a["attempt_kind"] for a in attempts] == ["ingest", "replay"]


def test_processing_status_rejects_publication_vocabulary(store, valid):
    from app.editorial.states import ForbiddenStateError

    with pytest.raises(ForbiddenStateError):
        store.set_processing_status(valid["event_key"], 1, "PUBLISHED")
