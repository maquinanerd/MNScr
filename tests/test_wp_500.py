#!/usr/bin/env python3
"""
Teste para investigar o erro 500 do WordPress
"""
import requests
import json
import os
from dotenv import load_dotenv

load_dotenv()

# Configuração WordPress
WP_URL = os.getenv('WORDPRESS_URL').rstrip('/')
WP_USER = os.getenv('WORDPRESS_USER')
WP_PASSWORD = os.getenv('WORDPRESS_PASSWORD')

# Extrair base URL (remover /wp-json/wp/v2 se existir)
if '/wp-json' in WP_URL:
    WP_URL = WP_URL.split('/wp-json')[0]

print(f"URL Base: {WP_URL}")
print(f"User: {WP_USER}")
print()

session = requests.Session()
session.auth = (WP_USER, WP_PASSWORD)
session.headers.update({'User-Agent': 'Test/1.0'})

# 1. Testar um POST SIMPLES (sem conteúdo complexo)
print("=" * 100)
print("TESTE 1: POST EXTREMAMENTE SIMPLES")
print("=" * 100)

simple_payload = {
    "title": "Teste Simples",
    "content": "Conteúdo de teste",
    "status": "publish",
    "categories": [1],  # Categoria padrão
}

posts_url = f"{WP_URL}/wp-json/wp/v2/posts"
resp = session.post(posts_url, json=simple_payload, timeout=60)
print(f"Status: {resp.status_code}")
print(f"Response: {resp.text[:500]}")
print()

# 2. Testar com uma categoria que falhou
print("=" * 100)
print("TESTE 2: POST COM CATEGORIA QUE FALHOU (24)")
print("=" * 100)

payload_with_cat = {
    "title": "Teste com Categoria",
    "content": "Conteúdo de teste",
    "status": "publish",
    "categories": [24],
}

resp2 = session.post(posts_url, json=payload_with_cat, timeout=60)
print(f"Status: {resp2.status_code}")
print(f"Response: {resp2.text[:500]}")
print()

# 3. Testar com featured media
print("=" * 100)
print("TESTE 3: POST COM FEATURED MEDIA")
print("=" * 100)

payload_with_media = {
    "title": "Teste com Media",
    "content": "Conteúdo de teste",
    "status": "publish",
    "featured_media": 70833,  # ID de media que falhou
}

resp3 = session.post(posts_url, json=payload_with_media, timeout=60)
print(f"Status: {resp3.status_code}")
print(f"Response: {resp3.text[:500]}")
print()

# 4. Listar todas as categorias disponíveis
print("=" * 100)
print("TESTE 4: LISTAR CATEGORIAS DISPONÍVEIS")
print("=" * 100)

cats_url = f"{WP_URL}/wp-json/wp/v2/categories"
resp_cats = session.get(cats_url, params={"per_page": 100}, timeout=60)
print(f"Status: {resp_cats.status_code}")
if resp_cats.ok:
    cats = resp_cats.json()
    print(f"Total de categorias: {len(cats)}")
    print("\nPrimeiras 10 categorias:")
    for cat in cats[:10]:
        print(f"  ID {cat['id']}: {cat['name']}")
    
    # Procurar as categorias que falharam
    print("\nProcurando categorias que falharam:")
    failed_cat_ids = [24, 20, 16063, 16337]
    for cat_id in failed_cat_ids:
        found = any(c['id'] == cat_id for c in cats)
        print(f"  Categoria {cat_id}: {'ENCONTRADA' if found else 'NAO ENCONTRADA'}")
else:
    print(f"Erro ao listar categorias: {resp_cats.text[:200]}")
