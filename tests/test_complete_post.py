#!/usr/bin/env python3
"""Testa a postagem completa com imagem"""
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
print('TESTE DE POSTAGEM COMPLETA COM IMAGEM')
print('=' * 80)
print()

try:
    # 1. Upload de imagem
    print('1️⃣  UPLOAD DE IMAGEM')
    print('-' * 80)
    
    image_url = 'https://static0.srcdn.com/wordpress/wp-content/uploads/2026/01/ben-affleck-in-triple-frontier.jpg'
    
    response = requests.get(image_url, timeout=25)
    response.raise_for_status()
    
    media_endpoint = f"{url}/media"
    filename = 'test-image.jpg'
    
    headers = {
        'Content-Disposition': f'attachment; filename="{filename}"',
        'Content-Type': 'image/jpeg',
    }
    
    media_response = requests.post(
        media_endpoint,
        headers=headers,
        data=response.content,
        auth=HTTPBasicAuth(user, password),
        timeout=40
    )
    
    if media_response.status_code in [200, 201]:
        media_data = media_response.json()
        media_id = media_data.get('id')
        print(f'✅ Imagem uploadada! Media ID: {media_id}')
        print(f'   URL: {media_data.get("source_url")}')
    else:
        print(f'❌ Erro ao upload: {media_response.status_code}')
        print(f'   {media_response.text}')
        media_id = None
    
    # 2. Criar post com imagem
    print()
    print('2️⃣  CRIAR POST COM IMAGEM')
    print('-' * 80)
    
    posts_endpoint = f"{url}/posts"
    
    payload = {
        'title': 'Teste Completo com Imagem',
        'content': '''<p>Este é um teste completo de postagem com imagem por Pablo Gameleira.</p>
<p>O sistema agora está usando as credenciais atualizadas e deve postar artigos com sucesso.</p>
<p>Esta postagem inclui uma imagem em destaque para validar todo o fluxo.</p>
<p>Estamos testando se as imagens estão sendo associadas corretamente ao post.</p>
<p>Se este teste passar, o pipeline deve estar funcionando normalmente.</p>''' * 2,
        'status': 'draft',
        'categories': [20, 24],  # Categorias padrão
        'tags': [100, 200],  # Tags padrão
    }
    
    if media_id:
        payload['featured_media'] = media_id
    
    print(f'Payload:')
    print(f'  - Title length: {len(payload["title"])}')
    print(f'  - Content length: {len(payload["content"])}')
    print(f'  - Featured media: {payload.get("featured_media", "None")}')
    print(f'  - Categories: {payload.get("categories")}')
    print(f'  - Tags: {payload.get("tags")}')
    print()
    
    post_response = requests.post(
        posts_endpoint,
        json=payload,
        auth=HTTPBasicAuth(user, password),
        timeout=60
    )
    
    if post_response.status_code in [200, 201]:
        post_data = post_response.json()
        post_id = post_data.get('id')
        print(f'✅ POST CRIADO COM SUCESSO!')
        print(f'   Post ID: {post_id}')
        print(f'   URL: {post_data.get("link")}')
        print(f'   Imagem em destaque: {post_data.get("featured_media")}')
        
        # Deletar post de teste
        print()
        print('Limpando... deletando post de teste')
        del_response = requests.delete(
            f'{posts_endpoint}/{post_id}',
            auth=HTTPBasicAuth(user, password),
            timeout=30,
            params={'force': True}
        )
        print(f'Post deletado: {del_response.status_code}')
    else:
        print(f'❌ ERRO {post_response.status_code} ao criar post!')
        print(f'   Resposta: {post_response.text[:500]}')
    
except Exception as e:
    print(f'ERRO: {e}')
    import traceback
    traceback.print_exc()

print()
print('=' * 80)
print('TESTE COMPLETO FINALIZADO')
print('=' * 80)
