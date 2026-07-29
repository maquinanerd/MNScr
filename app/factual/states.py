"""Vocabulary for the factual layer.

Every value here is a stable contract: the pipeline, the gate, the database and
the tests all match on these strings. Note what the words mean, because the
distinctions carry the whole design:

* ``UNSUPPORTED`` means *no source we received sustains this*. It does **not**
  mean the claim is false — absence of evidence is not evidence of absence.
* ``MENTIONS`` means a source talks about the same subject without confirming
  the claim. It is deliberately not support.
* ``UNRESOLVED`` is the only resolution status. MNScr never picks a winner
  between contradicting sources.
"""

from __future__ import annotations

from typing import Final, FrozenSet

# --- Claim types -----------------------------------------------------------

CLAIM_EVENT: Final[str] = "EVENT"
CLAIM_DATE: Final[str] = "DATE"
CLAIM_NUMBER: Final[str] = "NUMBER"
CLAIM_QUOTE: Final[str] = "QUOTE"
CLAIM_ATTRIBUTION: Final[str] = "ATTRIBUTION"
CLAIM_IDENTITY: Final[str] = "IDENTITY"
CLAIM_RELATIONSHIP: Final[str] = "RELATIONSHIP"
CLAIM_RELEASE: Final[str] = "RELEASE"
CLAIM_AVAILABILITY: Final[str] = "AVAILABILITY"
CLAIM_STATUS: Final[str] = "STATUS"
CLAIM_DESCRIPTION: Final[str] = "DESCRIPTION"
CLAIM_OTHER: Final[str] = "OTHER"

CLAIM_TYPES: Final[FrozenSet[str]] = frozenset(
    {
        CLAIM_EVENT, CLAIM_DATE, CLAIM_NUMBER, CLAIM_QUOTE, CLAIM_ATTRIBUTION,
        CLAIM_IDENTITY, CLAIM_RELATIONSHIP, CLAIM_RELEASE, CLAIM_AVAILABILITY,
        CLAIM_STATUS, CLAIM_DESCRIPTION, CLAIM_OTHER,
    }
)

#: Types that carry a checkable value. A DESCRIPTION is prose; a DATE is a fact.
FACTUAL_CLAIM_TYPES: Final[FrozenSet[str]] = frozenset(
    {
        CLAIM_EVENT, CLAIM_DATE, CLAIM_NUMBER, CLAIM_QUOTE, CLAIM_ATTRIBUTION,
        CLAIM_IDENTITY, CLAIM_RELATIONSHIP, CLAIM_RELEASE, CLAIM_AVAILABILITY,
        CLAIM_STATUS,
    }
)

# --- Claim status ----------------------------------------------------------

SUPPORTED: Final[str] = "SUPPORTED"
PARTIALLY_SUPPORTED: Final[str] = "PARTIALLY_SUPPORTED"
UNSUPPORTED: Final[str] = "UNSUPPORTED"
CONFLICTING: Final[str] = "CONFLICTING"
UNVERIFIED: Final[str] = "UNVERIFIED"

CLAIM_STATUSES: Final[FrozenSet[str]] = frozenset(
    {SUPPORTED, PARTIALLY_SUPPORTED, UNSUPPORTED, CONFLICTING, UNVERIFIED}
)

#: Statuses that always deserve a human's attention.
STATUSES_REQUIRING_REVIEW: Final[FrozenSet[str]] = frozenset(
    {UNSUPPORTED, CONFLICTING, UNVERIFIED}
)

# --- Evidence relation -----------------------------------------------------

SUPPORTS: Final[str] = "SUPPORTS"
PARTIALLY_SUPPORTS: Final[str] = "PARTIALLY_SUPPORTS"
CONTRADICTS: Final[str] = "CONTRADICTS"
MENTIONS: Final[str] = "MENTIONS"

EVIDENCE_RELATIONS: Final[FrozenSet[str]] = frozenset(
    {SUPPORTS, PARTIALLY_SUPPORTS, CONTRADICTS, MENTIONS}
)

