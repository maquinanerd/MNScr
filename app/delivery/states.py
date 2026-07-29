"""Delivery state machine.

A delivery is the act of handing one validated editorial payload to one external
destination. Its states are deliberately separate from the *draft* states of
MS-1 and the *gate* outcomes of MS-3: a draft can be perfectly generated and
gate-clear while its delivery is still pending, retrying or blocked.

``DELIVERED`` is terminal. Re-running a delivered item returns the stored result
instead of calling the destination again — that is the whole point of the
idempotency key.
"""

from __future__ import annotations

from typing import Dict, Final, FrozenSet

from app.editorial.states import FORBIDDEN_DRAFT_STATES, ForbiddenStateError

# --- States ----------------------------------------------------------------

DELIVERY_PENDING: Final[str] = "DELIVERY_PENDING"
DELIVERY_IN_PROGRESS: Final[str] = "DELIVERY_IN_PROGRESS"
DELIVERED: Final[str] = "DELIVERED"
DELIVERY_FAILED_RETRYABLE: Final[str] = "DELIVERY_FAILED_RETRYABLE"
DELIVERY_FAILED_PERMANENT: Final[str] = "DELIVERY_FAILED_PERMANENT"
DELIVERY_BLOCKED: Final[str] = "DELIVERY_BLOCKED"

#: The destination may or may not have created the draft — the response was
#: lost. Never resolved by guessing: it waits for reconciliation or a human.
DELIVERY_NEEDS_RECONCILIATION: Final[str] = "DELIVERY_NEEDS_RECONCILIATION"

#: The editorial content changed after a previous delivery succeeded. A new,
#: versioned delivery is recorded, but nothing is pushed: overwriting a draft a
#: reviewer may already have opened is not a decision MNScr gets to make.
DELIVERY_REQUIRES_MANUAL_REVIEW: Final[str] = "DELIVERY_REQUIRES_MANUAL_REVIEW"

DELIVERY_STATES: Final[FrozenSet[str]] = frozenset(
    {
        DELIVERY_PENDING,
        DELIVERY_IN_PROGRESS,
        DELIVERED,
        DELIVERY_FAILED_RETRYABLE,
        DELIVERY_FAILED_PERMANENT,
        DELIVERY_BLOCKED,
        DELIVERY_NEEDS_RECONCILIATION,
        DELIVERY_REQUIRES_MANUAL_REVIEW,
    }
)

#: States from which no further attempt is made automatically.
TERMINAL_STATES: Final[FrozenSet[str]] = frozenset(
    {
        DELIVERED,
        DELIVERY_FAILED_PERMANENT,
        DELIVERY_BLOCKED,
        DELIVERY_REQUIRES_MANUAL_REVIEW,
    }
)

#: States a scheduled retry may pick up.
RETRYABLE_STATES: Final[FrozenSet[str]] = frozenset(
    {DELIVERY_PENDING, DELIVERY_FAILED_RETRYABLE}
)

#: Allowed transitions. Anything absent is rejected — a delivery cannot go from
#: DELIVERED back to IN_PROGRESS, and BLOCKED never becomes DELIVERED.
ALLOWED_TRANSITIONS: Final[Dict[str, FrozenSet[str]]] = {
    DELIVERY_PENDING: frozenset(
        {
            DELIVERY_IN_PROGRESS,
            DELIVERY_BLOCKED,
            DELIVERY_FAILED_PERMANENT,
            DELIVERY_REQUIRES_MANUAL_REVIEW,
        }
    ),
    DELIVERY_IN_PROGRESS: frozenset(
        {
            DELIVERED,
            DELIVERY_FAILED_RETRYABLE,
            DELIVERY_FAILED_PERMANENT,
            DELIVERY_NEEDS_RECONCILIATION,
        }
    ),
    DELIVERY_FAILED_RETRYABLE: frozenset(
        {
            DELIVERY_IN_PROGRESS,
            DELIVERY_FAILED_PERMANENT,
            DELIVERY_BLOCKED,
        }
    ),
    DELIVERY_NEEDS_RECONCILIATION: frozenset(
        {
            DELIVERED,
            DELIVERY_IN_PROGRESS,
            DELIVERY_FAILED_PERMANENT,
            DELIVERY_REQUIRES_MANUAL_REVIEW,
        }
    ),
    # Terminal states have no outgoing transitions.
    DELIVERED: frozenset(),
    DELIVERY_FAILED_PERMANENT: frozenset(),
    DELIVERY_BLOCKED: frozenset(),
    DELIVERY_REQUIRES_MANUAL_REVIEW: frozenset(),
}


class InvalidDeliveryTransitionError(RuntimeError):
    """A delivery was asked to move somewhere it cannot go."""

    def __init__(self, current: str, target: str):
        self.current = current
        self.target = target
        allowed = sorted(ALLOWED_TRANSITIONS.get(current, frozenset()))
        super().__init__(
            f"Transicao invalida de '{current}' para '{target}'. "
            f"Permitidas a partir de '{current}': {allowed or 'nenhuma (estado terminal)'}"
        )


def validate_delivery_state(state: str) -> str:
    """Reject unknown states and the publication vocabulary.

    MNScr delivers drafts. ``PUBLISHED`` and friends have no meaning here and
    must never become a delivery state by accident.
    """
    normalized = (state or "").strip().upper()
    if normalized in FORBIDDEN_DRAFT_STATES:
        raise ForbiddenStateError(
            f"'{normalized}' e um estado de publicacao; a entrega so cria drafts."
        )
    if normalized not in DELIVERY_STATES:
        raise ValueError(f"Estado de entrega desconhecido: '{state}'")
    return normalized


def can_transition(current: str, target: str) -> bool:
    return target in ALLOWED_TRANSITIONS.get(current, frozenset())


def assert_transition(current: str, target: str) -> str:
    """Validate a transition, raising if it is not allowed."""
    current = validate_delivery_state(current)
    target = validate_delivery_state(target)
    if not can_transition(current, target):
        raise InvalidDeliveryTransitionError(current, target)
    return target


def is_terminal(state: str) -> bool:
    return validate_delivery_state(state) in TERMINAL_STATES


__all__ = [
    "ALLOWED_TRANSITIONS",
    "DELIVERED",
    "DELIVERY_BLOCKED",
    "DELIVERY_FAILED_PERMANENT",
    "DELIVERY_FAILED_RETRYABLE",
    "DELIVERY_IN_PROGRESS",
    "DELIVERY_NEEDS_RECONCILIATION",
    "DELIVERY_PENDING",
    "DELIVERY_REQUIRES_MANUAL_REVIEW",
    "DELIVERY_STATES",
    "InvalidDeliveryTransitionError",
    "RETRYABLE_STATES",
    "TERMINAL_STATES",
    "assert_transition",
    "can_transition",
    "is_terminal",
    "validate_delivery_state",
]
