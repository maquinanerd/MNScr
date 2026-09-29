"""Teto diário de gasto com IA (app/ai_spend.py) e onde ele trava o pipeline."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from app import ai_spend, config, pipeline
from app.ai_client_gemini import AIClient
from app.exceptions import BlockedPromptError
from app.teto import local_day, parse_budget_usd, seconds_until_next_day

# 12:00 em São Paulo.
MEIO_DIA = datetime(2026, 9, 29, 15, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _sem_gasto_pendente():
    ai_spend._pending.clear()
    yield
    ai_spend._pending.clear()


def _tokens(prompt=0, completion=0, thoughts=0):
    return {"prompt_tokens": prompt, "completion_tokens": completion, "thoughts_tokens": thoughts}


@pytest.fixture
def teto(monkeypatch):
    def definir(valor):
        monkeypatch.setattr(config, "AI_DAILY_BUDGET_USD", valor)

    definir(1.0)
    return definir


# --- custo -----------------------------------------------------------------


def test_custo_de_uma_materia_media_no_modelo_do_redator():
    # 17 mil de entrada e 2 mil de saída: o redator típico medido até 29/09/2026.
    custo = ai_spend.call_cost_usd("gemini-3.1-flash-lite", _tokens(17_000, 2_000))
    assert custo == pytest.approx(17_000 * 0.25 / 1e6 + 2_000 * 1.50 / 1e6)


def test_raciocinio_e_cobrado_como_saida():
    sem = ai_spend.call_cost_usd("gemini-3.1-flash-lite", _tokens(1_000, 1_000))
    com = ai_spend.call_cost_usd("gemini-3.1-flash-lite", _tokens(1_000, 1_000, thoughts=4_000))
    assert com - sem == pytest.approx(4_000 * 1.50 / 1e6)


def test_modelo_fora_da_tabela_conta_pelo_mais_caro():
    desconhecido = ai_spend.call_cost_usd("gemini-9-ultra", _tokens(1_000_000, 1_000_000))
    assert desconhecido == pytest.approx(0.30 + 2.50)


# --- dia de São Paulo -------------------------------------------------------


def test_o_dia_e_o_de_sao_paulo_e_nao_o_de_utc():
    # 02:30 UTC do dia 30 ainda é 23:30 do dia 29 em São Paulo.
    assert local_day(datetime(2026, 9, 30, 2, 30, tzinfo=timezone.utc)) == "2026-09-29"
    assert local_day(datetime(2026, 9, 30, 3, 0, tzinfo=timezone.utc)) == "2026-09-30"


def test_segundos_ate_a_meia_noite_de_sao_paulo():
    assert seconds_until_next_day(MEIO_DIA) == 12 * 3600


# --- valor do teto ----------------------------------------------------------


@pytest.mark.parametrize(
    ("bruto", "esperado"),
    [
        ("1.00", 1.0),
        ("1,50", 1.5),
        (" 2 ", 2.0),
        ("0", 0.0),
        ("-1", None),
        ("um dolar", None),
        ("nan", None),
        ("inf", None),
        ("+inf", None),
        ("1e309", None),
    ],
)
def test_valor_do_teto(bruto, esperado):
    assert parse_budget_usd(bruto) == esperado


def test_teto_invalido_impede_o_robo_de_subir(monkeypatch):
    monkeypatch.setattr(config, "AI_DAILY_BUDGET_USD", None)
    monkeypatch.setattr(config, "AI_DAILY_BUDGET_USD_RAW", "um dolar")
    assert any("MNSCR_AI_DAILY_BUDGET_USD" in issue for issue in config.get_runtime_config_issues())


def test_os_modelos_padrao_tem_preco():
    assert ai_spend.unpriced_models() == []


def test_modelo_configurado_sem_preco_impede_o_robo_de_subir(monkeypatch):
    monkeypatch.setattr(config, "AI_DAILY_BUDGET_USD", 1.0)
    monkeypatch.setattr(config, "FACTUAL_MODEL", "gemini-3.5-flash")
    issues = config.get_runtime_config_issues()
    assert any("gemini-3.5-flash" in issue and "PRICES_USD_PER_MTOK" in issue for issue in issues)

    # Com o teto desligado, a conta não importa.
    monkeypatch.setattr(config, "AI_DAILY_BUDGET_USD", 0.0)
    assert not any("PRICES_USD_PER_MTOK" in issue for issue in config.get_runtime_config_issues())


# --- soma e trava ------------------------------------------------------------


def test_gasto_soma_no_dia_e_trava_ao_chegar_no_teto(tmp_path, teto):
    banco = str(tmp_path / "app.db")
    teto(0.009)

    ai_spend.record_call("gemini-3.1-flash-lite", _tokens(17_000, 2_000), db_path=banco, now=MEIO_DIA)
    assert not ai_spend.budget_status(db_path=banco, now=MEIO_DIA).exhausted

    total = ai_spend.record_call("gemini-2.5-flash-lite", _tokens(2_000, 4_000), db_path=banco, now=MEIO_DIA)
    status = ai_spend.budget_status(db_path=banco, now=MEIO_DIA)
    assert total == pytest.approx(status.spent_usd)
    assert status.spent_usd == pytest.approx(0.00725 + 0.0018)
    assert status.exhausted


def test_o_dia_seguinte_comeca_do_zero(tmp_path, teto):
    banco = str(tmp_path / "app.db")
    teto(0.001)
    ai_spend.record_call("gemini-3.1-flash-lite", _tokens(17_000, 2_000), db_path=banco, now=MEIO_DIA)
    assert ai_spend.budget_status(db_path=banco, now=MEIO_DIA).exhausted

    amanha = datetime(2026, 9, 30, 3, 0, tzinfo=timezone.utc)
    status = ai_spend.budget_status(db_path=banco, now=amanha)
    assert status.day == "2026-09-30"
    assert status.spent_usd == 0
    assert not status.exhausted


def test_gasto_que_o_banco_recusou_conta_no_teto_e_entra_na_proxima_gravacao(tmp_path, teto, monkeypatch):
    banco = str(tmp_path / "app.db")
    teto(0.009)
    conectar = ai_spend._connect

    def banco_travado(_db_path):
        raise ai_spend.sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(ai_spend, "_connect", banco_travado)
    assert ai_spend.record_call("gemini-3.1-flash-lite", _tokens(17_000, 2_000), db_path=banco, now=MEIO_DIA) is None

    monkeypatch.setattr(ai_spend, "_connect", conectar)
    assert ai_spend.budget_status(db_path=banco, now=MEIO_DIA).spent_usd == pytest.approx(0.00725)

    total = ai_spend.record_call("gemini-2.5-flash-lite", _tokens(2_000, 4_000), db_path=banco, now=MEIO_DIA)
    assert total == pytest.approx(0.00725 + 0.0018)
    assert ai_spend._pending == {}
    status = ai_spend.budget_status(db_path=banco, now=MEIO_DIA)
    assert status.spent_usd == pytest.approx(0.00725 + 0.0018)
    assert status.exhausted


def test_banco_ilegivel_fecha_a_trava(tmp_path, teto, monkeypatch):
    def banco_travado(_db_path):
        raise ai_spend.sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(ai_spend, "_connect", banco_travado)
    status = ai_spend.budget_status(db_path=str(tmp_path / "app.db"), now=MEIO_DIA)
    assert status.unreadable
    assert status.exhausted


def test_teto_zero_desliga_a_trava_sem_abrir_o_banco(tmp_path, teto):
    teto(0.0)
    banco = tmp_path / "nao-existe" / "app.db"
    status = ai_spend.budget_status(db_path=str(banco), now=MEIO_DIA)
    assert not status.enabled
    assert not status.exhausted
    assert not banco.exists()


# --- o cliente do Gemini soma toda resposta paga -----------------------------


def _cliente(recorder):
    client = AIClient(keys=["test-key-1234"], min_interval_s=0, spend_recorder=recorder)
    client.pool.next_ready = Mock(return_value=SimpleNamespace(key="test-key-1234"))
    client.pool.penalize = Mock()
    client.rl.wait = Mock()
    return client


def _resposta(texto='{"ok": true}', prompt=1_200, saida=300, raciocinio=50, bloqueada=False):
    candidato = SimpleNamespace(content=SimpleNamespace(parts=[SimpleNamespace(text=texto)]))
    return SimpleNamespace(
        candidates=[] if bloqueada else [candidato],
        prompt_feedback=SimpleNamespace(block_reason="PROHIBITED_CONTENT" if bloqueada else None),
        usage_metadata=SimpleNamespace(
            prompt_token_count=prompt,
            candidates_token_count=saida,
            thoughts_token_count=raciocinio,
            total_token_count=prompt + saida + raciocinio,
        ),
    )


def test_cliente_entrega_modelo_e_tokens_de_cada_resposta():
    recorder = Mock()
    with patch("app.ai_client_gemini._generate_content", return_value=_resposta()):
        _cliente(recorder).generate_text("prompt", model_override="gemini-2.5-flash-lite")

    modelo, tokens = recorder.call_args.args
    assert modelo == "gemini-2.5-flash-lite"
    assert (tokens["prompt_tokens"], tokens["completion_tokens"], tokens["thoughts_tokens"]) == (1_200, 300, 50)


def test_prompt_bloqueado_tambem_e_somado_porque_a_entrada_foi_cobrada():
    recorder = Mock()
    with patch("app.ai_client_gemini._generate_content", return_value=_resposta(saida=0, bloqueada=True)):
        with pytest.raises(BlockedPromptError):
            _cliente(recorder).generate_text("prompt")
    recorder.assert_called_once()


def test_falha_ao_somar_nao_derruba_a_chamada_ja_paga():
    recorder = Mock(side_effect=RuntimeError("banco travado"))
    with patch("app.ai_client_gemini._generate_content", return_value=_resposta()):
        texto, _ = _cliente(recorder).generate_text("prompt")
    assert texto == '{"ok": true}'


def test_o_processador_liga_o_cliente_ao_teto():
    from app import ai_processor

    assert "spend_recorder=ai_spend.record_call" in open(ai_processor.__file__, encoding="utf-8").read()


# --- onde o pipeline trava ----------------------------------------------------


@pytest.fixture
def dia_estourado(monkeypatch):
    status = ai_spend.BudgetStatus(day="2026-09-29", spent_usd=1.02, budget_usd=1.0)
    monkeypatch.setattr(pipeline.ai_spend, "budget_status", lambda **_kw: status)
    monkeypatch.setattr(pipeline, "_ai_budget_notice_day", None)
    return status


def test_teto_batido_devolve_a_espera_ate_a_proxima_checagem(dia_estourado, monkeypatch):
    monkeypatch.setattr(pipeline.teto, "seconds_until_next_day", lambda: 40.0)
    assert pipeline._ai_daily_budget_pause() == 41.0
    monkeypatch.setattr(pipeline.teto, "seconds_until_next_day", lambda: 8 * 3600)
    assert pipeline._ai_daily_budget_pause() == pipeline.AI_BUDGET_RECHECK_S


def test_banco_ilegivel_espera_so_ate_a_proxima_checagem(monkeypatch):
    ilegivel = ai_spend.BudgetStatus(day="2026-09-29", spent_usd=0.0, budget_usd=1.0, unreadable=True)
    monkeypatch.setattr(pipeline.ai_spend, "budget_status", lambda **_kw: ilegivel)
    monkeypatch.setattr(pipeline.teto, "seconds_until_next_day", lambda: 8 * 3600)
    assert pipeline._ai_daily_budget_pause() == pipeline.AI_BUDGET_RECHECK_S


def test_com_saldo_nao_ha_espera(monkeypatch):
    livre = ai_spend.BudgetStatus(day="2026-09-29", spent_usd=0.4, budget_usd=1.0)
    monkeypatch.setattr(pipeline.ai_spend, "budget_status", lambda **_kw: livre)
    assert pipeline._ai_daily_budget_pause() is None


def test_once_para_antes_de_pegar_materia_e_deixa_a_fila_intacta(dia_estourado, monkeypatch):
    class Fila:
        def __len__(self):
            return 3

    class DB:
        def recover_stale_processing_claims(self, **_kwargs):
            return {"requeued": 0, "draft_recovered": 0, "failed_permanent": 0, "still_alive": 0}

        def reconcile_rssprime_event_tasks(self):
            return []

        def close(self):
            pass

    monkeypatch.setattr(pipeline, "article_queue", Fila())
    monkeypatch.setattr(pipeline, "Database", DB)
    monkeypatch.setattr(pipeline, "_process_one_queued_article", lambda: pytest.fail("nao pode pegar materia"))
    monkeypatch.setattr(pipeline, "run_pipeline_cycle", lambda **_kwargs: pytest.fail("nao deve ingerir"))

    result = pipeline.run_pipeline_once(deadline_seconds=60, max_items=5)

    assert result.stop_reason == "ai_daily_budget"
    assert result.exit_code == pipeline.EXIT_DEADLINE_EXCEEDED
    assert result.claimed == 0
    assert result.remaining == 3


def test_worker_confere_o_teto_antes_do_claim():
    from inspect import getsource

    source = getsource(pipeline.worker_loop)
    assert source.index("_ai_daily_budget_pause()") < source.index("article_queue.pop_claimed(claim_owner)")


def test_reprocessamento_manual_respeita_o_teto(dia_estourado, monkeypatch):
    monkeypatch.setattr(pipeline, "Database", lambda: pytest.fail("nao deve tocar no artigo"))
    evento = SimpleNamespace(event_key="evt", revision=1)
    with pytest.raises(RuntimeError, match="Teto diario de IA"):
        pipeline.process_stored_event(evento)
