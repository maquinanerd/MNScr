#!/usr/bin/env python3
"""Teste direto de criação de post em WordPress"""
import json
from requests.auth import HTTPBasicAuth
import requests

# Configuração WordPress
URL = "https://www.maquinanerd.com.br/wp-json/wp/v2/posts"
USER = "Pablo Gameleira"
PASSWORD = "aXvV GxAV GCMV jBfc ZIT2 5aWe"

# Post simples para teste
post_data = {
    "title": "TESTE SIMPLES 30JAN 2026",
    "content": "<!-- wp:paragraph -->\n<p>Conteudo de teste para verificar se o post é criado.</p>\n<!-- /wp:paragraph -->",
    "status": "publish",
    "categories": [24],  # Movies
    "excerpt": "Teste simples",
}

print("="*80)
print("TESTE DIRETO DE CRIACAO DE POST WORDPRESS")
print("="*80)
print(f"\nURL: {URL}")
print(f"User: {USER}")
print(f"Payload size: {len(json.dumps(post_data))} bytes")
print(f"\nConteudo payload:")
print(json.dumps(post_data, indent=2, ensure_ascii=False))

print("\n" + "="*80)
print("ENVIANDO...")
print("="*80)

try:
    response = requests.post(
        URL,
        json=post_data,
        auth=HTTPBasicAuth(USER, PASSWORD),
        timeout=30
    )
    
    print(f"\nStatus: {response.status_code}")
    print(f"Response:")
    print(response.text[:500])
    
    if response.ok:
        result = response.json()
        post_id = result.get('id')
        print(f"\n✓ POST CRIADO COM SUCESSO!")
        print(f"  Post ID: {post_id}")
        print(f"  Link: {result.get('link')}")
    else:
        print(f"\n✗ ERRO {response.status_code}")
        try:
            print(f"Response JSON: {response.json()}")
        except:
            print(f"Response raw: {response.text}")
            
except Exception as e:
    print(f"\n✗ EXCECAO: {type(e).__name__}: {e}")
