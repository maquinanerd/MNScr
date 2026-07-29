"""CLI do Editorial Gate: parsing, isolamento e execucao offline."""

import socket

import pytest

from app.editorial import DraftContent, DraftProvenance, EditorialDraft, SourceReference
from app.editorial.serialization import dumps_pretty
from app.entrypoint import build_parser
from app.gate_service import GateService, format_gate_listing, format_gate_result
from app.gate_store import GateStore

POLICY_V1 = "mnscr-editorial-gate-v1"
LONG_BODY = "<p>" + " ".join(["palavra"] * 420) + "</p>"


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _blocked(*args, **kwargs):
        raise AssertionError("CLI do gate tentou acessar a rede")

    monkeypatch.setattr(socket, "socket", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)


def make_draft(draft_id="draft-cli", title="Estudio confirma a estreia da nova fase"):
    return EditorialDraft(
        draft_id=draft_id, article_id=1,
        content=DraftContent(
            title=title, body_html=LONG_BODY,
            subtitle="Resumo editorial proprio, distinto do titulo e da meta.",
            meta_description="Meta description com o fato principal do acontecimento.",
        ),
        provenance=DraftProvenance(
            input_hash="a" * 64, output_hash="b" * 64,
            input_contract_version="rss-prime-event-v1",
            input_event_key="evt-1", input_revision=1,
            prompt_version="universal_prompt-ms1",
        ),
        event_key="evt-1", revision=1,
        sources=[
            SourceReference(url="https://variety.example/a", domain="variety.example",
                            is_primary=True, extraction_status="OK"),
            SourceReference(url="https://deadline.example/b", domain="deadline.example",
                            extraction_status="OK"),
        ],
    )


@pytest.fixture
def workspace(tmp_path):
    drafts = tmp_path / "drafts"
    drafts.mkdir()
    store = GateStore(db_path=str(tmp_path / "app.db"))
    service = GateService(gate_store=store, draft_dir=str(drafts))
    yield service, drafts
    store.close()


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def test_show_gate_is_parsed():
    assert build_parser().parse_args(["--show-gate", "draft-1"]).show_gate == "draft-1"


def test_reevaluate_gate_is_parsed():
    args = build_parser().parse_args(["--reevaluate-gate", "draft-1"])
    assert args.reevaluate_gate == "draft-1"
    assert args.gate_policy is None


def test_reevaluate_gate_with_policy_is_parsed():
    args = build_parser().parse_args(
        ["--reevaluate-gate", "draft-1", "--gate-policy", "mnscr-editorial-gate-v2"]
    )
    assert args.gate_policy == "mnscr-editorial-gate-v2"


def test_list_gate_flags_are_parsed():
    assert build_parser().parse_args(["--list-gate-blocked"]).list_gate_blocked is True
    assert build_parser().parse_args(["--list-gate-review-required"]).list_gate_review_required is True


def test_help_mentions_every_gate_command(capsys):
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--help"])
    output = capsys.readouterr().out
    for flag in ("--show-gate", "--reevaluate-gate", "--gate-policy",
                 "--list-gate-blocked", "--list-gate-review-required"):
        assert flag in output


def test_ms2_commands_survive():
    args = build_parser().parse_args(["--replay-event", "evt-1", "--revision", "2"])
    assert args.replay_event == "evt-1" and args.revision == 2


# ---------------------------------------------------------------------------
# Comportamento
# ---------------------------------------------------------------------------


def test_gate_policy_without_reevaluate_is_rejected(monkeypatch):
    from app import entrypoint

    monkeypatch.setattr(entrypoint, "configure_logging", lambda: entrypoint.logging.getLogger("t"))
    with pytest.raises(SystemExit) as exc:
        entrypoint.main(["--gate-policy", "mnscr-editorial-gate-v1"])
    assert exc.value.code == 2


def test_show_gate_of_unknown_draft_exits_with_error(monkeypatch, tmp_path):
    from app import entrypoint

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(entrypoint, "configure_logging", lambda: entrypoint.logging.getLogger("t"))
    with pytest.raises(SystemExit) as exc:
        entrypoint.main(["--show-gate", "draft-que-nao-existe"])
    assert exc.value.code == 1


def test_reevaluate_of_unknown_draft_exits_with_error(monkeypatch, tmp_path):
    from app import entrypoint

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(entrypoint, "configure_logging", lambda: entrypoint.logging.getLogger("t"))
    with pytest.raises(SystemExit) as exc:
        entrypoint.main(["--reevaluate-gate", "draft-que-nao-existe"])
    assert exc.value.code == 1


