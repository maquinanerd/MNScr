"""Tres perdas que o log de 31/08/2026 mostrou sem chamar de erro.

Nenhuma delas aparecia como falha: uma materia sumiu marcada `FAILED_PERMANENT`,
outra saiu com a pior nota do dia depois de uma salvaguarda desistir na primeira
tentativa, e um limpador devolveu ZERO palavra sem reclamar — resgatado por um
fallback que varre a pagina inteira junto.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from bs4 import BeautifulSoup

from app import ai_validator, pipeline
from app.ai_client_gemini import AIClient
from app.extractor import _MAX_PALAVRAS_DE_CTA, _remove_cta_elements

# ── 1. O CTA que levava a materia junto ────────────────────────────────────

def _corpo(paragrafos: int = 30) -> BeautifulSoup:
    """Corpo com um `click here` enterrado num widget, como as paginas reais."""
    miolo = "".join(
        f"<p>Paragrafo {i} com texto editorial de verdade e varias palavras.</p>"
        for i in range(paragrafos)
    )
    html = f"<section>{miolo}<div class='widget'><p>Click here for more</p></div></section>"
    return BeautifulSoup(html, "html.parser")


def test_um_click_here_no_rodape_nao_derruba_o_corpo():
    """A regressao medida: um `<section>` de 1.247 palavras foi decomposto por
    causa dessa substring, e o limpador especifico devolveu ZERO palavra."""
    corpo = _corpo()
    antes = len(corpo.get_text(" ", strip=True).split())

    _remove_cta_elements(corpo, "teste")

    depois = len(corpo.get_text(" ", strip=True).split())
    assert depois > antes * 0.8, "o corpo tem de sobreviver"
    assert "Click here" not in corpo.get_text(), "e o CTA tem de sair"


def test_a_linha_de_cta_continua_sendo_removida():
    corpo = BeautifulSoup("<div><p>Bom texto aqui.</p><p>Follow us on X</p></div>", "html.parser")
    assert _remove_cta_elements(corpo, "teste") == 1
    assert "Follow us" not in corpo.get_text()
    assert "Bom texto" in corpo.get_text()


def test_paragrafo_longo_que_apenas_menciona_a_frase_fica():
    """Um CTA e uma LINHA. Acima do limite o elemento e corpo COM um CTA dentro."""
    longo = " ".join(["palavra"] * (_MAX_PALAVRAS_DE_CTA + 5)) + " read more"
    corpo = BeautifulSoup(f"<div><p>{longo}</p></div>", "html.parser")
    assert _remove_cta_elements(corpo, "teste") == 0


def test_conteiner_nunca_e_candidato():
    """`div`, `article` e `section` sao contêiner: varre-los foi o defeito."""
    corpo = BeautifulSoup("<div><span>Subscribe now</span></div>", "html.parser")
    _remove_cta_elements(corpo, "teste")
    assert corpo.find("div") is not None, "o contêiner nao pode ser decomposto"
    assert corpo.find("span") is None, "o texto do CTA sim"


class _BancoFalso:
    def __init__(self, fail_count: int = 0):
        self._fail_count = fail_count
        self.status = None
        self.reason = None

    def get_article_fail_count(self, article_id):
        return self._fail_count

    def update_article_status(self, article_id, status, retry_at=None, reason=None, **kwargs):
        self.status, self.reason = status, reason
        return True


# ── 2. O runaway que ninguem sabia medir ──────────────────────────────────

def test_o_raciocinio_do_modelo_aparece_nos_tokens():
    """Modelo com raciocinio gasta token que NAO aparece no texto.

    Em 31/08/2026 o db 226 foi morto como runaway com `Saida=31985` contra teto
    de 32000 — e o `raw_text` salvo (gravado inteiro, sem corte) tem 4.577
    bytes, ~1.150 tokens. Os outros 30 mil nao estavam na resposta, e ninguem
    sabia dizer onde estavam: o SDK expoe `thoughts_token_count` e o cliente
    nunca leu. Sem este numero nao da para saber se e o modelo escrevendo
    demais ou PENSANDO demais — e a resposta muda o conserto.
    """
    client = AIClient(keys=["test-key-1234"], min_interval_s=0)
    client.pool.next_ready = Mock(return_value=SimpleNamespace(key="test-key-1234"))
    client.rl.wait = Mock()
    response = SimpleNamespace(
        text='{"resultados":[]}',
        candidates=[],
        usage_metadata=SimpleNamespace(
            prompt_token_count=23299,
            candidates_token_count=31985,
            thoughts_token_count=30800,
            cached_content_token_count=0,
            total_token_count=55284,
        ),
    )
    with patch("app.ai_client_gemini._generate_content", return_value=response):
        _, tokens = client.generate_text("prompt", generation_config={"max_output_tokens": 32000})

    assert tokens["thoughts_tokens"] == 30800


def test_o_total_vem_da_api_e_nao_da_soma_manual():
    """A soma `entrada + saida` ignora raciocinio e subestima o gasto real."""
    client = AIClient(keys=["test-key-1234"], min_interval_s=0)
    client.pool.next_ready = Mock(return_value=SimpleNamespace(key="test-key-1234"))
    client.rl.wait = Mock()
    response = SimpleNamespace(
        text="{}", candidates=[],
        usage_metadata=SimpleNamespace(
            prompt_token_count=100, candidates_token_count=200,
            thoughts_token_count=900, cached_content_token_count=0,
            total_token_count=1200,
        ),
    )
    with patch("app.ai_client_gemini._generate_content", return_value=response) as _:
        _, tokens = client.generate_text("prompt", generation_config={"max_output_tokens": 32000})
    assert tokens["thoughts_tokens"] == 900


def test_um_modelo_sem_raciocinio_continua_reportando_zero():
    """Nao pode quebrar quem nao expoe o campo."""
    client = AIClient(keys=["test-key-1234"], min_interval_s=0)
    client.pool.next_ready = Mock(return_value=SimpleNamespace(key="test-key-1234"))
    client.rl.wait = Mock()
    response = SimpleNamespace(
        text="{}", candidates=[],
        usage_metadata=SimpleNamespace(
            prompt_token_count=10, candidates_token_count=20,
            cached_content_token_count=0,
        ),
    )
    with patch("app.ai_client_gemini._generate_content", return_value=response):
        _, tokens = client.generate_text("prompt", generation_config={"max_output_tokens": 32000})
    assert tokens["thoughts_tokens"] == 0


def test_o_runaway_continua_terminal():
    """Nao mudei isto: `test_ai_runaway.py` fixa a decisao, e mudar comportamento
    por inferencia — sem saber quanto foi raciocinio — seria palpite."""
    db = _BancoFalso()
    reason = "TRUNCATED_BY_TOKEN_CEILING: runaway_token_ceiling"
    assert pipeline._handle_ai_processing_failure(db, 226, reason) == "FAILED_PERMANENT"


# ── 3. O validador que desistia de uma resposta vazia ──────────────────────

class _ClienteFalso:
    def __init__(self, respostas):
        self.respostas = list(respostas)
        self.chamadas = 0

    def generate_text(self, prompt, **kwargs):
        self.chamadas += 1
        resposta = self.respostas.pop(0) if self.respostas else ""
        return resposta, {"prompt_tokens": 10, "completion_tokens": 0, "model": "falso"}


BOM = '{"titulo_final": "T", "conteudo_final": "<p>C</p>", "meta_description": "M", "slug": "s"}'


@pytest.fixture
def validador_ligado(monkeypatch):
    monkeypatch.setattr(ai_validator, "AI_VALIDATOR_ENABLED", True)


def test_resposta_vazia_ganha_um_reenvio(validador_ligado):
    """`Saida=0` e o modelo nao respondendo — custa zero token e e transitorio."""
    cliente = _ClienteFalso(["", BOM])
    ai_validator.validate_and_fix_ai_json({"titulo_final": "x"}, api_client=cliente)
    assert cliente.chamadas == 2


def test_resposta_mal_formada_nao_ganha_reenvio(validador_ligado):
    """Recusa nao vazia custa a saida inteira; repetir raramente ajuda."""
    cliente = _ClienteFalso(["isto nao e json", "isto tambem nao"])
    ai_validator.validate_and_fix_ai_json({"titulo_final": "x"}, api_client=cliente)
    assert cliente.chamadas == 1


def test_o_reenvio_por_vazio_tem_limite(validador_ligado):
    """Vazio atras de vazio nao pode virar laco."""
    cliente = _ClienteFalso(["", "", "", ""])
    ai_validator.validate_and_fix_ai_json({"titulo_final": "x"}, api_client=cliente)
    assert cliente.chamadas == ai_validator.AI_VALIDATOR_MAX_RETRIES + ai_validator.AI_VALIDATOR_EMPTY_RETRIES
