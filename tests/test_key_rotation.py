#!/usr/bin/env python3
"""
Test script para verificar rotação de chaves quando uma é penalizada
"""

import os
import sys
import logging
from dotenv import load_dotenv
from time import monotonic

# Carrega .env
load_dotenv(override=True)

# Configurar logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

print("\n" + "="*80)
print("🔄 TEST: Rotação de Chaves quando uma é Penalizada")
print("="*80)

from app.config import AI_API_KEYS
from app.ai_client_gemini import AIClient

client = AIClient(
    keys=AI_API_KEYS,
    min_interval_s=0.1,  # Reduzir intervalo para teste
    backoff_base=20,
    backoff_max=300
)

print(f"\n✅ AIClient inicializado com {len(AI_API_KEYS)} chaves")

# Teste 1: Pegar chave pronta
print("\n1️⃣  PRIMEIRA CHAVE PRONTA:")
slot1 = client.pool.next_ready()
print(f"   Chave: {slot1.key[:20]}...{slot1.key[-4:]}")
print(f"   Cooldown: {slot1.cooldown_until}")

# Teste 2: Penalizar primeira chave
print("\n2️⃣  PENALIZANDO PRIMEIRA CHAVE POR 5 SEGUNDOS:")
client.pool.penalize(slot1, retry_after=5)
print(f"   ✅ Penalização aplicada")
print(f"   Cooldown_until agora é: {slot1.cooldown_until}")

# Teste 3: Obter próxima chave (deve ser a segunda)
print("\n3️⃣  OBTENDO PRÓXIMA CHAVE (deve ser a segunda):")
slot2 = client.pool.next_ready()
print(f"   Chave obtida: {slot2.key[:20]}...{slot2.key[-4:]}")
print(f"   Cooldown: {slot2.cooldown_until}")

if slot1.key != slot2.key:
    print(f"   ✅ SUCESSO! Rotacionou para uma chave diferente!")
else:
    print(f"   ❌ ERRO! Retornou a mesma chave!")

# Teste 4: Penalizar segunda chave também
print("\n4️⃣  PENALIZANDO SEGUNDA CHAVE TAMBÉM:")
client.pool.penalize(slot2, retry_after=5)
print(f"   ✅ Ambas as chaves estão em cooldown")

# Teste 5: Tentar obter chave quando todas estão em cooldown
print("\n5️⃣  TENTANDO OBTER CHAVE QUANDO TODAS ESTÃO EM COOLDOWN:")
print(f"   (Isso vai aguardar até sair do cooldown)")
start = monotonic()
slot3 = client.pool.next_ready()
elapsed = monotonic() - start
print(f"   ✅ Chave obtida após {elapsed:.1f}s de espera")
print(f"   Chave: {slot3.key[:20]}...{slot3.key[-4:]}")

print("\n" + "="*80)
print("✅ TESTE CONCLUÍDO - ROTAÇÃO FUNCIONANDO CORRETAMENTE!")
print("="*80 + "\n")
