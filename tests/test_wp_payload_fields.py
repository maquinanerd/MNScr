#!/usr/bin/env python3
"""
Teste para identificar qual campo do payload está causando erro 500
"""
import requests
import json
import os
from dotenv import load_dotenv

load_dotenv()

WP_URL = os.getenv('WORDPRESS_URL').rstrip('/')
if '/wp-json' in WP_URL:
    WP_URL = WP_URL.split('/wp-json')[0]

WP_USER = os.getenv('WORDPRESS_USER')
WP_PASSWORD = os.getenv('WORDPRESS_PASSWORD')

session = requests.Session()
session.auth = (WP_USER, WP_PASSWORD)
session.headers.update({'User-Agent': 'Test/1.0'})

posts_url = f"{WP_URL}/wp-json/wp/v2/posts"

print(f"Testing com URL: {posts_url}\n")

# Teste 1: Mínimo possível
print("=" * 80)
print("TESTE 1: Payload MÍNIMO (título + conteúdo)")
print("=" * 80)

minimal = {
    "title": "Teste Mínimo",
    "content": "Lorem ipsum dolor sit amet, consectetur adipiscing elit. " * 10,
    "status": "publish"
}

resp = session.post(posts_url, json=minimal, timeout=60)
print(f"Status: {resp.status_code}")
print(f"Response: {resp.text[:300]}")
if resp.ok:
    print("[OK] SUCESSO!")
else:
    print("[ERR] FALHOU")
print()

# Teste 2: Com categorias válidas
print("=" * 80)
print("TESTE 2: Com categorias [20, 24]")
print("=" * 80)

with_cats = {
    "title": "Teste com Categorias",
    "content": "Lorem ipsum dolor sit amet, consectetur adipiscing elit. " * 10,
    "status": "publish",
    "categories": [20, 24]
}

resp2 = session.post(posts_url, json=with_cats, timeout=60)
print(f"Status: {resp2.status_code}")
print(f"Response: {resp2.text[:300]}")
if resp2.ok:
    print("[OK] SUCESSO!")
else:
    print("[ERR] FALHOU")
print()

# Teste 3: Com tags
print("=" * 80)
print("TESTE 3: Com tags")
print("=" * 80)

with_tags = {
    "title": "Teste com Tags",
    "content": "Lorem ipsum dolor sit amet, consectetur adipiscing elit. " * 10,
    "status": "publish",
    "tags": [15549, 16899, 19148]
}

resp3 = session.post(posts_url, json=with_tags, timeout=60)
print(f"Status: {resp3.status_code}")
print(f"Response: {resp3.text[:300]}")
if resp3.ok:
    print("[OK] SUCESSO!")
else:
    print("[ERR] FALHOU")
print()

# Teste 4: Com featured_media
print("=" * 80)
print("TESTE 4: Com featured_media (ID: 70861)")
print("=" * 80)

with_media = {
    "title": "Teste com Featured Media",
    "content": "Lorem ipsum dolor sit amet, consectetur adipiscing elit. " * 10,
    "status": "publish",
    "featured_media": 70861
}

resp4 = session.post(posts_url, json=with_media, timeout=60)
print(f"Status: {resp4.status_code}")
print(f"Response: {resp4.text[:300]}")
if resp4.ok:
    print("[OK] SUCESSO!")
else:
    print("[ERR] FALHOU")
print()

# Teste 5: Com conteúdo HTML como no artigo real
print("=" * 80)
print("TESTE 5: Com conteúdo HTML (Gutenberg blocks como no artigo)")
print("=" * 80)

gutenberg_content = """<!-- wp:paragraph -->
<p>A última década testemunhou uma era de ouro para os filmes biográficos.</p>
<!-- /wp:paragraph -->

<!-- wp:heading {"level":2} -->
<h2>Bohemian Rhapsody (2018)</h2>
<!-- /wp:heading -->

<!-- wp:paragraph -->
<p><em>Bohemian Rhapsody</em> transcendeu o sucesso comercial, tornando-se um fenômeno cultural e arrecadando mais de US$ 900 milhões.</p>
<!-- /wp:paragraph -->"""

with_html = {
    "title": "Teste com HTML Gutenberg",
    "content": gutenberg_content,
    "status": "publish"
}

resp5 = session.post(posts_url, json=with_html, timeout=60)
print(f"Status: {resp5.status_code}")
print(f"Response: {resp5.text[:300]}")
if resp5.ok:
    print("[OK] SUCESSO!")
else:
    print("[ERR] FALHOU")
