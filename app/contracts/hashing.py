"""Canonical payload hashing for RSS Prime events.

The same *logical* event must always produce the same hash, on any machine and
in any Python version, regardless of key order, source order, date spelling or
transport noise. That is the whole point: the hash is what lets MNScr tell a
duplicate redelivery apart from a genuinely changed revision.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Final, FrozenSet, Iterable, Mapping

# The hash field itself can never take part in the content being hashed, and
# neither can anything the transport layer adds on the way in.
EXCLUDED_FROM_HASH: Final[FrozenSet[str]] = frozenset(
    {
        "payload_hash",
        "payloadHash",
        "payload_hash_origin",
        "cursor",
        "received_at",
        "receivedAt",
        "delivery_id",
        "deliveryId",
        "delivery_attempt",
        "_raw",
        "raw_payload",
        "etag",
        "request_id",
        "requestId",
    }
)

_SHA256_RE: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{64}$")
_TRAILING_Z = re.compile(r"[Zz]$")


def is_sha256(value: Any) -> bool:
    """True only for a lowercase 64-char hex digest."""
    if not isinstance(value, str):
        return False
    return bool(_SHA256_RE.match(value.strip()))


def normalize_datetime(value: Any) -> Any:
    """Collapse equivalent ISO 8601 spellings to a single UTC representation.

    ``2026-01-02T03:04:05Z``, ``2026-01-02T03:04:05+00:00`` and
    ``2026-01-02T00:04:05-03:00`` are the same instant and must hash the same.
    Anything unparseable is returned untouched — guessing would be worse than
    leaving the value alone.
    """
    if not isinstance(value, str):
        return value
    candidate = value.strip()
    if not candidate:
        return value
    iso_candidate = _TRAILING_Z.sub("+00:00", candidate)
    try:
        parsed = datetime.fromisoformat(iso_candidate)
    except ValueError:
        return value
    if parsed.tzinfo is None:
        # A naive timestamp is taken as UTC; the feed has no other timezone to
        # offer and inventing a local one would make the hash machine-dependent.
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


_DATE_FIELD_HINTS: Final[FrozenSet[str]] = frozenset(
    {
        "first_seen",
        "firstSeen",
        "last_seen",
        "lastSeen",
        "published_at",
        "publishedAt",
        "updated_at",
        "updatedAt",
        "date",
    }
)


def _canonicalize(value: Any, *, key: str | None = None) -> Any:
    """Recursively reduce a payload to hashable, order-stable JSON types."""
    if isinstance(value, Mapping):
        result = {}
        for raw_key in sorted(value.keys(), key=str):
            if str(raw_key) in EXCLUDED_FROM_HASH:
                continue
            result[str(raw_key)] = _canonicalize(value[raw_key], key=str(raw_key))
        return result
    if isinstance(value, (list, tuple)):
        items = [_canonicalize(item) for item in value]
        return _sort_sequence(items, key=key)
    if isinstance(value, datetime):
        return normalize_datetime(value.isoformat())
    if isinstance(value, str) and (key in _DATE_FIELD_HINTS):
        return normalize_datetime(value)
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float, str)):
        return value
    if hasattr(value, "to_hashable_dict"):
        return _canonicalize(value.to_hashable_dict())
    if hasattr(value, "__dict__"):
        return _canonicalize(dict(vars(value)))
    return str(value)


def _sort_sequence(items: list[Any], *, key: str | None) -> list[Any]:
    """Order a sequence deterministically.

    Sources carry no meaningful transport order — RSS Prime may reorder them
    between deliveries — so they are sorted by a stable identity. Everything
    else keeps the order it arrived in, because for ordinary lists the order is
    part of the content.
    """
    if key not in {"sources", "all_sources", "urls", "additional_urls"}:
        return items
    return sorted(items, key=_stable_sort_key)


def _stable_sort_key(item: Any) -> str:
    if isinstance(item, Mapping):
        for candidate in ("url", "source_id", "domain", "name"):
            value = item.get(candidate)
            if isinstance(value, str) and value.strip():
                return f"{candidate}:{value.strip()}"
        return json.dumps(item, sort_keys=True, ensure_ascii=False)
    return str(item)


def canonical_payload(event: Any) -> Any:
    """The exact structure that gets hashed. Exposed for debugging and tests."""
    if hasattr(event, "to_hashable_dict"):
        return _canonicalize(event.to_hashable_dict())
    return _canonicalize(event)


def canonical_payload_json(event: Any) -> str:
    return json.dumps(
        canonical_payload(event),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def calculate_event_payload_hash(event_without_hash: Any) -> str:
    """SHA-256 of the canonical payload.

    ``event_without_hash`` may still *contain* a ``payload_hash`` key; it is
    stripped before hashing, so passing a full event is safe and produces the
    same digest as passing one with the field removed.
    """
    encoded = canonical_payload_json(event_without_hash).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def verify_event_payload_hash(event: Any) -> bool:
    """True when the declared ``payload_hash`` matches the recomputed one.

    A missing or malformed declared hash is a failed verification, not an
    exception: the caller turns it into a structured issue.
    """
    declared = _declared_hash(event)
    if not is_sha256(declared):
        return False
    return calculate_event_payload_hash(event) == declared.strip().lower()


def _declared_hash(event: Any) -> Any:
    if isinstance(event, Mapping):
        return event.get("payload_hash") or event.get("payloadHash")
    return getattr(event, "payload_hash", None)


def strip_hash_fields(payload: Mapping[str, Any]) -> dict:
    """A shallow copy without the fields that never participate in the hash."""
    return {k: v for k, v in payload.items() if str(k) not in EXCLUDED_FROM_HASH}


def hash_of_strings(values: Iterable[str]) -> str:
    """Stable digest for a set of strings (used for local legacy identity)."""
    joined = "|".join(sorted(str(v).strip() for v in values if str(v).strip()))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


__all__ = [
    "EXCLUDED_FROM_HASH",
    "calculate_event_payload_hash",
    "canonical_payload",
    "canonical_payload_json",
    "hash_of_strings",
    "is_sha256",
    "normalize_datetime",
    "strip_hash_fields",
    "verify_event_payload_hash",
]
