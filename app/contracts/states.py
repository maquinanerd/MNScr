"""Ingestion state vocabulary.

Separate from ``app.editorial.states``: those describe the *output* artifact,
these describe what happened to an *input* event on its way in. The two meet at
DRAFT_GENERATED / DRAFT_FAILED, which are deliberately spelled the same.
"""

from __future__ import annotations

from typing import Final, FrozenSet

from app.editorial.states import (
    DRAFT_FAILED,
    DRAFT_GENERATED,
    FORBIDDEN_DRAFT_STATES,
    ForbiddenStateError,
)

# --- Validation outcome (what the contract layer decided) ------------------

RECEIVED: Final[str] = "RECEIVED"
VALIDATED: Final[str] = "VALIDATED"
INVALID: Final[str] = "INVALID"
DUPLICATE_DELIVERY: Final[str] = "DUPLICATE_DELIVERY"
REVISION_HASH_CONFLICT: Final[str] = "REVISION_HASH_CONFLICT"
STALE_REVISION: Final[str] = "STALE_REVISION"

VALIDATION_STATES: Final[FrozenSet[str]] = frozenset(
    {RECEIVED, VALIDATED, INVALID, DUPLICATE_DELIVERY, REVISION_HASH_CONFLICT, STALE_REVISION}
)

#: Only a VALIDATED event may ever reach the pipeline.
ACCEPTED_VALIDATION_STATES: Final[FrozenSet[str]] = frozenset({VALIDATED})

# --- Processing progress (what the pipeline did with it) -------------------

QUEUED: Final[str] = "QUEUED"
PROCESSING: Final[str] = "PROCESSING"

REPLAY_REQUESTED: Final[str] = "REPLAY_REQUESTED"
REPLAY_PROCESSING: Final[str] = "REPLAY_PROCESSING"
REPLAY_COMPLETED: Final[str] = "REPLAY_COMPLETED"
REPLAY_FAILED: Final[str] = "REPLAY_FAILED"

PROCESSING_STATES: Final[FrozenSet[str]] = frozenset(
    {
        QUEUED,
        PROCESSING,
        DRAFT_GENERATED,
        DRAFT_FAILED,
        REPLAY_REQUESTED,
        REPLAY_PROCESSING,
        REPLAY_COMPLETED,
        REPLAY_FAILED,
    }
)

REPLAY_STATES: Final[FrozenSet[str]] = frozenset(
    {REPLAY_REQUESTED, REPLAY_PROCESSING, REPLAY_COMPLETED, REPLAY_FAILED}
)

TERMINAL_PROCESSING_STATES: Final[FrozenSet[str]] = frozenset(
    {DRAFT_GENERATED, DRAFT_FAILED, REPLAY_COMPLETED, REPLAY_FAILED}
)

#: Legacy states that may still be *read* from an existing database. They are
#: never written by MNScr and never mean the content was published by us.
HISTORICAL_READ_ONLY_STATES: Final[FrozenSet[str]] = frozenset(
    {"PUBLISHED", "REWRITTEN", "FAILED", "FAILED_PERMANENT", "SUPERSEDED", "SKIPPED", "NEW"}
)


def validate_ingestion_state(state: str, allowed: FrozenSet[str]) -> str:
    """Reject the publication vocabulary wherever an ingestion state is set."""
    normalized = (state or "").strip()
    if normalized in FORBIDDEN_DRAFT_STATES:
        raise ForbiddenStateError(
            f"'{normalized}' e um estado de publicacao e nao pode ser atribuido pelo MNScr."
        )
    if normalized not in allowed:
        raise ValueError(f"Estado de ingestao desconhecido: '{normalized}'")
    return normalized


def validate_validation_status(status: str) -> str:
    return validate_ingestion_state(status, VALIDATION_STATES)


def validate_processing_status(status: str) -> str:
    return validate_ingestion_state(status, PROCESSING_STATES)


__all__ = [
    "ACCEPTED_VALIDATION_STATES",
    "DRAFT_FAILED",
    "DRAFT_GENERATED",
    "DUPLICATE_DELIVERY",
    "HISTORICAL_READ_ONLY_STATES",
    "INVALID",
    "PROCESSING",
    "PROCESSING_STATES",
    "QUEUED",
    "RECEIVED",
    "REPLAY_COMPLETED",
    "REPLAY_FAILED",
    "REPLAY_PROCESSING",
    "REPLAY_REQUESTED",
    "REPLAY_STATES",
    "REVISION_HASH_CONFLICT",
    "STALE_REVISION",
    "TERMINAL_PROCESSING_STATES",
    "VALIDATED",
    "VALIDATION_STATES",
    "validate_ingestion_state",
    "validate_processing_status",
    "validate_validation_status",
]
