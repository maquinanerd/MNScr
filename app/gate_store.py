"""Persistence and re-evaluation for Editorial Gate results.

A gate verdict is history, not state: a new policy version re-evaluating an old
draft must *add* a record, never overwrite the previous one. Otherwise you lose
the ability to answer "what did we think of this draft in July, and why?".

Re-evaluation reads only what is already stored — no feed, no AI, no network,
no cursor movement, and the draft's ``output_hash`` is never touched.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import DB_PATH
from .editorial_gate.models import (
    GATE_BLOCKED,
    GATE_REVIEW_REQUIRED,
    EditorialGateResult,
)
from .sqlite_utils import connect_sqlite

logger = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class GateStore:
    """SQLite persistence for gate results."""

    def __init__(self, db_path: str = DB_PATH):
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
            CREATE TABLE IF NOT EXISTS editorial_gate_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                gate_id TEXT NOT NULL UNIQUE,
                draft_id TEXT NOT NULL,
                event_key TEXT,
                revision INTEGER,
                policy_version TEXT NOT NULL,
                outcome TEXT NOT NULL,
                blocking_count INTEGER DEFAULT 0,
                warning_count INTEGER DEFAULT 0,
                info_count INTEGER DEFAULT 0,
                gate_hash TEXT NOT NULL,
                rules_json TEXT,
                metrics_json TEXT,
                evaluated_at TEXT NOT NULL,
                processing_attempt_id INTEGER
            )
            """
        )
        for statement in (
            "CREATE INDEX IF NOT EXISTS idx_gate_results_draft_id ON editorial_gate_results(draft_id)",
            "CREATE INDEX IF NOT EXISTS idx_gate_results_event_revision "
            "ON editorial_gate_results(event_key, revision)",
            "CREATE INDEX IF NOT EXISTS idx_gate_results_outcome ON editorial_gate_results(outcome)",
            "CREATE INDEX IF NOT EXISTS idx_gate_results_policy ON editorial_gate_results(policy_version)",
            "CREATE INDEX IF NOT EXISTS idx_gate_results_evaluated_at "
            "ON editorial_gate_results(evaluated_at)",
        ):
            cursor.execute(statement)
        self.conn.commit()

    # --- Writes ------------------------------------------------------------

    def save_result(
        self,
        result: EditorialGateResult,
        *,
        processing_attempt_id: Optional[int] = None,
    ) -> Optional[int]:
        """Append a verdict.

        Returns ``None`` when this exact ``gate_id`` is already stored — same
        draft, same policy, same findings. That makes re-evaluation idempotent
        without ever overwriting an earlier, different verdict.
        """
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO editorial_gate_results (
                    gate_id, draft_id, event_key, revision, policy_version, outcome,
                    blocking_count, warning_count, info_count, gate_hash,
                    rules_json, metrics_json, evaluated_at, processing_attempt_id
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    result.gate_id,
                    result.draft_id,
                    result.event_key,
                    int(result.revision),
                    result.policy_version,
                    result.outcome,
                    result.blocking_count,
                    result.warning_count,
                    result.info_count,
                    result.gate_hash,
                    _dumps([rule.to_dict() for rule in result.rules]),
                    _dumps(result.metrics),
                    result.evaluated_at,
                    processing_attempt_id,
                ),
            )
            self.conn.commit()
            return int(cursor.lastrowid)
        except sqlite3.IntegrityError:
            self.conn.rollback()
            logger.info(
                "[GATE_COMPLETED] draft_id=%s gate_id=%s ja registrado; nada reescrito",
                result.draft_id, result.gate_id,
            )
            return None

    # --- Reads -------------------------------------------------------------

    def get_latest_gate_result(self, draft_id: str) -> Optional[EditorialGateResult]:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM editorial_gate_results WHERE draft_id = ? ORDER BY id DESC LIMIT 1",
            (draft_id,),
        )
        row = cursor.fetchone()
        return self._row_to_result(row) if row else None

    def list_gate_results(self, draft_id: str) -> List[EditorialGateResult]:
        """Full history, oldest first."""
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM editorial_gate_results WHERE draft_id = ? ORDER BY id ASC",
            (draft_id,),
        )
        return [self._row_to_result(row) for row in cursor.fetchall()]

    def list_by_outcome(self, outcome: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Latest verdict per draft for one outcome, for the CLI listings."""
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT r.* FROM editorial_gate_results r
            JOIN (
                SELECT draft_id, MAX(id) AS latest_id
                FROM editorial_gate_results GROUP BY draft_id
            ) latest ON latest.latest_id = r.id
            WHERE r.outcome = ?
            ORDER BY r.evaluated_at DESC
            LIMIT ?
            """,
            (outcome, int(limit)),
        )
        return [dict(row) for row in cursor.fetchall()]

    def list_blocked(self, limit: int = 50) -> List[Dict[str, Any]]:
        return self.list_by_outcome(GATE_BLOCKED, limit)

    def list_review_required(self, limit: int = 50) -> List[Dict[str, Any]]:
        return self.list_by_outcome(GATE_REVIEW_REQUIRED, limit)

    @staticmethod
    def _row_to_result(row: sqlite3.Row) -> EditorialGateResult:
        def _loads(value: Any, fallback: Any) -> Any:
            if not value:
                return fallback
            try:
                return json.loads(value)
            except (TypeError, ValueError):
                return fallback

        return EditorialGateResult.from_dict(
            {
                "gate_id": row["gate_id"],
                "draft_id": row["draft_id"],
                "event_key": row["event_key"],
                "revision": row["revision"] or 1,
                "policy_version": row["policy_version"],
                "outcome": row["outcome"],
                "rules": _loads(row["rules_json"], []),
                "gate_hash": row["gate_hash"],
                "evaluated_at": row["evaluated_at"],
                "metrics": _loads(row["metrics_json"], {}),
            }
        )

    def close(self) -> None:
        if self.conn:
            self.conn.close()
            self.conn = None

    def __enter__(self) -> "GateStore":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()


__all__ = ["GateStore"]
