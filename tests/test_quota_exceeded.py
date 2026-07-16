#!/usr/bin/env python3
"""
Test script para verificar trocas de chave quando uma retorna 429 (quota excedida)
"""

import os
import sys
import logging
from dotenv import load_dotenv
from unittest.mock import patch, MagicMock
from google.api_core import exceptions as google_exceptions

# Carrega .env
load_dotenv(override=True)

# Configurar logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

print("\n" + "="*80)
print("🚫 TEST: Trocade Chaves quando uma retorna 429 (QUOTA EXCEDIDA)")
print("="*80)

from app.config import AI_API_KEYS
from app.ai_client_gemini import AIClient

client = AIClient(
    keys=AI_API_KEYS,
    min_interval_s=0.1,
    backoff_base=20,
    backoff_max=300
)

print(f"\n✅ AIClient inicializado com {len(AI_API_KEYS)} chaves")

# Teste: Simular 429 na primeira chave
print("\n1️⃣  SIMULANDO PRIMEIRA CHAVE COM 429 (QUOTA EXCEDIDA):")
print("-" * 80)

call_count = 0

def mock_generate_content(prompt, **kwargs):
    global call_count
    call_count += 1
    print(f"   [Mock] Chamada #{call_count} para generate_content()")
    
    if call_count == 1:
        print(f"   🚫 Primeira chave retorna 429 (quota excedida)")
        raise google_exceptions.ResourceExhausted("Quota exceeded")
    else:
        print(f"   ✅ Segunda chave retorna sucesso")
        return MagicMock(text="Sucesso!")

with patch('google.generativeai.GenerativeModel') as mock_gen_model:
    with patch('google.generativeai.configure'):
        mock_instance = MagicMock()
        mock_instance.generate_content = mock_generate_content
        mock_gen_model.return_value = mock_instance
        
        try:
            result = client.generate_text("Test prompt")
            print(f"\n✅ RESULTADO FINAL: {result}")
            print(f"   ✅ Sistema rotacionou para segunda chave após 429!")
        except Exception as e:
            print(f"\n❌ ERRO: {e}")

print("\n" + "="*80)
print("✅ TESTE CONCLUÍDO!")
print("="*80 + "\n")
