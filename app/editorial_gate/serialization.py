"""Canonical serialization for gate results.

Reuses the same discipline as the editorial and contract layers: sorted keys,
deterministic separators, UTF-8. Kept separate from
``app.editorial.serialization`` because the volatile-field set differs — the
gate strips its own identity fields, not the draft's.
"""

from __future__ import annotations

import json
from typing import Any, Final, FrozenSet, Mapping

#: Fields that must never take part in ``gate_hash``. A changed timestamp, a
#: reworded summary or the id derived from the hash itself are not a different
#: evaluation.
VOLATILE_GATE_FIELDS: Final[FrozenSet[str]] = frozenset(
    {"evaluated_at", "gate_id", "gate_hash", "summary", "message", "remediation"}
)


def strip_volatile(value: Any) -> Any:
    """Recursively drop the fields that identify *when*, not *what*."""
    if isinstance(value, Mapping):
        return {
            str(key): strip_volatile(item)
            for key, item in value.items()
            if str(key) not in VOLATILE_GATE_FIELDS
        }
    if isinstance(value, (list, tuple)):
        return [strip_volatile(item) for item in value]
    return value


def to_plain(value: Any) -> Any:
    """Reduce dataclasses and containers to JSON-native types."""
    if hasattr(value, "to_hashable_dict"):
        return to_plain(value.to_hashable_dict())
    if hasattr(value, "to_dict"):
        return to_plain(value.to_dict())
    if isinstance(value, Mapping):
        return {str(key): to_plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_plain(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def gate_canonical_json(value: Any) -> str:
    """Deterministic JSON: sorted keys, no incidental whitespace, UTF-8."""
    return json.dumps(
        strip_volatile(to_plain(value)),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def dumps_pretty(value: Any) -> str:
    """Readable JSON for artifacts and CLI output."""
    return json.dumps(to_plain(value), ensure_ascii=False, sort_keys=True, indent=2)


__all__ = [
    "VOLATILE_GATE_FIELDS",
    "dumps_pretty",
    "gate_canonical_json",
    "strip_volatile",
    "to_plain",
]
