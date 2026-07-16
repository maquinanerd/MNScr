#!/usr/bin/env python3
"""Teste sistemático para encontrar qual parte do conteúdo causa erro 500"""

import requests
from requests.auth import HTTPBasicAuth
import json
import os
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()

user = os.getenv('WORDPRESS_USER')
password = os.getenv('WORDPRESS_PASSWORD')
url = 'https://www.maquinanerd.com.br/wp-json/wp/v2/posts'

print("=" * 80)
print("TESTE SISTEMÁTICO: Encontrar causa do erro 500")
print("=" * 80)

test_cases = [
    {
        'name': 'Teste 1: Payload vazio',
        'payload': {
            'title': 'Teste 1 Vazio',
            'content': '',
            'status': 'draft',
        }
    },
    {
        'name': 'Teste 2: Conteúdo HTML puro',
        'payload': {
            'title': 'Teste 2 HTML Puro',
            'content': '<p>Parágrafo simples.</p>',
            'status': 'draft',
        }
    },
    {
        'name': 'Teste 3: Conteúdo Gutenberg simples',
        'payload': {
            'title': 'Teste 3 Gutenberg Simples',
            'content': '<!-- wp:paragraph --><p>Parágrafo simples.</p><!-- /wp:paragraph -->',
            'status': 'draft',
        }
    },
    {
        'name': 'Teste 4: Conteúdo Gutenberg com múltiplos parágrafos',
        'payload': {
            'title': 'Teste 4 Múltiplos Parágrafos',
            'content': '''<!-- wp:paragraph -->
<p>Parágrafo 1</p>
<!-- /wp:paragraph -->

<!-- wp:paragraph -->
<p>Parágrafo 2 com <b>negrito</b></p>
<!-- /wp:paragraph -->

<!-- wp:paragraph -->
<p>Parágrafo 3 com <i>itálico</i></p>
<!-- /wp:paragraph -->''',
            'status': 'draft',
        }
    },
    {
        'name': 'Teste 5: Com categorias e tags',
        'payload': {
            'title': 'Teste 5 Com Categorias',
            'content': '<!-- wp:paragraph --><p>Com categoria.</p><!-- /wp:paragraph -->',
            'status': 'draft',
            'categories': [20],
            'tags': [100],
        }
    },
    {
        'name': 'Teste 6: Com featured_media',
        'payload': {
            'title': 'Teste 6 Com Imagem',
            'content': '<!-- wp:paragraph --><p>Com imagem.</p><!-- /wp:paragraph -->',
            'status': 'draft',
            'categories': [20],
            'featured_media': 70846,
        }
    },
]

for i, test_case in enumerate(test_cases, 1):
    print(f"\n{test_case['name']}")
    print("-" * 80)
    
    payload_size = len(json.dumps(test_case['payload']))
    print(f"Tamanho: {payload_size} bytes")
    
    try:
        response = requests.post(url, auth=HTTPBasicAuth(user, password), json=test_case['payload'], timeout=30)
        
        if response.ok:
            post_id = response.json().get('id')
            print(f"✅ OK - Post ID: {post_id}")
        else:
            print(f"❌ Status {response.status_code}")
            # Salvar resposta para análise
            error_file = f'error_test_{i}.json'
            with open(error_file, 'w', encoding='utf-8') as f:
                try:
                    json.dump(response.json(), f, indent=2, ensure_ascii=False)
                except:
                    f.write(response.text)
            print(f"   Erro salvo em: {error_file}")
            
    except Exception as e:
        print(f"❌ Exceção: {str(e)[:100]}")

print("\n" + "=" * 80)
print("✅ Testes concluídos. Verificar arquivos error_test_*.json para detalhes.")
print("=" * 80)
