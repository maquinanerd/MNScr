"""Painel só leitura do MNScr (app/painel): acesso, páginas e a promessa de não gravar no robô."""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import ai_spend, pipeline
from app.cinerie_store import CinerieStore
from app.painel import __main__ as painel_main
from app.painel import dados
from app.painel.acesso import LoginThrottle, PainelStore, session_secret
from app.painel.web import create_app
from app.store import Database

SENHA = "senha-bem-comprida"
# 12:00 de 29/09/2026 em São Paulo.
MEIO_DIA = datetime(2026, 9, 29, 15, 0, tzinfo=timezone.utc)


def _publicacao(conn, n, *, quando, status="COMPLETED", outcome="PUBLISHED", slug=None, erro=None):
    iso = quando.isoformat()
    conn.execute(
        """
        INSERT INTO cinerie_publications (request_id, idempotency_key, draft_id, source_cluster_id, source_revision,
            delivery_mode, contract_name, contract_version, schema_hash, public_author_id, publication_intent,
            delivery_status, payload_outcome, canonical_slug, reason_codes, delivered_at, last_delivery_error_code,
            http_status, created_at, updated_at)
        VALUES (?, ?, ?, 'c', 1, 'AUTO_PUBLISH', 'x', '1', 'h', 'a', 'publish', ?, ?, ?, '[]', ?, ?, 201, ?, ?)
        """,
        (f"req-{n}", f"idem-{n}", f"draft-{n}", status, outcome, slug or f"materia-numero-{n}",
         iso if status == "COMPLETED" else None, erro, iso, iso),
    )


@pytest.fixture
def robo(tmp_path, monkeypatch):
    """Um banco do robô com o schema real e um pouco de tudo."""
    caminho = str(tmp_path / "app.db")
    db = Database(caminho)
    db.initialize()
    db.close()
    CinerieStore(caminho).conn.close()
    ai_spend._connect(caminho).close()

    conn = sqlite3.connect(caminho)
    with conn:
        # Hoje em São Paulo: três publicadas, uma delas às 01:00 (04:00 UTC).
        _publicacao(conn, 1, quando=datetime(2026, 9, 29, 4, 0, tzinfo=timezone.utc), slug="batman-parte-ii-ganha-data")
        _publicacao(conn, 2, quando=MEIO_DIA - timedelta(hours=2))
        _publicacao(conn, 3, quando=MEIO_DIA - timedelta(hours=1))
        # 23:30 de ontem em São Paulo, mas já dia 29 em UTC.
        _publicacao(conn, 4, quando=datetime(2026, 9, 29, 2, 30, tzinfo=timezone.utc))
        _publicacao(conn, 5, quando=MEIO_DIA, status="FAILED_PERMANENT", outcome="BLOCKED", erro="SCHEMA_REJECTED")
        conn.execute(
            "INSERT INTO ai_spend_daily (day, model, usd, calls, updated_at) VALUES "
            "('2026-09-29', 'gemini-3.1-flash-lite', 0.03, 4, 'x'), ('2026-09-29', 'gemini-2.5-flash-lite', 0.003, 3, 'x')"
        )
        conn.execute(
            "INSERT OR REPLACE INTO pipeline_state (key, value) VALUES (?, ?)",
            (dados.CICLO_KEY, (MEIO_DIA - timedelta(minutes=5)).isoformat()),
        )
        for i, (status, motivo) in enumerate(
            [
                ("NEW", None),
                ("NEW", None),
                ("PROCESSING", None),
                ("FAILED", "Cluster: fontes validas insuficientes"),
                ("FAILED", "EARLY_EVENT_KEY_AUSENTE: sem agrupamento"),
                ("FAILED_PERMANENT", "runaway_token_ceiling"),
            ]
        ):
            conn.execute(
                "INSERT INTO seen_articles (source_id, external_id, url, normalized_title, status, fail_reason, inserted_at) "
                "VALUES ('rssprime', ?, ?, ?, ?, ?, '2026-09-29 14:00:00')",
                (f"ext-{i}", f"https://fonte.test/{i}", f"titulo original {i}", status, motivo),
            )
    conn.close()
    return caminho


