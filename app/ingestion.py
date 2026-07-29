"""Ingestion service: from a raw feed item to a persisted, decided event.

This is the seam the MS-2 spec asks for. Transport (``app.feeds``) produces
mappings; this module selects the contract, validates, applies the revision
rules, persists and decides whether the cursor may move. The pipeline never
sees an untyped dict again after this point.

It makes no network calls of its own.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional

from .contracts import errors as E
from .contracts import states as S
from .contracts.revisions import (
    ACCEPT_NEW_EVENT,
    ACCEPT_NEW_REVISION,
    CONFLICT,
    DUPLICATE,
    STALE,
    RevisionDecision,
    decide_revision,
)
from .contracts.rssprime_event_v1 import RssPrimeEventV1
from .contracts.validation import ValidationResult, validate_payload
from .event_store import EventStore

logger = logging.getLogger(__name__)


@dataclass
class IngestionOutcome:
    """What happened to one incoming item."""

    accepted: bool
    validation_status: str
    event: Optional[RssPrimeEventV1] = None
    decision: Optional[str] = None
    errors: List[E.ContractIssue] = field(default_factory=list)
    warnings: List[E.ContractIssue] = field(default_factory=list)
    cursor_advanced: bool = False
    persisted_id: Optional[int] = None

    @property
    def error_codes(self) -> List[str]:
        return E.codes(self.errors)

    @property
    def warning_codes(self) -> List[str]:
        return E.codes(self.warnings)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "accepted": self.accepted,
            "validation_status": self.validation_status,
            "decision": self.decision,
            "event_key": self.event.event_key if self.event else None,
            "revision": self.event.revision if self.event else None,
            "payload_hash": self.event.payload_hash if self.event else None,
            "cursor_advanced": self.cursor_advanced,
            "errors": [issue.to_dict() for issue in self.errors],
            "warnings": [issue.to_dict() for issue in self.warnings],
        }


def payload_size_bytes(payload: Mapping[str, Any]) -> int:
    """Serialized size, used to enforce the ingest ceiling before persisting."""
    try:
        return len(json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8"))
    except (TypeError, ValueError):
        return 0


class IngestionService:
    """Validates, decides and persists incoming RSS Prime events."""

    def __init__(
        self,
        event_store: EventStore,
        *,
        accept_legacy: Optional[bool] = None,
        required_contract: Optional[str] = None,
        store_raw_payload: Optional[bool] = None,
        max_payload_bytes: Optional[int] = None,
    ):
        from . import config

        self.store = event_store
        self.accept_legacy = (
            config.ACCEPT_LEGACY_FEED if accept_legacy is None else bool(accept_legacy)
        )
        self.required_contract = (
            config.REQUIRED_INPUT_CONTRACT if required_contract is None else required_contract
        ) or None
        self.store_raw_payload = (
            config.STORE_RAW_EVENT_PAYLOAD if store_raw_payload is None else bool(store_raw_payload)
        )
        self.max_payload_bytes = int(
            config.MAX_EVENT_PAYLOAD_BYTES if max_payload_bytes is None else max_payload_bytes
        )

    # --- Main entry point --------------------------------------------------

    def ingest(self, payload: Mapping[str, Any], *, source_id: str = "rssprime") -> IngestionOutcome:
        """Take one raw item all the way to a persisted decision."""
        size = payload_size_bytes(payload)
        if self.max_payload_bytes and size > self.max_payload_bytes:
            issue = E.error(
                E.PAYLOAD_TOO_LARGE,
                f"payload de {size} bytes excede o limite de {self.max_payload_bytes}",
                field_name="payload",
                size_bytes=size,
                limit_bytes=self.max_payload_bytes,
            )
            logger.warning(
                "[CONTRACT_VALIDATION] source_id=%s status=%s error=%s size=%s",
                source_id, S.INVALID, E.PAYLOAD_TOO_LARGE, size,
            )
            return IngestionOutcome(
                accepted=False, validation_status=S.INVALID, errors=[issue]
            )

        result: ValidationResult = validate_payload(
            payload,
            accept_legacy=self.accept_legacy,
            required_contract=self.required_contract,
        )

        if result.event is None or not result.ok:
            return self._reject(result, payload, source_id)

        return self._decide_and_persist(result, payload, source_id)

    # --- Internals ---------------------------------------------------------

    def _reject(
        self,
        result: ValidationResult,
        payload: Mapping[str, Any],
        source_id: str,
    ) -> IngestionOutcome:
        """Persist the rejection when we know what event it belongs to.

        An invalid payload never advances the cursor — that is the whole point
        of validating before moving it.
        """
        persisted_id = None
        if result.event is not None and result.event.event_key:
            persisted_id = self.store.record_event(
                result.event,
                validation_status=S.INVALID,
                validation_errors=[i.to_dict() for i in result.errors],
                warnings=[i.to_dict() for i in result.warnings],
                raw_payload=dict(payload),
                processing_status=S.RECEIVED,
                store_raw_payload=self.store_raw_payload,
            )
        logger.warning(
            "[CURSOR_NOT_ADVANCED] source_id=%s reason=invalid_payload errors=%s",
            source_id,
            ",".join(result.error_codes) or "-",
        )
        return IngestionOutcome(
            accepted=False,
            validation_status=S.INVALID,
            event=result.event,
            errors=result.errors,
            warnings=result.warnings,
            persisted_id=persisted_id,
        )

    def _decide_and_persist(
        self,
        result: ValidationResult,
        payload: Mapping[str, Any],
        source_id: str,
    ) -> IngestionOutcome:
        event = result.event
        assert event is not None  # guarded by the caller

        stored = self.store.get_latest_revision(event.event_key)
        same_revision_hash = (
            self.store.get_revision_hash(event.event_key, event.revision) if stored else None
        )

        decision: RevisionDecision = decide_revision(
            event_key=event.event_key,
            incoming_revision=event.revision,
            incoming_payload_hash=event.payload_hash,
            stored=stored,
            stored_same_revision_hash=same_revision_hash,
        )

        warnings = list(result.warnings) + [i for i in decision.issues if not i.is_blocking]
        errors = list(result.errors) + [i for i in decision.issues if i.is_blocking]

        if decision.decision == DUPLICATE:
            # Idempotent: the row already exists, nothing is written, nothing is
            # regenerated, and the cursor may still move because this delivery
            # was legitimate — it just carried nothing new.
            cursor_advanced = self.store.advance_cursor(
                source_id=source_id,
                contract_version=event.contract_version,
                cursor_value=event.cursor,
                last_event_key=event.event_key,
                last_revision=event.revision,
            )
            return IngestionOutcome(
                accepted=False,
                validation_status=S.DUPLICATE_DELIVERY,
                event=event,
                decision=DUPLICATE,
                warnings=warnings,
                cursor_advanced=cursor_advanced,
            )

        if decision.decision in (CONFLICT, STALE):
            status = (
                S.REVISION_HASH_CONFLICT if decision.decision == CONFLICT else S.STALE_REVISION
            )
            # Neither a conflict nor a stale revision may move the cursor: doing
            # so would skip past a delivery we refused to process.
            logger.warning(
                "[CURSOR_NOT_ADVANCED] source_id=%s event_key=%s reason=%s",
                source_id, event.event_key, status,
            )
            persisted_id = self.store.record_event(
                event,
                validation_status=status,
                validation_errors=[i.to_dict() for i in errors],
                warnings=[i.to_dict() for i in warnings],
                raw_payload=dict(payload),
                processing_status=S.RECEIVED,
                store_raw_payload=self.store_raw_payload,
            )
            return IngestionOutcome(
                accepted=False,
                validation_status=status,
                event=event,
                decision=decision.decision,
                errors=errors,
                warnings=warnings,
                persisted_id=persisted_id,
            )

        # ACCEPT_NEW_EVENT or ACCEPT_NEW_REVISION.
        persisted_id = self.store.persist_event_and_advance_cursor(
            event,
            source_id=source_id,
            validation_status=S.VALIDATED,
            validation_errors=[],
            warnings=[i.to_dict() for i in warnings],
            raw_payload=dict(payload),
            store_raw_payload=self.store_raw_payload,
        )
        if persisted_id is None:
            # Lost a race with a concurrent worker; the UNIQUE constraint held.
            return IngestionOutcome(
                accepted=False,
                validation_status=S.DUPLICATE_DELIVERY,
                event=event,
                decision=DUPLICATE,
                warnings=warnings,
            )

        if not event.cursor:
            self.store.mark_cursor_unavailable(source_id, event.contract_version)

        return IngestionOutcome(
            accepted=True,
            validation_status=S.VALIDATED,
            event=event,
            decision=decision.decision,
            warnings=warnings,
            cursor_advanced=bool(event.cursor),
            persisted_id=persisted_id,
        )


__all__ = [
    "ACCEPT_NEW_EVENT",
    "ACCEPT_NEW_REVISION",
    "IngestionOutcome",
    "IngestionService",
    "payload_size_bytes",
]
