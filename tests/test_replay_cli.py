"""CLI de replay: parsing dos argumentos e isolamento.

Os comandos de leitura e replay nao podem subir o agendador, validar chaves de
IA nem tocar a rede.
"""

import socket

import pytest

from app.entrypoint import build_parser


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _blocked(*args, **kwargs):
        raise AssertionError("CLI tentou acessar a rede")

    monkeypatch.setattr(socket, "socket", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def test_replay_event_is_parsed():
    args = build_parser().parse_args(["--replay-event", "evt-1"])
    assert args.replay_event == "evt-1"
    assert args.revision is None


def test_replay_event_with_revision_is_parsed():
    args = build_parser().parse_args(["--replay-event", "evt-1", "--revision", "3"])
    assert args.replay_event == "evt-1"
    assert args.revision == 3


def test_list_event_revisions_is_parsed():
    args = build_parser().parse_args(["--list-event-revisions", "evt-1"])
    assert args.list_event_revisions == "evt-1"


def test_once_still_works():
    assert build_parser().parse_args(["--once"]).once is True


def test_no_arguments_is_the_continuous_mode():
    args = build_parser().parse_args([])
    assert args.once is False
    assert args.replay_event is None
    assert args.list_event_revisions is None


def test_revision_must_be_an_integer():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--replay-event", "evt-1", "--revision", "duas"])


def test_help_mentions_the_new_commands(capsys):
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--help"])
    output = capsys.readouterr().out
    assert "--replay-event" in output
    assert "--list-event-revisions" in output
    assert "--revision" in output


def test_help_states_that_replay_does_not_advance_the_cursor(capsys):
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--help"])
    output = capsys.readouterr().out.replace("\n", " ")
    assert "cursor" in output


# ---------------------------------------------------------------------------
# Comportamento
# ---------------------------------------------------------------------------


def test_revision_without_replay_event_is_rejected(monkeypatch, tmp_path):
    from app import entrypoint

    monkeypatch.setattr(entrypoint, "configure_logging", lambda: entrypoint.logging.getLogger("t"))
    with pytest.raises(SystemExit) as exc:
        entrypoint.main(["--revision", "2"])
    assert exc.value.code == 2


def test_list_revisions_of_unknown_event_exits_with_error(monkeypatch, tmp_path):
    """Banco vazio em tmp_path: o comando sai com erro, sem estourar excecao."""
    from app import entrypoint

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(entrypoint, "configure_logging", lambda: entrypoint.logging.getLogger("t"))

    with pytest.raises(SystemExit) as exc:
        entrypoint.main(["--list-event-revisions", "evt-que-nao-existe"])
    assert exc.value.code == 1


def test_replay_of_unknown_event_exits_with_error(monkeypatch, tmp_path):
    from app import entrypoint

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(entrypoint, "configure_logging", lambda: entrypoint.logging.getLogger("t"))

    with pytest.raises(SystemExit) as exc:
        entrypoint.main(["--replay-event", "evt-que-nao-existe"])
    assert exc.value.code == 1


def test_read_commands_do_not_validate_ai_configuration(monkeypatch, tmp_path):
    """--list-event-revisions nao pode exigir chave Gemini nem subir scheduler."""
    from app import entrypoint

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(entrypoint, "configure_logging", lambda: entrypoint.logging.getLogger("t"))

    def _fail(*args, **kwargs):
        raise AssertionError("comando de leitura nao pode validar configuracao de runtime")

    monkeypatch.setattr(entrypoint, "validate_startup_configuration", _fail)
    monkeypatch.setattr(entrypoint, "initialize_database", _fail)
    monkeypatch.setattr(entrypoint, "run_forever", _fail)
    monkeypatch.setattr(entrypoint, "run_once", _fail)

    with pytest.raises(SystemExit):
        entrypoint.main(["--list-event-revisions", "evt-inexistente"])
