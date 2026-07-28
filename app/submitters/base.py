"""Submitter contract for editorial drafts.

A submitter is the only component allowed to move a draft out of MNScr. It
never publishes: it hands a draft over to a destination that a human editor
controls.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Protocol, runtime_checkable

from app.editorial.models import EditorialDraft
from app.editorial.states import ALLOWED_SUBMISSION_STATES


class SubmitterError(RuntimeError):
    """Base error for submitter failures."""


class SubmitterNotEnabledError(SubmitterError):
    """Raised when a submitter is selected but its contract is not enabled."""


@dataclass
class SubmissionResult:
    """Outcome of handing a draft to a destination."""

    success: bool
    destination: str
    status: str
    submission_id: Optional[str] = None
    artifact_path: Optional[str] = None
    error: Optional[str] = None
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status not in ALLOWED_SUBMISSION_STATES:
            raise ValueError(
                f"Unknown submission status {self.status!r}. "
                f"Allowed: {sorted(ALLOWED_SUBMISSION_STATES)}"
            )
        self.destination = str(self.destination or "").strip() or "unknown"


@runtime_checkable
class DraftSubmitter(Protocol):
    """Anything that can accept an :class:`EditorialDraft`."""

    destination: str

    def submit(self, draft: EditorialDraft) -> SubmissionResult:
        """Hand the draft to the destination and report what happened."""
        ...
