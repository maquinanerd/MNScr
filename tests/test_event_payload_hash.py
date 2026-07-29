"""Hash canonico do payload de evento.

O mesmo evento logico precisa produzir o mesmo hash sempre. E o que separa uma
reentrega idempotente de uma revisao que realmente mudou.
"""

import copy
import json
from pathlib import Path

import pytest

from app.contracts.hashing import (
    EXCLUDED_FROM_HASH,
    calculate_event_payload_hash,
    canonical_payload,
    is_sha256,
    normalize_datetime,
    verify_event_payload_hash,
)
from app.contracts.rssprime_event_v1 import RssPrimeEventV1

FIXTURES = Path(__file__).parent / "fixtures" / "rssprime"


@pytest.fixture
def payload() -> dict:
    return json.loads((FIXTURES / "event_v1_valid.json").read_text(encoding="utf-8"))


def event_of(data: dict) -> RssPrimeEventV1:
    return RssPrimeEventV1.from_mapping(data)


def hash_of(data: dict) -> str:
    return event_of(data).calculate_payload_hash()


# ---------------------------------------------------------------------------
# Determinismo
# ---------------------------------------------------------------------------


def test_hash_is_sha256(payload):
    assert is_sha256(hash_of(payload))


def test_hash_is_stable_across_repeated_calls(payload):
    assert len({hash_of(payload) for _ in range(20)}) == 1


def test_key_order_does_not_change_the_hash(payload):
    reordered = {key: payload[key] for key in sorted(payload, reverse=True)}
    assert hash_of(reordered) == hash_of(payload)


def test_nested_key_order_does_not_change_the_hash(payload):
    shuffled = copy.deepcopy(payload)
    shuffled["sources"] = [
        {key: source[key] for key in sorted(source, reverse=True)} for source in shuffled["sources"]
    ]
    assert hash_of(shuffled) == hash_of(payload)


def test_source_order_does_not_change_the_hash(payload):
    """RSS Prime pode reordenar as fontes entre entregas; isso nao e mudanca."""
    reversed_sources = copy.deepcopy(payload)
    reversed_sources["sources"] = list(reversed(reversed_sources["sources"]))
    assert hash_of(reversed_sources) == hash_of(payload)


@pytest.mark.parametrize(
    "first, second",
    [
        ("2026-07-01T10:00:00Z", "2026-07-01T10:00:00+00:00"),
        ("2026-07-01T10:00:00Z", "2026-07-01T07:00:00-03:00"),
        ("2026-07-01T10:00:00Z", "2026-07-01T10:00:00"),
    ],
    ids=["z-vs-offset", "fuso-diferente", "naive-vira-utc"],
)
def test_equivalent_date_spellings_hash_the_same(payload, first, second):
    a = copy.deepcopy(payload)
    b = copy.deepcopy(payload)
    a["first_seen"] = first
    b["first_seen"] = second
    assert hash_of(a) == hash_of(b)


def test_unparseable_date_is_left_alone_instead_of_guessed():
    assert normalize_datetime("ontem de manha") == "ontem de manha"
    assert normalize_datetime("") == ""
    assert normalize_datetime(None) is None


# ---------------------------------------------------------------------------
# Exclusoes
# ---------------------------------------------------------------------------


def test_payload_hash_never_hashes_itself(payload):
    without = copy.deepcopy(payload)
    without.pop("payload_hash")
    assert hash_of(without) == hash_of(payload)


def test_a_wrong_declared_hash_does_not_change_the_recomputed_one(payload):
    tampered = {**payload, "payload_hash": "f" * 64}
    assert hash_of(tampered) == hash_of(payload)


@pytest.mark.parametrize("volatile", ["cursor", "received_at", "delivery_id", "etag", "request_id"])
def test_transport_fields_are_excluded_from_the_hash(payload, volatile):
    noisy = {**payload, volatile: "valor-que-muda-a-cada-entrega"}
    assert hash_of(noisy) == hash_of(payload)


def test_excluded_set_covers_the_hash_field_itself():
    assert "payload_hash" in EXCLUDED_FROM_HASH
    assert "cursor" in EXCLUDED_FROM_HASH


def test_canonical_payload_has_no_excluded_keys(payload):
    canonical = canonical_payload(event_of(payload))
    assert "payload_hash" not in canonical
    assert "cursor" not in canonical


# ---------------------------------------------------------------------------
# Sensibilidade a mudanca real
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field, value",
    [
        ("title", "Um titulo completamente diferente"),
        ("topic", "tv"),
        ("event_key", "evt-outro"),
        ("revision", 7),
        ("first_seen", "2020-01-01T00:00:00Z"),
        ("cluster_confidence", 0.10),
        ("source_count", 3),
    ],
)
def test_real_content_change_changes_the_hash(payload, field, value):
    changed = {**payload, field: value}
    assert hash_of(changed) != hash_of(payload)


def test_changing_a_source_url_changes_the_hash(payload):
    changed = copy.deepcopy(payload)
    changed["sources"][0]["url"] = "https://variety.example/2026/07/outra-materia"
    assert hash_of(changed) != hash_of(payload)


def test_adding_a_source_changes_the_hash(payload):
    changed = copy.deepcopy(payload)
    changed["sources"].append(
        {
            "source_id": "thr", "domain": "thr.example",
            "url": "https://thr.example/x", "is_primary": False,
        }
    )
    assert hash_of(changed) != hash_of(payload)


def test_changing_metadata_changes_the_hash(payload):
    changed = copy.deepcopy(payload)
    changed["metadata"]["rssprime_batch"] = "batch-99"
    assert hash_of(changed) != hash_of(payload)


def test_revision_2_has_a_different_hash_from_revision_1(payload):
    rev2 = json.loads((FIXTURES / "event_v1_revision_2.json").read_text(encoding="utf-8"))
    assert rev2["payload_hash"] != payload["payload_hash"]


# ---------------------------------------------------------------------------
# Verificacao
# ---------------------------------------------------------------------------


def test_verify_accepts_a_matching_declared_hash(payload):
    assert verify_event_payload_hash(event_of(payload)) is True


def test_verify_rejects_a_divergent_declared_hash(payload):
    event = event_of(payload)
    event.payload_hash = "a" * 64
    assert verify_event_payload_hash(event) is False


@pytest.mark.parametrize("bad", ["", None, "nao-hash", "A" * 64])
def test_verify_rejects_a_malformed_declared_hash(payload, bad):
    event = event_of(payload)
    event.payload_hash = bad
    assert verify_event_payload_hash(event) is False


def test_verify_works_on_a_plain_mapping_too(payload):
    """Um payload v1 bem formado hasheia igual como mapping ou como evento.

    E o que permite ao RSS Prime calcular o hash sem replicar o modelo de
    dominio do MNScr: canonicalizar o JSON basta.
    """
    assert verify_event_payload_hash(payload) is True
    assert calculate_event_payload_hash(payload) == hash_of(payload)


def test_a_mapping_with_extra_transport_noise_still_verifies(payload):
    noisy = {**payload, "received_at": "2026-07-28T00:00:00Z", "delivery_id": "d-1"}
    assert verify_event_payload_hash(noisy) is True


def test_calculate_accepts_a_mapping_without_the_hash_field(payload):
    without = copy.deepcopy(payload)
    without.pop("payload_hash")
    assert is_sha256(calculate_event_payload_hash(without))


def test_is_sha256_is_strict():
    assert is_sha256("a" * 64)
    assert not is_sha256("A" * 64)
    assert not is_sha256("a" * 63)
    assert not is_sha256(None)
    assert not is_sha256(12345)
