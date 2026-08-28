"""Build, re-evaluate and query factual assessments.

Re-evaluation exists because the rules change, not because the facts do. It
reads the stored draft and the stored source material, produces a new
assessment beside the old one, and re-runs the Editorial Gate so the verdict
stays consistent with the new picture.

It never contacts RSS Prime, never searches the web, never advances a cursor and
never touches the draft's ``output_hash``. The editorial body is not its
business — only what the body asserts.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .config import DB_PATH
from .factual import states as S
from .factual.errors import AssessmentNotFoundError
from .factual.models import FactualAssessment
from .factual_builder import build_factual_assessment, source_material_from_draft
from .factual_store import FactualStore

logger = logging.getLogger(__name__)


@dataclass
class FactualReevaluationResult:
    """What a re-evaluation changed, if anything."""

    draft_id: str
    assessment: FactualAssessment
    previous_hash: Optional[str] = None
    previous_coverage_ratio: Optional[float] = None
    created_new_record: bool = False
    gate_outcome: Optional[str] = None

    @property
    def changed(self) -> bool:
        return self.previous_hash != self.assessment.assessment_hash

    def to_dict(self) -> Dict[str, Any]:
        return {
            "draft_id": self.draft_id,
            "assessment_id": self.assessment.assessment_id,
            "assessment_hash": self.assessment.assessment_hash,
            "coverage_ratio": self.assessment.coverage.coverage_ratio,
            "previous_hash": self.previous_hash,
            "previous_coverage_ratio": self.previous_coverage_ratio,
            "created_new_record": self.created_new_record,
            "changed": self.changed,
            "gate_outcome": self.gate_outcome,
        }


class FactualService:
    """Assessment lifecycle over local data only."""

    def __init__(
        self,
        store: Optional[FactualStore] = None,
        *,
        draft_dir: Optional[str] = None,
        db_path: str = DB_PATH,
    ):
        from . import config

        self._owns_store = store is None
        self.store = store or FactualStore(db_path=db_path)
        self.db_path = db_path
        self.draft_dir = Path(
            draft_dir or getattr(config, "LOCAL_DRAFT_DIR", "artifacts/local-drafts")
        )

    # --- Build -------------------------------------------------------------

    def build_and_store(
        self,
        draft: Any,
        source_material: Optional[Sequence[Dict[str, Any]]] = None,
        *,
        mode: Optional[str] = None,
        claim_response: Any = None,
        processing_attempt_id: Optional[int] = None,
    ) -> FactualAssessment:
        """Assess a live draft and persist the result."""
        assessment = build_factual_assessment(
            draft, source_material, mode=mode, claim_response=claim_response
        )
        self.store.save_assessment(assessment, processing_attempt_id=processing_attempt_id)
        return assessment

    # --- Queries -----------------------------------------------------------

    def get_factual_assessment(self, draft_id: str) -> Optional[FactualAssessment]:
        return self.store.get_assessment(draft_id)

    def list_factual_assessments(self, draft_id: str) -> List[FactualAssessment]:
        return self.store.list_assessments(draft_id)

    def list_unsupported_claims(self, limit: int = 100) -> List[Dict[str, Any]]:
        return self.store.list_unsupported_claims(limit)

    def list_conflicting_claims(self, limit: int = 100) -> List[Dict[str, Any]]:
        return self.store.list_conflicts(limit)

    # --- Re-evaluation -----------------------------------------------------

    def load_draft(self, draft_id: str):
        """Read a draft back from its local artifact."""
        from .gate_service import draft_from_artifact

        path = self.draft_dir / f"{draft_id}.json"
        if not path.is_file():
            raise AssessmentNotFoundError(draft_id)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise AssessmentNotFoundError(draft_id) from exc
        return draft_from_artifact(payload)

    def stored_source_material(self, draft_id: str) -> List[Dict[str, Any]]:
        """Source material recovered from the previous assessment's evidence.

        This is what makes re-evaluation possible without going back to the
        feed: the excerpts we already kept are the sources, as far as replay is
        concerned.
        """
        previous = self.store.get_assessment(draft_id)
        if previous is None:
            return []
        grouped: Dict[str, Dict[str, Any]] = {}
        for item in previous.evidence:
            entry = grouped.setdefault(
                item.source_url,
                {
                    "url": item.source_url,
                    "source_id": item.source_id,
                    "source_name": item.source_name,
                    "source_domain": item.source_domain,
                    "title": item.source_title,
                    "published_at": item.published_at,
                    "is_primary": item.strength == S.PRIMARY,
                    "content": "",
                },
            )
            entry["content"] = f"{entry['content']} {item.excerpt}".strip()
        return list(grouped.values())

    def reevaluate_factual_assessment(
        self,
        draft_id: str,
        *,
        mode: Optional[str] = None,
        rerun_gate: bool = True,
    ) -> FactualReevaluationResult:
        """Re-assess a stored draft using only local data.

        Idempotent when nothing changed: the same draft under the same rules
        produces the same ``assessment_id`` and no second record.
        """
        previous = self.store.get_assessment(draft_id)
        logger.info(
            "[FACTUAL_REEVALUATION_STARTED] draft_id=%s mode=%s previous_hash=%s",
            draft_id, mode or "<config>",
            (previous.assessment_hash[:16] if previous else "-"),
        )

        try:
            draft = self.load_draft(draft_id)
        except AssessmentNotFoundError:
            logger.error(
                "[FACTUAL_REEVALUATION_FAILED] draft_id=%s motivo=draft_nao_encontrado", draft_id
            )
            raise

        output_hash_before = draft.provenance.output_hash

        material = self.stored_source_material(draft_id) or source_material_from_draft(draft)
        assessment = build_factual_assessment(draft, material, mode=mode)

        if draft.provenance.output_hash != output_hash_before:
            logger.error(
                "[FACTUAL_REEVALUATION_FAILED] draft_id=%s motivo=output_hash_alterado", draft_id
            )
            raise RuntimeError(
                "A reavaliacao factual alterou o output_hash do draft; isso e um bug."
            )

        row_id = self.store.save_assessment(assessment)

        gate_outcome = None
        if rerun_gate:
            gate_outcome = self._rerun_gate(draft, assessment)

        logger.info(
            "[FACTUAL_REEVALUATION_COMPLETED] draft_id=%s assessment_id=%s "
            "coverage_ratio=%s assessment_hash=%s novo_registro=%s",
            draft_id, assessment.assessment_id, assessment.coverage.coverage_ratio,
            assessment.assessment_hash[:16], row_id is not None,
        )
        return FactualReevaluationResult(
            draft_id=draft_id,
            assessment=assessment,
            previous_hash=previous.assessment_hash if previous else None,
            previous_coverage_ratio=previous.coverage.coverage_ratio if previous else None,
            created_new_record=row_id is not None,
            gate_outcome=gate_outcome,
        )

    def _rerun_gate(self, draft: Any, assessment: FactualAssessment) -> Optional[str]:
        """Re-run the Editorial Gate so its verdict matches the new facts.

        A gate failure never fails the re-evaluation: the assessment is already
        stored, and losing it because the gate stumbled would be the wrong
        trade.
        """
        from .editorial_gate import EditorialGate
        from .gate_store import GateStore

        gate_store = None
        try:
            draft.factual_assessment = assessment
            result = EditorialGate().evaluate(
                draft, context={"factual_assessment": assessment}
            )
            gate_store = GateStore(db_path=self.db_path)
            gate_store.save_result(result)
            return result.outcome
        except Exception as exc:  # noqa: BLE001 - a avaliacao factual ja esta salva
            logger.warning(
                "[FACTUAL_REEVALUATION_COMPLETED] draft_id=%s gate nao reavaliado: %s",
                draft.draft_id, type(exc).__name__,
            )
            return None
        finally:
            if gate_store is not None:
                gate_store.close()

    def close(self) -> None:
        if self._owns_store:
            self.store.close()


__all__ = ["FactualReevaluationResult", "FactualService"]