#: MENTIONS is excluded on purpose: talking about the same subject is not
#: confirming the claim, and treating it as support is how false confidence
#: gets manufactured.
SUPPORTING_RELATIONS: Final[FrozenSet[str]] = frozenset({SUPPORTS, PARTIALLY_SUPPORTS})

# --- Evidence strength -----------------------------------------------------

PRIMARY: Final[str] = "PRIMARY"
SECONDARY: Final[str] = "SECONDARY"
WEAK: Final[str] = "WEAK"
STRENGTH_UNKNOWN: Final[str] = "UNKNOWN"

EVIDENCE_STRENGTHS: Final[FrozenSet[str]] = frozenset(
    {PRIMARY, SECONDARY, WEAK, STRENGTH_UNKNOWN}
)

# --- Conflict types --------------------------------------------------------

VALUE_MISMATCH: Final[str] = "VALUE_MISMATCH"
DATE_MISMATCH: Final[str] = "DATE_MISMATCH"
NUMBER_MISMATCH: Final[str] = "NUMBER_MISMATCH"
IDENTITY_MISMATCH: Final[str] = "IDENTITY_MISMATCH"
ATTRIBUTION_MISMATCH: Final[str] = "ATTRIBUTION_MISMATCH"
STATUS_MISMATCH: Final[str] = "STATUS_MISMATCH"
QUOTE_MISMATCH: Final[str] = "QUOTE_MISMATCH"
SOURCE_DISAGREEMENT: Final[str] = "SOURCE_DISAGREEMENT"

CONFLICT_TYPES: Final[FrozenSet[str]] = frozenset(
    {
        VALUE_MISMATCH, DATE_MISMATCH, NUMBER_MISMATCH, IDENTITY_MISMATCH,
        ATTRIBUTION_MISMATCH, STATUS_MISMATCH, QUOTE_MISMATCH, SOURCE_DISAGREEMENT,
    }
)

#: Conflicts on these types are material by default: they change what the
#: reader would understand as the fact.
CRITICAL_CONFLICT_TYPES: Final[FrozenSet[str]] = frozenset(
    {
        DATE_MISMATCH, NUMBER_MISMATCH, IDENTITY_MISMATCH,
        ATTRIBUTION_MISMATCH, STATUS_MISMATCH,
    }
)

# --- Resolution ------------------------------------------------------------

#: The only value MS-4 ever writes. Automatic resolution does not exist: MNScr
#: does not pick a winner, does not count votes and does not use source
#: reputation as a tiebreaker.
UNRESOLVED: Final[str] = "UNRESOLVED"

RESOLUTION_STATUSES: Final[FrozenSet[str]] = frozenset({UNRESOLVED})

# --- Draft locations -------------------------------------------------------

LOCATION_TITLE: Final[str] = "title"
LOCATION_SUBTITLE: Final[str] = "subtitle"
LOCATION_SUMMARY: Final[str] = "summary"
LOCATION_META: Final[str] = "meta_description"
LOCATION_BODY_PREFIX: Final[str] = "body:p:"

#: Where an unsupported claim is loud enough to block. A wrong headline is not
#: the same problem as a wrong aside in the tenth paragraph.
CRITICAL_LOCATIONS: Final[FrozenSet[str]] = frozenset(
    {LOCATION_TITLE, LOCATION_SUBTITLE, LOCATION_META, LOCATION_SUMMARY, "body:p:1"}
)

# --- Assessment modes ------------------------------------------------------

MODE_DETERMINISTIC: Final[str] = "deterministic"
MODE_HYBRID: Final[str] = "hybrid"
ASSESSMENT_MODES: Final[FrozenSet[str]] = frozenset({MODE_DETERMINISTIC, MODE_HYBRID})

ASSESSMENT_VERSION_V1: Final[str] = "mnscr-factual-assessment-v1"
ASSESSMENT_VERSIONS: Final[FrozenSet[str]] = frozenset({ASSESSMENT_VERSION_V1})

