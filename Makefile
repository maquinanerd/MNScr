.PHONY: help install run run-once test lint clean

VENV_NAME=.venv
PYTHON=$(VENV_NAME)/Scripts/python

help:
	@echo "Comandos disponiveis:"
	@echo "  install    - Cria o ambiente virtual e instala as dependencias"
	@echo "  run        - Executa o pipeline editorial em loop"
	@echo "  run-once   - Executa um unico ciclo do pipeline"
	@echo "  test       - Roda a suite de testes"
	@echo "  lint       - Roda o ruff"
	@echo "  clean      - Remove ambiente virtual, caches e drafts locais"

install:
	python -m venv $(VENV_NAME)
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -e ".[dev]"

run:
	$(PYTHON) -m app.main

run-once:
	$(PYTHON) -m app.main --once

test:
	$(PYTHON) -m pytest

lint:
	$(PYTHON) -m ruff check .

clean:
	rm -rf $(VENV_NAME) __pycache__ app/__pycache__ tests/__pycache__ .pytest_cache .ruff_cache .coverage
	rm -rf artifacts/local-drafts data/*.db*
