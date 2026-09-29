"""Teto diário de gasto com IA, em dólares.

Cada resposta do Gemini diz quantos tokens foram cobrados. Aqui eles viram dólares e
somam no dia de São Paulo, no próprio banco (`ai_spend_daily`), por modelo. Quando o
dia chega ao teto (`MNSCR_AI_DAILY_BUDGET_USD`, padrão 1.00; 0 desliga), o worker para
de pegar matéria nova: a fila fica como está e volta a andar à meia-noite.

A trava fica ANTES do claim (app/pipeline.py), e não dentro do cliente. Uma exceção no
meio do artigo cairia em `_handle_ai_processing_failure`, que devolve a matéria para a
fila com `fail_count + 1`: cinco dias batendo no teto e ela virava FAILED_PERMANENT.
Assim, o artigo que já começou termina, e o dia passa do teto por no máximo o custo de
um artigo (US$ 0,01 em média; o pior visto até 29/09/2026 foi US$ 0,05).

Por que somar aqui e não no `logs/tokens`: aquele registro só conta o redator. A
checagem factual e o validador (gemini-2.5-flash-lite) passam pelo mesmo cliente e
nunca entraram nele — em 25/09/2026 foram 132 das 208 chamadas do dia.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Optional

from . import config
from .sqlite_utils import connect_sqlite

logger = logging.getLogger(__name__)

#: USD por 1 milhão de tokens (entrada, saída), tabela paga do Gemini em 29/09/2026
#: (https://ai.google.dev/gemini-api/docs/pricing). O raciocínio é cobrado como saída.
PRICES_USD_PER_MTOK: dict[str, tuple[float, float]] = {
    "gemini-3.1-flash-lite": (0.25, 1.50),
    "gemini-2.5-flash-lite": (0.10, 0.40),
    "gemini-2.5-flash": (0.30, 2.50),
}

# Modelo fora da tabela conta pelo preço mais caro dela: melhor a trava fechar cedo
# do que um modelo novo passar de graça pela conta.
_UNKNOWN_MODEL_PRICE = max(PRICES_USD_PER_MTOK.values(), key=lambda price: price[1])

# O Brasil não tem horário de verão desde 2019: um deslocamento fixo dispensa a base
# de fusos (tzdata), que no Windows nem sempre está instalada.
SAO_PAULO = timezone(timedelta(hours=-3), "America/Sao_Paulo")

# Gasto que o banco recusou gravar (lock além do busy_timeout, disco cheio). A
# chamada já foi paga: o valor fica aqui, entra na próxima gravação e, até lá, conta
# na checagem do teto. Um lock só serializa as gravações deste processo — o watchdog
# pode deixar um artigo ainda chamando a IA enquanto o worker pega o próximo.
_record_lock = threading.Lock()
_pending: dict[tuple[str, str], list[float]] = {}
_schema_ready: set[str] = set()


def unpriced_models() -> list[str]:
    """Modelos configurados que não estão na tabela: com eles a conta do dia mentiria."""
    from .ai_client_gemini import MODEL_CHAIN
    from .ai_validator import AI_VALIDATOR_MODEL

    configured = [*MODEL_CHAIN, AI_VALIDATOR_MODEL, config.FACTUAL_MODEL]
    return sorted({model for model in configured if model and model not in PRICES_USD_PER_MTOK})


def call_cost_usd(model: str, tokens_info: Mapping[str, Any]) -> float:
    """Custo de UMA resposta. Tokens em cache contam cheio (a conta erra para cima)."""
    input_price, output_price = PRICES_USD_PER_MTOK.get(model, _UNKNOWN_MODEL_PRICE)
    prompt_tokens = int(tokens_info.get("prompt_tokens") or 0)
    output_tokens = int(tokens_info.get("completion_tokens") or 0) + int(tokens_info.get("thoughts_tokens") or 0)
    return (prompt_tokens * input_price + output_tokens * output_price) / 1_000_000


def local_day(now: Optional[datetime] = None) -> str:
    """O dia do teto: a data em São Paulo, `AAAA-MM-DD`."""
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(SAO_PAULO).date().isoformat()


def seconds_until_next_day(now: Optional[datetime] = None) -> float:
    """Quanto falta para a meia-noite de São Paulo, quando o teto zera."""
    moment = (now or datetime.now(timezone.utc)).astimezone(SAO_PAULO)
    midnight = datetime.combine(moment.date() + timedelta(days=1), datetime.min.time(), tzinfo=SAO_PAULO)
    return max(0.0, (midnight - moment).total_seconds())


@dataclass(frozen=True)
class BudgetStatus:
    day: str
    spent_usd: float
    #: None quando MNSCR_AI_DAILY_BUDGET_USD é inválida (o startup já recusa).
    budget_usd: Optional[float]
    #: O banco não respondeu: sem saber o gasto, a trava fecha e reconfere depois.
    unreadable: bool = False

    @property
    def enabled(self) -> bool:
        return self.budget_usd is None or self.budget_usd > 0

    @property
    def exhausted(self) -> bool:
        if not self.enabled:
            return False
        if self.unreadable or self.budget_usd is None:
            return True
        return self.spent_usd >= self.budget_usd


def _connect(db_path: Optional[str]) -> sqlite3.Connection:
    path = str(db_path or config.DB_PATH)
    conn = connect_sqlite(path)
    if path not in _schema_ready:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ai_spend_daily (
                day TEXT NOT NULL,
                model TEXT NOT NULL,
                usd REAL NOT NULL DEFAULT 0,
                calls INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (day, model)
            )
            """
        )
        conn.commit()
        _schema_ready.add(path)
    return conn


