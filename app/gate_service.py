"""Evaluation and re-evaluation of the Editorial Gate over stored drafts.

Re-evaluation exists because policies change. When they do, the honest thing is
to record a *new* verdict beside the old one, so an operator can see both what
we thought then and what we think now.

Everything here is local: it reads the draft artifact and the SQLite record. It
never contacts the feed, never calls the AI, never advances a cursor, never
mutates the draft's editorial content, and never touches ``output_hash``.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from .editorial.models import (
    DraftContent,
    DraftProvenance,
    EditorialDraft,
    EvidenceRecord,
    MediaCandidate,
    SourceReference,
)
from .editorial_gate.engine import EditorialGate
from .editorial_gate.errors import GateResultNotFoundError
from .editorial_gate.models import EditorialGateResult
from .gate_store import GateStore

logger = logging.getLogger(__name__)


@dataclass
class ReevaluationResult:
    """What a re-evaluation changed, if anything."""

    draft_id: str
    result: EditorialGateResult
    previous_outcome: Optional[str] = None
    previous_gate_hash: Optional[str] = None
    created_new_record: bool = False

    @property
    def changed(self) -> bool:
        return self.previous_gate_hash != self.result.gate_hash

    def to_dict(self) -> Dict[str, Any]:
        return {
            "draft_id": self.draft_id,
            "outcome": self.result.outcome,
            "gate_hash": self.result.gate_hash,
            "policy_version": self.result.policy_version,
            "previous_outcome": self.previous_outcome,
            "previous_gate_hash": self.previous_gate_hash,
            "created_new_record": self.created_new_record,
            "changed": self.changed,
        }


def draft_from_artifact(payload: Dict[str, Any]) -> EditorialDraft:
    """Rebuild an ``EditorialDraft`` from its persisted JSON.

    The reconstruction is faithful to what was written: nothing is regenerated,
    recomputed or filled in. In particular ``provenance.output_hash`` comes
    straight from the artifact, so re-evaluating can never change it.
    """
    content_payload = payload.get("content") or {}
    provenance_payload = payload.get("provenance") or {}

    content = DraftContent(
        title=content_payload.get("title") or "",
        body_html=content_payload.get("body_html") or "",
        subtitle=content_payload.get("subtitle"),
        summary=content_payload.get("summary"),
        meta_description=content_payload.get("meta_description"),
        slug_suggestion=content_payload.get("slug_suggestion"),
        focus_keyphrase=content_payload.get("focus_keyphrase"),
        related_keyphrases=list(content_payload.get("related_keyphrases") or []),
        category_suggestions=list(content_payload.get("category_suggestions") or []),
        tag_suggestions=list(content_payload.get("tag_suggestions") or []),
    )

    known = {
        "input_hash", "output_hash", "pipeline_version", "commit_sha",
        "input_contract_version", "output_contract_version", "input_event_key",
        "input_revision", "input_payload_hash", "input_cursor", "model_provider",
        "model_name", "prompt_version", "created_at", "duration_ms",
        "input_tokens", "output_tokens", "total_tokens",
    }
    provenance = DraftProvenance(
        **{k: v for k, v in provenance_payload.items() if k in known}
    )

    sources = [
        SourceReference(
            url=item.get("url") or "",
            source_id=item.get("source_id"),
            name=item.get("name"),
            domain=item.get("domain"),
            title=item.get("title"),
            author=item.get("author"),
            published_at=item.get("published_at"),
            extraction_status=item.get("extraction_status"),
            is_primary=bool(item.get("is_primary")),
        )
        for item in (payload.get("sources") or [])
        if item.get("url")
    ]

    evidence = [
        EvidenceRecord(
            evidence_id=item.get("evidence_id") or "",
            source_url=item.get("source_url") or "",
            source_name=item.get("source_name"),
            source_excerpt=item.get("source_excerpt"),
            claim=item.get("claim"),
            status=item.get("status") or "observed",
        )
        for item in (payload.get("evidence") or [])
        if item.get("evidence_id")
    ]

    media = [
        MediaCandidate(
            source_url=item.get("source_url") or "",
            source_domain=item.get("source_domain"),
            original_alt=item.get("original_alt"),
            original_caption=item.get("original_caption"),
            credit=item.get("credit"),
            license=item.get("license"),
            media_type=item.get("media_type") or "image",
            status=item.get("status") or "unverified",
        )
        for item in (payload.get("media_candidates") or [])
        if item.get("source_url")
    ]

    factual_payload = payload.get("factual_assessment")
    gate_payload = payload.get("editorial_gate")
    factual_assessment = None
    editorial_gate = None
    if isinstance(factual_payload, dict):
        from app.factual.models import FactualAssessment

        factual_assessment = FactualAssessment.from_dict(factual_payload)
    if isinstance(gate_payload, dict):
        from app.editorial_gate.models import EditorialGateResult

        editorial_gate = EditorialGateResult.from_dict(gate_payload)

    return EditorialDraft(
        draft_id=payload.get("draft_id") or "",
        article_id=payload.get("article_id") or "",
        content=content,
        provenance=provenance,
        event_key=payload.get("event_key"),
        cluster_signature=payload.get("cluster_signature"),
        revision=payload.get("revision") or 1,
        first_seen=payload.get("first_seen"),
        last_seen=payload.get("last_seen"),
        cluster_confidence=payload.get("cluster_confidence"),
        sources=sources,
        evidence=evidence,
        media_candidates=media,
        warnings=list(payload.get("warnings") or []),
        blocking_errors=list(payload.get("blocking_errors") or []),
        seo_source=dict(payload.get("seo_source") or {}),
        factual_assessment=factual_assessment,
        editorial_gate=editorial_gate,
    )


class GateService:
    """Evaluate, re-evaluate and query gate verdicts."""

    def __init__(
        self,
        gate_store: Optional[GateStore] = None,
        *,
        draft_dir: Optional[str] = None,
        db_path: str = "data/app.db",
    ):
        from app import config

        self._owns_store = gate_store is None
        self.store = gate_store or GateStore(db_path=db_path)
        self.draft_dir = Path(draft_dir or getattr(config, "LOCAL_DRAFT_DIR", "artifacts/local-drafts"))

    # --- Evaluation --------------------------------------------------------

    def evaluate_draft(
        self,
        draft: EditorialDraft,
        policy_version: Optional[str] = None,
        *,
        context: Optional[Dict[str, Any]] = None,
        processing_attempt_id: Optional[int] = None,
    ) -> EditorialGateResult:
        """Evaluate a live draft and persist the verdict."""
        result = EditorialGate(policy_version=policy_version).evaluate(draft, context=context)
        self.store.save_result(result, processing_attempt_id=processing_attempt_id)
        return result

    # --- Re-evaluation -----------------------------------------------------

    def load_draft(self, draft_id: str) -> EditorialDraft:
        """Read a draft back from its local artifact."""
        path = self.draft_dir / f"{draft_id}.json"
        if not path.is_file():
            raise GateResultNotFoundError(draft_id)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise GateResultNotFoundError(draft_id) from exc
        return draft_from_artifact(payload)

    def reevaluate_draft(
        self,
        draft_id: str,
        policy_version: Optional[str] = None,
        *,
        context: Optional[Dict[str, Any]] = None,
    ) -> ReevaluationResult:
        """Re-run the gate over a stored draft.

        Idempotent: the same draft under the same policy produces the same
        ``gate_id`` and no second record. A different policy — or a different
        verdict — appends a new record and leaves the old one intact.
        """
        previous = self.store.get_latest_gate_result(draft_id)
        logger.info(
            "[GATE_REEVALUATION_STARTED] draft_id=%s policy_version=%s previous_outcome=%s",
            draft_id, policy_version or "<config>", previous.outcome if previous else "-",
        )

        try:
            draft = self.load_draft(draft_id)
        except GateResultNotFoundError:
            logger.error("[GATE_REEVALUATION_FAILED] draft_id=%s motivo=draft_nao_encontrado", draft_id)
            raise

        output_hash_before = draft.provenance.output_hash
        result = EditorialGate(policy_version=policy_version).evaluate(draft, context=context)

        if draft.provenance.output_hash != output_hash_before:
            logger.error("[GATE_REEVALUATION_FAILED] draft_id=%s motivo=output_hash_alterado", draft_id)
            raise RuntimeError(
                "A reavaliacao alterou o output_hash do draft; isso e um bug."
            )

        row_id = self.store.save_result(result)
        logger.info(
            "[GATE_REEVALUATION_COMPLETED] draft_id=%s policy_version=%s outcome=%s "
            "gate_hash=%s novo_registro=%s",
            draft_id, result.policy_version, result.outcome,
            result.gate_hash[:16], row_id is not None,
        )
        return ReevaluationResult(
            draft_id=draft_id,
            result=result,
            previous_outcome=previous.outcome if previous else None,
            previous_gate_hash=previous.gate_hash if previous else None,
            created_new_record=row_id is not None,
        )

    # --- Queries -----------------------------------------------------------

    def get_latest_gate_result(self, draft_id: str) -> Optional[EditorialGateResult]:
        return self.store.get_latest_gate_result(draft_id)

    def list_gate_results(self, draft_id: str) -> List[EditorialGateResult]:
        return self.store.list_gate_results(draft_id)

    def list_blocked(self, limit: int = 50) -> List[Dict[str, Any]]:
        return self.store.list_blocked(limit)

    def list_review_required(self, limit: int = 50) -> List[Dict[str, Any]]:
        return self.store.list_review_required(limit)

    def close(self) -> None:
        if self._owns_store:
            self.store.close()


def format_gate_result(result: EditorialGateResult) -> str:
    """Human-readable verdict for ``--show-gate``. Never prints the body."""
    lines = [
        f"draft_id       : {result.draft_id}",
        f"event_key      : {result.event_key or '-'}",
        f"revision       : {result.revision}",
        f"policy_version : {result.policy_version}",
        f"outcome        : {result.outcome}",
        f"blocking_count : {result.blocking_count}",
        f"warning_count  : {result.warning_count}",
        f"info_count     : {result.info_count}",
        f"gate_hash      : {result.gate_hash}",
        f"evaluated_at   : {result.evaluated_at}",
        "",
        "regras acionadas:",
    ]
    triggered = result.triggered_rules
    if not triggered:
        lines.append("  (nenhuma)")
    for rule in triggered:
        lines.append(f"  [{rule.severity:<8}] {rule.rule_code}: {rule.message}")
        for item in rule.evidence:
            lines.append(f"      evidencia: {item}")
        if rule.remediation:
            lines.append(f"      acao: {rule.remediation}")
    return "\n".join(lines)


def format_gate_listing(rows: List[Dict[str, Any]], title: str) -> str:
    """Compact table for the listing commands."""
    header = (
        f"{'DRAFT_ID':<40}  {'EVENT_KEY':<28}  {'REV':>3}  "
        f"{'BLOQ':>4}  {'WARN':>4}  {'POLICY':<26}  EVALUATED_AT"
    )
    lines = [title, header, "-" * len(header)]
    if not rows:
        lines.append("(nenhum draft nesta situacao)")
        return "\n".join(lines)
    for row in rows:
        lines.append(
            f"{str(row.get('draft_id') or '-'):<40}  "
            f"{str(row.get('event_key') or '-'):<28}  "
            f"{int(row.get('revision') or 1):>3}  "
            f"{int(row.get('blocking_count') or 0):>4}  "
            f"{int(row.get('warning_count') or 0):>4}  "
            f"{str(row.get('policy_version') or '-'):<26}  "
            f"{row.get('evaluated_at') or '-'}"
        )
    return "\n".join(lines)


__all__ = [
    "GateService",
    "ReevaluationResult",
    "draft_from_artifact",
    "format_gate_listing",
    "format_gate_result",
]
