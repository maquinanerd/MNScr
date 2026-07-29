"""The destination boundary.

This module defines *what* a destination must do, never *how*. It imports no
HTTP library, no URL, no header and no status code — those live in
``app.delivery.payload_cms``. The editorial pipeline depends on this file and
never on the transport, which is what lets the whole delivery path be tested
without a socket.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import Any, Dict, Optional, Protocol, runtime_checkable

from .contract import EditorialDeliveryPayload
from .errors import DeliveryError

#: Identifies the destination in persistence and logs.
DESTINATION_PAYLOAD_CMS: str = "payload_cms"


@dataclass
class DeliveryResult:
    """What happened when a payload was handed to a destination."""

    success: bool
    destination: str
    status: str
    delivery_id: str
    remote_draft_id: Optional[str] = None
    remote_url: Optional[str] = None
    http_status: Optional[int] = None
    error: Optional[str] = None
    error_class: Optional[str] = None
    retryable: bool = False
    attempts: int = 0
    duration_ms: Optional[int] = None
    idempotent_replay: bool = False
    details: Dict[str, Any] = dc_field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "destination": self.destination,
            "status": self.status,
            "delivery_id": self.delivery_id,
            "remote_draft_id": self.remote_draft_id,
            "remote_url": self.remote_url,
            "http_status": self.http_status,
            "error": self.error,
            "error_class": self.error_class,
            "retryable": self.retryable,
            "attempts": self.attempts,
            "duration_ms": self.duration_ms,
            "idempotent_replay": self.idempotent_replay,
        }

    @classmethod
    def from_error(
        cls,
        error: DeliveryError,
        *,
        destination: str,
        delivery_id: str,
        status: str,
        attempts: int = 0,
        duration_ms: Optional[int] = None,
    ) -> "DeliveryResult":
        return cls(
            success=False,
            destination=destination,
            status=status,
            delivery_id=delivery_id,
            http_status=error.http_status,
            error=str(error),
            error_class=error.error_class,
            retryable=error.retryable,
            attempts=attempts,
            duration_ms=duration_ms,
        )


@dataclass(frozen=True)
class RemoteDraft:
    """What a destination reports after accepting a payload."""

    remote_id: str
    url: Optional[str] = None
    raw: Dict[str, Any] = dc_field(default_factory=dict)


@runtime_checkable
class EditorialDestination(Protocol):
    """Anything that can accept a validated editorial delivery payload.

    Implementations must never publish: they create a draft for human review.
    """

    name: str

    def submit_draft(self, payload: EditorialDeliveryPayload) -> RemoteDraft:
        """Create a remote draft. Raise a :class:`DeliveryError` on failure."""
        ...

    def find_existing_draft(self, delivery_id: str) -> Optional[RemoteDraft]:
        """Look up a draft previously created for ``delivery_id``.

        Used for reconciliation after an ambiguous failure. Returning ``None``
        must mean "not found"; a destination that cannot look up at all should
        raise :class:`NotImplementedError` so the caller records the ambiguity
        instead of assuming nothing was created.
        """
        ...


def build_idempotency_key(
    *, draft_id: str, content_hash: str, destination: str
) -> str:
    """The stable identity of one delivery.

    ``draft_id`` already encodes ``event_key + revision`` (MS-2's
    ``build_draft_id``), so the source event identity is carried without
    duplicating fields. ``content_hash`` is the draft's ``output_hash``, so a
    genuine editorial change produces a different key — and therefore a new,
    versioned delivery rather than a silent overwrite. ``destination`` keeps two
    targets from colliding.
    """
    draft_id = str(draft_id or "").strip()
    content_hash = str(content_hash or "").strip()
    destination = str(destination or "").strip()
    if not draft_id or not content_hash or not destination:
        raise ValueError(
            "idempotency key exige draft_id, content_hash e destination nao vazios"
        )
    seed = f"draft:{draft_id}|content:{content_hash}|dest:{destination}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def build_delivery_id(idempotency_key: str) -> str:
    """A short, stable, human-quotable id derived from the idempotency key."""
    return f"dlv-{idempotency_key[:32]}"


__all__ = [
    "DESTINATION_PAYLOAD_CMS",
    "DeliveryResult",
    "EditorialDestination",
    "RemoteDraft",
    "build_delivery_id",
    "build_idempotency_key",
]