def test_reevaluate_with_unknown_policy_exits_with_error(monkeypatch, tmp_path):
    from app import entrypoint

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(entrypoint, "configure_logging", lambda: entrypoint.logging.getLogger("t"))
    with pytest.raises(SystemExit) as exc:
        entrypoint.main(["--reevaluate-gate", "d", "--gate-policy", "politica-inexistente"])
    assert exc.value.code == 1


def test_listing_commands_work_on_an_empty_database(monkeypatch, tmp_path, capsys):
    from app import entrypoint

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(entrypoint, "configure_logging", lambda: entrypoint.logging.getLogger("t"))

    for flag in ("--list-gate-blocked", "--list-gate-review-required"):
        with pytest.raises(SystemExit) as exc:
            entrypoint.main([flag])
        assert exc.value.code == 0
    assert "nenhum draft" in capsys.readouterr().out.lower()


def test_gate_commands_never_start_the_pipeline(monkeypatch, tmp_path):
    """Comandos de leitura nao sobem scheduler nem validam chave de IA."""
    from app import entrypoint

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(entrypoint, "configure_logging", lambda: entrypoint.logging.getLogger("t"))

    def _fail(*args, **kwargs):
        raise AssertionError("comando de gate nao pode iniciar o pipeline")

    monkeypatch.setattr(entrypoint, "validate_startup_configuration", _fail)
    monkeypatch.setattr(entrypoint, "initialize_database", _fail)
    monkeypatch.setattr(entrypoint, "run_forever", _fail)
    monkeypatch.setattr(entrypoint, "run_once", _fail)

    with pytest.raises(SystemExit):
        entrypoint.main(["--list-gate-blocked"])


# ---------------------------------------------------------------------------
# Formatacao
# ---------------------------------------------------------------------------


def test_show_gate_output_never_prints_the_body(workspace):
    service, drafts = workspace
    draft = make_draft()
    (drafts / f"{draft.draft_id}.json").write_text(dumps_pretty(draft), encoding="utf-8")
    result = service.evaluate_draft(draft, POLICY_V1)

    rendered = format_gate_result(result)
    assert "palavra palavra" not in rendered
    assert draft.content.body_html not in rendered


def test_show_gate_output_has_the_required_fields(workspace):
    service, drafts = workspace
    draft = make_draft()
    result = service.evaluate_draft(draft, POLICY_V1)
    rendered = format_gate_result(result)
    for label in ("draft_id", "event_key", "revision", "policy_version",
                  "outcome", "blocking_count", "warning_count", "gate_hash"):
        assert label in rendered


def test_show_gate_lists_the_triggered_rules(workspace):
    service, drafts = workspace
    draft = make_draft(title="")
    result = service.evaluate_draft(draft, POLICY_V1)
    assert "GATE_MISSING_TITLE" in format_gate_result(result)


def test_listing_renders_a_summary(workspace):
    service, _ = workspace
    blocked = make_draft(draft_id="draft-blocked", title="")
    service.evaluate_draft(blocked, POLICY_V1)

    rendered = format_gate_listing(service.list_blocked(), "Bloqueados")
    assert "draft-blocked" in rendered
    assert "DRAFT_ID" in rendered


def test_listing_of_empty_result_says_so():
    assert "nenhum draft" in format_gate_listing([], "Bloqueados").lower()


def test_reevaluate_end_to_end_offline(workspace):
    service, drafts = workspace
    draft = make_draft()
    (drafts / f"{draft.draft_id}.json").write_text(dumps_pretty(draft), encoding="utf-8")

    first = service.reevaluate_draft(draft.draft_id, POLICY_V1)
    assert first.created_new_record is True

    second = service.reevaluate_draft(draft.draft_id, POLICY_V1)
    assert second.created_new_record is False
    assert second.result.gate_hash == first.result.gate_hash


def test_gate_service_and_engine_never_import_network_libraries():
    from pathlib import Path

    for module in ("app/gate_service.py", "app/gate_store.py", "app/editorial_gate/engine.py"):
        source = Path(module).read_text(encoding="utf-8")
        for forbidden in ("import requests", "import httpx", "urlopen", "feedparser"):
            assert forbidden not in source, f"{module} importa {forbidden}"
