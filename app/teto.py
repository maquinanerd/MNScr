"""O teto diário de gasto com IA: o valor configurado e o dia em que ele conta.

Sem dependência nenhuma do resto do app. O robô (app/config.py, app/ai_spend.py) e o
painel (app/painel) leem daqui; o painel não pode importar app/config.py, que carrega o
`.env` do robô e acusa a falta de chave de IA — chave que o painel não tem nem precisa.
"""

from __future__ import annotations

import math
import os
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

BUDGET_ENV = "MNSCR_AI_DAILY_BUDGET_USD"
DEFAULT_BUDGET = "1.00"

# O Brasil não tem horário de verão desde 2019: um deslocamento fixo dispensa a base
# de fusos (tzdata), que no Windows nem sempre está instalada.
SAO_PAULO = timezone(timedelta(hours=-3), "America/Sao_Paulo")


def parse_budget_usd(raw: Optional[str]) -> Optional[float]:
    """`1.00`, `1,50` ou `0`. None quando o valor não é um número finito >= 0."""
    try:
        value = float(str(raw).strip().replace(",", "."))
    except (TypeError, ValueError):
        return None
    # NaN, infinito ("inf", "1e309") e negativo: só o 0 desliga o teto.
    if not math.isfinite(value) or value < 0:
        return None
    return value


def budget_from_env() -> Tuple[str, Optional[float]]:
    """O valor cru de `MNSCR_AI_DAILY_BUDGET_USD` e o teto em dólares (None se inválido)."""
    raw = os.getenv(BUDGET_ENV, DEFAULT_BUDGET)
    return raw, parse_budget_usd(raw)


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
