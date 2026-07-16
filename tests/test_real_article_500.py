#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test real posts with WordPress to debug 500 errors."""

import sys
import logging
import sqlite3
from pathlib import Path

# Setup logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Ensure app can be imported
sys.path.insert(0, str(Path(__file__).parent))

from app.config import WORDPRESS_CONFIG
from app.wordpress import WordPressClient

print("=" * 80)
print("[TESTE] Debugar erro 500 com posts reais")
print("=" * 80)

# Init WordPress
print("\n[1] Conectando ao WordPress...")
wp_client = WordPressClient(
    config=WORDPRESS_CONFIG,
    categories_map={'movies': 20, 'tv': 21, 'news': 1}
)
print("   OK - Conectado")

# Get database
print("\n[2] Conectando ao banco de dados...")
conn = sqlite3.connect('data/app.db')
conn.row_factory = sqlite3.Row
cursor = conn.cursor()
print("   OK - Banco conectado")

# Check table structure
print("\n[3] Inspecionando estrutura do banco...")
cursor.execute("SELECT COUNT(*) FROM seen_articles WHERE status='processed'")
processed_count = cursor.fetchone()[0]
print(f"   Articles com status 'processed': {processed_count}")

# Find articles to test
print("\n[4] Buscando artigos para teste...")
test_articles = cursor.execute("""
    SELECT id, url, fail_reason, status FROM seen_articles 
    ORDER BY id DESC
    LIMIT 5
""").fetchall()

print(f"\n[5] Encontrados {len(test_articles)} artigos para inspecionar")

for i, article in enumerate(test_articles, 1):
    article_dict = dict(article)
    print(f"\n   ARTIGO {i}:")
    print(f"      ID: {article_dict['id']}")
    print(f"      URL: {article_dict['url']}")
    if article_dict.get('fail_reason'):
        print(f"      Motivo falha: {article_dict['fail_reason'][:100]}...")

# Now let's try to fetch real article content and test posting
print("\n[6] Testando criacao de posts com dados reais...")

# We need to get articles with actual content
for i in range(1, 3):
    print(f"\n   TESTE {i}: Criando post simples...")
    
    payload = {
        'title': f"Test Post {i} - Titulo do Teste",
        'content': '<p>Este eh um artigo de teste para debugar o erro 500 do WordPress.</p><p>Conteudo de teste.</p>',
        'excerpt': "Artigo de teste para debugar erro 500",
        'status': 'draft',
        'categories': [20],
        'tags': [141]
    }
    
    try:
        result = wp_client.create_post(payload=payload)
        print(f"      [OK] Post criado! ID: {result}")
        
        # Delete the test post
        try:
            wp_client._http_delete(f"/posts/{result}?force=true")
            print(f"      [LIMPEZA] Post deletado")
        except:
            print(f"      [AVISO] Nao conseguiu deletar post {result}")
            
    except Exception as e:
        error_msg = str(e)[:300]
        print(f"      [ERRO] {error_msg}")
        logger.error(f"Erro no teste {i}: {e}")

conn.close()

print("\n" + "=" * 80)
print("[CONCLUIDO]")
print("=" * 80)
