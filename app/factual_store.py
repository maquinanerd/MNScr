"""Persistence for factual assessments.

Five tables, one per concept, so a claim can be queried without loading the
whole assessment. Assessments are append-only history: re-evaluating under new
rules records a *new* assessment beside the old one, because "what did we think
this said in July, and on what evidence?" must stay answerable.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from .factual import states as S
from .factual.models import (
    ClaimEvidenceLink,
    FactualAssessment,
    FactualClaim,
    FactualConflict,
    FactualCoverage,
    FactualEvidence,
)
from .sqlite_utils import connect_sqlite

logger = logging.getLogger(__name__)


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _loads(value: Any, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


class FactualStore:
    """SQLite persistence for the factual layer."""

    def __init__(self, db_path: str = "data/app.db"):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        self.conn = connect_sqlite(db_path, row_factory=sqlite3.Row)
        self._ensure_schema()

    # --- Schema ------------------------------------------------------------

    def _ensure_schema(self) -> None:
        """Additive and idempotent. Never drops or rewrites existing data."""
        cursor = self.conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS factual_assessments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                assessment_id TEXT NOT NULL UNIQUE,
                draft_id TEXT NOT NULL,
                event_key TEXT,
                revision INTEGER,
                contract_version TEXT,
                version TEXT,
                mode TEXT,
                assessment_hash TEXT NOT NULL,
                coverage_ratio REAL,
                total_material_claims INTEGER DEFAULT 0,
                supported_material_claims INTEGER DEFAULT 0,
                partially_supported_material_claims INTEGER DEFAULT 0,
                unsupported_material_claims INTEGER DEFAULT 0,
                conflicting_material_claims INTEGER DEFAULT 0,
                unverified_material_claims INTEGER DEFAULT 0,
                source_diversity INTEGER DEFAULT 0,
                primary_evidence_count INTEGER DEFAULT 0,
                review_required INTEGER DEFAULT 1,
                warnings_json TEXT,
                blocking_errors_json TEXT,
                created_at TEXT NOT NULL,
                processing_attempt_id INTEGER
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS factual_claims (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                claim_id TEXT NOT NULL,
                assessment_id TEXT NOT NULL,
                draft_id TEXT,
                claim_type TEXT NOT NULL,
                display_text TEXT NOT NULL,
                normalized_subject TEXT,
                predicate TEXT,
                normalized_value TEXT,
                source_sentence TEXT,
                status TEXT NOT NULL,
                confidence REAL,
                is_material INTEGER DEFAULT 1,
                requires_review INTEGER DEFAULT 0,
                claim_hash TEXT NOT NULL,
                draft_locations_json TEXT,
                warnings_json TEXT,
                UNIQUE(assessment_id, claim_id)
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS factual_evidence (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                evidence_id TEXT NOT NULL,
                assessment_id TEXT NOT NULL,
                source_id TEXT,
                source_url TEXT NOT NULL,
                source_name TEXT,
                source_domain TEXT,
                source_title TEXT,
                excerpt TEXT NOT NULL,
                normalized_excerpt TEXT,
                published_at TEXT,
                strength TEXT,
                evidence_hash TEXT NOT NULL,
                editorial_origin_key TEXT,
                UNIQUE(assessment_id, evidence_id)
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS claim_evidence_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                link_id TEXT NOT NULL,
                assessment_id TEXT NOT NULL,
                claim_id TEXT NOT NULL,
                evidence_id TEXT NOT NULL,
                relation TEXT NOT NULL,
                strength TEXT,
                similarity REAL,
                rationale TEXT,
                UNIQUE(assessment_id, link_id)
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS factual_conflicts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conflict_id TEXT NOT NULL,
                assessment_id TEXT NOT NULL,
                draft_id TEXT,
                conflict_type TEXT NOT NULL,
                subject TEXT,
                values_json TEXT,
                claim_ids_json TEXT,
                evidence_ids_json TEXT,
                description TEXT,
                requires_review INTEGER DEFAULT 1,
                resolution_status TEXT NOT NULL,
                conflict_hash TEXT NOT NULL,
                UNIQUE(assessment_id, conflict_id)
            )
            """
        )

        for statement in (
            "CREATE INDEX IF NOT EXISTS idx_fa_draft ON factual_assessments(draft_id)",
            "CREATE INDEX IF NOT EXISTS idx_fa_event ON factual_assessments(event_key, revision)",
            "CREATE INDEX IF NOT EXISTS idx_fa_created ON factual_assessments(created_at)",
            "CREATE INDEX IF NOT EXISTS idx_fc_assessment ON factual_claims(assessment_id)",
            "CREATE INDEX IF NOT EXISTS idx_fc_status ON factual_claims(status)",
            "CREATE INDEX IF NOT EXISTS idx_fc_draft ON factual_claims(draft_id)",
            "CREATE INDEX IF NOT EXISTS idx_fe_assessment ON factual_evidence(assessment_id)",
            "CREATE INDEX IF NOT EXISTS idx_fe_origin ON factual_evidence(editorial_origin_key)",
            "CREATE INDEX IF NOT EXISTS idx_cel_assessment ON claim_evidence_links(assessment_id)",
            "CREATE INDEX IF NOT EXISTS idx_cel_claim ON claim_evidence_links(claim_id)",
            "CREATE INDEX IF NOT EXISTS idx_cfl_assessment ON factual_conflicts(assessment_id)",
            "CREATE INDEX IF NOT EXISTS idx_cfl_draft ON factual_conflicts(draft_id)",
        ):
            cursor.execute(statement)
        self.conn.commit()

    # --- Writes ------------------------------------------------------------

    def save_assessment(
        self,
        assessment: FactualAssessment,
        *,
        processing_attempt_id: Optional[int] = None,
    ) -> Optional[int]:
        """Append an assessment and all its parts, in one transaction.

        Returns ``None`` when this exact ``assessment_id`` is already stored —
        same draft, same facts. That makes re-evaluation idempotent without ever
        overwriting an earlier, different picture.
        """
        cursor = self.conn.cursor()
        coverage = assessment.coverage
        try:
            cursor.execute("BEGIN")
            cursor.execute(
                """
                INSERT INTO factual_assessments (
                    assessment_id, draft_id, event_key, revision, contract_version,
                    version, mode, assessment_hash, coverage_ratio,
                    total_material_claims, supported_material_claims,
                    partially_supported_material_claims, unsupported_material_claims,
                    conflicting_material_claims, unverified_material_claims,
                    source_diversity, primary_evidence_count, review_required,
                    warnings_json, blocking_errors_json, created_at, processing_attempt_id
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    assessment.assessment_id, assessment.draft_id, assessment.event_key,
                    int(assessment.revision), assessment.contract_version,
                    assessment.version, assessment.mode, assessment.assessment_hash,
                    coverage.coverage_ratio, coverage.total_material_claims,
                    coverage.supported_material_claims,
                    coverage.partially_supported_material_claims,
                    coverage.unsupported_material_claims,
                    coverage.conflicting_material_claims,
                    coverage.unverified_material_claims,
                    coverage.source_diversity, coverage.primary_evidence_count,
                    1 if coverage.review_required else 0,
                    _dumps(assessment.warnings), _dumps(assessment.blocking_errors),
                    assessment.created_at, processing_attempt_id,
                ),
            )
            row_id = int(cursor.lastrowid)

            for claim in assessment.claims:
                cursor.execute(
                    """
                    INSERT INTO factual_claims (
                        claim_id, assessment_id, draft_id, claim_type, display_text,
                        normalized_subject, predicate, normalized_value, source_sentence,
                        status, confidence, is_material, requires_review, claim_hash,
                        draft_locations_json, warnings_json
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        claim.claim_id, assessment.assessment_id, assessment.draft_id,
                        claim.claim_type, claim.display_text, claim.normalized_subject,
                        claim.predicate, claim.normalized_value, claim.source_sentence,
                        claim.status, claim.confidence, 1 if claim.is_material else 0,
                        1 if claim.requires_review else 0, claim.claim_hash,
                        _dumps(claim.draft_locations), _dumps(claim.warnings),
                    ),
                )

            for item in assessment.evidence:
                cursor.execute(
                    """
                    INSERT INTO factual_evidence (
                        evidence_id, assessment_id, source_id, source_url, source_name,
                        source_domain, source_title, excerpt, normalized_excerpt,
                        published_at, strength, evidence_hash, editorial_origin_key
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        item.evidence_id, assessment.assessment_id, item.source_id,
                        item.source_url, item.source_name, item.source_domain,
                        item.source_title, item.excerpt, item.normalized_excerpt,
                        item.published_at, item.strength, item.evidence_hash,
                        item.editorial_origin_key,
                    ),
                )

            for link in assessment.links:
                cursor.execute(
                    """
                    INSERT INTO claim_evidence_links (
                        link_id, assessment_id, claim_id, evidence_id,
                        relation, strength, similarity, rationale
                    ) VALUES (?,?,?,?,?,?,?,?)
                    """,
                    (
                        link.link_id, assessment.assessment_id, link.claim_id,
                        link.evidence_id, link.relation, link.strength,
                        link.similarity, link.rationale,
                    ),
                )

            for conflict in assessment.conflicts:
                cursor.execute(
                    """
                    INSERT INTO factual_conflicts (
                        conflict_id, assessment_id, draft_id, conflict_type, subject,
                        values_json, claim_ids_json, evidence_ids_json, description,
                        requires_review, resolution_status, conflict_hash
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        conflict.conflict_id, assessment.assessment_id, assessment.draft_id,
                        conflict.conflict_type, conflict.subject, _dumps(conflict.values),
                        _dumps(conflict.claim_ids), _dumps(conflict.evidence_ids),
                        conflict.description, 1, conflict.resolution_status,
                        conflict.conflict_hash,
                    ),
                )

            self.conn.commit()
            return row_id
        except sqlite3.IntegrityError:
            self.conn.rollback()
            logger.info(
                "[FACTUAL_ASSESSMENT_COMPLETED] draft_id=%s assessment_id=%s ja registrado",
                assessment.draft_id, assessment.assessment_id,
            )
            return None
        except sqlite3.Error:
            self.conn.rollback()
            logger.exception(
                "[FACTUAL_ASSESSMENT_FAILED] draft_id=%s erro ao persistir", assessment.draft_id
            )
            raise

    # --- Reads -------------------------------------------------------------

    def get_assessment(self, draft_id: str) -> Optional[FactualAssessment]:
        """Most recent assessment for a draft."""
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM factual_assessments WHERE draft_id = ? ORDER BY id DESC LIMIT 1",
            (draft_id,),
        )
        row = cursor.fetchone()
        return self._hydrate(row) if row else None

    def get_assessment_by_id(self, assessment_id: str) -> Optional[FactualAssessment]:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM factual_assessments WHERE assessment_id = ?", (assessment_id,)
        )
        row = cursor.fetchone()
        return self._hydrate(row) if row else None

    def list_assessments(self, draft_id: str) -> List[FactualAssessment]:
        """Full history, oldest first."""
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM factual_assessments WHERE draft_id = ? ORDER BY id ASC",
            (draft_id,),
        )
        return [self._hydrate(row) for row in cursor.fetchall()]

    def list_unsupported_claims(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Material unsupported claims from each draft's latest assessment."""
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT c.draft_id, c.claim_id, c.status, c.display_text, c.draft_locations_json
            FROM factual_claims c
            JOIN (
                SELECT draft_id, MAX(id) AS latest_id
                FROM factual_assessments GROUP BY draft_id
            ) latest ON 1=1
            JOIN factual_assessments a ON a.id = latest.latest_id
                AND a.assessment_id = c.assessment_id
            WHERE c.status = ? AND c.is_material = 1
            ORDER BY c.id DESC
            LIMIT ?
            """,
            (S.UNSUPPORTED, int(limit)),
        )
        rows = []
        for row in cursor.fetchall():
            locations = _loads(row["draft_locations_json"], [])
            rows.append(
                {
                    "draft_id": row["draft_id"],
                    "claim_id": row["claim_id"],
                    "status": row["status"],
                    "display_text": row["display_text"],
                    "location": ", ".join(locations) if locations else "-",
                }
            )
        return rows

    def list_conflicts(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Conflicts from each draft's latest assessment."""
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT f.draft_id, f.conflict_id, f.conflict_type, f.subject,
                   f.values_json, f.requires_review, f.resolution_status
            FROM factual_conflicts f
            JOIN (
                SELECT draft_id, MAX(id) AS latest_id
                FROM factual_assessments GROUP BY draft_id
            ) latest ON 1=1
            JOIN factual_assessments a ON a.id = latest.latest_id
                AND a.assessment_id = f.assessment_id
            ORDER BY f.id DESC
            LIMIT ?
            """,
            (int(limit),),
        )
        return [
            {
                "draft_id": row["draft_id"],
                "conflict_id": row["conflict_id"],
                "conflict_type": row["conflict_type"],
                "subject": row["subject"],
                "values": _loads(row["values_json"], []),
                "requires_review": bool(row["requires_review"]),
                "resolution_status": row["resolution_status"],
            }
            for row in cursor.fetchall()
        ]

    # --- Hydration ---------------------------------------------------------

    def _hydrate(self, row: sqlite3.Row) -> FactualAssessment:
        assessment_id = row["assessment_id"]
        cursor = self.conn.cursor()

        cursor.execute("SELECT * FROM factual_claims WHERE assessment_id = ? ORDER BY id",
                       (assessment_id,))
        claims = [
            FactualClaim.from_dict(
                {
                    "claim_id": c["claim_id"],
                    "claim_type": c["claim_type"],
                    "display_text": c["display_text"],
                    "normalized_subject": c["normalized_subject"],
                    "predicate": c["predicate"],
                    "normalized_value": c["normalized_value"],
                    "source_sentence": c["source_sentence"],
                    "status": c["status"],
                    "confidence": c["confidence"],
                    "is_material": bool(c["is_material"]),
                    "requires_review": bool(c["requires_review"]),
                    "claim_hash": c["claim_hash"],
                    "draft_locations": _loads(c["draft_locations_json"], []),
                    "warnings": _loads(c["warnings_json"], []),
                }
            )
            for c in cursor.fetchall()
        ]

        cursor.execute("SELECT * FROM factual_evidence WHERE assessment_id = ? ORDER BY id",
                       (assessment_id,))
        evidence = [
            FactualEvidence.from_dict(
                {
                    "evidence_id": e["evidence_id"], "source_id": e["source_id"],
                    "source_url": e["source_url"], "source_name": e["source_name"],
                    "source_domain": e["source_domain"], "source_title": e["source_title"],
                    "excerpt": e["excerpt"], "normalized_excerpt": e["normalized_excerpt"],
                    "published_at": e["published_at"], "strength": e["strength"],
                    "evidence_hash": e["evidence_hash"],
                    "editorial_origin_key": e["editorial_origin_key"],
                }
            )
            for e in cursor.fetchall()
        ]

        cursor.execute("SELECT * FROM claim_evidence_links WHERE assessment_id = ? ORDER BY id",
                       (assessment_id,))
        links = [
            ClaimEvidenceLink.from_dict(
                {
                    "link_id": link["link_id"], "claim_id": link["claim_id"],
                    "evidence_id": link["evidence_id"], "relation": link["relation"],
                    "strength": link["strength"], "similarity": link["similarity"],
                    "rationale": link["rationale"],
                }
            )
            for link in cursor.fetchall()
        ]

        cursor.execute("SELECT * FROM factual_conflicts WHERE assessment_id = ? ORDER BY id",
                       (assessment_id,))
        conflicts = [
            FactualConflict.from_dict(
                {
                    "conflict_id": f["conflict_id"], "conflict_type": f["conflict_type"],
                    "subject": f["subject"], "values": _loads(f["values_json"], []),
                    "claim_ids": _loads(f["claim_ids_json"], []),
                    "evidence_ids": _loads(f["evidence_ids_json"], []),
                    "description": f["description"],
                    "resolution_status": f["resolution_status"],
                    "conflict_hash": f["conflict_hash"],
                }
            )
            for f in cursor.fetchall()
        ]

        coverage = FactualCoverage(
            total_material_claims=row["total_material_claims"] or 0,
            supported_material_claims=row["supported_material_claims"] or 0,
            partially_supported_material_claims=row["partially_supported_material_claims"] or 0,
            unsupported_material_claims=row["unsupported_material_claims"] or 0,
            conflicting_material_claims=row["conflicting_material_claims"] or 0,
            unverified_material_claims=row["unverified_material_claims"] or 0,
            coverage_ratio=row["coverage_ratio"] or 0.0,
            source_diversity=row["source_diversity"] or 0,
            primary_evidence_count=row["primary_evidence_count"] or 0,
            review_required=bool(row["review_required"]),
        )

        return FactualAssessment(
            draft_id=row["draft_id"], claims=claims, evidence=evidence, links=links,
            conflicts=conflicts, coverage=coverage, event_key=row["event_key"],
            revision=row["revision"] or 1, contract_version=row["contract_version"] or "",
            version=row["version"] or S.ASSESSMENT_VERSION_V1,
            mode=row["mode"] or S.MODE_HYBRID,
            warnings=_loads(row["warnings_json"], []),
            blocking_errors=_loads(row["blocking_errors_json"], []),
            assessment_id=assessment_id, assessment_hash=row["assessment_hash"],
            created_at=row["created_at"],
        )

    def close(self) -> None:
        if self.conn:
            self.conn.close()
            self.conn = None

    def __enter__(self) -> "FactualStore":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()


__all__ = ["FactualStore"]
