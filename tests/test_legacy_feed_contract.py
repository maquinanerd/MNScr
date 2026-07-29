"""Compatibilidade temporaria com o superfeed pre-contrato.

O adaptador legado nao pode fingir que o RSS Prime enviou o que ele nao envia.
Ausente permanece ausente.
"""

import json
import socket
from pathlib import Path

import pytest

from app.contracts import errors as E
from app.contracts.legacy_feed_v0 import CONTRACT_VERSION as LEGACY
from app.contracts.legacy_feed_v0 import LegacyFeedV0Adapter
from app.contracts.validation import validate_payload
from app.feeds import extract_event_envelope

FIXTURES = Path(__file__).parent / "fixtures" / "rssprime"


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _blocked(*args, **kwargs):
        raise AssertionError("Teste do contrato legado tentou acessar a rede")

    monkeypatch.setattr(socket, "socket", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)


@pytest.fixture
def legacy_item() -> dict:
    return json.loads((FIXTURES / "legacy_superfeed_item.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Adaptacao
# ---------------------------------------------------------------------------


def test_legacy_item_is_adapted_to_the_normalized_domain(legacy_item):
    event = LegacyFeedV0Adapter().adapt(legacy_item)
    assert event.event_key == "evt-legacy-superfeed-001"
    assert event.contract_version == LEGACY
    assert event.source_count == 2
    assert event.is_multi_source is True


def test_legacy_revision_is_always_one(legacy_item):
    """O feed legado nao tem revisoes; alegar outra coisa seria invencao."""
    assert LegacyFeedV0Adapter().adapt(legacy_item).revision == 1


def test_legacy_sources_are_built_from_the_pipe_separated_urls(legacy_item):
    event = LegacyFeedV0Adapter().adapt(legacy_item)
    assert [s.url for s in event.sources] == [
        "https://variety.example/2026/07/nova-fase",
        "https://deadline.example/2026/07/nova-fase",
    ]


def test_legacy_primary_source_follows_the_item_url(legacy_item):
    event = LegacyFeedV0Adapter().adapt(legacy_item)
    assert event.primary_source.url == legacy_item["url"]
    assert sum(1 for s in event.sources if s.is_primary) == 1


def test_legacy_item_validates_successfully(legacy_item):
    result = validate_payload(legacy_item)
    assert result.ok, result.error_codes


# ---------------------------------------------------------------------------
# Warning obrigatorio e hash local
# ---------------------------------------------------------------------------


def test_legacy_always_emits_the_legacy_warning(legacy_item):
    result = validate_payload(legacy_item)
    assert E.LEGACY_CONTRACT in result.warning_codes


def test_legacy_hash_is_calculated_locally_and_declared_as_such(legacy_item):
    result = validate_payload(legacy_item)
    assert result.event.payload_hash_origin == "mnscr-calculated-legacy"
    assert len(result.event.payload_hash) == 64


def test_legacy_hash_is_deterministic(legacy_item):
    a = LegacyFeedV0Adapter().adapt(legacy_item).payload_hash
    b = LegacyFeedV0Adapter().adapt(legacy_item).payload_hash
    assert a == b


def test_legacy_hash_changes_when_the_item_really_changes(legacy_item):
    a = LegacyFeedV0Adapter().adapt(legacy_item).payload_hash
    b = LegacyFeedV0Adapter().adapt({**legacy_item, "title": "Outro titulo"}).payload_hash
    assert a != b


# ---------------------------------------------------------------------------
# Nao inventar
# ---------------------------------------------------------------------------


def test_legacy_never_invents_an_author(legacy_item):
    event = LegacyFeedV0Adapter().adapt(legacy_item)
    assert all(source.author is None for source in event.sources)


def test_legacy_never_invents_a_per_source_date(legacy_item):
    event = LegacyFeedV0Adapter().adapt(legacy_item)
    assert all(source.published_at is None for source in event.sources)


def test_legacy_never_invents_cluster_confidence(legacy_item):
    assert LegacyFeedV0Adapter().adapt(legacy_item).cluster_confidence is None


def test_legacy_never_invents_a_cursor(legacy_item):
    assert LegacyFeedV0Adapter().adapt(legacy_item).cursor is None


def test_legacy_absences_surface_as_warnings(legacy_item):
    result = validate_payload(legacy_item)
    assert E.MISSING_CLUSTER_CONFIDENCE in result.warning_codes
    assert E.MISSING_CURSOR in result.warning_codes
    assert E.SOURCE_AUTHOR_UNKNOWN in result.warning_codes
    assert E.SOURCE_DATE_UNKNOWN in result.warning_codes


def test_unknown_legacy_fields_remain_unknown(legacy_item):
    """Campos que o MNScr nao modela sao preservados, nao promovidos."""
    event = LegacyFeedV0Adapter().adapt(legacy_item)
    unmapped = event.metadata["legacy_unmapped"]
    assert unmapped["campo_desconhecido_do_feed"] == "valor que o MNScr nao modela"


def test_legacy_metadata_marks_its_own_origin(legacy_item):
    assert LegacyFeedV0Adapter().adapt(legacy_item).metadata["legacy_source"] is True


# ---------------------------------------------------------------------------
# Desativacao
# ---------------------------------------------------------------------------


def test_legacy_is_rejected_when_compatibility_is_off(legacy_item):
    result = validate_payload(legacy_item, accept_legacy=False)
    assert not result.ok
    assert E.LEGACY_CONTRACT_DISABLED in result.error_codes
    assert result.event is None


def test_legacy_is_rejected_when_v1_is_required(legacy_item):
    result = validate_payload(legacy_item, required_contract="rss-prime-event-v1")
    assert E.LEGACY_CONTRACT_DISABLED in result.error_codes


def test_v1_still_passes_when_legacy_is_off():
    v1 = json.loads((FIXTURES / "event_v1_valid.json").read_text(encoding="utf-8"))
    assert validate_payload(v1, accept_legacy=False).ok


def test_legacy_contract_is_never_treated_as_definitive(legacy_item):
    """O contrato legado e explicitamente marcado como temporario."""
    result = validate_payload(legacy_item)
    assert result.contract_version == LEGACY
    assert result.contract_version != "rss-prime-event-v1"


# ---------------------------------------------------------------------------
# Envelope: transporte nao decide contrato
# ---------------------------------------------------------------------------


def test_envelope_passes_a_legacy_item_through_untouched(legacy_item):
    assert extract_event_envelope(legacy_item) == legacy_item


def test_envelope_extracts_an_embedded_v1_json_document():
    v1 = json.loads((FIXTURES / "event_v1_valid.json").read_text(encoding="utf-8"))
    wrapped = {"title": "irrelevante", "rssprime_event": json.dumps(v1)}
    assert extract_event_envelope(wrapped)["event_key"] == v1["event_key"]


def test_envelope_extracts_an_embedded_v1_mapping():
    v1 = json.loads((FIXTURES / "event_v1_valid.json").read_text(encoding="utf-8"))
    assert extract_event_envelope({"contract_payload": v1})["event_key"] == v1["event_key"]


def test_envelope_falls_back_to_legacy_on_malformed_json(legacy_item):
    """Decidir que algo e invalido e papel do contrato, nao do parser."""
    broken = {**legacy_item, "rssprime_event": "{isto nao e json"}
    assert extract_event_envelope(broken)["event_key"] == legacy_item["event_key"]


def test_envelope_does_not_validate_or_apply_revision_rules():
    """O extrator devolve o mapping cru; nao valida nem decide revisao."""
    envelope = extract_event_envelope({"contract_version": "rss-prime-event-v9", "revision": 0})
    assert envelope["contract_version"] == "rss-prime-event-v9"
    assert envelope["revision"] == 0
