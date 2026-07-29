"""Deterministic identity for factual objects.

Same draft plus same source material must always yield the same claim ids,
evidence ids, conflict ids and assessment hash — otherwise two runs of the same
input would look like two different sets of facts.

Volatile fields (``created_at``, the ids derived from the hashes, prose that a
human may reword) never take part.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Final, FrozenSet, Mapping

VOLATILE_FACTUAL_FIELDS: Final[FrozenSet[str]] = frozenset(
    {
        "created_at",
        "assessment_id",
        "assessment_hash",
        "claim_hash",
        "evidence_hash",
        "conflict_hash",
        "link_id",
        "rationale",
        "description",
    }
)


def strip_volatile(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): strip_volatile(item)
            for key, item in value.items()
            if str(key) not in VOLATILE_FACTUAL_FIELDS
        }
    if isinstance(value, (list, tuple)):
        return [strip_volatile(item) for item in value]
    return value


def to_plain(value: Any) -> Any:
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


def canonical_json(value: Any) -> str:
    return json.dumps(
        strip_volatile(to_plain(value)),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def stable_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _digest(seed: str, prefix: str, length: int = 32) -> str:
    return f"{prefix}-{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:length]}"


def build_claim_id(
    *, draft_id: str, claim_type: str, normalized_subject: str, predicate: str,
    normalized_value: str, display_text: str,
) -> str:
    """Identity of a claim: what it asserts, not where it appears.

    ``display_text`` participates so two claims of the same shape but different
    wording stay distinct; ``draft_locations`` does not, so the same claim
    appearing in both title and body remains one claim.
    """
    seed = "|".join(
        [
            f"draft:{draft_id}", f"type:{claim_type}", f"subject:{normalized_subject}",
            f"predicate:{predicate}", f"value:{normalized_value}", f"text:{display_text}",
        ]
    )
    return _digest(seed, "claim")


def build_evidence_id(*, source_url: str, normalized_excerpt: str) -> str:
    """Identity of an evidence excerpt: which source, which passage."""
    return _digest(f"url:{source_url}|excerpt:{normalized_excerpt}", "ev")


def build_link_id(*, claim_id: str, evidence_id: str, relation: str) -> str:
    return _digest(f"claim:{claim_id}|evidence:{evidence_id}|relation:{relation}", "link")


def build_conflict_id(
    *, conflict_type: str, subject: str, values: list[str], claim_ids: list[str]
) -> str:
    """Values and claims are sorted: the order two sources arrived in is noise."""
    seed = "|".join(
        [
            f"type:{conflict_type}", f"subject:{subject}",
            f"values:{','.join(sorted(values))}", f"claims:{','.join(sorted(claim_ids))}",
        ]
    )
    return _digest(seed, "conflict")


def build_assessment_id(*, draft_id: str, version: str, assessment_hash: str) -> str:
    return _digest(f"draft:{draft_id}|version:{version}|hash:{assessment_hash}", "fa")


__all__ = [
    "VOLATILE_FACTUAL_FIELDS",
    "build_assessment_id",
    "build_claim_id",
    "build_conflict_id",
    "build_evidence_id",
    "build_link_id",
    "canonical_json",
    "stable_hash",
    "strip_volatile",
    "to_plain",
]
