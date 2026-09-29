"""O processo do painel no VPS.

    python -m app.painel                  sobe o painel (porta PORT, padrão 8080)
    python -m app.painel --novo-acesso    esqueceu a senha: apaga o administrador,
                                          derruba as sessões e imprime um código novo

Variáveis (nenhuma é segredo):

- ``MNSCR_DB_PATH`` — o banco do robô, lido só em modo leitura (``/data/app.db``);
- ``MNSCR_PAINEL_DB_PATH`` — onde o painel guarda o próprio acesso (padrão: ``painel.db``
  ao lado do banco do robô);
- ``MNSCR_PAINEL_SECURE_COOKIES`` — ``false`` só para rodar sem HTTPS em teste local;
- ``FORWARDED_ALLOW_IPS`` — de quem aceitar ``X-Forwarded-For`` (padrão: as redes
  privadas, onde fica o proxy do Coolify);
- ``CHECK_INTERVAL_MINUTES`` — o intervalo de ciclo do robô, para o alerta de robô parado;
- ``CINERIE_PUBLIC_BASE_URL`` — a base dos links para as matérias no Cinerie;
- ``MNSCR_AI_DAILY_BUDGET_USD`` — o teto, para a barra de gasto (o compose passa o mesmo
  valor aos dois serviços).
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path


def _flag(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in ("0", "false", "no", "off", "")


def _paths() -> tuple[str, str]:
    # O mesmo padrão de app/config.py, sem importá-lo: ele carregaria o `.env` do robô.
    robot_db = os.getenv("MNSCR_DB_PATH", "data/app.db")
    painel_db = os.getenv("MNSCR_PAINEL_DB_PATH") or str(Path(robot_db).with_name("painel.db"))
    return robot_db, painel_db


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.painel", description="Painel só leitura do MNScr")
    parser.add_argument("--novo-acesso", action="store_true", help="apaga o administrador e gera um código novo")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(name)s - %(message)s")
    logger = logging.getLogger("app.painel")

    from .acesso import PainelStore

    robot_db, painel_db = _paths()
    store = PainelStore(painel_db)

    if args.novo_acesso:
        code = store.reset_admin()
        print(f"[MNSCR_PAINEL] administrador apagado; sessões abertas derrubadas. Código novo: {code}")
        print("Abra o painel: ele pede o código e cria o administrador de novo.")
        return 0

    import uvicorn

    from ..teto import budget_from_env
    from .dados import public_base_url
    from .web import create_app

    code = store.ensure_setup_code()
    if code:
        logger.warning("[MNSCR_PAINEL] primeiro acesso: abra o painel e informe o código %s", code)

    try:
        intervalo = max(1, int(os.getenv("CHECK_INTERVAL_MINUTES", "15")))
    except ValueError:
        intervalo = 15
    teto_bruto, teto = budget_from_env()
    if teto is None:
        logger.error("[MNSCR_PAINEL] MNSCR_AI_DAILY_BUDGET_USD=%r inválido: a barra de gasto fica sem teto", teto_bruto)
    app = create_app(
        store,
        robot_db=robot_db,
        teto=teto,
        secure_cookies=_flag("MNSCR_PAINEL_SECURE_COOKIES", True),
        base_url=public_base_url(),
        intervalo_min=intervalo,
    )
    port = int(os.getenv("PORT", "8080"))
    logger.info("[MNSCR_PAINEL] painel na porta %s, lendo %s", port, robot_db)
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port,
        proxy_headers=True,
        forwarded_allow_ips=os.getenv("FORWARDED_ALLOW_IPS", "127.0.0.1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"),
        log_level="info",
        access_log=False,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
