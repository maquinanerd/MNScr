#!/usr/bin/env python3
"""Teste de leitura de posts (GET)"""
import requests
from requests.auth import HTTPBasicAuth

URL = "https://www.maquinanerd.com.br/wp-json/wp/v2/posts?per_page=1"
USER = "Pablo Gameleira"
PASSWORD = "aXvV GxAV GCMV jBfc ZIT2 5aWe"

print("="*80)
print("TESTE DE LEITURA DE POSTS (GET)")
print("="*80)

try:
    response = requests.get(
        URL,
        auth=HTTPBasicAuth(USER, PASSWORD),
        timeout=30
    )
    
    print(f"\nStatus: {response.status_code}")
    
    if response.ok:
        data = response.json()
        print(f"✓ LEITURA OK - {len(data)} posts retornados")
        if data:
            post = data[0]
            print(f"  Ultimo post: {post.get('title', 'SEM TITULO')[:50]}")
            print(f"  ID: {post.get('id')}")
            print(f"  Status: {post.get('status')}")
    else:
        print(f"✗ ERRO {response.status_code}")
        print(response.text[:200])
        
except Exception as e:
    print(f"✗ EXCECAO: {type(e).__name__}: {e}")
