.PHONY: help install run run-once test clean

VENV_NAME=.venv
PYTHON=$(VENV_NAME)/Scripts/python

help:
	@echo "Comandos disponíveis:"
	@echo "  install    - Cria o ambiente virtual e instala as dependências"
	@echo "  run        - Inicia o scheduler para rodar o pipeline em loop"
	@echo "  run-once   - Roda o pipeline uma única vez para teste"
	@echo "  test       - Roda os testes unitários"
	@echo "  clean      - Remove o ambiente virtual e arquivos de cache"

install:
	@echo "Criando ambiente virtual em $(VENV_NAME)..."
	python -m venv $(VENV_NAME)
	@echo "Instalando dependências de requirements.txt..."
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -r requirements.txt
	@echo "Instalação concluída. Ative o ambiente com: source $(VENV_NAME)/bin/activate (Linux/macOS) ou .\\$(VENV_NAME)\\Scripts\\activate (Windows)"

run:
	$(PYTHON) -m app.main

run-once:
	$(PYTHON) -m app.main --once

test:
	$(PYTHON) -m pytest

clean:
	@echo "Limpando ambiente..."
	rm -rf $(VENV_NAME) __pycache__ app/__pycache__ tests/__pycache__ .pytest_cache .coverage data/*.db*