def _spent(conn: sqlite3.Connection, day: str) -> float:
    row = conn.execute("SELECT COALESCE(SUM(usd), 0) FROM ai_spend_daily WHERE day = ?", (day,)).fetchone()
    return float(row[0] or 0)


def _pending_usd(day: str) -> float:
    return sum(usd for (pending_day, _model), (usd, _calls) in _pending.items() if pending_day == day)


def record_call(
    model: str,
    tokens_info: Mapping[str, Any],
    *,
    db_path: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Optional[float]:
    """Soma uma resposta no dia e devolve o gasto do dia. None se o banco falhar.

    Falhar aqui não derruba a chamada que já foi paga nem perde o valor: ele fica em
    `_pending`, conta no teto e é gravado junto da próxima resposta.
    """
    cost = call_cost_usd(model, tokens_info)
    day = local_day(now)
    stamp = (now or datetime.now(timezone.utc)).isoformat()
    with _record_lock:
        entry = _pending.setdefault((day, model), [0.0, 0])
        entry[0] += cost
        entry[1] += 1
        try:
            conn = _connect(db_path)
            try:
                with conn:
                    conn.executemany(
                        """
                        INSERT INTO ai_spend_daily (day, model, usd, calls, updated_at)
                        VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(day, model) DO UPDATE SET
                            usd = usd + excluded.usd,
                            calls = calls + excluded.calls,
                            updated_at = excluded.updated_at
                        """,
                        [
                            (pending_day, pending_model, usd, int(calls), stamp)
                            for (pending_day, pending_model), (usd, calls) in _pending.items()
                        ],
                    )
                _pending.clear()
                spent = _spent(conn, day)
            finally:
                conn.close()
        except sqlite3.Error as exc:
            logger.error(
                "[AI_SPEND] gasto nao gravado (modelo=%s custo_usd=%.5f), guardado para a proxima gravacao: %s",
                model,
                cost,
                exc,
            )
            return None

    logger.info(
        "[AI_SPEND] dia=%s modelo=%s custo_usd=%.5f gasto_dia_usd=%.4f teto_usd=%s",
        day,
        model,
        cost,
        spent,
        _format_budget(config.AI_DAILY_BUDGET_USD),
    )
    return spent


def budget_status(*, db_path: Optional[str] = None, now: Optional[datetime] = None) -> BudgetStatus:
    """Onde o dia está em relação ao teto, contando o que ainda não foi gravado.

    Se o banco não responder, a trava fecha (`unreadable`): sem saber o gasto, não
    se começa matéria nova. O pipeline reconfere em minutos.
    """
    day = local_day(now)
    budget = config.AI_DAILY_BUDGET_USD
    if budget == 0:
        return BudgetStatus(day=day, spent_usd=0.0, budget_usd=0.0)
    # Sob o mesmo lock da gravação: o pendente não pode ser contado duas vezes, antes
    # e depois de uma gravação concorrente.
    with _record_lock:
        pending = _pending_usd(day)
        try:
            conn = _connect(db_path)
            try:
                spent = _spent(conn, day)
            finally:
                conn.close()
        except sqlite3.Error as exc:
            logger.error("[AI_DAILY_BUDGET] gasto do dia ilegivel: %s", exc)
            return BudgetStatus(day=day, spent_usd=pending, budget_usd=budget, unreadable=True)
    return BudgetStatus(day=day, spent_usd=spent + pending, budget_usd=budget)


def _format_budget(budget: Optional[float]) -> str:
    if budget is None:
        return "invalido"
    return "desligado" if budget == 0 else f"{budget:.2f}"
