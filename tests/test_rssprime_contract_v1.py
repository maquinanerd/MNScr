"""Contrato ``rss-prime-event-v1``: validacao de campos e regras de dominio.

Nenhum teste aqui acessa rede, RSS Prime real, Gemini ou Payload.
"""

import copy
import json
import socket
from pathlib import Path

import pytest

from app.contracts import errors as E
from app.contracts.rssprime_event_v1 import RssPrimeEventV1
from app.contracts.validation import (
    KNOWN_CONTRACT_VERSIONS,
    detect_contract_version,
    validate_event,
    validate_payload,
)

FIXTURES = Path(__file__).parent / "fixtures" / "rssprime"


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Qualquer uso de socket em teste de contrato e falha dura."""

    def _blocked(*args, **kwargs):
        raise AssertionError("Teste de contrato tentou acessar a rede")

    monkeypatch.setattr(socket, "socket", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def valid_event() -> dict:
    return load_fixture("event_v1_valid.json")


def rehash(payload: dict) -> dict:
    """Recalcula o hash apos alterar o payload (evita falso PAYLOAD_HASH_MISMATCH)."""
    payload = copy.deepcopy(payload)
    payload.pop("payload_hash", None)
    payload["payload_hash"] = RssPrimeEventV1.from_mapping(payload).calculate_payload_hash()
    return payload


# ---------------------------------------------------------------------------
# Evento valido
# ---------------------------------------------------------------------------


def test_valid_event_passes(valid_event):
    result = validate_payload(valid_event)
    assert result.ok, result.error_codes
    assert result.error_codes == []
    assert result.contract_version == "rss-prime-event-v1"


def test_valid_event_is_marked_as_verified_by_rssprime(valid_event):
    result = validate_payload(valid_event)
    assert result.event.payload_hash_origin == "rss-prime-verified"


def test_valid_event_preserves_declared_metadata(valid_event):
    result = validate_payload(valid_event)
    assert result.event.metadata["rssprime_batch"] == "batch-77"


def test_unknown_fields_are_preserved_in_metadata(valid_event):
    payload = rehash({**valid_event, "campo_novo_do_rssprime": {"x": 1}})
    result = validate_payload(payload)
    assert result.event.metadata["campo_novo_do_rssprime"] == {"x": 1}


def test_primary_source_is_identified(valid_event):
    result = validate_payload(valid_event)
    assert result.event.primary_source.domain == "variety.example"
    assert result.event.additional_urls == ["https://deadline.example/2026/07/nova-fase"]


# ---------------------------------------------------------------------------
# Versao de contrato
# ---------------------------------------------------------------------------


def test_unknown_contract_version_is_rejected_by_name(valid_event):
    payload = {**valid_event, "contract_version": "rss-prime-event-v9"}
    result = validate_payload(payload)
    assert not result.ok
    assert E.UNKNOWN_CONTRACT_VERSION in result.error_codes


def test_detect_contract_version_reads_both_spellings():
    assert detect_contract_version({"contract_version": "rss-prime-event-v1"}) == "rss-prime-event-v1"
    assert detect_contract_version({"contractVersion": "rss-prime-event-v1"}) == "rss-prime-event-v1"
    assert detect_contract_version({}) is None


def test_known_contract_versions_are_exactly_two():
    assert KNOWN_CONTRACT_VERSIONS == {"rss-prime-event-v1", "legacy-feed-input-v0"}


def test_camel_case_payload_is_accepted(valid_event):
    camel = {
        "contractVersion": valid_event["contract_version"],
        "eventKey": valid_event["event_key"],
        "revision": valid_event["revision"],
        "topic": valid_event["topic"],
        "title": valid_event["title"],
        "firstSeen": valid_event["first_seen"],
        "lastSeen": valid_event["last_seen"],
        "isMultiSource": valid_event["is_multi_source"],
        "sourceCount": valid_event["source_count"],
        "clusterConfidence": valid_event["cluster_confidence"],
        "cursor": valid_event["cursor"],
        "sources": valid_event["sources"],
        "metadata": valid_event["metadata"],
    }
    assert RssPrimeEventV1.from_mapping(camel).event_key == valid_event["event_key"]


# ---------------------------------------------------------------------------
# event_key, revision, title, topic
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("empty", ["", "   ", None])
def test_empty_event_key_is_rejected(valid_event, empty):
    result = validate_payload(rehash({**valid_event, "event_key": empty}))
    assert E.INVALID_EVENT_KEY in result.error_codes


@pytest.mark.parametrize("revision", [0, -1, -99])
def test_revision_below_one_is_rejected(valid_event, revision):
    result = validate_payload(rehash({**valid_event, "revision": revision}))
    assert E.INVALID_REVISION in result.error_codes


def test_non_numeric_revision_is_rejected(valid_event):
    result = validate_payload(rehash({**valid_event, "revision": "segunda"}))
    assert E.INVALID_REVISION in result.error_codes


def test_missing_title_is_rejected(valid_event):
    result = validate_payload(rehash({**valid_event, "title": ""}))
    assert E.INVALID_TITLE in result.error_codes


def test_missing_topic_is_rejected(valid_event):
    result = validate_payload(rehash({**valid_event, "topic": ""}))
    assert E.INVALID_TOPIC in result.error_codes


def test_games_topic_stays_blocked(valid_event):
    """Games continuam bloqueados: a MS-2 nao afrouxa politica editorial."""
    result = validate_payload(rehash({**valid_event, "topic": "games"}))
    assert not result.ok
    assert E.BLOCKED_TOPIC in result.error_codes


def test_games_topic_is_case_insensitive(valid_event):
    result = validate_payload(rehash({**valid_event, "topic": "GAMES"}))
    assert E.BLOCKED_TOPIC in result.error_codes


# ---------------------------------------------------------------------------
# Hash declarado
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_hash",
    ["", "nao-e-hash", "abc123", "Z" * 64, "a" * 63],
    ids=["vazio", "texto", "curto", "nao-hex", "63-chars"],
)
def test_invalid_payload_hash_is_rejected(valid_event, bad_hash):
    payload = {**valid_event, "payload_hash": bad_hash}
    result = validate_payload(payload)
    assert E.INVALID_PAYLOAD_HASH in result.error_codes


def test_divergent_payload_hash_is_rejected(valid_event):
    payload = {**valid_event, "payload_hash": "b" * 64}
    result = validate_payload(payload)
    assert E.PAYLOAD_HASH_MISMATCH in result.error_codes


def test_payload_hash_mismatch_reports_both_hashes(valid_event):
    result = validate_payload({**valid_event, "payload_hash": "b" * 64})
    issue = next(i for i in result.errors if i.code == E.PAYLOAD_HASH_MISMATCH)
    assert issue.context["declared"] == "b" * 64
    assert issue.context["recomputed"] == valid_event["payload_hash"]


# ---------------------------------------------------------------------------
# Datas
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_date", ["ontem", "2026-13-45T99:99:99Z", "não é data", ""],
)
def test_invalid_dates_are_rejected(valid_event, bad_date):
    result = validate_payload(rehash({**valid_event, "first_seen": bad_date}))
    assert E.INVALID_EVENT_DATES in result.error_codes


def test_last_seen_before_first_seen_is_rejected(valid_event):
    payload = rehash(
        {**valid_event, "first_seen": "2026-07-05T10:00:00Z", "last_seen": "2026-07-01T10:00:00Z"}
    )
    result = validate_payload(payload)
    assert E.INVALID_EVENT_DATES in result.error_codes


def test_equal_dates_are_accepted(valid_event):
    same = valid_event["first_seen"]
    result = validate_payload(rehash({**valid_event, "last_seen": same}))
    assert result.ok, result.error_codes


# ---------------------------------------------------------------------------
# Fontes
# ---------------------------------------------------------------------------


def test_source_count_mismatch_is_rejected():
    result = validate_payload(load_fixture("event_v1_invalid_source_count.json"))
    assert not result.ok
    assert E.INVALID_SOURCE_COUNT in result.error_codes


def test_source_count_below_one_is_rejected(valid_event):
    payload = rehash({**valid_event, "sources": [], "source_count": 0, "is_multi_source": False})
    result = validate_payload(payload)
    assert E.INVALID_SOURCE_COUNT in result.error_codes


def test_missing_primary_source_is_rejected(valid_event):
    payload = copy.deepcopy(valid_event)
    for source in payload["sources"]:
        source["is_primary"] = False
    result = validate_payload(rehash(payload))
    assert E.NO_PRIMARY_SOURCE in result.error_codes


def test_multiple_primary_sources_are_rejected(valid_event):
    payload = copy.deepcopy(valid_event)
    for source in payload["sources"]:
        source["is_primary"] = True
    result = validate_payload(rehash(payload))
    assert E.MULTIPLE_PRIMARY_SOURCES in result.error_codes


@pytest.mark.parametrize(
    "bad_url",
    ["", "ftp://variety.example/x", "javascript:alert(1)", "não é url", "/apenas/caminho"],
)
def test_invalid_source_url_is_rejected(valid_event, bad_url):
    payload = copy.deepcopy(valid_event)
    payload["sources"][1]["url"] = bad_url
    result = validate_payload(rehash(payload))
    assert E.INVALID_SOURCE_URL in result.error_codes


def test_duplicate_source_url_is_rejected(valid_event):
    payload = copy.deepcopy(valid_event)
    payload["sources"][1]["url"] = payload["sources"][0]["url"]
    payload["sources"][1]["domain"] = payload["sources"][0]["domain"]
    result = validate_payload(rehash(payload))
    assert E.DUPLICATE_SOURCE_URL in result.error_codes


def test_domain_that_contradicts_the_url_is_rejected(valid_event):
    payload = copy.deepcopy(valid_event)
    payload["sources"][0]["domain"] = "outrodominio.example"
    result = validate_payload(rehash(payload))
    assert E.INVALID_SOURCE_DOMAIN in result.error_codes


def test_multi_source_flag_must_match_source_count(valid_event):
    payload = rehash({**valid_event, "is_multi_source": False})
    result = validate_payload(payload)
    assert E.INVALID_MULTI_SOURCE_FLAG in result.error_codes


def test_single_source_event_is_not_multi_source(valid_event):
    payload = copy.deepcopy(valid_event)
    payload["sources"] = payload["sources"][:1]
    payload["source_count"] = 1
    payload["is_multi_source"] = False
    result = validate_payload(rehash(payload))
    assert result.ok, result.error_codes


def test_source_without_author_emits_warning_not_error(valid_event):
    payload = copy.deepcopy(valid_event)
    payload["sources"][0].pop("author")
    result = validate_payload(rehash(payload))
    assert result.ok
    assert E.SOURCE_AUTHOR_UNKNOWN in result.warning_codes


def test_source_without_date_emits_warning_not_error(valid_event):
    payload = copy.deepcopy(valid_event)
    payload["sources"][0].pop("published_at")
    result = validate_payload(rehash(payload))
    assert result.ok
    assert E.SOURCE_DATE_UNKNOWN in result.warning_codes


# ---------------------------------------------------------------------------
# cluster_confidence
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", [-0.1, 1.1, 42, -1])
def test_cluster_confidence_outside_zero_to_one_is_rejected(valid_event, value):
    result = validate_payload(rehash({**valid_event, "cluster_confidence": value}))
    assert E.INVALID_CLUSTER_CONFIDENCE in result.error_codes


@pytest.mark.parametrize("value", [0.0, 0.5, 1.0])
def test_cluster_confidence_within_range_is_accepted(valid_event, value):
    result = validate_payload(rehash({**valid_event, "cluster_confidence": value}))
    assert result.ok, result.error_codes


def test_missing_cluster_confidence_is_a_warning(valid_event):
    payload = copy.deepcopy(valid_event)
    payload.pop("cluster_confidence")
    result = validate_payload(rehash(payload))
    assert result.ok
    assert E.MISSING_CLUSTER_CONFIDENCE in result.warning_codes


def test_non_numeric_cluster_confidence_is_rejected(valid_event):
    result = validate_payload(rehash({**valid_event, "cluster_confidence": "alta"}))
    assert E.INVALID_CLUSTER_CONFIDENCE in result.error_codes


# ---------------------------------------------------------------------------
# Cursor
# ---------------------------------------------------------------------------


def test_missing_cursor_is_a_warning_not_an_error(valid_event):
    payload = copy.deepcopy(valid_event)
    payload.pop("cursor")
    result = validate_payload(rehash(payload))
    assert result.ok
    assert E.MISSING_CURSOR in result.warning_codes


# ---------------------------------------------------------------------------
# Validacao total
# ---------------------------------------------------------------------------


def test_validation_collects_every_issue_instead_of_stopping_at_the_first(valid_event):
    payload = copy.deepcopy(valid_event)
    payload["event_key"] = ""
    payload["title"] = ""
    payload["topic"] = ""
    result = validate_payload(rehash(payload))
    assert {E.INVALID_EVENT_KEY, E.INVALID_TITLE, E.INVALID_TOPIC}.issubset(set(result.error_codes))


def test_issues_are_structured_not_free_text(valid_event):
    result = validate_payload(rehash({**valid_event, "revision": 0}))
    issue = next(i for i in result.errors if i.code == E.INVALID_REVISION)
    assert issue.to_dict()["code"] == E.INVALID_REVISION
    assert issue.to_dict()["severity"] == "error"
    assert issue.field == "revision"


def test_validation_result_serializes_for_persistence(valid_event):
    result = validate_payload(rehash({**valid_event, "topic": "games"}))
    payload = result.to_dict()
    assert payload["status"] == "INVALID"
    assert any(err["code"] == E.BLOCKED_TOPIC for err in payload["errors"])


def test_validate_event_can_be_called_directly(valid_event):
    event = RssPrimeEventV1.from_mapping(valid_event)
    assert validate_event(event, expect_declared_hash=True).ok


def test_contract_never_emits_publication_vocabulary(valid_event):
    result = validate_payload(valid_event)
    assert result.status in {"VALIDATED", "INVALID"}
    for forbidden in ("PUBLISHED", "APPROVED", "PUBLICATION_READY"):
        assert forbidden not in json.dumps(result.to_dict())
