"""Rodizio de chaves do pool da Gemini.

Este arquivo era um SCRIPT: montava um `AIClient` com as chaves REAIS do `.env`
no import — ou seja, na COLETA do pytest — e falhava com
`ValueError: min() iterable argument is empty` em qualquer ambiente sem
credencial. Um clone limpo, um CI ou um `.env` sem chave derrubavam a suite
inteira antes de rodar um unico teste.

O comportamento sob teste nao precisa de chave de verdade: o pool so manipula
strings e relogio. As chaves aqui sao sinteticas de proposito.
"""

import logging
from time import monotonic

import pytest

from app.limiter import KeyPool

# Sem o prefixo `AIza`: ver a nota em `tests/test_api_logging.py`.
CHAVES = ("CHAVE-DE-TESTE-SEM-VALOR-0001", "CHAVE-DE-TESTE-SEM-VALOR-0002")


@pytest.fixture
def pool():
    return KeyPool(list(CHAVES))


def test_pool_entrega_uma_chave_pronta(pool):
    slot = pool.next_ready()
    assert slot.key in CHAVES
    assert slot.cooldown_until == 0.0


def test_chave_penalizada_entra_em_cooldown(pool):
    slot = pool.next_ready()
    pool.penalize(slot, retry_after=5)
    assert slot.cooldown_until > monotonic()


def test_rodizio_entrega_chave_diferente_apos_penalidade(pool):
    """O ponto do pool: uma chave punida nao volta na tentativa seguinte."""
    primeira = pool.next_ready()
    pool.penalize(primeira, retry_after=30)

    segunda = pool.next_ready()
    assert segunda.key != primeira.key


def test_com_todas_penalizadas_espera_ate_alguma_liberar(pool, monkeypatch):
    """Sem cortar o relogio, este teste dormiria o cooldown inteiro.

    `next_ready` bloqueia com `time.sleep` ate alguma chave sair do cooldown.
    Avancar o relogio prova a regra sem transformar a suite em espera real — e
    um `sleep(30)` dentro de um teste e a razao mais comum para alguem desligar
    a suite.
    """
    primeira = pool.next_ready()
    pool.penalize(primeira, retry_after=5)
    segunda = pool.next_ready()
    pool.penalize(segunda, retry_after=5)

    futuro = monotonic() + 60
    monkeypatch.setattr("app.limiter.monotonic", lambda: futuro)
    monkeypatch.setattr(
        "app.limiter.time.sleep",
        lambda _s: pytest.fail("nao deveria dormir: o cooldown ja passou"),
    )

    assert pool.next_ready().key in CHAVES


def test_pool_de_uma_chave_so_devolve_ela():
    assert KeyPool([CHAVES[0]]).next_ready().key == CHAVES[0]


def test_chave_nunca_aparece_inteira_no_log(caplog, pool):
    """Log de chave e sempre mascarado: `****` mais os quatro ultimos caracteres."""
    with caplog.at_level(logging.DEBUG, logger="app.limiter"):
        pool.penalize(pool.next_ready(), retry_after=1)

    registrado = "\n".join(record.getMessage() for record in caplog.records)
    assert CHAVES[0] not in registrado
    assert CHAVES[1] not in registrado
    assert "****" in registrado
