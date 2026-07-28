"""Canonical serialization and deterministic hashing for editorial drafts.

The canonical form is the single source of truth for `input_hash` /
`output_hash` and for the on-disk artifact. It must be stable across runs,
machines and Python versions:

- UTF-8, never ASCII-escaped
- object keys sorted
- deterministic separators (no incidental whitespace)
- volatile fields (timestamps, durations, ids derived from time) excluded
  from anything that feeds a hash
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, is_dataclass
from typing import Any, Iterable, Mapping

# Fields that must never take part in a hash: they change on every run even
# when the editorial input and output are identical.
VOLATILE_FIELDS: frozenset[str] = frozenset(
    {
        "created_at",
        "duration_ms",
        "generated_at",
        "submitted_at",
        "timestamp",
        "input_hash",
        "output_hash",
        "draft_id",
    }
)


def to_plain(value: Any) -> Any:
    """Recursively convert dataclasses/mappings/sequences into plain JSON types."""
    if is_dataclass(value) and not isinstance(value, type):
        return to_plain(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): to_plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_plain(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted(to_plain(item) for item in value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def strip_volatile(value: Any, volatile: Iterable[str] = VOLATILE_FIELDS) -> Any:
    """Remove volatile keys at any depth so hashes stay reproducible."""
    volatile_set = frozenset(volatile)
    if isinstance(value, Mapping):
        return {
            key: strip_volatile(item, volatile_set)
            for key, item in value.items()
            if key not in volatile_set
        }
    if isinstance(value, list):
        return [strip_volatile(item, volatile_set) for item in value]
    return value


def canonical_json(value: Any) -> str:
    """Serialize to the canonical JSON form used for hashing and comparison."""
    return json.dumps(
        to_plain(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def stable_hash(value: Any) -> str:
    """SHA-256 over the canonical JSON form, with volatile fields removed."""
    payload = strip_volatile(to_plain(value))
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def dumps_pretty(value: Any) -> str:
    """Human-readable UTF-8 JSON for the on-disk artifact."""
    return json.dumps(
        to_plain(value),
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
    )