CLAIM_PROMPT_VERSION_V1: Final[str] = "factual-claim-extraction-v1"


def _validate(value: str, allowed: FrozenSet[str], label: str) -> str:
    normalized = (value or "").strip().upper()
    if normalized not in allowed:
        raise ValueError(f"{label} desconhecido: '{value}'")
    return normalized


def validate_claim_type(value: str) -> str:
    return _validate(value, CLAIM_TYPES, "ClaimType")


def validate_claim_status(value: str) -> str:
    return _validate(value, CLAIM_STATUSES, "ClaimStatus")


def validate_relation(value: str) -> str:
    return _validate(value, EVIDENCE_RELATIONS, "EvidenceRelation")


def validate_strength(value: str) -> str:
    return _validate(value, EVIDENCE_STRENGTHS, "EvidenceStrength")


def validate_conflict_type(value: str) -> str:
    return _validate(value, CONFLICT_TYPES, "ConflictType")


def validate_resolution_status(value: str) -> str:
    return _validate(value, RESOLUTION_STATUSES, "ResolutionStatus")


def validate_mode(value: str) -> str:
    normalized = (value or "").strip().lower()
    if normalized not in ASSESSMENT_MODES:
        raise ValueError(f"Modo de avaliacao factual desconhecido: '{value}'")
    return normalized


def is_critical_location(location: str) -> bool:
    return (location or "").strip() in CRITICAL_LOCATIONS


__all__ = [
    "ASSESSMENT_MODES",
    "ASSESSMENT_VERSIONS",
    "ASSESSMENT_VERSION_V1",
    "ATTRIBUTION_MISMATCH",
    "CLAIM_ATTRIBUTION",
    "CLAIM_AVAILABILITY",
    "CLAIM_DATE",
    "CLAIM_DESCRIPTION",
    "CLAIM_EVENT",
    "CLAIM_IDENTITY",
    "CLAIM_NUMBER",
    "CLAIM_OTHER",
    "CLAIM_PROMPT_VERSION_V1",
    "CLAIM_QUOTE",
    "CLAIM_RELATIONSHIP",
    "CLAIM_RELEASE",
    "CLAIM_STATUS",
    "CLAIM_STATUSES",
    "CLAIM_TYPES",
    "CONFLICTING",
    "CONFLICT_TYPES",
    "CONTRADICTS",
    "CRITICAL_CONFLICT_TYPES",
    "CRITICAL_LOCATIONS",
    "DATE_MISMATCH",
    "EVIDENCE_RELATIONS",
    "EVIDENCE_STRENGTHS",
    "FACTUAL_CLAIM_TYPES",
    "IDENTITY_MISMATCH",
    "LOCATION_BODY_PREFIX",
    "LOCATION_META",
    "LOCATION_SUBTITLE",
    "LOCATION_SUMMARY",
    "LOCATION_TITLE",
    "MENTIONS",
    "MODE_DETERMINISTIC",
    "MODE_HYBRID",
    "NUMBER_MISMATCH",
    "PARTIALLY_SUPPORTED",
    "PARTIALLY_SUPPORTS",
    "PRIMARY",
    "QUOTE_MISMATCH",
    "RESOLUTION_STATUSES",
    "SECONDARY",
    "SOURCE_DISAGREEMENT",
    "STATUSES_REQUIRING_REVIEW",
    "STATUS_MISMATCH",
    "STRENGTH_UNKNOWN",
    "SUPPORTED",
    "SUPPORTING_RELATIONS",
    "SUPPORTS",
    "UNRESOLVED",
    "UNSUPPORTED",
    "UNVERIFIED",
    "VALUE_MISMATCH",
    "WEAK",
    "is_critical_location",
    "validate_claim_status",
    "validate_claim_type",
    "validate_conflict_type",
    "validate_mode",
    "validate_relation",
    "validate_resolution_status",
    "validate_strength",
]
