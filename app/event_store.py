"""Persistence for received RSS Prime events, cursors and replay attempts.

Lives beside ``app.store`` rather than inside it: the operational article store
knows about articles, this one knows about *deliveries*. It shares the same
SQLite file, so an event and its cursor advance in one transaction.

Nothing here interprets XML namespaces — it receives already-normalized domain
objects. Nothing here writes to the Screen-App or Payload databases.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .contracts import states as S
from .contracts.revisions import StoredRevision
from .contracts.rssprime_event_v1 import RssPrimeEventV1
from .sqlite_utils import connect_sqlite

logger = logging.getLogger(__name__)

CURSOR_MODE_UNAVAILABLE = "unavailable"
CURSOR_MODE_ACTIVE = "active"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dumps(value: Any) -> Optional[str]:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass
class StoredEvent:
    """One persisted delivery of one revision."""

    id: int
    event_key: str
    revision: int
    contract_version: str
    payload_hash: str
    payload_hash_origin: str
    topic: Optional[str]
    cursor: Optional[str]
    first_seen: Optional[str]
    last_seen: Optional[str]
    received_at: str
    validation_status: str
    processing_status: str
    validation_errors: List[Dict[str, Any]]
    warnings: List[Dict[str, Any]]
    normalized_payload: Optional[Dict[str, Any]] = None
    raw_payload: Optional[Dict[str, Any]] = None
    draft_id: Optional[str] = None
    draft_output_hash: Optional[str] = None

    def to_event(self) -> RssPrimeEventV1:
        """Rebuild the domain object from what was stored. Used by replay."""
        if not self.normalized_payload:
            raise ValueError(
                f"Evento {self.event_key} rev {self.revision} nao tem payload normalizado persistido."
            )
        event = RssPrimeEventV1.from_mapping(self.normalized_payload)
        event.payload_hash = self.payload_hash
        event.payload_hash_origin = self.payload_hash_origin
        return event


class EventStore:
    """SQLite persistence for the ingestion contract layer."""

    def __init__(self, db_path: str = "data/app.db"):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        self.conn = connect_sqlite(db_path, row_factory=sqlite3.Row)
        self._ensure_schema()

    # --- Schema ------------------------------------------------------------

    def _ensure_schema(self) -> None:
        """Additive and idempotent. Never drops or rewrites existing tables."""
        cursor = self.conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS rssprime_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_key TEXT NOT NULL,
                revision INTEGER NOT NULL,
                contract_version TEXT NOT NULL,
                payload_hash TEXT NOT NULL,
                payload_hash_origin TEXT,
                topic TEXT,
                cursor TEXT,
                first_seen TEXT,
                last_seen TEXT,
                received_at TEXT NOT NULL,
                validation_status TEXT NOT NULL,
                validation_errors_json TEXT,
                warnings_json TEXT,
                raw_payload_json TEXT,
                normalized_payload_json TEXT,
                processing_status TEXT,
                draft_id TEXT,
                draft_output_hash TEXT,
                UNIQUE(event_key, revision)
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS ingestion_cursors (
                source_id TEXT NOT NULL,
                contract_version TEXT NOT NULL,
                cursor TEXT,
                cursor_mode TEXT DEFAULT 'active',
                updated_at TEXT,
                last_event_key TEXT,
                last_revision INTEGER,
                PRIMARY KEY (source_id, contract_version)
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS event_processing_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_key TEXT NOT NULL,
                revision INTEGER NOT NULL,
                attempt_kind TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                draft_id TEXT,
                draft_output_hash TEXT,
                error TEXT
            )
            """
        )

        # Additive column migrations for databases created by an earlier build.
        self._add_missing_columns(
            "rssprime_events",
            {
                "payload_hash_origin": "ALTER TABLE rssprime_events ADD COLUMN payload_hash_origin TEXT",
                "draft_id": "ALTER TABLE rssprime_events ADD COLUMN draft_id TEXT",
                "draft_output_hash": "ALTER TABLE rssprime_events ADD COLUMN draft_output_hash TEXT",
                "warnings_json": "ALTER TABLE rssprime_events ADD COLUMN warnings_json TEXT",
            },
        )
        self._add_missing_columns(
            "ingestion_cursors",
            {
                "cursor_mode": "ALTER TABLE ingestion_cursors ADD COLUMN cursor_mode TEXT DEFAULT 'active'",
            },
        )

        for statement in (
            "CREATE INDEX IF NOT EXISTS idx_rssprime_events_event_key ON rssprime_events(event_key)",
            "CREATE INDEX IF NOT EXISTS idx_rssprime_events_payload_hash ON rssprime_events(payload_hash)",
            "CREATE INDEX IF NOT EXISTS idx_rssprime_events_cursor ON rssprime_events(cursor)",
            "CREATE INDEX IF NOT EXISTS idx_rssprime_events_processing_status "
            "ON rssprime_events(processing_status)",
            "CREATE INDEX IF NOT EXISTS idx_rssprime_events_received_at ON rssprime_events(received_at)",
            "CREATE INDEX IF NOT EXISTS idx_event_attempts_event ON event_processing_attempts(event_key, revision)",
        ):
            cursor.execute(statement)
        self.conn.commit()

    def _add_missing_columns(self, table: str, migrations: Dict[str, str]) -> None:
        cursor = self.conn.cursor()
        cursor.execute(f"PRAGMA table_info({table})")
        existing = {row["name"] for row in cursor.fetchall()}
        for column, statement in migrations.items():
            if column not in existing:
                logger.info("Applying SQLite migration: %s.%s", table, column)
                cursor.execute(statement)

    # --- Reads -------------------------------------------------------------

    def get_latest_revision(self, event_key: str) -> Optional[StoredRevision]:
        """Highest revision on record, whatever its validation status."""
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT event_key, revision, payload_hash, processing_status, draft_id
            FROM rssprime_events
            WHERE event_key = ?
            ORDER BY revision DESC
            LIMIT 1
            """,
            (event_key,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return StoredRevision(
            event_key=row["event_key"],
            revision=int(row["revision"]),
            payload_hash=row["payload_hash"] or "",
            processing_status=row["processing_status"],
            draft_id=row["draft_id"],
        )

    def get_revision_hash(self, event_key: str, revision: int) -> Optional[str]:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT payload_hash FROM rssprime_events WHERE event_key = ? AND revision = ?",
            (event_key, int(revision)),
        )
        row = cursor.fetchone()
        return row["payload_hash"] if row else None

    def get_event(self, event_key: str, revision: Optional[int] = None) -> Optional[StoredEvent]:
        """A stored delivery. Without ``revision``, the highest one."""
        cursor = self.conn.cursor()
        if revision is None:
            cursor.execute(
                "SELECT * FROM rssprime_events WHERE event_key = ? ORDER BY revision DESC LIMIT 1",
                (event_key,),
            )
        else:
            cursor.execute(
                "SELECT * FROM rssprime_events WHERE event_key = ? AND revision = ?",
                (event_key, int(revision)),
            )
        row = cursor.fetchone()
        return self._row_to_event(row) if row else None

    def list_event_revisions(self, event_key: str) -> List[StoredEvent]:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM rssprime_events WHERE event_key = ? ORDER BY revision ASC",
            (event_key,),
        )
        return [self._row_to_event(row) for row in cursor.fetchall()]

    def list_attempts(self, event_key: str, revision: Optional[int] = None) -> List[Dict[str, Any]]:
        cursor = self.conn.cursor()
        if revision is None:
            cursor.execute(
                "SELECT * FROM event_processing_attempts WHERE event_key = ? ORDER BY id ASC",
                (event_key,),
            )
        else:
            cursor.execute(
                "SELECT * FROM event_processing_attempts WHERE event_key = ? AND revision = ? ORDER BY id ASC",
                (event_key, int(revision)),
            )
        return [dict(row) for row in cursor.fetchall()]

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> StoredEvent:
        def _loads(value: Any, fallback: Any) -> Any:
            if not value:
                return fallback
            try:
                return json.loads(value)
            except (TypeError, ValueError):
                return fallback

        return StoredEvent(
            id=int(row["id"]),
            event_key=row["event_key"],
            revision=int(row["revision"]),
            contract_version=row["contract_version"],
            payload_hash=row["payload_hash"] or "",
            payload_hash_origin=row["payload_hash_origin"] or "",
            topic=row["topic"],
            cursor=row["cursor"],
            first_seen=row["first_seen"],
            last_seen=row["last_seen"],
            received_at=row["received_at"],
            validation_status=row["validation_status"],
            processing_status=row["processing_status"] or S.RECEIVED,
            validation_errors=_loads(row["validation_errors_json"], []),
            warnings=_loads(row["warnings_json"], []),
            normalized_payload=_loads(row["normalized_payload_json"], None),
            raw_payload=_loads(row["raw_payload_json"], None),
            draft_id=row["draft_id"],
            draft_output_hash=row["draft_output_hash"],
        )

    # --- Writes ------------------------------------------------------------

    def record_event(
        self,
        event: RssPrimeEventV1,
        *,
        validation_status: str,
        validation_errors: Optional[List[Dict[str, Any]]] = None,
        warnings: Optional[List[Dict[str, Any]]] = None,
        raw_payload: Optional[Dict[str, Any]] = None,
        processing_status: str = S.RECEIVED,
        store_raw_payload: bool = True,
    ) -> Optional[int]:
        """Persist one delivery.

        Returns the row id, or ``None`` when ``(event_key, revision)`` is already
        stored — the UNIQUE constraint is the idempotency backstop, so a repeated
        delivery can never create a second row.
        """
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO rssprime_events (
                    event_key, revision, contract_version, payload_hash, payload_hash_origin,
                    topic, cursor, first_seen, last_seen, received_at,
                    validation_status, validation_errors_json, warnings_json,
                    raw_payload_json, normalized_payload_json, processing_status
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    event.event_key,
                    int(event.revision),
                    event.contract_version,
                    event.payload_hash,
                    event.payload_hash_origin,
                    event.topic,
                    event.cursor,
                    event.first_seen,
                    event.last_seen,
                    _utc_now_iso(),
                    validation_status,
                    _dumps(validation_errors or []),
                    _dumps(warnings or []),
                    _dumps(raw_payload) if (store_raw_payload and raw_payload is not None) else None,
                    _dumps(event.to_hashable_dict()),
                    processing_status,
                ),
            )
            self.conn.commit()
            logger.info(
                "[EVENT_RECEIVED] event_key=%s revision=%s contract=%s payload_hash=%s "
                "status=%s cursor=%s",
                event.event_key,
                event.revision,
                event.contract_version,
                event.payload_hash[:12],
                validation_status,
                "present" if event.cursor else "absent",
            )
            return int(cursor.lastrowid)
        except sqlite3.IntegrityError:
            self.conn.rollback()
            logger.info(
                "[EVENT_DUPLICATE] event_key=%s revision=%s ja persistido",
                event.event_key,
                event.revision,
            )
            return None

    def set_processing_status(
        self,
        event_key: str,
        revision: int,
        status: str,
        *,
        draft_id: Optional[str] = None,
        draft_output_hash: Optional[str] = None,
    ) -> bool:
        S.validate_processing_status(status)
        cursor = self.conn.cursor()
        cursor.execute(
            """
            UPDATE rssprime_events
            SET processing_status = ?,
                draft_id = COALESCE(?, draft_id),
                draft_output_hash = COALESCE(?, draft_output_hash)
            WHERE event_key = ? AND revision = ?
            """,
            (status, draft_id, draft_output_hash, event_key, int(revision)),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def record_attempt(
        self,
        event_key: str,
        revision: int,
        *,
        attempt_kind: str,
        status: str,
        started_at: Optional[str] = None,
        finished_at: Optional[str] = None,
        draft_id: Optional[str] = None,
        draft_output_hash: Optional[str] = None,
        error: Optional[str] = None,
    ) -> int:
        """Append to the attempt history. Never overwrites a previous attempt."""
        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO event_processing_attempts (
                event_key, revision, attempt_kind, status,
                started_at, finished_at, draft_id, draft_output_hash, error
            ) VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (
                event_key,
                int(revision),
                attempt_kind,
                status,
                started_at or _utc_now_iso(),
                finished_at,
                draft_id,
                draft_output_hash,
                error,
            ),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    # --- Cursor ------------------------------------------------------------

    def get_cursor(self, source_id: str, contract_version: str) -> Optional[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM ingestion_cursors WHERE source_id = ? AND contract_version = ?",
            (source_id, contract_version),
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def advance_cursor(
        self,
        *,
        source_id: str,
        contract_version: str,
        cursor_value: Optional[str],
        last_event_key: Optional[str] = None,
        last_revision: Optional[int] = None,
    ) -> bool:
        """Move the cursor forward.

        Callers must only reach this after the event was validated *and*
        persisted. The cursor value is opaque: it is stored and returned as-is,
        never parsed, compared as a date or ordered.
        """
        if cursor_value is None:
            logger.info(
                "[CURSOR_NOT_ADVANCED] source_id=%s reason=no_cursor_in_payload", source_id
            )
            return False
        conn_cursor = self.conn.cursor()
        conn_cursor.execute(
            """
            INSERT INTO ingestion_cursors (
                source_id, contract_version, cursor, cursor_mode,
                updated_at, last_event_key, last_revision
            ) VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(source_id, contract_version) DO UPDATE SET
                cursor = excluded.cursor,
                cursor_mode = excluded.cursor_mode,
                updated_at = excluded.updated_at,
                last_event_key = excluded.last_event_key,
                last_revision = excluded.last_revision
            """,
            (
                source_id,
                contract_version,
                cursor_value,
                CURSOR_MODE_ACTIVE,
                _utc_now_iso(),
                last_event_key,
                int(last_revision) if last_revision is not None else None,
            ),
        )
        self.conn.commit()
        logger.info(
            "[CURSOR_ADVANCED] source_id=%s contract=%s last_event_key=%s last_revision=%s",
            source_id,
            contract_version,
            last_event_key or "-",
            last_revision,
        )
        return True

    def mark_cursor_unavailable(self, source_id: str, contract_version: str) -> None:
        """Record that this source simply has no cursor (the legacy feed)."""
        conn_cursor = self.conn.cursor()
        conn_cursor.execute(
            """
            INSERT INTO ingestion_cursors (
                source_id, contract_version, cursor, cursor_mode, updated_at
            ) VALUES (?,?,?,?,?)
            ON CONFLICT(source_id, contract_version) DO UPDATE SET
                cursor_mode = excluded.cursor_mode,
                updated_at = excluded.updated_at
            """,
            (source_id, contract_version, None, CURSOR_MODE_UNAVAILABLE, _utc_now_iso()),
        )
        self.conn.commit()

    # --- Transactional ingest ---------------------------------------------

    def persist_event_and_advance_cursor(
        self,
        event: RssPrimeEventV1,
        *,
        source_id: str,
        validation_status: str,
        validation_errors: Optional[List[Dict[str, Any]]] = None,
        warnings: Optional[List[Dict[str, Any]]] = None,
        raw_payload: Optional[Dict[str, Any]] = None,
        store_raw_payload: bool = True,
    ) -> Optional[int]:
        """Persist the event and, only if that succeeded, move the cursor.

        Both writes happen in one transaction: a failure anywhere rolls back the
        event *and* the cursor, so the cursor can never run ahead of what was
        actually stored.
        """
        if validation_status != S.VALIDATED:
            logger.info(
                "[CURSOR_NOT_ADVANCED] source_id=%s event_key=%s reason=%s",
                source_id, event.event_key, validation_status,
            )
            return self.record_event(
                event,
                validation_status=validation_status,
                validation_errors=validation_errors,
                warnings=warnings,
                raw_payload=raw_payload,
                store_raw_payload=store_raw_payload,
            )

        cursor = self.conn.cursor()
        try:
            cursor.execute("BEGIN")
            cursor.execute(
                """
                INSERT INTO rssprime_events (
                    event_key, revision, contract_version, payload_hash, payload_hash_origin,
                    topic, cursor, first_seen, last_seen, received_at,
                    validation_status, validation_errors_json, warnings_json,
                    raw_payload_json, normalized_payload_json, processing_status
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    event.event_key,
                    int(event.revision),
                    event.contract_version,
                    event.payload_hash,
                    event.payload_hash_origin,
                    event.topic,
                    event.cursor,
                    event.first_seen,
                    event.last_seen,
                    _utc_now_iso(),
                    validation_status,
                    _dumps(validation_errors or []),
                    _dumps(warnings or []),
                    _dumps(raw_payload) if (store_raw_payload and raw_payload is not None) else None,
                    _dumps(event.to_hashable_dict()),
                    S.QUEUED,
                ),
            )
            row_id = int(cursor.lastrowid)

            if event.cursor:
                cursor.execute(
                    """
                    INSERT INTO ingestion_cursors (
                        source_id, contract_version, cursor, cursor_mode,
                        updated_at, last_event_key, last_revision
                    ) VALUES (?,?,?,?,?,?,?)
                    ON CONFLICT(source_id, contract_version) DO UPDATE SET
                        cursor = excluded.cursor,
                        cursor_mode = excluded.cursor_mode,
                        updated_at = excluded.updated_at,
                        last_event_key = excluded.last_event_key,
                        last_revision = excluded.last_revision
                    """,
                    (
                        source_id,
                        event.contract_version,
                        event.cursor,
                        CURSOR_MODE_ACTIVE,
                        _utc_now_iso(),
                        event.event_key,
                        int(event.revision),
                    ),
                )
            self.conn.commit()
        except sqlite3.IntegrityError:
            self.conn.rollback()
            logger.info(
                "[EVENT_DUPLICATE] event_key=%s revision=%s ja persistido; cursor nao avancou",
                event.event_key, event.revision,
            )
            return None
        except sqlite3.Error:
            self.conn.rollback()
            logger.exception(
                "[CURSOR_NOT_ADVANCED] event_key=%s reason=transaction_failed", event.event_key
            )
            raise

        if event.cursor:
            logger.info(
                "[CURSOR_ADVANCED] source_id=%s contract=%s event_key=%s revision=%s",
                source_id, event.contract_version, event.event_key, event.revision,
            )
        else:
            logger.info(
                "[CURSOR_NOT_ADVANCED] source_id=%s event_key=%s reason=no_cursor_in_payload",
                source_id, event.event_key,
            )
        return row_id

    def close(self) -> None:
        if self.conn:
            self.conn.close()
            self.conn = None

    def __enter__(self) -> "EventStore":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()


__all__ = [
    "CURSOR_MODE_ACTIVE",
    "CURSOR_MODE_UNAVAILABLE",
    "EventStore",
    "StoredEvent",
]
