"""Orchestration of editorial delivery.

The order here is the guarantee:

    check blocking → build contract → validate → persist PENDING
    → attempt (with retry) → persist result

Every refusal happens before a socket is opened. The destination is only
constructed once the payload has passed validation, so an adapter cannot be used
to bypass the gate even by mistake.

Delivery semantics are **at-least-once with best-effort deduplication**. An
`Idempotency-Key` is sent and the local store refuses to create a second logical
delivery for the same key, but MNScr cannot prove the destination honours the
header. Where the outcome is genuinely unknown the delivery is parked in
``DELIVERY_NEEDS_RECONCILIATION`` rather than retried blindly. This is not
exactly-once and is not described as such.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .delivery import states as S
from .delivery.builder import build_delivery_payload, check_deliverable
from .delivery.contract import EditorialDeliveryPayload, ensure_valid
from .delivery.destination import (
    DESTINATION_PAYLOAD_CMS,
    DeliveryResult,
    RemoteDraft,
    build_delivery_id,
    build_idempotency_key,
)
from .delivery.errors import (
    DeliveryBlockedError,
    DeliveryConflictError,
    DeliveryError,
    DeliveryTimeoutError,
    InvalidDeliveryPayloadError,
    InvalidRemoteResponseError,
)
from .delivery.retry import Clock, RetryPolicy, Sleeper
from .delivery_store import DeliveryRecord, DeliveryStore

logger = logging.getLogger(__name__)


@dataclass
class DeliveryOutcome:
    """What the service decided, and what it persisted."""

    result: DeliveryResult
    record: Optional[DeliveryRecord] = None
    blocked_reasons: List[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.blocked_reasons is None:
            self.blocked_reasons = []

    @property
    def delivered(self) -> bool:
        return self.result.success and self.result.status == S.DELIVERED


class DeliveryService:
    """Builds, validates, persists and attempts an editorial delivery."""

    def __init__(
        self,
        store: Optional[DeliveryStore] = None,
        destination: Optional[Any] = None,
        *,
        db_path: str = "data/app.db",
        retry_policy: Optional[RetryPolicy] = None,
        sleeper: Optional[Sleeper] = None,
        clock: Optional[Clock] = None,
        destination_name: str = DESTINATION_PAYLOAD_CMS,
    ):
        self._owns_store = store is None
        self.store = store or DeliveryStore(db_path=db_path)
        self.destination = destination
        self.destination_name = destination_name
        self.retry_policy = retry_policy or RetryPolicy.from_config()
        self.sleeper = sleeper or Sleeper()
        self.clock = clock or Clock()

    # --- Public API --------------------------------------------------------

    def deliver_draft(
        self,
        draft: Any,
        *,
        topic: Optional[str] = None,
    ) -> DeliveryOutcome:
        """Deliver one approved draft, or explain why it cannot be delivered."""
        reasons = check_deliverable(draft)
        if reasons:
            return self._blocked(draft, reasons)

        try:
            payload = build_delivery_payload(
                draft, destination=self.destination_name, topic=topic
            )
        except DeliveryBlockedError as exc:
            return self._blocked(draft, [str(exc)])

        try:
            ensure_valid(payload)
        except InvalidDeliveryPayloadError as exc:
            return self._permanent_failure(draft, payload, exc)

        return self.deliver_payload(payload, draft_id=draft.draft_id)

    def deliver_payload(
        self, payload: EditorialDeliveryPayload, *, draft_id: Optional[str] = None
    ) -> DeliveryOutcome:
        """Deliver an already-validated payload, honouring idempotency."""
        draft_id = draft_id or payload.draft_id
        idempotency_key = str(payload.metadata.get("idempotency_key") or "").strip()
        if not idempotency_key:
            idempotency_key = build_idempotency_key(
                draft_id=draft_id,
                content_hash=payload.content_hash,
                destination=self.destination_name,
            )

        existing = self.store.get_by_idempotency_key(idempotency_key)
        if existing is not None and existing.status == S.DELIVERED:
            # Same draft, same content, already delivered: return the stored
            # result instead of creating a second remote draft.
            logger.info(
                "[delivery_succeeded] delivery_id=%s draft_id=%s idempotent_replay=true "
                "remote_draft_id=%s",
                existing.delivery_id, draft_id, existing.remote_draft_id,
            )
            return DeliveryOutcome(
                result=DeliveryResult(
                    success=True,
                    destination=self.destination_name,
                    status=S.DELIVERED,
                    delivery_id=existing.delivery_id,
                    remote_draft_id=existing.remote_draft_id,
                    remote_url=existing.remote_url,
                    attempts=existing.attempt_count,
                    idempotent_replay=True,
                ),
                record=existing,
            )

        if existing is not None and S.is_terminal(existing.status):
            return DeliveryOutcome(
                result=DeliveryResult(
                    success=False,
                    destination=self.destination_name,
                    status=existing.status,
                    delivery_id=existing.delivery_id,
                    error=existing.last_error,
                    error_class=existing.error_class,
                    attempts=existing.attempt_count,
                    idempotent_replay=True,
                ),
                record=existing,
            )

        superseded = self._previous_delivery_with_other_content(draft_id, payload.content_hash)
        record = existing or self.store.create_delivery(
            delivery_id=payload.delivery_id,
            idempotency_key=idempotency_key,
            draft_id=draft_id,
            content_hash=payload.content_hash,
            destination=self.destination_name,
            schema_version=payload.schema_version,
            source_event_id=payload.source_event_id,
            source_revision=payload.source_revision,
            status=S.DELIVERY_PENDING,
            supersedes_delivery_id=superseded.delivery_id if superseded else None,
        )
        if record is None:
            record = self.store.get_by_idempotency_key(idempotency_key)

        if superseded is not None:
            # The content changed after a delivery already succeeded. A new,
            # versioned delivery is recorded and the previous one is preserved,
            # but nothing is pushed: overwriting a draft a reviewer may already
            # have opened is a human decision, not ours.
            updated = self.store.update_status(
                record.delivery_id,
                S.DELIVERY_REQUIRES_MANUAL_REVIEW,
                error=(
                    "Conteudo alterado apos entrega anterior "
                    f"({superseded.delivery_id}); revisao humana necessaria."
                ),
                error_class="CONTENT_SUPERSEDED",
            )
            logger.info(
                "[delivery_blocked] delivery_id=%s draft_id=%s motivo=content_superseded "
                "supersedes=%s",
                record.delivery_id, draft_id, superseded.delivery_id,
            )
            return DeliveryOutcome(
                result=DeliveryResult(
                    success=False,
                    destination=self.destination_name,
                    status=S.DELIVERY_REQUIRES_MANUAL_REVIEW,
                    delivery_id=record.delivery_id,
                    error=updated.last_error,
                    error_class="CONTENT_SUPERSEDED",
                ),
                record=updated,
            )

        return self._attempt_with_retry(payload, record)

    # --- Internals ---------------------------------------------------------

    def _previous_delivery_with_other_content(
        self, draft_id: str, content_hash: str
    ) -> Optional[DeliveryRecord]:
        previous = self.store.latest_delivered_for_draft(draft_id)
        if previous is None or previous.content_hash == content_hash:
            return None
        return previous

    def _resolve_destination(self):
        """Construct the destination lazily.

        Deliberately after validation: a blocked or malformed delivery must
        never even build a transport object, let alone open a connection.
        """
        if self.destination is not None:
            return self.destination
        from .delivery.payload_cms import PayloadCmsDestination

        self.destination = PayloadCmsDestination()
        return self.destination

    def _attempt_with_retry(
        self, payload: EditorialDeliveryPayload, record: DeliveryRecord
    ) -> DeliveryOutcome:
        delivery_id = record.delivery_id
        destination = self._resolve_destination()
        last_error: Optional[DeliveryError] = None
        attempts = 0

        for attempt in range(1, self.retry_policy.max_attempts + 1):
            delay = self.retry_policy.delay_for(
                attempt,
                seed=delivery_id,
                retry_after_seconds=(
                    last_error.retry_after_seconds if last_error is not None else None
                ),
            )
            if delay > 0:
                logger.info(
                    "[delivery_retry_scheduled] delivery_id=%s attempt=%s delay_s=%s",
                    delivery_id, attempt, delay,
                )
                self.sleeper.sleep(delay)

            current = self.store.get_by_delivery_id(delivery_id)
            self.store.update_status(
                delivery_id, S.DELIVERY_IN_PROGRESS,
                enforce_transition=current.status != S.DELIVERY_IN_PROGRESS,
            )

            attempts = attempt
            started = self.clock.now()
            logger.info(
                "[delivery_attempt_started] delivery_id=%s draft_id=%s attempt=%s destination=%s",
                delivery_id, record.draft_id, attempt, self.destination_name,
            )

            try:
                remote = destination.submit_draft(payload)
            except DeliveryError as exc:
                duration_ms = self.clock.elapsed_ms_since(started)
                last_error = exc
                self.store.record_attempt(
                    delivery_id,
                    attempt=attempt,
                    status=S.DELIVERY_FAILED_RETRYABLE
                    if exc.retryable
                    else S.DELIVERY_FAILED_PERMANENT,
                    error=str(exc),
                    error_class=exc.error_class,
                    http_status=exc.http_status,
                    duration_ms=duration_ms,
                )
                logger.warning(
                    "[delivery_attempt_failed] delivery_id=%s attempt=%s error_class=%s "
                    "http_status=%s retryable=%s duration_ms=%s",
                    delivery_id, attempt, exc.error_class, exc.http_status,
                    exc.retryable, duration_ms,
                )

                if not exc.retryable:
                    return self._finish_failure(record, exc, attempts, duration_ms)
                if attempt >= self.retry_policy.max_attempts:
                    return self._finish_retryable_exhausted(record, exc, attempts, duration_ms)
                self.store.update_status(
                    delivery_id, S.DELIVERY_FAILED_RETRYABLE,
                    error=str(exc), error_class=exc.error_class, http_status=exc.http_status,
                )
                continue
            except Exception as exc:  # noqa: BLE001 - nunca engolir em silencio
                duration_ms = self.clock.elapsed_ms_since(started)
                wrapped = InvalidRemoteResponseError(
                    f"erro inesperado no destino: {type(exc).__name__}"
                )
                self.store.record_attempt(
                    delivery_id, attempt=attempt, status=S.DELIVERY_FAILED_PERMANENT,
                    error=str(wrapped), error_class=wrapped.error_class, duration_ms=duration_ms,
                )
                logger.exception(
                    "[delivery_attempt_failed] delivery_id=%s attempt=%s erro_inesperado",
                    delivery_id, attempt,
                )
                return self._finish_failure(record, wrapped, attempts, duration_ms)

            duration_ms = self.clock.elapsed_ms_since(started)
            return self._finish_success(record, remote, attempts, duration_ms)

        # Unreachable: the loop always returns. Kept explicit rather than
        # relying on that, so a future edit cannot fall through silently.
        return self._finish_retryable_exhausted(record, last_error, attempts, None)

    def _finish_success(
        self, record: DeliveryRecord, remote: RemoteDraft, attempts: int, duration_ms: int
    ) -> DeliveryOutcome:
        self.store.record_attempt(
            record.delivery_id, attempt=attempts, status=S.DELIVERED, duration_ms=duration_ms
        )
        updated = self.store.update_status(
            record.delivery_id, S.DELIVERED,
            remote_draft_id=remote.remote_id, remote_url=remote.url,
        )
        logger.info(
            "[delivery_succeeded] delivery_id=%s draft_id=%s attempt=%s remote_draft_id=%s "
            "duration_ms=%s",
            record.delivery_id, record.draft_id, attempts, remote.remote_id, duration_ms,
        )
        return DeliveryOutcome(
            result=DeliveryResult(
                success=True,
                destination=self.destination_name,
                status=S.DELIVERED,
                delivery_id=record.delivery_id,
                remote_draft_id=remote.remote_id,
                remote_url=remote.url,
                attempts=attempts,
                duration_ms=duration_ms,
            ),
            record=updated,
        )

    def _finish_failure(
        self,
        record: DeliveryRecord,
        error: DeliveryError,
        attempts: int,
        duration_ms: Optional[int],
    ) -> DeliveryOutcome:
        """A permanent failure — or an ambiguous one that must not be retried."""
        target = S.DELIVERY_FAILED_PERMANENT
        if isinstance(error, (DeliveryConflictError, InvalidRemoteResponseError)):
            # The destination may well have created the draft. Recreating would
            # duplicate it; giving up would lose it. Park it for reconciliation.
            target = S.DELIVERY_NEEDS_RECONCILIATION

        updated = self.store.update_status(
            record.delivery_id, target,
            error=str(error), error_class=error.error_class, http_status=error.http_status,
        )
        logger.error(
            "[delivery_attempt_failed] delivery_id=%s status=%s error_class=%s http_status=%s",
            record.delivery_id, target, error.error_class, error.http_status,
        )
        return DeliveryOutcome(
            result=DeliveryResult.from_error(
                error,
                destination=self.destination_name,
                delivery_id=record.delivery_id,
                status=target,
                attempts=attempts,
                duration_ms=duration_ms,
            ),
            record=updated,
        )

    def _finish_retryable_exhausted(
        self,
        record: DeliveryRecord,
        error: Optional[DeliveryError],
        attempts: int,
        duration_ms: Optional[int],
    ) -> DeliveryOutcome:
        """Retries ran out. A timeout leaves the outcome genuinely unknown."""
        target = (
            S.DELIVERY_NEEDS_RECONCILIATION
            if isinstance(error, DeliveryTimeoutError)
            else S.DELIVERY_FAILED_PERMANENT
        )
        message = str(error) if error else "tentativas esgotadas"
        error_class = error.error_class if error else "UNKNOWN"
        current = self.store.get_by_delivery_id(record.delivery_id)
        updated = self.store.update_status(
            record.delivery_id, target,
            error=f"{message} (apos {attempts} tentativa(s))",
            error_class=error_class,
            http_status=error.http_status if error else None,
            enforce_transition=S.can_transition(current.status, target),
        )
        logger.error(
            "[delivery_attempt_failed] delivery_id=%s status=%s attempts=%s error_class=%s",
            record.delivery_id, target, attempts, error_class,
        )
        return DeliveryOutcome(
            result=DeliveryResult(
                success=False,
                destination=self.destination_name,
                status=target,
                delivery_id=record.delivery_id,
                error=message,
                error_class=error_class,
                retryable=False,
                attempts=attempts,
                duration_ms=duration_ms,
                http_status=error.http_status if error else None,
            ),
            record=updated,
        )

    def _blocked(self, draft: Any, reasons: List[str]) -> DeliveryOutcome:
        """Refuse without any network activity, and record the refusal."""
        draft_id = getattr(draft, "draft_id", "") or "desconhecido"
        logger.warning(
            "[delivery_blocked] draft_id=%s motivos=%s", draft_id, "; ".join(reasons)[:300]
        )
        record = None
        content_hash = str(
            getattr(getattr(draft, "provenance", None), "output_hash", "") or ""
        )
        if draft_id != "desconhecido" and content_hash:
            key = build_idempotency_key(
                draft_id=draft_id,
                content_hash=content_hash,
                destination=self.destination_name,
            )
            record = self.store.get_by_idempotency_key(key) or self.store.create_delivery(
                delivery_id=build_delivery_id(key),
                idempotency_key=key,
                draft_id=draft_id,
                content_hash=content_hash,
                destination=self.destination_name,
                schema_version="1",
                status=S.DELIVERY_BLOCKED,
            )
        return DeliveryOutcome(
            result=DeliveryResult(
                success=False,
                destination=self.destination_name,
                status=S.DELIVERY_BLOCKED,
                delivery_id=record.delivery_id if record else "",
                error="; ".join(reasons),
                error_class="BLOCKED",
            ),
            record=record,
            blocked_reasons=reasons,
        )

    def _permanent_failure(
        self, draft: Any, payload: EditorialDeliveryPayload, error: DeliveryError
    ) -> DeliveryOutcome:
        logger.error(
            "[delivery_blocked] draft_id=%s motivo=payload_invalido error=%s",
            getattr(draft, "draft_id", "-"), str(error)[:300],
        )
        return DeliveryOutcome(
            result=DeliveryResult.from_error(
                error,
                destination=self.destination_name,
                delivery_id=payload.delivery_id,
                status=S.DELIVERY_FAILED_PERMANENT,
            ),
            blocked_reasons=[str(error)],
        )

    # --- Queries -----------------------------------------------------------

    def get_delivery(self, delivery_id: str) -> Optional[DeliveryRecord]:
        return self.store.get_by_delivery_id(delivery_id)

    def list_for_draft(self, draft_id: str) -> List[DeliveryRecord]:
        return self.store.list_for_draft(draft_id)

    def list_by_status(self, status: str, limit: int = 50) -> List[DeliveryRecord]:
        return self.store.list_by_status(status, limit)

    def attempts_for(self, delivery_id: str) -> List[Dict[str, Any]]:
        return self.store.list_attempts(delivery_id)

    def close(self) -> None:
        if self._owns_store:
            self.store.close()


__all__ = ["DeliveryOutcome", "DeliveryService"]
