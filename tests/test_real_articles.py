#!/usr/bin/env python3
"""
Buscar artigos não publicados e tentar enviar novamente
para isolar qual conteúdo está causando erro 500
"""

import sqlite3
import requests
from requests.auth import HTTPBasicAuth
import json
import os
from dotenv import load_dotenv
from app.html_utils import html_to_gutenberg_blocks

load_dotenv()

user = os.getenv('WORDPRESS_USER')
password = os.getenv('WORDPRESS_PASSWORD')
url = 'https://www.maquinanerd.com.br/wp-json/wp/v2/posts'

print("=" * 80)
print("ISOLAMENTO: Testar conteúdo real de artigos não publicados")
print("=" * 80)

# Conectar ao banco
try:
    db = sqlite3.connect('data/app.db')
    db.row_factory = sqlite3.Row
    cursor = db.cursor()
    
    # Buscar artigos recentes
    cursor.execute("""
        SELECT id, title, content_html 
        FROM posts 
        WHERE content_html IS NOT NULL 
        ORDER BY id DESC 
        LIMIT 3
    """)
    
    articles = cursor.fetchall()
    
    if not articles:
        print("❌ Nenhum artigo encontrado no banco de dados")
    else:
        for idx, article in enumerate(articles, 1):
            article_id, title, content_html = article
            
            print(f"\n\nARTIGO {idx} (ID: {article_id})")
            print("-" * 80)
            print(f"Título: {title[:60]}...")
            
            # Converter para Gutenberg
            gutenberg_content = html_to_gutenberg_blocks(content_html)
            
            payload = {
                'title': title,
                'content': gutenberg_content,
                'status': 'draft',
                'categories': [20],
            }
            
            payload_size = len(json.dumps(payload))
            print(f"Tamanho payload: {payload_size} bytes ({payload_size/1024:.2f} KB)")
            
            # Validar caracteres suspeitos
            problematic_chars = []
            for char in gutenberg_content:
                if ord(char) > 127 and ord(char) < 160:
                    problematic_chars.append(f"0x{ord(char):02X}")
            
            if problematic_chars:
                print(f"⚠️  Caracteres suspeitos encontrados: {set(problematic_chars)}")
            
            # Tentar enviar
            print(f"📤 Enviando...")
            try:
                response = requests.post(url, auth=HTTPBasicAuth(user, password), json=payload, timeout=30)
                
                if response.ok:
                    post_id = response.json().get('id')
                    print(f"✅ POST criado com sucesso! ID: {post_id}")
                else:
                    print(f"❌ Status {response.status_code}")
                    print(f"   Mensagem: {response.json().get('message', 'N/A')[:100]}")
                    
                    # Salvar conteúdo para análise
                    debug_file = f'debug_article_{article_id}.json'
                    with open(debug_file, 'w', encoding='utf-8') as f:
                        json.dump({
                            'title': title,
                            'content_sample': gutenberg_content[:500],
                            'payload_size': payload_size,
                            'response': response.text[:500]
                        }, f, indent=2, ensure_ascii=False)
                    print(f"   Salvo em: {debug_file}")
                    
            except Exception as e:
                print(f"❌ Exceção: {str(e)[:100]}")
    
    db.close()
    
except Exception as e:
    print(f"❌ Erro ao conectar ao banco: {str(e)}")

print("\n" + "=" * 80)
