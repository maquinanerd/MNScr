"""Operational states for the MNScr editorial draft pipeline.

MNScr produces drafts. It never approves and never publishes. Any state that
would imply editorial approval or public visibility is explicitly forbidden and
rejected at construction time.
"""

from __future__ import annotations

from typing import Final, FrozenSet

# --- Draft lifecycle -------------------------------------------------------

DRAFT_GENERATED: Final[str] = "DRAFT_GENERATED"
DRAFT_FAILED: Final[str] = "DRAFT_FAILED"

ALLOWED_DRAFT_STATES: Final[FrozenSet[str]] = frozenset(
    {
        DRAFT_GENERATED,
        DRAFT_FAILED,
    }
)

# States that MNScr must never assign. Human editorial authority lives in the
# CMS, not here.
FORBIDDEN_DRAFT_STATES: Final[FrozenSet[str]] = frozenset(
    {
        "APPROVED",
        "PUBLISHED",
        "PUBLICATION_READY",
        "SCHEDULED",
        "LIVE",
    }
)

# --- Submission lifecycle --------------------------------------------------

SUBMISSION_WRITTEN: Final[str] = "WRITTEN"
SUBMISSION_UNCHANGED: Final[str] = "UNCHANGED"
SUBMISSION_CONFLICT: Final[str] = "CONFLICT"
SUBMISSION_ERROR: Final[str] = "ERROR"
SUBMISSION_DISABLED: Final[str] = "DISABLED"

ALLOWED_SUBMISSION_STATES: Final[FrozenSet[str]] = frozenset(
    {
        SUBMISSION_WRITTEN,
        SUBMISSION_UNCHANGED,
        SUBMISSION_CONFLICT,
        SUBMISSION_ERROR,
        SUBMISSION_DISABLED,
    }
)

# --- Evidence status -------------------------------------------------------
# Controlled vocabulary only. MS-1 does not define a conflict-resolution
# policy; it only records what was observed.

EVIDENCE_OBSERVED: Final[str] = "observed"
EVIDENCE_UNSUPPORTED: Final[str] = "unsupported"
EVIDENCE_CONFLICTING: Final[str] = "conflicting"
EVIDENCE_UNVERIFIED: Final[str] = "unverified"

ALLOWED_EVIDENCE_STATUSES: Final[FrozenSet[str]] = frozenset(
    {
        EVIDENCE_OBSERVED,
        EVIDENCE_UNSUPPORTED,
        EVIDENCE_CONFLICTING,
        EVIDENCE_UNVERIFIED,
    }
)

# --- Media candidate status ------------------------------------------------
# MNScr never downloads, never re-hosts and never asserts rights it did not
# read from the source. Unknown stays unknown.

MEDIA_UNVERIFIED: Final[str] = "unverified"
MEDIA_LICENSED: Final[str] = "licensed"
MEDIA_REJECTED: Final[str] = "rejected"

ALLOWED_MEDIA_STATUSES: Final[FrozenSet[str]] = frozenset(
    {
        MEDIA_UNVERIFIED,
        MEDIA_LICENSED,
        MEDIA_REJECTED,
    }
)


class ForbiddenStateError(ValueError):
    """Raised when a forbidden (approval/publication) state is requested."""


def validate_draft_status(status: str) -> str:
    """Return the status when allowed; raise otherwise."""
    normalized = (status or "").strip()
    if normalized in FORBIDDEN_DRAFT_STATES:
        raise ForbiddenStateError(
            f"MNScr must not assign the state {normalized!r}: "
            "editorial approval and publication are not MNScr responsibilities."
        )
    if normalized not in ALLOWED_DRAFT_STATES:
        raise ValueError(
            f"Unknown draft status {normalized!r}. "
            f"Allowed: {sorted(ALLOWED_DRAFT_STATES)}"
        )
    return normalized
