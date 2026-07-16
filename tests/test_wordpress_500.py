#!/usr/bin/env python3
"""
Test script para debugar erro 500 do WordPress
"""

import os
import json
import logging
from dotenv import load_dotenv

# Carrega .env
load_dotenv(override=True)

# Configurar logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

logger = logging.getLogger(__name__)

print("\n" + "="*80)
print("[TESTE] Debugar erro 500 do WordPress")
print("="*80 + "\n")

from app.wordpress import WordPressClient
from app.config import WORDPRESS_CONFIG, WORDPRESS_CATEGORIES

# 1. Conectar ao WordPress
print("[1] Conectando ao WordPress...")
wp_client = WordPressClient(
    config=WORDPRESS_CONFIG,
    categories_map=WORDPRESS_CATEGORIES
)
print("   OK - Conectado\n")

# 2. Preparar payload de teste (simples)
print("[2] Preparando payload de teste...")
test_payload = {
    'title': 'Teste de Erro 500 - ' + str(os.urandom(4).hex()),
    'slug': 'teste-erro-500-' + str(os.urandom(4).hex()),
    'content': '<p>Este é um artigo de teste para debugar o erro 500 do WordPress.</p>' * 5,  # ~350 chars
    'excerpt': 'Artigo de teste para debugar erro 500',
    'categories': [20],
    'tags': ['teste'],
    'featured_media': None,
    'status': 'draft'
}

print(f"   - Titulo: {test_payload['title']}")
print(f"   - Content length: {len(test_payload['content'])} chars")
print(f"   - Excerpt length: {len(test_payload['excerpt'])} chars")
print(f"   - Payload size: {len(json.dumps(test_payload))} bytes\n")

# 3. Tentar criar post
print("[3] Tentando criar post de teste...")
try:
    post_id = wp_client.create_post(test_payload)
    if post_id:
        print(f"   OK - Post criado! ID: {post_id}\n")
        
        # Tentar deletar o post de teste
        print("[4] Limpando post de teste...")
        try:
            import requests
            from requests.auth import HTTPBasicAuth
            url = WORDPRESS_CONFIG['url'].rstrip('/')
            delete_url = f"{url}/posts/{post_id}"
            response = requests.delete(
                delete_url,
                auth=HTTPBasicAuth(WORDPRESS_CONFIG['user'], WORDPRESS_CONFIG['password']),
                params={'force': True},
                timeout=30
            )
            if response.status_code == 200:
                print(f"   OK - Post deletado com sucesso\n")
            else:
                print(f"   AVISO - Resposta: {response.status_code}\n")
        except Exception as e:
            print(f"   AVISO - Erro ao deletar: {e}\n")
    else:
        print("   ERRO - Falha ao criar post - verifique os logs acima\n")
        
except Exception as e:
    logger.error(f"ERRO durante teste: {e}", exc_info=True)

print("="*80)
print("[OK] TESTE CONCLUIDO - Verifique os logs acima para detalhes")
print("="*80)
