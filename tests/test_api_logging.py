"""Mascaramento da chave de API no log.

Este arquivo era um SCRIPT, e o pior dos tres desta rodada: no import — portanto
na COLETA do pytest — ele fazia `load_dotenv(override=True)`, instanciava o
`AIProcessor` com as chaves REAIS e chamava `ai.rewrite_batch(...)`. Com
credencial presente, rodar `pytest` disparava uma chamada de verdade a Gemini e
gastava cota; sem credencial, derrubava a coleta da suite inteira com
`AIProcessorError`.

O que realmente importa aqui e uma regra de seguranca, e ela se prova sem rede:
a chave aparece SEMPRE mascarada. Log que vaza credencial e incidente, e log e
exatamente o que as pessoas colam em issue, chat e print de tela.
"""

import logging

import pytest

from app.ai_client_gemini import AIClient
from app.limiter import KeyPool

# Sem o prefixo `AIza` de proposito: `test_no_api_key_literals_anywhere` varre o
# repositorio atras desse padrao, e uma chave de mentira com forma de chave de
# verdade treina o time a ignorar o alarme que existe para o caso real.
CHAVE = "CHAVE-DE-TESTE-SEM-VALOR-abcdNurQ"
SUFIXO = CHAVE[-4:]


@pytest.fixture
def registrado(caplog):
    caplog.set_level(logging.DEBUG)
    return lambda: "\n".join(record.getMessage() for record in caplog.records)


def test_uso_de_chave_e_logado_mascarado(registrado):
    KeyPool([CHAVE]).next_ready()
    texto = registrado()
    assert CHAVE not in texto
    assert f"****{SUFIXO}" in texto


def test_penalidade_de_chave_e_logada_mascarada(registrado):
    pool = KeyPool([CHAVE])
    pool.penalize(pool.next_ready(), retry_after=1)
    texto = registrado()
    assert CHAVE not in texto
    assert f"****{SUFIXO}" in texto


def test_inicializacao_do_cliente_nao_imprime_chave(registrado):
    AIClient(keys=[CHAVE], min_interval_s=0.01)
    assert CHAVE not in registrado()


def test_chave_usada_e_exposta_mascarada():
    """`get_last_used_key` alimenta o log do pipeline: nunca devolve a chave crua."""
    client = AIClient(keys=[CHAVE], min_interval_s=0.01)
    client.last_used_key = CHAVE
    exposta = client.get_last_used_key()
    assert exposta == f"****{SUFIXO}"
    assert CHAVE not in exposta


def test_sem_chamada_alguma_a_chave_usada_e_desconhecida():
    assert AIClient(keys=[CHAVE], min_interval_s=0.01).get_last_used_key() == "UNKNOWN"


def test_a_suite_nao_depende_de_credencial_real():
    """Regressao: coletar e rodar a suite nao pode exigir GEMINI_KEY no ambiente.

    Tres arquivos faziam trabalho no import — chamada de rede, leitura do log de
    producao e montagem de cliente com chave real. Num ambiente limpo a suite
    nao COLETAVA, e o erro apontava para o arquivo errado.
    """
    assert KeyPool([CHAVE]).next_ready().key == CHAVE
