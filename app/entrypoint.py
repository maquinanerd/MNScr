import argparse
import atexit
import logging
import os
import sys
import threading

os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

from apscheduler.schedulers.blocking import BlockingScheduler

from app.config import SCHEDULE_CONFIG, validate_runtime_config
from app.pipeline import run_pipeline_cycle
from app.store import Database

_FILE_HANDLER: logging.Handler | None = None
_CONSOLE_HANDLER: logging.Handler | None = None
_RUNTIME_JOB_LOCK = threading.Lock()


def configure_logging() -> logging.Logger:
    """Configure the application logger once and return the entrypoint logger."""
    global _FILE_HANDLER, _CONSOLE_HANDLER

    os.makedirs("logs", exist_ok=True)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass

    logger = logging.getLogger(__name__)
    logger.handlers.clear()
    logger.propagate = True
    logger.setLevel(logging.INFO)

    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)

    _FILE_HANDLER = logging.FileHandler("logs/app.log", mode="a", encoding="utf-8")
    _FILE_HANDLER.setLevel(logging.INFO)

    _CONSOLE_HANDLER = logging.StreamHandler(sys.stdout)
    _CONSOLE_HANDLER.setLevel(logging.INFO)

    formatter = logging.Formatter(
        "%(asctime)s - %(levelname)s - %(module)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    _FILE_HANDLER.setFormatter(formatter)
    _CONSOLE_HANDLER.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(_FILE_HANDLER)
    root_logger.addHandler(_CONSOLE_HANDLER)

    return logger


def flush_logs() -> None:
    """Flush currently configured log handlers."""
    for handler in (_FILE_HANDLER, _CONSOLE_HANDLER):
        if handler is None:
            continue
        try:
            handler.flush()
        except Exception:
            pass


atexit.register(flush_logs)


def print_startup_banner() -> None:
    print("\n" + "=" * 80)
    print("Ativando o ambiente virtual...")
    print("=" * 80)
    print("OK - Ambiente virtual ativado com sucesso.\n")
    print("=" * 80)
    print("Iniciando o programa...")
    print("=" * 80)


def validate_startup_configuration(logger: logging.Logger) -> None:
    """Fail fast when critical runtime configuration is missing."""
    try:
        validate_runtime_config()
        logger.info("Configuração crítica validada com sucesso.")
        flush_logs()
    except Exception as e:
        logger.critical(f"Falha na validação de configuração: {e}", exc_info=True)
        flush_logs()
        sys.exit(1)


def initialize_database(logger: logging.Logger) -> None:
    """Initialize SQLite schema before running the pipeline."""
    logger.info("Verificando o esquema do banco de dados...")
    flush_logs()
    try:
        db = Database()
        db.initialize()
        db.close()
        logger.info("Verificação do banco de dados concluída com sucesso.")
        flush_logs()
    except Exception as e:
        logger.critical(f"Falha ao inicializar o banco de dados: {e}", exc_info=True)
        flush_logs()
        sys.exit(1)


def run_pipeline_cycle_guarded() -> None:
    """Run a pipeline cycle while serializing against maintenance work."""
    with _RUNTIME_JOB_LOCK:
        run_pipeline_cycle()


def run_once(logger: logging.Logger) -> None:
    """Execute a single pipeline cycle and exit."""
    logger.info("Executando um único ciclo do pipeline (--once).")
    flush_logs()
    try:
        run_pipeline_cycle_guarded()
    except Exception as e:
        logger.critical(f"Erro crítico durante a execução do ciclo único: {e}", exc_info=True)
        flush_logs()
    finally:
        logger.info("Ciclo único finalizado.")
        flush_logs()


def run_forever(logger: logging.Logger) -> None:
    """Run the scheduler loop for continuous processing."""
    interval = SCHEDULE_CONFIG.get("check_interval_minutes", 15)
    logger.info(f"Agendador iniciado. O pipeline será executado a cada {interval} minutos.")

    logger.info("Executando primeira verificação imediatamente...")
    try:
        run_pipeline_cycle_guarded()
    except Exception as e:
        logger.error(f"Erro na execução inicial do pipeline: {e}", exc_info=True)

    scheduler = BlockingScheduler(timezone="America/Sao_Paulo")
    scheduler.add_job(run_pipeline_cycle_guarded, "interval", minutes=interval)

    logger.info("Pressione Ctrl+C para sair.")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Agendador interrompido pelo usuário.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Executa o pipeline editorial do MNScr (gera drafts, nao publica)."
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Executa o ciclo do pipeline uma vez e sai.",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    logger = configure_logging()
    print_startup_banner()

    args = build_parser().parse_args(argv)

    validate_startup_configuration(logger)
    initialize_database(logger)

    if args.once:
        run_once(logger)
    else:
        run_forever(logger)
