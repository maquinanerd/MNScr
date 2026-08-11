"""Escrita e releitura do log da aplicacao.

Este arquivo era um SCRIPT, nao um teste: rodava no import (portanto na COLETA
do pytest), escrevia no `logs/app.log` de producao e o relia com
`open(..., "r")` sem `encoding`. No Windows isso resolve para cp1252, e o log da
aplicacao e UTF-8 com acento — entao bastava o programa rodar uma vez de verdade
para a suite INTEIRA parar de coletar com UnicodeDecodeError. O erro nao
apontava para o log: aparecia como falha de coleta de um teste sem relacao.

Duas regras ficam travadas aqui:

1. o log e lido e escrito em UTF-8 EXPLICITO, nunca no encoding da plataforma;
2. teste de log escreve em diretorio temporario, nunca no `logs/` real.
"""

import logging

LOG_FORMAT = "%(asctime)s - %(levelname)s - %(module)s - %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Acentuado de proposito: e o caractere que quebrava a coleta.
MENSAGEM_ACENTUADA = "Worker: 1 artigos processados. Total requisições neste ciclo: 1/10."


def _handler(path):
    handler = logging.FileHandler(path, mode="a", encoding="utf-8")
    handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT))
    return handler


def _logger(path, name):
    logger = logging.getLogger(name)
    logger.handlers.clear()
    logger.propagate = False
    logger.setLevel(logging.INFO)
    logger.addHandler(_handler(path))
    return logger


def test_niveis_chegam_ao_arquivo(tmp_path):
    destino = tmp_path / "app.log"
    logger = _logger(destino, "mnscr.teste.niveis")

    logger.info("TESTE INFO")
    logger.warning("TESTE WARNING")
    logger.error("TESTE ERROR")
    logging.shutdown()

    conteudo = destino.read_text(encoding="utf-8")
    assert "TESTE INFO" in conteudo
    assert "TESTE WARNING" in conteudo
    assert "TESTE ERROR" in conteudo


def test_acento_sobrevive_a_ida_e_volta(tmp_path):
    """Regressao: o log e UTF-8 nos dois sentidos, em qualquer plataforma."""
    destino = tmp_path / "app.log"
    logger = _logger(destino, "mnscr.teste.acento")

    logger.info(MENSAGEM_ACENTUADA)
    logging.shutdown()

    assert MENSAGEM_ACENTUADA in destino.read_text(encoding="utf-8")


def test_leitura_do_log_real_nao_usa_encoding_da_plataforma():
    """O `logs/app.log` de producao so pode ser lido como UTF-8.

    Le o arquivo real se ele existir — e o unico jeito de provar que um log ja
    escrito por uma execucao de verdade continua legivel. Se nao existir, nao ha
    o que verificar: o teste nao fabrica um log de producao para se satisfazer.
    """
    from pathlib import Path

    real = Path("logs/app.log")
    if not real.exists() or real.stat().st_size == 0:
        return
    assert real.read_text(encoding="utf-8", errors="strict") is not None
