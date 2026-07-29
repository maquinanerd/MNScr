"""Revision rules for a repeated ``event_key``.

Pure decision logic: it takes what arrived and what is already stored, and says
what should happen. It touches no database and no clock, which is what makes it
exhaustively testable.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from . import errors as E
from . import states as S

logger = logging.getLogger(__name__)

# --- Decisions -------------------------------------------------------------

ACCEPT_NEW_EVENT = "ACCEPT_NEW_EVENT"
ACCEPT_NEW_REVISION = "ACCEPT_NEW_REVISION"
DUPLICATE = "DUPLICATE"
CONFLICT = "CONFLICT"
STALE = "STALE"

#: Only these two ever produce a draft.
ACCEPTING_DECISIONS = frozenset({ACCEPT_NEW_EVENT, ACCEPT_NEW_REVISION})


@dataclass
class StoredRevision:
    """The highest revision already recorded for an ``event_key``."""

    event_key: str
    revision: int
    payload_hash: str
    processing_status: Optional[str] = None
    draft_id: Optional[str] = None


@dataclass
class RevisionDecision:
    """What to do with an incoming revision."""

    decision: str
    validation_status: str
    issues: List[E.ContractIssue] = field(default_factory=list)
    missing_revisions: List[int] = field(default_factory=list)

    @property
    def accepted(self) -> bool:
        return self.decision in ACCEPTING_DECISIONS

    @property
    def should_generate_draft(self) -> bool:
        return self.accepted

    @property
    def error_codes(self) -> List[str]:
        return E.codes([i for i in self.issues if i.is_blocking])

    @property
    def warning_codes(self) -> List[str]:
        return E.codes([i for i in self.issues if not i.is_blocking])

    def to_dict(self) -> Dict[str, Any]:
        return {
            "decision": self.decision,
            "validation_status": self.validation_status,
            "errors": [i.to_dict() for i in self.issues if i.is_blocking],
            "warnings": [i.to_dict() for i in self.issues if not i.is_blocking],
            "missing_revisions": list(self.missing_revisions),
        }


def decide_revision(
    *,
    event_key: str,
    incoming_revision: int,
    incoming_payload_hash: str,
    stored: Optional[StoredRevision] = None,
    stored_same_revision_hash: Optional[str] = None,
) -> RevisionDecision:
    """Apply the revision rules.

    ``stored`` is the highest revision on record for this event.
    ``stored_same_revision_hash`` is the payload hash already stored for
    *exactly* ``incoming_revision``, when one exists — that is what separates an
    idempotent redelivery from a conflict.
    """
    incoming_revision = int(incoming_revision)
    incoming_payload_hash = (incoming_payload_hash or "").strip().lower()

    if stored is None:
        logger.info(
            "[EVENT_REVISION] event_key=%s revision=%s decision=%s",
            event_key, incoming_revision, ACCEPT_NEW_EVENT,
        )
        return RevisionDecision(decision=ACCEPT_NEW_EVENT, validation_status=S.VALIDATED)

    stored_revision = int(stored.revision)

    # Same revision: idempotent redelivery, or a conflict.
    if incoming_revision == stored_revision or stored_same_revision_hash is not None:
        reference_hash = (stored_same_revision_hash or stored.payload_hash or "").strip().lower()
        if reference_hash == incoming_payload_hash:
            logger.info(
                "[EVENT_DUPLICATE] event_key=%s revision=%s payload_hash=%s",
                event_key, incoming_revision, incoming_payload_hash[:12],
            )
            return RevisionDecision(
                decision=DUPLICATE, validation_status=S.DUPLICATE_DELIVERY
            )
        logger.warning(
            "[EVENT_CONFLICT] event_key=%s revision=%s stored_hash=%s incoming_hash=%s",
            event_key, incoming_revision, reference_hash[:12], incoming_payload_hash[:12],
        )
        return RevisionDecision(
            decision=CONFLICT,
            validation_status=S.REVISION_HASH_CONFLICT,
            issues=[
                E.error(
                    E.REVISION_HASH_CONFLICT,
                    "mesma revisao com payload_hash diferente",
                    field_name="payload_hash",
                    event_key=event_key,
                    revision=incoming_revision,
                    stored_hash=reference_hash,
                    incoming_hash=incoming_payload_hash,
                )
            ],
        )

    if incoming_revision < stored_revision:
        logger.warning(
            "[EVENT_STALE] event_key=%s revision=%s stored_revision=%s",
            event_key, incoming_revision, stored_revision,
        )
        return RevisionDecision(
            decision=STALE,
            validation_status=S.STALE_REVISION,
            issues=[
                E.error(
                    E.STALE_REVISION,
                    "revisao anterior a ultima processada",
                    field_name="revision",
                    event_key=event_key,
                    revision=incoming_revision,
                    stored_revision=stored_revision,
                )
            ],
        )

    # Newer revision. A gap is a warning, never a rejection: MNScr records which
    # revisions it never saw and does not invent them.
    issues: List[E.ContractIssue] = []
    missing: List[int] = []
    if incoming_revision > stored_revision + 1:
        missing = list(range(stored_revision + 1, incoming_revision))
        issues.append(
            E.warning(
                E.REVISION_GAP,
                f"revisoes ausentes: {missing}",
                field_name="revision",
                event_key=event_key,
                from_revision=stored_revision,
                to_revision=incoming_revision,
                missing_revisions=missing,
            )
        )
        logger.warning(
            "[EVENT_REVISION] event_key=%s revision=%s stored_revision=%s gap=%s",
            event_key, incoming_revision, stored_revision, missing,
        )
    else:
        logger.info(
            "[EVENT_REVISION] event_key=%s revision=%s stored_revision=%s decision=%s",
            event_key, incoming_revision, stored_revision, ACCEPT_NEW_REVISION,
        )

    return RevisionDecision(
        decision=ACCEPT_NEW_REVISION,
        validation_status=S.VALIDATED,
        issues=issues,
        missing_revisions=missing,
    )


__all__ = [
    "ACCEPTING_DECISIONS",
    "ACCEPT_NEW_EVENT",
    "ACCEPT_NEW_REVISION",
    "CONFLICT",
    "DUPLICATE",
    "RevisionDecision",
    "STALE",
    "StoredRevision",
    "decide_revision",
]
