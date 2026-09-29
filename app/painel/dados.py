"""O que o painel mostra, lido do banco do robô SOMENTE em modo leitura.

A conexão abre com `mode=ro` e `query_only`: nenhuma consulta daqui consegue gravar no
`app.db`, nem por engano. Banco ausente (antes da virada) ou tabela que ainda não
existe viram tela vazia com explicação, nunca erro 500.

As datas do banco vêm em UTC — com fuso (`...+00:00`) ou sem (`datetime.utcnow()`) — e
a tela mostra tudo no horário de São Paulo, o mesmo dia do teto de gasto.
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections import Counter, defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

from ..teto import SAO_PAULO

#: Onde o robô grava o início de cada ciclo (app/pipeline.py).
CICLO_KEY = "ultimo_ciclo_utc"

#: Status de `seen_articles` que ainda vão passar pela IA.
FILA_STATUS = ("NEW", "QUEUED", "DEFERRED", "PROCESSING")
FALHA_STATUS = ("FAILED", "FAILED_PERMANENT", "DRAFT_FAILED")

#: Descarte do filtro de entrada: o robô decidiu não escrever. É o normal, não erro.
_FILTRO_PREFIXOS = ("EARLY_", "Cluster: fontes validas insuficientes")


def _utc(value: object) -> Optional[datetime]:
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(str(value).strip())
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _local(value: object) -> Optional[datetime]:
    moment = _utc(value)
    return moment.astimezone(SAO_PAULO) if moment else None


def agora() -> datetime:
    return datetime.now(timezone.utc)


def human_slug(slug: str) -> str:
    text = (slug or "").strip("/").replace("-", " ").strip()
    return text[:1].upper() + text[1:] if text else ""


def public_base_url() -> str:
    """A base dos links para o Cinerie (`CINERIE_PUBLIC_BASE_URL`, que o compose passa ao
    painel). O painel nunca abre o `.env` do robô: as chaves não são dele."""
    return _safe_url((os.getenv("CINERIE_PUBLIC_BASE_URL") or "").strip()) or ""


def _safe_url(value: Optional[str]) -> Optional[str]:
    """Só http(s) vira link: a URL de um feed é dado de terceiro, e um `javascript:`
    passaria pelo autoescape do Jinja direto para o href."""
    text = (value or "").strip()
    return text if text.lower().startswith(("https://", "http://")) else None


def _public_url(base: str, slug: Optional[str]) -> Optional[str]:
    slug_text = (slug or "").strip().strip("/")
    if not slug_text or not _safe_url(base):
        return None
    return f"{base.rstrip('/')}/{slug_text}"


@contextmanager
def leitura(db_path: str) -> Iterator[Optional[sqlite3.Connection]]:
    """Conexão só leitura com o banco do robô; None se ele ainda não existe."""
    path = Path(db_path)
    if not path.exists() or path.stat().st_size == 0:
        yield None
        return
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA query_only = ON")
        yield conn
    finally:
        conn.close()


def _tables(conn: sqlite3.Connection) -> set:
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


# --- modelos da tela -----------------------------------------------------------


@dataclass
class Publicacao:
    quando: Optional[datetime]
    titulo: str
    url: Optional[str]
    ok: bool
    situacao: str
    fonte: str = ""
    detalhe: str = ""


@dataclass
class Visao:
    banco_ok: bool
    agora: datetime
    ultimo_ciclo: Optional[datetime] = None
    ciclo_atrasado: bool = False
    ultima_publicacao: Optional[datetime] = None
    gasto_hoje: float = 0.0
    teto: Optional[float] = None
    teto_pct: Optional[int] = None
    teto_atingido: bool = False
    publicadas_hoje: int = 0
    publicadas_ontem: int = 0
    publicadas_7d: int = 0
    custo_medio: Optional[float] = None
    fila: Dict[str, int] = field(default_factory=dict)
    ultimas: List[Publicacao] = field(default_factory=list)
    envios_com_problema: int = 0

    @property
    def fila_total(self) -> int:
        return sum(self.fila.values())


@dataclass
class DiaGasto:
    dia: date
    usd: float
    chamadas: int
    por_modelo: List[Tuple[str, float, int]]
    publicadas: int

    @property
    def medido(self) -> bool:
        """Dia sem nenhuma linha em `ai_spend_daily`: a conta ainda não existia (ou o robô
        não chamou a IA). Mostrar US$ 0,00 aí seria mentir."""
        return bool(self.por_modelo)

    @property
    def custo_por_materia(self) -> Optional[float]:
        return self.usd / self.publicadas if self.medido and self.publicadas else None


@dataclass
class ItemFalho:
    quando: Optional[datetime]
    status: str
    motivo: str
    titulo: str
    url: str


@dataclass
class Falhas:
    banco_ok: bool
    motivos_erro: List[Tuple[str, int]] = field(default_factory=list)
    motivos_filtro: List[Tuple[str, int]] = field(default_factory=list)
    erros: List[ItemFalho] = field(default_factory=list)
    envios: List[Publicacao] = field(default_factory=list)


def _e_filtro(motivo: str) -> bool:
    return (motivo or "").startswith(_FILTRO_PREFIXOS)


# --- consultas -----------------------------------------------------------------


def _situacao(ok: bool, delivery_status: Optional[str], outcome: Optional[str]) -> str:
    if ok:
        return "publicada"
    if outcome == "BLOCKED":
        return "barrada pelo Cinerie"
    if delivery_status == "COMPLETED":
        return f"entregue, mas {outcome or 'sem resultado'}"
    return f"{(delivery_status or 'sem status').lower()} ({outcome or 'sem resposta'})"


def _publicacoes(conn: sqlite3.Connection, base: str, where: str = "", params: tuple = (), limit: int = 50):
    tables = _tables(conn)
    if "cinerie_publications" not in tables:
        return []
    join = "LEFT JOIN seen_articles s ON s.draft_id = p.draft_id" if "seen_articles" in tables else ""
    fonte = "s.normalized_title" if join else "''"
    rows = conn.execute(
        f"""
        SELECT p.delivered_at, p.created_at, p.updated_at, p.canonical_slug, p.payload_outcome,
               p.delivery_status, p.reason_codes, p.last_delivery_error_code, p.http_status,
               {fonte} AS fonte
        FROM cinerie_publications p {join}
        {where}
        ORDER BY p.id DESC LIMIT ?
        """,
        (*params, limit),
    ).fetchall()
    result = []
    for row in rows:
        ok = row["delivery_status"] == "COMPLETED" and row["payload_outcome"] == "PUBLISHED"
        motivos = []
        try:
            motivos = [str(code) for code in json.loads(row["reason_codes"] or "[]")]
        except (TypeError, ValueError):
            pass
        if row["last_delivery_error_code"]:
            motivos.append(str(row["last_delivery_error_code"]))
        result.append(
            Publicacao(
                quando=_local(row["delivered_at"] or row["updated_at"] or row["created_at"]),
                titulo=human_slug(row["canonical_slug"]) or "(sem slug)",
                url=_public_url(base, row["canonical_slug"]) if ok else None,
                ok=ok,
                situacao=_situacao(ok, row["delivery_status"], row["payload_outcome"]),
                fonte=row["fonte"] or "",
                detalhe=", ".join(motivos),
            )
        )
    return result


def _publicadas_por_dia(conn: sqlite3.Connection, desde: datetime) -> Counter:
    por_dia: Counter = Counter()
    if "cinerie_publications" not in _tables(conn):
        return por_dia
    rows = conn.execute(
        "SELECT delivered_at FROM cinerie_publications "
        "WHERE delivery_status = 'COMPLETED' AND payload_outcome = 'PUBLISHED' AND delivered_at >= ?",
        (desde.astimezone(timezone.utc).strftime("%Y-%m-%d"),),
    ).fetchall()
    for row in rows:
        local = _local(row["delivered_at"])
        if local:
            por_dia[local.date()] += 1
    return por_dia


def _gasto_por_dia(conn: sqlite3.Connection, desde: date) -> Dict[date, List[Tuple[str, float, int]]]:
    gastos: Dict[date, List[Tuple[str, float, int]]] = defaultdict(list)
    if "ai_spend_daily" not in _tables(conn):
        return gastos
    rows = conn.execute(
        "SELECT day, model, usd, calls FROM ai_spend_daily WHERE day >= ? ORDER BY day DESC, usd DESC",
        (desde.isoformat(),),
    ).fetchall()
    for row in rows:
        try:
            gastos[date.fromisoformat(row["day"])].append((row["model"], float(row["usd"]), int(row["calls"])))
        except (TypeError, ValueError):
            continue
    return gastos


def visao(
    db_path: str,
    *,
    teto: Optional[float],
    base_url: str = "",
    now: Optional[datetime] = None,
    intervalo_min: int = 15,
) -> Visao:
    """`teto`: o teto diário em dólares; 0 desligado; None se a variável é inválida."""
    momento = now or agora()
    hoje = momento.astimezone(SAO_PAULO).date()
    with leitura(db_path) as conn:
        if conn is None:
            return Visao(banco_ok=False, agora=momento, teto=teto)
        tables = _tables(conn)
        v = Visao(banco_ok=True, agora=momento, teto=teto)

        if "pipeline_state" in tables:
            row = conn.execute("SELECT value FROM pipeline_state WHERE key = ?", (CICLO_KEY,)).fetchone()
            v.ultimo_ciclo = _local(row["value"]) if row else None
        # Um ciclo a cada `intervalo_min`; passar de 2,5 intervalos sem nenhum é sinal de
        # robô parado ou travado.
        if v.ultimo_ciclo is not None:
            v.ciclo_atrasado = momento - v.ultimo_ciclo > timedelta(minutes=intervalo_min * 2.5)

        gastos = _gasto_por_dia(conn, hoje - timedelta(days=6))
        v.gasto_hoje = sum(usd for _model, usd, _calls in gastos.get(hoje, []))
        if teto:
            v.teto_pct = min(100, int(round(v.gasto_hoje / teto * 100)))
            v.teto_atingido = v.gasto_hoje >= teto

        por_dia = _publicadas_por_dia(conn, momento - timedelta(days=8))
        v.publicadas_hoje = por_dia.get(hoje, 0)
        v.publicadas_ontem = por_dia.get(hoje - timedelta(days=1), 0)
        v.publicadas_7d = sum(n for dia, n in por_dia.items() if dia > hoje - timedelta(days=7))
        # Custo médio só nos dias em que o gasto foi medido (a conta começou em 29/09/2026).
        dias_medidos = [dia for dia in gastos if por_dia.get(dia)]
        materias = sum(por_dia[dia] for dia in dias_medidos)
        if materias:
            v.custo_medio = sum(usd for dia in dias_medidos for _m, usd, _c in gastos[dia]) / materias

        if "seen_articles" in tables:
            marks = ",".join("?" for _ in FILA_STATUS)
            for row in conn.execute(
                f"SELECT status, COUNT(*) AS n FROM seen_articles WHERE status IN ({marks}) GROUP BY status",
                FILA_STATUS,
            ):
                v.fila[row["status"]] = int(row["n"])

        v.ultimas = _publicacoes(conn, base_url, limit=10)
        publicadas = [p for p in v.ultimas if p.ok and p.quando]
        v.ultima_publicacao = publicadas[0].quando if publicadas else None
        if "cinerie_publications" in tables:
            v.envios_com_problema = conn.execute(
                "SELECT COUNT(*) FROM cinerie_publications WHERE delivery_status <> 'COMPLETED' "
                "OR payload_outcome IS NULL OR payload_outcome <> 'PUBLISHED'"
            ).fetchone()[0]
        return v


def publicacoes(db_path: str, *, base_url: str = "", limit: int = 100) -> Optional[List[Publicacao]]:
    with leitura(db_path) as conn:
        if conn is None:
            return None
        return _publicacoes(conn, base_url, limit=limit)


def gasto(db_path: str, *, now: Optional[datetime] = None, dias: int = 30) -> Optional[List[DiaGasto]]:
    momento = now or agora()
    hoje = momento.astimezone(SAO_PAULO).date()
    with leitura(db_path) as conn:
        if conn is None:
            return None
        gastos = _gasto_por_dia(conn, hoje - timedelta(days=dias - 1))
        por_dia = _publicadas_por_dia(conn, momento - timedelta(days=dias + 1))
        result = []
        for offset in range(dias):
            dia = hoje - timedelta(days=offset)
            modelos = gastos.get(dia, [])
            if not modelos and not por_dia.get(dia):
                continue
            result.append(
                DiaGasto(
                    dia=dia,
                    usd=sum(usd for _m, usd, _c in modelos),
                    chamadas=sum(calls for _m, _u, calls in modelos),
                    por_modelo=modelos,
                    publicadas=por_dia.get(dia, 0),
                )
            )
        return result


def falhas(db_path: str, *, base_url: str = "", limit: int = 50) -> Falhas:
    with leitura(db_path) as conn:
        if conn is None:
            return Falhas(banco_ok=False)
        f = Falhas(banco_ok=True)
        tables = _tables(conn)
        if "seen_articles" in tables:
            marks = ",".join("?" for _ in FALHA_STATUS)
            erros: Counter = Counter()
            filtro: Counter = Counter()
            for row in conn.execute(
                f"SELECT fail_reason, COUNT(*) AS n FROM seen_articles WHERE status IN ({marks}) GROUP BY fail_reason",
                FALHA_STATUS,
            ):
                motivo = (row["fail_reason"] or "(sem motivo registrado)")[:160]
                (filtro if _e_filtro(motivo) else erros)[motivo] += int(row["n"])
            f.motivos_erro = erros.most_common()
            f.motivos_filtro = filtro.most_common()
            for row in conn.execute(
                f"""
                SELECT status, fail_reason, normalized_title, url, inserted_at
                FROM seen_articles WHERE status IN ({marks}) ORDER BY id DESC LIMIT ?
                """,
                (*FALHA_STATUS, limit * 4),
            ):
                motivo = row["fail_reason"] or ""
                if _e_filtro(motivo):
                    continue
                f.erros.append(
                    ItemFalho(
                        quando=_local(row["inserted_at"]),
                        status=row["status"],
                        motivo=motivo,
                        titulo=row["normalized_title"] or row["url"] or "",
                        url=_safe_url(row["url"]) or "",
                    )
                )
                if len(f.erros) >= limit:
                    break
        f.envios = _publicacoes(
            conn,
            base_url,
            where="WHERE p.delivery_status <> 'COMPLETED' OR p.payload_outcome IS NULL OR p.payload_outcome <> 'PUBLISHED'",
            limit=30,
        )
        return f
