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

# E402 deliberado: os imports abaixo emitem log no import time e precisam
# encontrar stdout/stderr ja reconfigurados para UTF-8 (bloco acima).
from apscheduler.schedulers.blocking import BlockingScheduler  # noqa: E402

from app.config import SCHEDULE_CONFIG, validate_runtime_config  # noqa: E402
from app.pipeline import run_pipeline_cycle  # noqa: E402
from app.store import Database  # noqa: E402

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
    parser.add_argument(
        "--replay-event",
        metavar="EVENT_KEY",
        help=(
            "Reprocessa um acontecimento ja recebido, lendo apenas o payload "
            "persistido no SQLite. Nao consulta o RSS Prime e nao avanca o cursor."
        ),
    )
    parser.add_argument(
        "--revision",
        type=int,
        metavar="N",
        help="Revisao especifica para --replay-event. Sem ela, usa a mais recente.",
    )
    parser.add_argument(
        "--list-event-revisions",
        metavar="EVENT_KEY",
        help="Lista as revisoes registradas de um acontecimento (sem exibir o payload).",
    )
    parser.add_argument(
        "--show-gate",
        metavar="DRAFT_ID",
        help="Mostra o veredito do Editorial Gate de um draft (sem exibir o corpo da materia).",
    )
    parser.add_argument(
        "--reevaluate-gate",
        metavar="DRAFT_ID",
        help=(
            "Reavalia o Editorial Gate usando apenas dados locais. "
            "Nao consulta o feed, nao chama IA e nao avanca o cursor."
        ),
    )
    parser.add_argument(
        "--gate-policy",
        metavar="VERSAO",
        help="Versao da politica a usar em --reevaluate-gate. Sem ela, usa a configurada.",
    )
    parser.add_argument(
        "--list-gate-blocked",
        action="store_true",
        help="Lista os drafts cujo ultimo veredito foi GATE_BLOCKED.",
    )
    parser.add_argument(
        "--list-gate-review-required",
        action="store_true",
        help="Lista os drafts cujo ultimo veredito foi GATE_REVIEW_REQUIRED.",
    )
    return parser


def _run_show_gate(logger: logging.Logger, draft_id: str) -> int:
    """``--show-gate``: read-only, no network, no pipeline."""
    from app.gate_service import GateService, format_gate_result

    service = GateService()
    try:
        result = service.get_latest_gate_result(draft_id)
    finally:
        service.close()

    if result is None:
        logger.error("Nenhum veredito de gate registrado para o draft '%s'.", draft_id)
        return 1

    # Exibir um veredito é sempre uma operação bem-sucedida: o outcome do gate
    # é o conteúdo do relatório, não o status do comando.
    print(format_gate_result(result))
    return 0


def _run_reevaluate_gate(
    logger: logging.Logger, draft_id: str, policy_version: str | None
) -> int:
    """``--reevaluate-gate``: local data only."""
    from app.editorial_gate.errors import EditorialGateError
    from app.gate_service import GateService, format_gate_result

    service = GateService()
    try:
        outcome = service.reevaluate_draft(draft_id, policy_version)
    except EditorialGateError as exc:
        logger.error(str(exc))
        return 1
    finally:
        service.close()

    print(format_gate_result(outcome.result))
    print()
    print(
        f"veredito anterior: {outcome.previous_outcome or '-'} | "
        f"mudou: {'sim' if outcome.changed else 'nao'} | "
        f"novo registro: {'sim' if outcome.created_new_record else 'nao'}"
    )
    return 0


def _run_list_gate(logger: logging.Logger, blocked: bool) -> int:
    """``--list-gate-blocked`` / ``--list-gate-review-required``."""
    from app.gate_service import GateService, format_gate_listing

    service = GateService()
    try:
        rows = service.list_blocked() if blocked else service.list_review_required()
    finally:
        service.close()

    title = (
        "Drafts bloqueados pelo Editorial Gate"
        if blocked
        else "Drafts que exigem revisao editorial humana"
    )
    print(format_gate_listing(rows, title))
    return 0


def _run_list_event_revisions(logger: logging.Logger, event_key: str) -> int:
    """``--list-event-revisions``: read-only, no network, no pipeline."""
    from app.event_store import EventStore
    from app.replay import ReplayError, ReplayService, format_revision_table

    store = EventStore()
    try:
        rows = ReplayService(store).list_revisions(event_key)
    except ReplayError as exc:
        logger.error(str(exc))
        return 1
    finally:
        store.close()

    print(f"Acontecimento: {event_key}")
    print(format_revision_table(rows))
    return 0


def _run_replay(logger: logging.Logger, event_key: str, revision: int | None) -> int:
    """``--replay-event``: rebuild processing from the stored payload only."""
    from app.event_store import EventStore
    from app.pipeline import process_stored_event
    from app.replay import ReplayError, ReplayService

    store = EventStore()
    try:
        result = ReplayService(store, processor=process_stored_event).replay(event_key, revision)
    except ReplayError as exc:
        logger.error(str(exc))
        return 1
    finally:
        store.close()

    if result.ok:
        logger.info(
            "Replay concluido: event_key=%s revision=%s draft_id=%s (cursor nao avancou)",
            result.event_key, result.revision, result.draft_id or "-",
        )
        return 0
    logger.error(
        "Replay falhou: event_key=%s revision=%s erro=%s",
        result.event_key, result.revision, result.error,
    )
    return 1


def main(argv: list[str] | None = None) -> None:
    logger = configure_logging()

    args = build_parser().parse_args(argv)

    # Operações de leitura/replay não sobem o agendador nem validam credenciais
    # de IA: elas trabalham exclusivamente sobre o que já está no SQLite.
    if args.list_event_revisions:
        raise SystemExit(_run_list_event_revisions(logger, args.list_event_revisions))

    if args.replay_event:
        raise SystemExit(_run_replay(logger, args.replay_event, args.revision))

    if args.show_gate:
        raise SystemExit(_run_show_gate(logger, args.show_gate))

    if args.reevaluate_gate:
        raise SystemExit(_run_reevaluate_gate(logger, args.reevaluate_gate, args.gate_policy))

    if args.list_gate_blocked:
        raise SystemExit(_run_list_gate(logger, blocked=True))

    if args.list_gate_review_required:
        raise SystemExit(_run_list_gate(logger, blocked=False))

    if args.revision is not None:
        logger.error("--revision so pode ser usado junto com --replay-event.")
        raise SystemExit(2)

    if args.gate_policy is not None:
        logger.error("--gate-policy so pode ser usado junto com --reevaluate-gate.")
        raise SystemExit(2)

    print_startup_banner()
    validate_startup_configuration(logger)
    initialize_database(logger)

    if args.once:
        run_once(logger)
    else:
        run_forever(logger)
