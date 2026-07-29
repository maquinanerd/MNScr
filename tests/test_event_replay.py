"""Replay local e controlado.

Replay le somente o SQLite. Nao busca no RSS Prime, nao altera o payload, nao
avanca o cursor, nao publica e nao toca no Payload CMS.
"""

import copy
import json
import socket
from pathlib import Path

import pytest

from app.contracts import states as S
from app.contracts.rssprime_event_v1 import RssPrimeEventV1
from app.event_store import EventStore
from app.ingestion import IngestionService
from app.replay import (
    ATTEMPT_KIND_REPLAY,
    ReplayError,
    ReplayService,
    format_revision_table,
)

FIXTURES = Path(__file__).parent / "fixtures" / "rssprime"
SOURCE_ID = "rssprime_movies"


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Replay que toca a rede e um bug, nao um detalhe de ambiente."""

    def _blocked(*args, **kwargs):
        raise AssertionError("Replay tentou acessar a rede")

    monkeypatch.setattr(socket, "socket", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def valid() -> dict:
    return fixture("event_v1_valid.json")


@pytest.fixture
def store(tmp_path) -> EventStore:
    event_store = EventStore(db_path=str(tmp_path / "app.db"))
    yield event_store
    event_store.close()


@pytest.fixture
def ingested(store, valid):
    """Um evento ja recebido, com duas revisoes."""
    service = IngestionService(store)
    service.ingest(valid, source_id=SOURCE_ID)
    service.ingest(fixture("event_v1_revision_2.json"), source_id=SOURCE_ID)
    return valid["event_key"]


class RecordingProcessor:
    """Processador falso: registra o que recebeu, nao acessa nada externo."""

    def __init__(self, draft_id="draft-replay", output_hash="d" * 64):
        self.calls = []
        self.draft_id = draft_id
        self.output_hash = output_hash

    def __call__(self, event: RssPrimeEventV1):
        self.calls.append(event)
        return {"draft_id": f"{self.draft_id}-r{event.revision}", "output_hash": self.output_hash}


# ---------------------------------------------------------------------------
# Listagem de revisoes
# ---------------------------------------------------------------------------


def test_list_revisions_returns_every_stored_revision(store, ingested):
    rows = ReplayService(store).list_revisions(ingested)
    assert [row.revision for row in rows] == [1, 2]


def test_list_revisions_exposes_the_required_columns(store, ingested):
    row = ReplayService(store).list_revisions(ingested)[0]
    payload = row.to_dict()
    for key in (
        "revision", "payload_hash", "validation_status",
        "processing_status", "draft_id", "received_at",
    ):
        assert key in payload


def test_list_revisions_does_not_expose_the_payload_by_default(store, ingested):
    rows = ReplayService(store).list_revisions(ingested)
    rendered = format_revision_table(rows)
    assert "variety.example" not in rendered
    assert "Estudio confirma" not in rendered


def test_list_revisions_on_unknown_event_raises(store):
    with pytest.raises(ReplayError, match="Nenhuma revisao"):
        ReplayService(store).list_revisions("evt-inexistente")


# ---------------------------------------------------------------------------
# Replay por evento
# ---------------------------------------------------------------------------


def test_replay_without_revision_uses_the_latest(store, ingested):
    processor = RecordingProcessor()
    result = ReplayService(store, processor).replay(ingested)
    assert result.ok
    assert result.revision == 2
    assert processor.calls[0].revision == 2


def test_replay_with_an_explicit_revision(store, ingested):
    processor = RecordingProcessor()
    result = ReplayService(store, processor).replay(ingested, revision=1)
    assert result.revision == 1
    assert processor.calls[0].revision == 1


def test_replay_rebuilds_the_event_from_the_stored_payload(store, ingested, valid):
    processor = RecordingProcessor()
    ReplayService(store, processor).replay(ingested, revision=1)
    replayed = processor.calls[0]
    assert replayed.event_key == valid["event_key"]
    assert replayed.payload_hash == valid["payload_hash"]
    assert [s.url for s in replayed.sources] == [s["url"] for s in valid["sources"]]


def test_replay_of_unknown_event_raises(store):
    with pytest.raises(ReplayError, match="nao encontrado"):
        ReplayService(store, RecordingProcessor()).replay("evt-que-nao-existe")


def test_replay_of_unknown_revision_raises(store, ingested):
    with pytest.raises(ReplayError, match="Revisao 99"):
        ReplayService(store, RecordingProcessor()).replay(ingested, revision=99)


def test_replay_without_a_processor_fails_explicitly(store, ingested):
    result = ReplayService(store, processor=None).replay(ingested)
    assert not result.ok
    assert result.status == S.REPLAY_FAILED
    assert "processador" in result.error


# ---------------------------------------------------------------------------
# Invariantes
# ---------------------------------------------------------------------------


def test_replay_never_advances_the_cursor(store, ingested):
    before = store.get_cursor(SOURCE_ID, "rss-prime-event-v1")["cursor"]
    result = ReplayService(store, RecordingProcessor()).replay(ingested)
    assert result.cursor_advanced is False
    assert store.get_cursor(SOURCE_ID, "rss-prime-event-v1")["cursor"] == before


def test_replay_never_modifies_the_stored_payload(store, ingested):
    before = copy.deepcopy(store.get_event(ingested, 1).normalized_payload)
    hash_before = store.get_event(ingested, 1).payload_hash

    ReplayService(store, RecordingProcessor()).replay(ingested, revision=1)

    after = store.get_event(ingested, 1)
    assert after.normalized_payload == before
    assert after.payload_hash == hash_before


def test_replay_never_changes_the_input_revision(store, ingested):
    ReplayService(store, RecordingProcessor()).replay(ingested, revision=1)
    assert [item.revision for item in store.list_event_revisions(ingested)] == [1, 2]


def test_replay_never_creates_a_new_event_key(store, ingested):
    ReplayService(store, RecordingProcessor()).replay(ingested)
    cursor = store.conn.cursor()
    cursor.execute("SELECT DISTINCT event_key FROM rssprime_events")
    assert [row["event_key"] for row in cursor.fetchall()] == [ingested]


def test_replay_does_not_create_extra_event_rows(store, ingested):
    before = len(store.list_event_revisions(ingested))
    ReplayService(store, RecordingProcessor()).replay(ingested)
    assert len(store.list_event_revisions(ingested)) == before


# ---------------------------------------------------------------------------
# Historico de tentativas
# ---------------------------------------------------------------------------


def test_replay_records_a_new_operational_attempt(store, ingested):
    ReplayService(store, RecordingProcessor()).replay(ingested, revision=1)
    attempts = store.list_attempts(ingested, 1)
    assert len(attempts) == 1
    assert attempts[0]["attempt_kind"] == ATTEMPT_KIND_REPLAY
    assert attempts[0]["status"] == S.REPLAY_COMPLETED


def test_repeated_replays_accumulate_attempts(store, ingested):
    service = ReplayService(store, RecordingProcessor())
    service.replay(ingested, revision=1)
    service.replay(ingested, revision=1)
    service.replay(ingested, revision=1)
    assert len(store.list_attempts(ingested, 1)) == 3


def test_failed_replay_is_recorded_as_a_failed_attempt(store, ingested):
    def exploding(_event):
        raise RuntimeError("falha simulada no processamento")

    result = ReplayService(store, exploding).replay(ingested, revision=1)
    assert result.status == S.REPLAY_FAILED
    attempts = store.list_attempts(ingested, 1)
    assert attempts[0]["status"] == S.REPLAY_FAILED
    assert "falha simulada" in attempts[0]["error"]


def test_replay_updates_the_processing_status(store, ingested):
    ReplayService(store, RecordingProcessor()).replay(ingested, revision=1)
    assert store.get_event(ingested, 1).processing_status == S.REPLAY_COMPLETED


def test_replay_records_the_draft_it_produced(store, ingested):
    result = ReplayService(store, RecordingProcessor()).replay(ingested, revision=1)
    assert result.draft_id == "draft-replay-r1"
    assert store.get_event(ingested, 1).draft_id == "draft-replay-r1"


def test_replay_of_different_revisions_yields_different_drafts(store, ingested):
    service = ReplayService(store, RecordingProcessor())
    first = service.replay(ingested, revision=1)
    second = service.replay(ingested, revision=2)
    assert first.draft_id != second.draft_id


# ---------------------------------------------------------------------------
# Isolamento
# ---------------------------------------------------------------------------


def test_replay_does_not_import_or_touch_payload(store, ingested):
    """O replay usa o submitter configurado; nao fala com o Payload CMS."""
    import app.replay as replay_module

    source = Path(replay_module.__file__).read_text(encoding="utf-8")
    assert "PayloadDraftSubmitter" not in source
    assert "requests" not in source
    assert "httpx" not in source


def test_replay_result_never_uses_publication_vocabulary(store, ingested):
    result = ReplayService(store, RecordingProcessor()).replay(ingested)
    rendered = json.dumps(result.to_dict())
    for forbidden in ("PUBLISHED", "APPROVED", "PUBLICATION_READY"):
        assert forbidden not in rendered
