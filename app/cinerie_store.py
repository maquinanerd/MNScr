"""Persistencia da publicacao no Cinerie.

Uma linha por **trabalho editorial** (unica por ``idempotency_key``) e um log
append-only de tentativas. O log de tentativas e o que torna um retry auditavel:
"acabou funcionando" nao e a mesma historia que "falhou duas vezes com 503 e
depois funcionou".

Aditiva e idempotente, na mesma forma de ``app.delivery_store``,
``app.gate_store`` e ``app.factual_store``. Nada de MS-1..MS-5 e lido para
modificacao nem apagado.

O que **nunca** e persistido: API key, header ``Authorization``, corpo do pedido,
texto inedito, evidencias completas. O que se guarda e identidade, desfecho,
codigos e tempo.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .sqlite_utils import connect_sqlite

logger = logging.getLogger(__name__)

#: Estado local da entrega. Distinto do desfecho REMOTO (`payload_outcome`):
#: "o Cinerie respondeu" e "o pipeline terminou" sao coisas diferentes.
STATUS_PENDING: str = "PENDING"
STATUS_IN_PROGRESS: str = "IN_PROGRESS"
STATUS_COMPLETED: str = "COMPLETED"
STATUS_BLOCKED: str = "BLOCKED"
STATUS_AWAITING_HUMAN: str = "AWAITING_HUMAN"
STATUS_NEEDS_RECONCILIATION: str = "NEEDS_RECONCILIATION"
STATUS_FAILED_RETRYABLE: str = "FAILED_RETRYABLE"
STATUS_FAILED_PERMANENT: str = "FAILED_PERMANENT"

#: Teto diario da redacao esgotado. Estado proprio, e nao um sabor de falha:
#: o pedido esta correto, nada foi persistido do outro lado, e o mesmo corpo
#: passa depois de `next_eligible_at` sem uma linha alterada. Guardado a parte
#: para que um relatorio saiba distinguir "a materia nao entrou porque tem
#: defeito" de "a materia nao entrou porque a redacao ja publicou o teto de
#: hoje" — as duas frases pedem providencias opostas.
STATUS_DEFERRED: str = "DEFERRED"

DELIVERY_STATUSES = frozenset(
    {
        STATUS_PENDING,
        STATUS_IN_PROGRESS,
        STATUS_COMPLETED,
        STATUS_BLOCKED,
        STATUS_AWAITING_HUMAN,
        STATUS_DEFERRED,
        STATUS_NEEDS_RECONCILIATION,
        STATUS_FAILED_RETRYABLE,
        STATUS_FAILED_PERMANENT,
    }
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _iso_from_now(seconds: int) -> str:
    """Instante futuro em ISO, comparavel como TEXTO no SQLite.

    Todos os carimbos desta tabela sao ISO-8601 em UTC, entao a ordem
    lexicografica coincide com a cronologica e o `<=` do SQL vale.
    """
    return (datetime.now(timezone.utc) + timedelta(seconds=max(0, int(seconds)))).isoformat()


def _dumps(value: Any) -> Optional[str]:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _loads(value: Any) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return None


@dataclass
class PublicationRecord:
    """Uma publicacao pedida ao Cinerie."""

    request_id: str
    idempotency_key: str
    draft_id: str
    source_cluster_id: str
    source_revision: int
    delivery_mode: str
    contract_name: str
    contract_version: str
    schema_hash: str
    public_author_id: str
    publication_intent: str
    delivery_status: str
    payload_outcome: Optional[str] = None
    payload_article_id: Optional[str] = None
    canonical_slug: Optional[str] = None
    reason_codes: Optional[List[str]] = None
    next_eligible_at: Optional[str] = None
    delivered_at: Optional[str] = None
    retry_count: int = 0
    last_delivery_error_code: Optional[str] = None
    http_status: Optional[int] = None
    idempotent_replay: bool = False
    created_at: str = ""
    updated_at: str = ""

    @property
    def is_published(self) -> bool:
        return self.payload_outcome == "PUBLISHED"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "request_id": self.request_id,
            "idempotency_key": self.idempotency_key,
            "draft_id": self.draft_id,
            "source_cluster_id": self.source_cluster_id,
            "source_revision": self.source_revision,
            "delivery_mode": self.delivery_mode,
            "contract_name": self.contract_name,
            "contract_version": self.contract_version,
            # Hash truncado tambem aqui: o valor inteiro nao acrescenta nada num
            # relatorio e ocupa a linha.
            "schema_hash": self.schema_hash[:19],
            "public_author_id": self.public_author_id,
            "publication_intent": self.publication_intent,
            "delivery_status": self.delivery_status,
            "payload_outcome": self.payload_outcome,
            "payload_article_id": self.payload_article_id,
            "canonical_slug": self.canonical_slug,
            "reason_codes": list(self.reason_codes or []),
            "next_eligible_at": self.next_eligible_at,
            "delivered_at": self.delivered_at,
            "retry_count": self.retry_count,
            "last_delivery_error_code": self.last_delivery_error_code,
            "http_status": self.http_status,
            "idempotent_replay": self.idempotent_replay,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class CinerieStore:
    """Duas tabelas: publicacoes e tentativas."""

    def __init__(self, db_path: str = "data/app.db"):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = connect_sqlite(db_path)
        self.conn.row_factory = sqlite3.Row
        self._ensure_schema()

    # -- schema ------------------------------------------------------------

    def _ensure_schema(self) -> None:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS cinerie_publications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id TEXT NOT NULL UNIQUE,
                idempotency_key TEXT NOT NULL UNIQUE,
                draft_id TEXT NOT NULL,
                source_cluster_id TEXT NOT NULL,
                source_revision INTEGER NOT NULL,
                delivery_mode TEXT NOT NULL,
                contract_name TEXT NOT NULL,
                contract_version TEXT NOT NULL,
                schema_hash TEXT NOT NULL,
                public_author_id TEXT NOT NULL,
                publication_intent TEXT NOT NULL,
                delivery_status TEXT NOT NULL,
                payload_outcome TEXT,
                payload_article_id TEXT,
                canonical_slug TEXT,
                reason_codes TEXT,
                next_eligible_at TEXT,
                delivered_at TEXT,
                retry_count INTEGER NOT NULL DEFAULT 0,
                last_delivery_error_code TEXT,
                http_status INTEGER,
                idempotent_replay INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                claimed_by TEXT,
                lease_until TEXT
            )
            """
        )
        # Bancos criados antes da posse existir nao tem as duas colunas. ALTER
        # em vez de recriar a tabela: ha entrega em voo la dentro.
        existentes = {
            row["name"]
            for row in cursor.execute("PRAGMA table_info(cinerie_publications)")
        }
        for coluna in ("claimed_by", "lease_until"):
            if coluna not in existentes:
                cursor.execute(
                    f"ALTER TABLE cinerie_publications ADD COLUMN {coluna} TEXT"
                )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_cinerie_publications_claim "
            "ON cinerie_publications(delivery_status, lease_until)"
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS cinerie_publication_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id TEXT NOT NULL,
                attempt INTEGER NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                duration_ms INTEGER,
                http_status INTEGER,
                outcome TEXT,
                error_code TEXT,
                error_message TEXT,
                retry_after_seconds REAL,
                UNIQUE(request_id, attempt)
            )
            """
        )
        for statement in (
            "CREATE INDEX IF NOT EXISTS idx_cinerie_pub_draft ON cinerie_publications(draft_id)",
            "CREATE INDEX IF NOT EXISTS idx_cinerie_pub_cluster ON cinerie_publications(source_cluster_id)",
            "CREATE INDEX IF NOT EXISTS idx_cinerie_pub_status ON cinerie_publications(delivery_status)",
            "CREATE INDEX IF NOT EXISTS idx_cinerie_pub_outcome ON cinerie_publications(payload_outcome)",
            "CREATE INDEX IF NOT EXISTS idx_cinerie_attempt_request ON cinerie_publication_attempts(request_id)",
        ):
            cursor.execute(statement)
        self.conn.commit()

    # -- escrita -----------------------------------------------------------

    def create_publication(
        self,
        *,
        request_id: str,
        idempotency_key: str,
        draft_id: str,
        source_cluster_id: str,
        source_revision: int,
        delivery_mode: str,
        contract_name: str,
        contract_version: str,
        schema_hash: str,
        public_author_id: str,
        publication_intent: str,
        delivery_status: str = STATUS_PENDING,
    ) -> Optional[PublicationRecord]:
        """Cria o registro, ou ``None`` quando a chave ja existe.

        ``None`` nao e erro: e a idempotencia funcionando. O chamador le o
        registro existente em vez de criar um segundo.
        """
        now = _utc_now_iso()
        try:
            self.conn.execute(
                """
                INSERT INTO cinerie_publications (
                    request_id, idempotency_key, draft_id, source_cluster_id,
                    source_revision, delivery_mode, contract_name, contract_version,
                    schema_hash, public_author_id, publication_intent,
                    delivery_status, created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    request_id,
                    idempotency_key,
                    draft_id,
                    source_cluster_id,
                    int(source_revision),
                    delivery_mode,
                    contract_name,
                    contract_version,
                    schema_hash,
                    public_author_id,
                    publication_intent,
                    delivery_status,
                    now,
                    now,
                ),
            )
            self.conn.commit()
        except sqlite3.IntegrityError:
            self.conn.rollback()
            return None
        return self.get_by_request_id(request_id)

    def update_result(
        self,
        request_id: str,
        *,
        delivery_status: str,
        payload_outcome: Optional[str] = None,
        payload_article_id: Optional[str] = None,
        canonical_slug: Optional[str] = None,
        reason_codes: Optional[List[str]] = None,
        next_eligible_at: Optional[str] = None,
        http_status: Optional[int] = None,
        error_code: Optional[str] = None,
        retry_count: Optional[int] = None,
        idempotent_replay: Optional[bool] = None,
        delivered: bool = False,
    ) -> Optional[PublicationRecord]:
        if delivery_status not in DELIVERY_STATUSES:
            raise ValueError(f"delivery_status desconhecido: {delivery_status!r}")

        now = _utc_now_iso()
        fields: List[str] = ["delivery_status = ?", "updated_at = ?"]
        values: List[Any] = [delivery_status, now]

        for column, value in (
            ("payload_outcome", payload_outcome),
            ("payload_article_id", payload_article_id),
            ("canonical_slug", canonical_slug),
            ("next_eligible_at", next_eligible_at),
            ("http_status", http_status),
            ("last_delivery_error_code", error_code),
        ):
            if value is not None:
                fields.append(f"{column} = ?")
                values.append(value)

        if reason_codes is not None:
            fields.append("reason_codes = ?")
            values.append(_dumps(list(reason_codes)))
        if retry_count is not None:
            fields.append("retry_count = ?")
            values.append(int(retry_count))
        if idempotent_replay is not None:
            fields.append("idempotent_replay = ?")
            values.append(1 if idempotent_replay else 0)
        if delivered:
            fields.append("delivered_at = ?")
            values.append(now)

        values.append(request_id)
        self.conn.execute(
            f"UPDATE cinerie_publications SET {', '.join(fields)} WHERE request_id = ?",
            values,
        )
        self.conn.commit()
        return self.get_by_request_id(request_id)

    def record_attempt(
        self,
        *,
        request_id: str,
        attempt: int,
        started_at: str,
        duration_ms: Optional[int] = None,
        http_status: Optional[int] = None,
        outcome: Optional[str] = None,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
        retry_after_seconds: Optional[float] = None,
    ) -> None:
        """Append-only. Uma tentativa registrada nunca e reescrita."""
        try:
            self.conn.execute(
                """
                INSERT INTO cinerie_publication_attempts (
                    request_id, attempt, started_at, finished_at, duration_ms,
                    http_status, outcome, error_code, error_message, retry_after_seconds
                ) VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    request_id,
                    int(attempt),
                    started_at,
                    _utc_now_iso(),
                    duration_ms,
                    http_status,
                    outcome,
                    error_code,
                    # Mensagem TRUNCADA: o suficiente para diagnosticar, curto o
                    # bastante para nao virar deposito de texto.
                    (error_message or "")[:300] or None,
                    retry_after_seconds,
                ),
            )
            self.conn.commit()
        except sqlite3.IntegrityError:
            self.conn.rollback()

    # -- leitura -----------------------------------------------------------

    def get_by_request_id(self, request_id: str) -> Optional[PublicationRecord]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM cinerie_publications WHERE request_id = ?", (request_id,))
        row = cursor.fetchone()
        return self._row_to_record(row) if row else None

    def get_by_idempotency_key(self, idempotency_key: str) -> Optional[PublicationRecord]:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM cinerie_publications WHERE idempotency_key = ?", (idempotency_key,)
        )
        row = cursor.fetchone()
        return self._row_to_record(row) if row else None

    def list_for_draft(self, draft_id: str) -> List[PublicationRecord]:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM cinerie_publications WHERE draft_id = ? ORDER BY id", (draft_id,)
        )
        return [self._row_to_record(row) for row in cursor.fetchall()]

    def list_for_cluster(self, source_cluster_id: str) -> List[PublicationRecord]:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM cinerie_publications WHERE source_cluster_id = ? ORDER BY source_revision",
            (source_cluster_id,),
        )
        return [self._row_to_record(row) for row in cursor.fetchall()]

    def highest_applied_revision(self, source_cluster_id: str) -> Optional[int]:
        """Maior revisao ja aceita pelo Cinerie neste agrupamento.

        Usada na reconciliacao de ``CONFLICT``: uma revisao menor ou igual a
        essa e um pedido que chegou fora de ordem, nao uma atualizacao.
        """
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT MAX(source_revision) AS top FROM cinerie_publications
            WHERE source_cluster_id = ? AND payload_outcome IN ('PUBLISHED', 'ROUTED_TO_REVIEW')
            """,
            (source_cluster_id,),
        )
        row = cursor.fetchone()
        return int(row["top"]) if row and row["top"] is not None else None

    def published_article_for_cluster(self, source_cluster_id: str) -> Optional[str]:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT payload_article_id FROM cinerie_publications
            WHERE source_cluster_id = ? AND payload_article_id IS NOT NULL
            ORDER BY source_revision DESC LIMIT 1
            """,
            (source_cluster_id,),
        )
        row = cursor.fetchone()
        return str(row["payload_article_id"]) if row and row["payload_article_id"] else None

    #: Estados que voltam para a varredura. `COMPLETED`, `BLOCKED`,
    #: `FAILED_PERMANENT` e `AWAITING_HUMAN` ficam de fora de proposito: os dois
    #: primeiros ja terminaram, o terceiro nao melhora com repeticao e o quarto
    #: espera gente, nao maquina.
    CLAIMABLE_STATUSES: tuple = (
        STATUS_PENDING,
        STATUS_FAILED_RETRYABLE,
        STATUS_DEFERRED,
        STATUS_NEEDS_RECONCILIATION,
        STATUS_IN_PROGRESS,
    )

    def claim_pending(
        self,
        *,
        worker_id: str,
        limit: int = 10,
        lease_seconds: int = 300,
    ) -> List[PublicationRecord]:
        """Toma posse de entregas reivindicaveis. Escrita condicional, nao leitura.

        Ler o que esta pendente e depois enviar nao serve quando ha mais de um
        processo: dois workers leem a mesma linha e mandam o mesmo pedido. Do
        outro lado a chave idempotente evita artigo duplicado, mas o teto diario
        e consumido POR CHAVE — entao a dupla entrega custa COTA, e cota
        esgotada so volta a meia-noite do fuso da redacao.

        A posse e tomada por um UPDATE condicional. No SQLite a escrita e
        serializada, entao dois workers concorrentes nao conseguem casar a mesma
        condicao: quem perder a corrida simplesmente nao vera a linha.

        Regras que o UPDATE codifica:

        - `IN_PROGRESS` so volta com o lease VENCIDO. E como um worker morto
          devolve o trabalho sem ninguem intervir na mao.
        - `DEFERRED` so volta depois de `next_eligible_at`. Antes disso ele nao
          e trabalho: reenviar produz outro 429 e nada mais.
        - `retry_count` NAO e tocado aqui. Tomar posse nao e tentar; quem conta
          tentativa e quem envia. Sem isso um `DEFERRED` seria aposentado por
          excesso de tentativas antes de a cota sequer virar.
        - `delivery_status` tambem NAO e tocado. Posse e estado de entrega sao
          coisas diferentes: o estado diz o que aconteceu com o pedido, a posse
          diz quem esta cuidando dele agora. Sobrescrever o estado com
          `IN_PROGRESS` ao reivindicar apagava justamente o motivo pelo qual o
          item precisava de atencao — um `DEFERRED` devolvido deixava de se saber
          adiado, e um `NEEDS_RECONCILIATION` deixava de pedir reconciliacao.
          Quem marca `IN_PROGRESS` e quem de fato envia.
        """
        agora = _utc_now_iso()
        # Marca unica desta reivindicacao: e ela que permite reler exatamente as
        # linhas que ESTE claim tomou, sem correr atras de rowid.
        marca = f"{worker_id}#{uuid.uuid4().hex}"
        vencimento = _iso_from_now(lease_seconds)

        with self.conn:
            self.conn.execute(
                f"""
                UPDATE cinerie_publications
                   SET claimed_by = ?, lease_until = ?, updated_at = ?
                 WHERE id IN (
                       SELECT id FROM cinerie_publications
                        WHERE delivery_status IN ({",".join("?" * len(self.CLAIMABLE_STATUSES))})
                          AND (delivery_status != ? OR lease_until IS NULL OR lease_until <= ?)
                          AND (delivery_status != ? OR next_eligible_at IS NULL OR next_eligible_at <= ?)
                          AND (claimed_by IS NULL OR lease_until IS NULL OR lease_until <= ?)
                        ORDER BY created_at
                        LIMIT ?
                 )
                """,
                (
                    marca, vencimento, agora,
                    *self.CLAIMABLE_STATUSES,
                    STATUS_IN_PROGRESS, agora,
                    STATUS_DEFERRED, agora,
                    agora,
                    int(limit),
                ),
            )

        rows = self.conn.execute(
            "SELECT * FROM cinerie_publications WHERE claimed_by = ? ORDER BY created_at",
            (marca,),
        ).fetchall()
        registros = [self._row_to_record(row) for row in rows]
        if registros:
            logger.info(
                "[CINERIE_CLAIMED] worker=%s tomadas=%s lease_until=%s",
                worker_id, len(registros), vencimento,
            )
        return registros

    def release_claim(self, request_id: str) -> None:
        """Devolve a posse sem alterar o estado da entrega."""
        with self.conn:
            self.conn.execute(
                "UPDATE cinerie_publications SET claimed_by = NULL, lease_until = NULL, "
                "updated_at = ? WHERE request_id = ?",
                (_utc_now_iso(), request_id),
            )

    def list_by_status(self, status: str, limit: int = 50) -> List[PublicationRecord]:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM cinerie_publications WHERE delivery_status = ? ORDER BY id DESC LIMIT ?",
            (status, int(limit)),
        )
        return [self._row_to_record(row) for row in cursor.fetchall()]

    def list_attempts(self, request_id: str) -> List[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM cinerie_publication_attempts WHERE request_id = ? ORDER BY attempt",
            (request_id,),
        )
        return [dict(row) for row in cursor.fetchall()]

    # -- infra -------------------------------------------------------------

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> PublicationRecord:
        return PublicationRecord(
            request_id=row["request_id"],
            idempotency_key=row["idempotency_key"],
            draft_id=row["draft_id"],
            source_cluster_id=row["source_cluster_id"],
            source_revision=int(row["source_revision"]),
            delivery_mode=row["delivery_mode"],
            contract_name=row["contract_name"],
            contract_version=row["contract_version"],
            schema_hash=row["schema_hash"],
            public_author_id=row["public_author_id"],
            publication_intent=row["publication_intent"],
            delivery_status=row["delivery_status"],
            payload_outcome=row["payload_outcome"],
            payload_article_id=row["payload_article_id"],
            canonical_slug=row["canonical_slug"],
            reason_codes=_loads(row["reason_codes"]) or [],
            next_eligible_at=row["next_eligible_at"],
            delivered_at=row["delivered_at"],
            retry_count=int(row["retry_count"] or 0),
            last_delivery_error_code=row["last_delivery_error_code"],
            http_status=row["http_status"],
            idempotent_replay=bool(row["idempotent_replay"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:  # pragma: no cover - defensivo
            logger.debug("[cinerie] falha ao fechar conexao", exc_info=True)

    def __enter__(self) -> "CinerieStore":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()


__all__ = [
    "CinerieStore",
    "DELIVERY_STATUSES",
    "PublicationRecord",
    "STATUS_AWAITING_HUMAN",
    "STATUS_BLOCKED",
    "STATUS_COMPLETED",
    "STATUS_DEFERRED",
    "STATUS_FAILED_PERMANENT",
    "STATUS_FAILED_RETRYABLE",
    "STATUS_IN_PROGRESS",
    "STATUS_NEEDS_RECONCILIATION",
    "STATUS_PENDING",
]
