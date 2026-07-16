#!/usr/bin/env python3
"""
Script simples para testar POST direto no WordPress via API
"""
import requests
from requests.auth import HTTPBasicAuth
import json
import os
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()

user = os.getenv('WORDPRESS_USER')
password = os.getenv('WORDPRESS_PASSWORD')

print("=" * 70)
print("TESTE DIRETO DE POST NO WORDPRESS")
print("=" * 70)

# Teste de diferentes tamanhos
test_sizes = [
    ('Pequeno', 1000),
    ('Médio', 5000),
    ('Grande', 10000),
    ('Muito Grande', 15000),
    ('XL', 20000),
]

url = 'https://www.maquinanerd.com.br/wp-json/wp/v2/posts'

for name, content_size in test_sizes:
    print(f"\n[TESTE] Payload {name} (~{content_size} bytes)")
    print("-" * 70)
    
    payload = {
        'title': f'Teste {name} - {datetime.now().strftime("%H:%M:%S")}',
        'content': '<p>' + ('x' * (content_size - 100)) + '</p>',
        'status': 'draft',
        'categories': [20],
    }
    
    payload_size = len(json.dumps(payload))
    
    print(f'Payload size: {payload_size} bytes ({payload_size/1024:.2f} KB)')
    
    try:
        response = requests.post(url, auth=HTTPBasicAuth(user, password), json=payload, timeout=30)
        print(f'Status: {response.status_code}', end=' ')
        
        if response.ok:
            post_id = response.json().get('id')
            print(f'✅ OK ID: {post_id}')
        else:
            print(f'❌ Erro {response.status_code}')
            try:
                error_data = response.json()
                print(f'  Código: {error_data.get("code")}')
            except:
                pass
                
    except Exception as e:
        print(f'❌ Exceção: {str(e)[:100]}')

print()
print("=" * 70)
