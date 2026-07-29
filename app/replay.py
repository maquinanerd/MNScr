"""Local, controlled replay of an already-received event.

Replay reads only what SQLite already holds. It never contacts RSS Prime, never
re-fetches a feed, never mutates the stored payload, never invents a new
``event_key`` and never advances the cursor. What it does produce is a new
*operational attempt*, recorded in the attempt history, so an operator can tell
"we tried again" apart from "a new revision arrived".
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .contracts import states as S
from .contracts.rssprime_event_v1 import RssPrimeEventV1
from .event_store import EventStore, StoredEvent

logger = logging.getLogger(__name__)

ATTEMPT_KIND_REPLAY = "replay"
ATTEMPT_KIND_INGEST = "ingest"


class ReplayError(RuntimeError):
    """Replay could not start: unknown event, unknown revision, no payload."""


@dataclass
class ReplayResult:
    """Outcome of one replay attempt."""

    event_key: str
    revision: int
    status: str
    attempt_id: Optional[int] = None
    draft_id: Optional[str] = None
    draft_output_hash: Optional[str] = None
    error: Optional[str] = None
    cursor_advanced: bool = False
    warnings: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == S.REPLAY_COMPLETED

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_key": self.event_key,
            "revision": self.revision,
            "status": self.status,
            "attempt_id": self.attempt_id,
            "draft_id": self.draft_id,
            "draft_output_hash": self.draft_output_hash,
            "cursor_advanced": self.cursor_advanced,
            "error": self.error,
        }


@dataclass
class RevisionSummary:
    """One row of ``--list-event-revisions``.

    The full payload is deliberately absent: listing revisions is an operational
    question, and dumping stored source content by default would be noise at
    best and a leak at worst.
    """

    revision: int
    payload_hash: str
    validation_status: str
    processing_status: str
    draft_id: Optional[str]
    received_at: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "revision": self.revision,
            "payload_hash": self.payload_hash,
            "validation_status": self.validation_status,
            "processing_status": self.processing_status,
            "draft_id": self.draft_id,
            "received_at": self.received_at,
        }


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ReplayService:
    """Rebuilds processing for a stored event revision."""

    def __init__(self, event_store: EventStore, processor: Any = None):
        """``processor`` is a callable taking ``RssPrimeEventV1`` and returning
        an object exposing ``draft_id`` / ``output_hash``. Injected so replay is
        testable without running the whole pipeline."""
        self.store = event_store
        self.processor = processor

    # --- Listing -----------------------------------------------------------

    def list_revisions(self, event_key: str) -> List[RevisionSummary]:
        stored = self.store.list_event_revisions(event_key)
        if not stored:
            raise ReplayError(f"Nenhuma revisao registrada para o evento '{event_key}'.")
        return [
            RevisionSummary(
                revision=item.revision,
                payload_hash=item.payload_hash,
                validation_status=item.validation_status,
                processing_status=item.processing_status,
                draft_id=item.draft_id,
                received_at=item.received_at,
            )
            for item in stored
        ]

    # --- Replay ------------------------------------------------------------

    def _load(self, event_key: str, revision: Optional[int]) -> StoredEvent:
        stored = self.store.get_event(event_key, revision)
        if stored is None:
            if revision is None:
                raise ReplayError(f"Evento '{event_key}' nao encontrado no banco local.")
            raise ReplayError(
                f"Revisao {revision} do evento '{event_key}' nao encontrada no banco local."
            )
        if not stored.normalized_payload:
            raise ReplayError(
                f"Evento '{event_key}' revisao {stored.revision} nao tem payload persistido; "
                "replay impossivel."
            )
        return stored

    def replay(self, event_key: str, revision: Optional[int] = None) -> ReplayResult:
        """Reprocess one stored revision.

        The input revision is never changed: a replay of revision 2 stays
        revision 2. Only the operational attempt is new.
        """
        stored = self._load(event_key, revision)
        event: RssPrimeEventV1 = stored.to_event()
        payload_hash_before = stored.payload_hash

        started_at = _utc_now_iso()
        logger.info(
            "[REPLAY_STARTED] event_key=%s revision=%s payload_hash=%s contract=%s",
            event.event_key, event.revision, event.payload_hash[:12], event.contract_version,
        )
        self.store.set_processing_status(event.event_key, event.revision, S.REPLAY_PROCESSING)

        if self.processor is None:
            error = "Nenhum processador configurado para replay."
            attempt_id = self.store.record_attempt(
                event.event_key, event.revision,
                attempt_kind=ATTEMPT_KIND_REPLAY, status=S.REPLAY_FAILED,
                started_at=started_at, finished_at=_utc_now_iso(), error=error,
            )
            self.store.set_processing_status(event.event_key, event.revision, S.REPLAY_FAILED)
            logger.error("[REPLAY_FAILED] event_key=%s revision=%s error=%s",
                         event.event_key, event.revision, error)
            return ReplayResult(
                event_key=event.event_key, revision=event.revision,
                status=S.REPLAY_FAILED, attempt_id=attempt_id, error=error,
            )

        try:
            outcome = self.processor(event)
        except Exception as exc:  # noqa: BLE001 - the attempt record is the report
            attempt_id = self.store.record_attempt(
                event.event_key, event.revision,
                attempt_kind=ATTEMPT_KIND_REPLAY, status=S.REPLAY_FAILED,
                started_at=started_at, finished_at=_utc_now_iso(), error=str(exc),
            )
            self.store.set_processing_status(event.event_key, event.revision, S.REPLAY_FAILED)
            logger.error("[REPLAY_FAILED] event_key=%s revision=%s error=%s",
                         event.event_key, event.revision, exc)
            return ReplayResult(
                event_key=event.event_key, revision=event.revision,
                status=S.REPLAY_FAILED, attempt_id=attempt_id, error=str(exc),
            )

        draft_id = getattr(outcome, "draft_id", None) or (
            outcome.get("draft_id") if isinstance(outcome, dict) else None
        )
        output_hash = getattr(outcome, "output_hash", None) or (
            outcome.get("output_hash") if isinstance(outcome, dict) else None
        )

        attempt_id = self.store.record_attempt(
            event.event_key, event.revision,
            attempt_kind=ATTEMPT_KIND_REPLAY, status=S.REPLAY_COMPLETED,
            started_at=started_at, finished_at=_utc_now_iso(),
            draft_id=draft_id, draft_output_hash=output_hash,
        )
        self.store.set_processing_status(
            event.event_key, event.revision, S.REPLAY_COMPLETED,
            draft_id=draft_id, draft_output_hash=output_hash,
        )

        # The stored payload must come out of a replay byte-identical.
        after = self.store.get_event(event.event_key, event.revision)
        if after and after.payload_hash != payload_hash_before:
            logger.error(
                "[REPLAY_FAILED] event_key=%s revision=%s payload alterado durante replay",
                event.event_key, event.revision,
            )
            raise ReplayError("O replay alterou o payload persistido; isso e um bug.")

        logger.info(
            "[REPLAY_COMPLETED] event_key=%s revision=%s draft_id=%s cursor_advanced=false",
            event.event_key, event.revision, draft_id or "-",
        )
        return ReplayResult(
            event_key=event.event_key,
            revision=event.revision,
            status=S.REPLAY_COMPLETED,
            attempt_id=attempt_id,
            draft_id=draft_id,
            draft_output_hash=output_hash,
            cursor_advanced=False,  # replay nunca avanca cursor
        )


def format_revision_table(rows: List[RevisionSummary]) -> str:
    """Plain-text table for the CLI."""
    header = f"{'REV':>4}  {'PAYLOAD_HASH':<16}  {'VALIDATION':<22}  {'PROCESSING':<18}  {'DRAFT_ID':<40}  RECEIVED_AT"
    lines = [header, "-" * len(header)]
    for row in rows:
        lines.append(
            f"{row.revision:>4}  {row.payload_hash[:16]:<16}  {row.validation_status:<22}  "
            f"{row.processing_status:<18}  {(row.draft_id or '-'):<40}  {row.received_at}"
        )
    return "\n".join(lines)


__all__ = [
    "ATTEMPT_KIND_INGEST",
    "ATTEMPT_KIND_REPLAY",
    "ReplayError",
    "ReplayResult",
    "ReplayService",
    "RevisionSummary",
    "format_revision_table",
]
