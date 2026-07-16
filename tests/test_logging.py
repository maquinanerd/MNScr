#!/usr/bin/env python3
"""Teste de logging"""
import logging
import os

os.makedirs("logs", exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(module)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("logs/app.log", mode='a', encoding='utf-8')
    ]
)

logger = logging.getLogger(__name__)

print("Testando logging...")
logger.info("TESTE INFO")
logger.warning("TESTE WARNING")
logger.error("TESTE ERROR")
print("Pronto!")

import time
time.sleep(1)

# Verificar arquivo
with open("logs/app.log", "r") as f:
    content = f.read()
    print(f"Arquivo tem {len(content)} bytes")
    print(content[-200:])
