"""Foundation for submitting editorial drafts to Payload CMS.

Payload CMS is the CMS decided for Cinerie. This module builds the real
transformation and validation, but **does not transmit anything** in MS-1:
the editorial contract (collection shape, required fields, review workflow,
media policy) is still owned by Cinerie Editorial.

Guarantees in this phase:
- with ``PAYLOAD_ENABLED=false`` the class performs no network I/O at all;
- with ``PAYLOAD_ENABLED=true`` it fails loudly instead of guessing a contract.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Optional

from app.editorial.models import EditorialDraft
from app.editorial.serialization import to_plain
from app.editorial.states import SUBMISSION_DISABLED
from app.editorial_gate.models import GATE_CLEAR

from .base import SubmissionResult, SubmitterNotEnabledError

logger = logging.getLogger(__name__)

DESTINATION = "payload"

NOT_ENABLED_MESSAGE = (
    "Payload submission is not enabled until the Cinerie Editorial "
    "contract is finalized."
)

GATE_REJECTION_MESSAGE = (
    "Draft rejected by the Editorial Gate: only GATE_CLEAR drafts may ever be "
    "submitted, and clearing the gate is still not editorial approval."
)


@dataclass(frozen=True)
class PayloadConfig:
    """Runtime configuration for the Payload destination."""

    enabled: bool = False
    base_url: Optional[str] = None
    api_token: Optional[str] = None
    drafts_collection: str = "editorial-drafts"

    @classmethod
    def from_env(cls) -> "PayloadConfig":
        return cls(
            enabled=os.getenv("PAYLOAD_ENABLED", "false").strip().lower() == "true",
            base_url=(os.getenv("PAYLOAD_BASE_URL") or "").strip() or None,
            api_token=(os.getenv("PAYLOAD_API_TOKEN") or "").strip() or None,
            drafts_collection=(
                os.getenv("PAYLOAD_DRAFTS_COLLECTION") or "editorial-drafts"
            ).strip()
            or "editorial-drafts",
        )

    def validate(self) -> list[str]:
        """Return configuration problems that would block a real submission."""
        issues: list[str] = []
        if not self.enabled:
            return issues
        if not self.base_url:
            issues.append("PAYLOAD_BASE_URL nao configurada")
        if not self.api_token:
            issues.append("PAYLOAD_API_TOKEN nao configurado")
        if not self.drafts_collection:
            issues.append("PAYLOAD_DRAFTS_COLLECTION nao configurada")
        return issues


class PayloadDraftSubmitter:
    """Transforms a draft into a neutral Payload document. Does not transmit."""

    destination = DESTINATION

    def __init__(self, config: Optional[PayloadConfig] = None) -> None:
        self.config = config or PayloadConfig.from_env()

    # -- transformation -----------------------------------------------------

    def _build_document(self, draft: EditorialDraft) -> dict[str, Any]:
        """Map an EditorialDraft onto a CMS-neutral Payload document.

        Field names here are provisional and intentionally mirror the MNScr
        domain rather than inventing a Cinerie schema. The only concession to
        Payload is `_status: "draft"`, which is Payload's own draft flag — it
        never means approved or published.
        """
        content = draft.content
        return {
            "_status": "draft",
            "mnscrDraftId": draft.draft_id,
            "mnscrRevision": draft.revision,
            "mnscrStatus": draft.status,
            "eventKey": draft.event_key,
            "clusterSignature": draft.cluster_signature,
            "title": content.title,
            "subtitle": content.subtitle,
            "summary": content.summary,
            "bodyHtml": content.body_html,
            "metaDescription": content.meta_description,
            "slugSuggestion": content.slug_suggestion,
            "focusKeyphrase": content.focus_keyphrase,
            "relatedKeyphrases": list(content.related_keyphrases),
            "categorySuggestions": list(content.category_suggestions),
            "tagSuggestions": list(content.tag_suggestions),
            "sources": to_plain(draft.sources),
            "evidence": to_plain(draft.evidence),
            "mediaCandidates": to_plain(draft.media_candidates),
            "warnings": list(draft.warnings),
            "blockingErrors": list(draft.blocking_errors),
            "provenance": to_plain(draft.provenance),
        }

    def build_document(self, draft: EditorialDraft) -> dict[str, Any]:
        """Public accessor used by tests and by future transport code."""
        return self._build_document(draft)

    def collection_endpoint(self) -> str:
        base = (self.config.base_url or "").rstrip("/")
        return f"{base}/api/{self.config.drafts_collection}"

    # -- submitter ----------------------------------------------------------

    def submit(self, draft: EditorialDraft) -> SubmissionResult:
        # MS-3: the Editorial Gate is checked before anything else, so that even
        # a fully enabled Payload destination can never receive a draft that a
        # human has not cleared. A missing verdict counts as not cleared: the
        # absence of an opinion is not an approval.
        gate = getattr(draft, "editorial_gate", None)
        gate_outcome = getattr(gate, "outcome", None) if gate is not None else None
        if gate_outcome != GATE_CLEAR:
            reason = gate_outcome or "GATE_NOT_EVALUATED"
            logger.warning(
                "[PAYLOAD] submissao recusada pelo Editorial Gate draft_id=%s gate_outcome=%s",
                draft.draft_id, reason,
            )
            return SubmissionResult(
                success=False,
                destination=self.destination,
                status=SUBMISSION_DISABLED,
                submission_id=draft.draft_id,
                error=f"{GATE_REJECTION_MESSAGE} (gate={reason})",
            )

        if not self.config.enabled:
            logger.info(
                "[PAYLOAD] destino desabilitado draft_id=%s motivo=contract_pending",
                draft.draft_id,
            )
            return SubmissionResult(
                success=False,
                destination=self.destination,
                status=SUBMISSION_DISABLED,
                submission_id=draft.draft_id,
                error=NOT_ENABLED_MESSAGE,
            )

        # Enabled but the contract is still open: fail loudly, never guess.
        issues = self.config.validate()
        if issues:
            logger.error("[PAYLOAD] configuracao incompleta issues=%s", issues)
        raise SubmitterNotEnabledError(NOT_ENABLED_MESSAGE)
