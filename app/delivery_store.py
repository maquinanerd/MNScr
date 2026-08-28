"""Persistence for editorial deliveries.

Two tables: one row per logical delivery (unique by idempotency key) and an
append-only log of every attempt made against it. The attempt log is what makes
a retry auditable — "it eventually worked" is not the same story as "it failed
twice with 503 and then worked".

Additive and idempotent, in the same shape as ``app.gate_store`` and
``app.factual_store``. Nothing from MS-1..MS-4 is read-modified or deleted.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import DB_PATH
from .delivery import states as S
from .sqlite_utils import connect_sqlite

logger = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dumps(value: Any) -> Optional[str]:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass
class DeliveryRecord:
    """One logical delivery."""

    delivery_id: str
    draft_id: str
    idempotency_key: str
    content_hash: str
    destination: str
    status: str
    schema_version: str
    source_event_id: Optional[str] = None
    source_revision: Optional[int] = None
    attempt_count: int = 0
    last_error: Optional[str] = None
    error_class: Optional[str] = None
    http_status: Optional[int] = None
    first_attempt_at: Optional[str] = None
    last_attempt_at: Optional[str] = None
    delivered_at: Optional[str] = None
    remote_draft_id: Optional[str] = None
    remote_url: Optional[str] = None
    supersedes_delivery_id: Optional[str] = None
    created_at: str = ""

    @property
    def is_delivered(self) -> bool:
        return self.status == S.DELIVERED

    def to_dict(self) -> Dict[str, Any]:
        return {
            "delivery_id": self.delivery_id,
            "draft_id": self.draft_id,
            "idempotency_key": self.idempotency_key,
            "content_hash": self.content_hash,
            "destination": self.destination,
            "status": self.status,
            "schema_version": self.schema_version,
            "source_event_id": self.source_event_id,
            "source_revision": self.source_revision,
            "attempt_count": self.attempt_count,
            "last_error": self.last_error,
            "error_class": self.error_class,
            "http_status": self.http_status,
            "first_attempt_at": self.first_attempt_at,
            "last_attempt_at": self.last_attempt_at,
            "delivered_at": self.delivered_at,
            "remote_draft_id": self.remote_draft_id,
            "remote_url": self.remote_url,
            "supersedes_delivery_id": self.supersedes_delivery_id,
            "created_at": self.created_at,
        }


class DeliveryStore:
    """SQLite persistence for the delivery layer."""

    def __init__(self, db_path: str = DB_PATH):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        self.conn = connect_sqlite(db_path, row_factory=sqlite3.Row)
        self._ensure_schema()

    # --- Schema ------------------------------------------------------------

    def _ensure_schema(self) -> None:
        """Additive and idempotent. Safe on an empty and on a migrated database."""
        cursor = self.conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS editorial_deliveries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                delivery_id TEXT NOT NULL UNIQUE,
                idempotency_key TEXT NOT NULL UNIQUE,
                draft_id TEXT NOT NULL,
                source_event_id TEXT,
                source_revision INTEGER,
                content_hash TEXT NOT NULL,
                destination TEXT NOT NULL,
                status TEXT NOT NULL,
                schema_version TEXT NOT NULL,
                attempt_count INTEGER DEFAULT 0,
                last_error TEXT,
                error_class TEXT,
                http_status INTEGER,
                first_attempt_at TEXT,
                last_attempt_at TEXT,
                delivered_at TEXT,
                remote_draft_id TEXT,
                remote_url TEXT,
                supersedes_delivery_id TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS delivery_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                delivery_id TEXT NOT NULL,
                attempt INTEGER NOT NULL,
                status TEXT NOT NULL,
                error_class TEXT,
                http_status INTEGER,
                error TEXT,
                duration_ms INTEGER,
                started_at TEXT,
                finished_at TEXT,
                UNIQUE(delivery_id, attempt)
            )
            """
        )
        for statement in (
            "CREATE INDEX IF NOT EXISTS idx_deliveries_draft ON editorial_deliveries(draft_id)",
            "CREATE INDEX IF NOT EXISTS idx_deliveries_status ON editorial_deliveries(status)",
            "CREATE INDEX IF NOT EXISTS idx_deliveries_event "
            "ON editorial_deliveries(source_event_id, source_revision)",
            "CREATE INDEX IF NOT EXISTS idx_deliveries_created ON editorial_deliveries(created_at)",
            "CREATE INDEX IF NOT EXISTS idx_delivery_attempts_delivery "
            "ON delivery_attempts(delivery_id)",
        ):
            cursor.execute(statement)
        self.conn.commit()

    # --- Writes ------------------------------------------------------------

    def create_delivery(
        self,
        *,
        delivery_id: str,
        idempotency_key: str,
        draft_id: str,
        content_hash: str,
        destination: str,
        schema_version: str,
        source_event_id: Optional[str] = None,
        source_revision: Optional[int] = None,
        status: str = S.DELIVERY_PENDING,
        supersedes_delivery_id: Optional[str] = None,
    ) -> Optional[DeliveryRecord]:
        """Register a delivery.

        Returns ``None`` when this idempotency key already exists — that is the
        database-level backstop that keeps a retry from creating a second
        logical delivery even if the caller forgets to look first.
        """
        S.validate_delivery_state(status)
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO editorial_deliveries (
                    delivery_id, idempotency_key, draft_id, source_event_id, source_revision,
                    content_hash, destination, status, schema_version, created_at,
                    supersedes_delivery_id
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    delivery_id, idempotency_key, draft_id, source_event_id,
                    int(source_revision) if source_revision is not None else None,
                    content_hash, destination, status, schema_version, _utc_now_iso(),
                    supersedes_delivery_id,
                ),
            )
            self.conn.commit()
            logger.info(
                "[delivery_created] delivery_id=%s draft_id=%s destination=%s status=%s",
                delivery_id, draft_id, destination, status,
            )
            return self.get_by_delivery_id(delivery_id)
        except sqlite3.IntegrityError:
            self.conn.rollback()
            logger.info(
                "[delivery_created] delivery_id=%s ja registrado; reutilizando", delivery_id
            )
            return None

    def update_status(
        self,
        delivery_id: str,
        target_status: str,
        *,
        error: Optional[str] = None,
        error_class: Optional[str] = None,
        http_status: Optional[int] = None,
        remote_draft_id: Optional[str] = None,
        remote_url: Optional[str] = None,
        enforce_transition: bool = True,
    ) -> DeliveryRecord:
        """Move a delivery to a new state, refusing invalid transitions."""
        record = self.get_by_delivery_id(delivery_id)
        if record is None:
            raise KeyError(f"Delivery desconhecida: {delivery_id}")

        if enforce_transition:
            S.assert_transition(record.status, target_status)
        else:
            S.validate_delivery_state(target_status)

        delivered_at = record.delivered_at
        if target_status == S.DELIVERED and not delivered_at:
            delivered_at = _utc_now_iso()

        cursor = self.conn.cursor()
        cursor.execute(
            """
            UPDATE editorial_deliveries
            SET status = ?,
                last_error = ?,
                error_class = ?,
                http_status = ?,
                remote_draft_id = COALESCE(?, remote_draft_id),
                remote_url = COALESCE(?, remote_url),
                delivered_at = ?
            WHERE delivery_id = ?
            """,
            (
                target_status, error, error_class, http_status,
                remote_draft_id, remote_url, delivered_at, delivery_id,
            ),
        )
        self.conn.commit()
        return self.get_by_delivery_id(delivery_id)

    def record_attempt(
        self,
        delivery_id: str,
        *,
        attempt: int,
        status: str,
        error: Optional[str] = None,
        error_class: Optional[str] = None,
        http_status: Optional[int] = None,
        duration_ms: Optional[int] = None,
        started_at: Optional[str] = None,
    ) -> int:
        """Append one attempt. Never overwrites an earlier one."""
        now = _utc_now_iso()
        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT OR IGNORE INTO delivery_attempts (
                delivery_id, attempt, status, error_class, http_status,
                error, duration_ms, started_at, finished_at
            ) VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (
                delivery_id, int(attempt), status, error_class, http_status,
                error, duration_ms, started_at or now, now,
            ),
        )
        cursor.execute(
            """
            UPDATE editorial_deliveries
            SET attempt_count = ?,
                first_attempt_at = COALESCE(first_attempt_at, ?),
                last_attempt_at = ?
            WHERE delivery_id = ?
            """,
            (int(attempt), started_at or now, now, delivery_id),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    # --- Reads -------------------------------------------------------------

    def get_by_delivery_id(self, delivery_id: str) -> Optional[DeliveryRecord]:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM editorial_deliveries WHERE delivery_id = ?", (delivery_id,)
        )
        row = cursor.fetchone()
        return self._row_to_record(row) if row else None

    def get_by_idempotency_key(self, idempotency_key: str) -> Optional[DeliveryRecord]:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM editorial_deliveries WHERE idempotency_key = ?", (idempotency_key,)
        )
        row = cursor.fetchone()
        return self._row_to_record(row) if row else None

    def list_for_draft(self, draft_id: str) -> List[DeliveryRecord]:
        """Every delivery ever recorded for a draft, oldest first."""
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM editorial_deliveries WHERE draft_id = ? ORDER BY id ASC", (draft_id,)
        )
        return [self._row_to_record(row) for row in cursor.fetchall()]

    def latest_delivered_for_draft(self, draft_id: str) -> Optional[DeliveryRecord]:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT * FROM editorial_deliveries
            WHERE draft_id = ? AND status = ?
            ORDER BY id DESC LIMIT 1
            """,
            (draft_id, S.DELIVERED),
        )
        row = cursor.fetchone()
        return self._row_to_record(row) if row else None

    def list_by_status(self, status: str, limit: int = 50) -> List[DeliveryRecord]:
        S.validate_delivery_state(status)
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM editorial_deliveries WHERE status = ? ORDER BY id DESC LIMIT ?",
            (status, int(limit)),
        )
        return [self._row_to_record(row) for row in cursor.fetchall()]

    def list_attempts(self, delivery_id: str) -> List[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM delivery_attempts WHERE delivery_id = ? ORDER BY attempt ASC",
            (delivery_id,),
        )
        return [dict(row) for row in cursor.fetchall()]

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> DeliveryRecord:
        return DeliveryRecord(
            delivery_id=row["delivery_id"],
            draft_id=row["draft_id"],
            idempotency_key=row["idempotency_key"],
            content_hash=row["content_hash"],
            destination=row["destination"],
            status=row["status"],
            schema_version=row["schema_version"],
            source_event_id=row["source_event_id"],
            source_revision=row["source_revision"],
            attempt_count=row["attempt_count"] or 0,
            last_error=row["last_error"],
            error_class=row["error_class"],
            http_status=row["http_status"],
            first_attempt_at=row["first_attempt_at"],
            last_attempt_at=row["last_attempt_at"],
            delivered_at=row["delivered_at"],
            remote_draft_id=row["remote_draft_id"],
            remote_url=row["remote_url"],
            supersedes_delivery_id=row["supersedes_delivery_id"],
            created_at=row["created_at"],
        )

    def close(self) -> None:
        if self.conn:
            self.conn.close()
            self.conn = None

    def __enter__(self) -> "DeliveryStore":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()


__all__ = ["DeliveryRecord", "DeliveryStore"]
