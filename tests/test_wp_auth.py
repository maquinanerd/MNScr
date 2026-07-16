#!/usr/bin/env python3
"""Testa a autenticação WordPress"""
import os
from dotenv import load_dotenv
import requests
from requests.auth import HTTPBasicAuth
import json

load_dotenv()

url = os.getenv('WORDPRESS_URL', '').rstrip('/')
user = os.getenv('WORDPRESS_USER')
password = os.getenv('WORDPRESS_PASSWORD')

print('=' * 80)
print('TESTE DE AUTENTICAÇÃO WORDPRESS')
print('=' * 80)
print(f'URL: {url}')
print(f'Usuário: {user}')
print(f'Senha configurada: {"Sim" if password else "Não"}')
print()

try:
    # Testar endpoint de posts
    endpoint = url + '/posts'
    print(f'Testando GET em: {endpoint}')
    print()
    
    response = requests.get(
        endpoint,
        auth=HTTPBasicAuth(user, password),
        timeout=10,
        params={'per_page': 1}
    )
    
    print(f'Status Code: {response.status_code}')
    
    if response.status_code == 200:
        print('✅ AUTENTICAÇÃO SUCESSO!')
        data = response.json()
        print(f'Resposta: {json.dumps(data, indent=2)[:300]}...')
    elif response.status_code == 401:
        print('❌ ERRO 401: Credenciais inválidas!')
        print(f'Resposta: {response.text}')
    elif response.status_code == 500:
        print('❌ ERRO 500: Problema no servidor WordPress!')
        print(f'Resposta: {response.text}')
    else:
        print(f'❌ ERRO {response.status_code}')
        print(f'Resposta: {response.text[:200]}')
    
except Exception as e:
    print(f'ERRO: {e}')
    import traceback
    traceback.print_exc()

# Agora testar criação de post
print()
print('=' * 80)
print('TESTE DE CRIAÇÃO DE POST')
print('=' * 80)

try:
    endpoint = url + '/posts'
    payload = {
        'title': 'Teste de Credenciais',
        'content': 'Este é um teste para validar as novas credenciais de Pablo Gameleira. ' * 10,
        'status': 'draft',  # Salvar como rascunho para não poluir o site
    }
    
    print(f'POST para: {endpoint}')
    print(f'Payload: {json.dumps(payload, indent=2)[:200]}...')
    print()
    
    response = requests.post(
        endpoint,
        auth=HTTPBasicAuth(user, password),
        json=payload,
        timeout=30
    )
    
    print(f'Status Code: {response.status_code}')
    
    if response.status_code in [200, 201]:
        print('✅ POST CRIADO COM SUCESSO!')
        data = response.json()
        post_id = data.get('id')
        print(f'Post ID: {post_id}')
        print(f'URL: {data.get("link")}')
        
        # Tentar deletar o post de teste
        print()
        print('Deletando post de teste...')
        del_response = requests.delete(
            f'{endpoint}/{post_id}',
            auth=HTTPBasicAuth(user, password),
            timeout=30,
            params={'force': True}
        )
        print(f'Delete Status: {del_response.status_code}')
    else:
        print(f'❌ ERRO {response.status_code}')
        print(f'Resposta: {response.text}')
    
except Exception as e:
    print(f'ERRO: {e}')
    import traceback
    traceback.print_exc()