def _digest(path: str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@pytest.fixture
def store(tmp_path):
    return PainelStore(str(tmp_path / "painel.db"))


@pytest.fixture
def client(store, robo):
    app = create_app(store, robot_db=robo, teto=2.0, secure_cookies=False, base_url="https://cinerie.com/pt/noticias")
    return TestClient(app, follow_redirects=False)


def _csrf(response) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', response.text)
    assert match, response.text
    return match.group(1)


@pytest.fixture
def admin(client, store):
    code = store.ensure_setup_code()
    page = client.get("/setup")
    response = client.post(
        "/setup",
        data={"code": code, "user": "pablo", "password": SENHA, "confirm": SENHA, "csrf": _csrf(page)},
    )
    assert response.status_code == 303 and response.headers["location"] == "/"
    return client


# --- acesso -----------------------------------------------------------------------


def test_health_e_publico_e_nao_diz_nada_do_robo(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_sem_administrador_tudo_leva_ao_primeiro_acesso(client):
    for path in ("/", "/publicacoes", "/gasto", "/falhas", "/login"):
        response = client.get(path)
        assert response.status_code == 303
        assert response.headers["location"] == "/setup"


def test_primeiro_acesso_exige_o_codigo_do_log(client, store):
    store.ensure_setup_code()
    page = client.get("/setup")
    response = client.post(
        "/setup",
        data={"code": "ERRADO00", "user": "pablo", "password": SENHA, "confirm": SENHA, "csrf": _csrf(page)},
    )
    assert response.headers["location"] == "/setup"
    assert not store.admin_exists()


def test_formulario_sem_csrf_e_recusado(client, store):
    code = store.ensure_setup_code()
    response = client.post("/setup", data={"code": code, "user": "u", "password": SENHA, "confirm": SENHA})
    assert response.status_code == 400
    assert not store.admin_exists()


def test_login_errado_e_limitado(admin, store):
    admin.cookies.clear()
    page = admin.get("/login")
    csrf = _csrf(page)
    for _ in range(5):
        admin.post("/login", data={"user": "pablo", "password": "errada-errada", "csrf": csrf})
    # A sexta, mesmo com a senha certa, espera a janela passar.
    admin.post("/login", data={"user": "pablo", "password": SENHA, "csrf": csrf})
    assert admin.get("/").headers["location"] == "/login"


def test_sair_invalida_inclusive_um_cookie_copiado(admin):
    copiado = dict(admin.cookies)
    page = admin.get("/")
    assert page.status_code == 200
    admin.post("/logout", data={"csrf": _csrf(page)})

    outro = TestClient(admin.app, follow_redirects=False, cookies=copiado)
    assert outro.get("/").headers["location"] == "/login"


def test_visitante_anonimo_nao_derruba_a_sessao_do_administrador(admin, store):
    epoca = store.session_epoch()
    visitante = TestClient(admin.app, follow_redirects=False)
    page = visitante.get("/login")
    visitante.post("/logout", data={"csrf": _csrf(page)})
    assert store.session_epoch() == epoca
    assert admin.get("/").status_code == 200


def test_senha_esquecida_gera_codigo_novo_e_derruba_as_sessoes(admin, store, monkeypatch, capsys):
    monkeypatch.setattr(painel_main, "_paths", lambda: ("robo.db", store.db_path))
    assert painel_main.main(["--novo-acesso"]) == 0
    code = re.search(r"Código novo: ([0-9A-F]{16})\b", capsys.readouterr().out).group(1)
    assert store.setup_code() == code
    assert not store.admin_exists()
    assert admin.get("/").headers["location"] == "/setup"


def test_segredo_da_sessao_fica_num_arquivo_0600_fora_do_banco(store):
    segredo = session_secret(store)
    arquivo = Path(store.db_path).with_name("painel.secret")
    assert arquivo.read_text(encoding="ascii").strip() == segredo
    assert session_secret(store) == segredo
    if os.name == "posix":
        assert (arquivo.stat().st_mode & 0o777) == 0o600
    conn = sqlite3.connect(store.db_path)
    assert segredo not in str(conn.execute("SELECT * FROM painel_settings").fetchall())
    conn.close()


def test_x_forwarded_for_forjado_nao_escapa_do_limite_de_login(store, robo):
    # Como em produção: o uvicorn só aceita o cabeçalho do proxy (rede privada) e anda
    # da direita para a esquerda até o primeiro IP fora dela — o que o proxy acrescentou.
    # O que o cliente forja fica à esquerda e não conta.
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

    store.ensure_setup_code()
    app = create_app(store, robot_db=robo, teto=2.0, secure_cookies=False)
    proxied = ProxyHeadersMiddleware(app, trusted_hosts="127.0.0.1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16")
    client = TestClient(proxied, follow_redirects=False, client=("172.18.0.2", 40000))
    csrf = _csrf(client.get("/setup"))
    avisos = []
    for tentativa in range(6):
        forjado = f"198.51.100.{tentativa}, 10.0.0.{tentativa}, 203.0.113.7"
        response = client.post(
            "/setup",
            data={"code": "ERRADO", "user": "x", "password": SENHA, "confirm": SENHA, "csrf": csrf},
            headers={"X-Forwarded-For": forjado},
        )
        assert response.status_code == 303
        avisos.append("muitas tentativas" in client.get("/setup").text)
    assert avisos == [False] * 5 + [True]


def test_limite_de_login_conta_ipv6_pela_rede():
    throttle = LoginThrottle(limit=2)
    assert throttle.attempt("2001:db8::1")
    assert throttle.attempt("2001:db8::2")
    assert not throttle.attempt("2001:db8::ffff")


def test_cabecalhos_de_seguranca_em_toda_resposta(client):
    response = client.get("/health")
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"


# --- páginas ------------------------------------------------------------------------


def test_paginas_abrem_e_nao_gravam_nada_no_banco_do_robo(admin, robo):
    antes = _digest(robo)
    for path in ("/", "/publicacoes", "/gasto", "/falhas"):
        response = admin.get(path)
        assert response.status_code == 200, path
    assert _digest(robo) == antes
    assert not Path(robo + "-wal").exists() or Path(robo + "-wal").stat().st_size == 0


def test_visao_geral_mostra_o_robo(admin):
    page = admin.get("/").text
    assert "Batman parte ii ganha data" in page
    assert 'href="https://cinerie.com/pt/noticias/batman-parte-ii-ganha-data"' in page
    assert "Fila" in page and "sendo escrita agora" in page
    assert "1 envio(s) ao Cinerie" in page


def test_dados_da_visao_no_dia_de_sao_paulo(robo):
    v = dados.visao(robo, teto=2.0, base_url="https://cinerie.com/pt/noticias", now=MEIO_DIA)
    assert v.banco_ok
    assert v.publicadas_hoje == 3
    assert v.publicadas_ontem == 1
    assert v.gasto_hoje == pytest.approx(0.033)
    assert v.teto == 2.0 and v.teto_pct == 2 and not v.teto_atingido
    assert v.custo_medio == pytest.approx(0.033 / 3)
    assert v.fila == {"NEW": 2, "PROCESSING": 1}
    assert v.envios_com_problema == 1
    assert not v.ciclo_atrasado
    assert v.ultimo_ciclo.strftime("%H:%M") == "11:55"


def test_robo_sem_ciclo_ha_muito_tempo_aparece_parado(robo):
    v = dados.visao(robo, teto=2.0, now=MEIO_DIA + timedelta(hours=2))
    assert v.ciclo_atrasado


def test_teto_batido_aparece_na_visao(robo):
    v = dados.visao(robo, teto=0.03, now=MEIO_DIA)
    assert v.teto_atingido and v.teto_pct == 100


def test_falhas_separam_erro_de_descarte_do_filtro(robo):
    f = dados.falhas(robo)
    assert f.motivos_erro == [("runaway_token_ceiling", 1)]
    assert dict(f.motivos_filtro) == {
        "Cluster: fontes validas insuficientes": 1,
        "EARLY_EVENT_KEY_AUSENTE: sem agrupamento": 1,
    }
    assert [e.motivo for e in f.erros] == ["runaway_token_ceiling"]
    assert len(f.envios) == 1 and not f.envios[0].ok
    assert f.envios[0].situacao == "barrada pelo Cinerie"
    assert "SCHEMA_REJECTED" in f.envios[0].detalhe
    assert f.envios[0].url is None


def test_gasto_por_dia_com_custo_por_materia(robo):
    dias = dados.gasto(robo, now=MEIO_DIA)
    hoje = dias[0]
    assert hoje.dia.isoformat() == "2026-09-29"
    assert hoje.chamadas == 7 and hoje.publicadas == 3
    assert hoje.custo_por_materia == pytest.approx(0.011)
    assert [modelo for modelo, _usd, _calls in hoje.por_modelo] == ["gemini-3.1-flash-lite", "gemini-2.5-flash-lite"]
    # Ontem saiu matéria, mas a conta de gasto não tem linha: não é "US$ 0".
    ontem = dias[1]
    assert ontem.dia.isoformat() == "2026-09-28" and ontem.publicadas == 1
    assert not ontem.medido and ontem.custo_por_materia is None


def test_antes_da_virada_as_paginas_explicam_em_vez_de_quebrar(admin, tmp_path, store):
    vazio = create_app(store, robot_db=str(tmp_path / "nao-chegou.db"), teto=2.0, secure_cookies=False)
    outro = TestClient(vazio, follow_redirects=False, cookies=dict(admin.cookies))
    for path in ("/", "/publicacoes", "/gasto", "/falhas"):
        response = outro.get(path)
        assert response.status_code == 200, path
        assert "ainda não chegou" in response.text
    assert not (tmp_path / "nao-chegou.db").exists()


def test_base_dos_links_vem_da_variavel_e_so_aceita_http(monkeypatch):
    monkeypatch.setenv("CINERIE_PUBLIC_BASE_URL", "https://cinerie.com/pt/noticias")
    assert dados.public_base_url() == "https://cinerie.com/pt/noticias"
    monkeypatch.setenv("CINERIE_PUBLIC_BASE_URL", "javascript:alert(1)")
    assert dados.public_base_url() == ""
    monkeypatch.delenv("CINERIE_PUBLIC_BASE_URL")
    assert dados.public_base_url() == ""


def test_url_de_feed_que_nao_e_http_nao_vira_link(admin, robo):
    conn = sqlite3.connect(robo)
    with conn:
        conn.execute(
            "INSERT INTO seen_articles (source_id, external_id, url, normalized_title, status, fail_reason) "
            "VALUES ('rssprime', 'hostil', 'javascript:alert(1)', 'titulo hostil', 'FAILED', 'erro qualquer')"
        )
    conn.close()
    page = admin.get("/falhas").text
    assert "titulo hostil" in page
    assert "javascript:" not in page


def test_o_painel_nao_carrega_a_configuracao_nem_as_chaves_do_robo():
    # app/config.py lê o `.env` do robô e acusa a falta de chave de IA: nada disso é do
    # painel. Um processo novo, para o sys.modules deste não interferir.
    codigo = (
        "import sys; import app.painel.__main__, app.painel.web, app.painel.dados; "
        "print('app.config' in sys.modules, 'app.ai_spend' in sys.modules, 'app.pipeline' in sys.modules)"
    )
    saida = subprocess.run([sys.executable, "-c", codigo], capture_output=True, text=True, check=True)
    assert saida.stdout.strip() == "False False False"


# --- o robô marca o ciclo ---------------------------------------------------------------


def test_o_painel_le_a_mesma_chave_que_o_robo_grava():
    assert dados.CICLO_KEY == pipeline.CYCLE_HEARTBEAT_KEY


def test_robo_registra_o_inicio_do_ciclo(tmp_path, monkeypatch):
    caminho = str(tmp_path / "app.db")
    db = Database(caminho)
    db.initialize()
    db.close()
    monkeypatch.setattr(pipeline, "Database", lambda *a, **k: Database(caminho))
    pipeline._mark_cycle_heartbeat()
    v = dados.visao(caminho, teto=2.0, now=datetime.now(timezone.utc))
    assert v.ultimo_ciclo is not None and not v.ciclo_atrasado
