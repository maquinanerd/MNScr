#!/usr/bin/env python3
"""Verificar posts publicados em 29/10/2025"""

import sqlite3
from datetime import datetime

# Conectar ao banco de dados
conn = sqlite3.connect('data/app.db')
cursor = conn.cursor()

try:
    print("="*70)
    print("ARTIGOS PUBLICADOS EM 29/10/2025")
    print("="*70 + "\n")
    
    # Query para contar posts de 29/10/2025
    cursor.execute('''
        SELECT COUNT(*) as total,
               COUNT(CASE WHEN published = 1 THEN 1 END) as published,
               COUNT(CASE WHEN published = 0 THEN 1 END) as draft
        FROM posts 
        WHERE DATE(created_at) = '2025-10-29'
    ''')
    
    resultado = cursor.fetchone()
    if resultado and resultado[0] > 0:
        print(f'📊 RESUMO:')
        print(f'  Total: {resultado[0]} posts')
        print(f'  ✅ Publicados: {resultado[1]}')
        print(f'  📝 Rascunhos: {resultado[2]}')
        
        # Listar detalhes
        print(f'\n📋 DETALHES DOS POSTS:\n')
        cursor.execute('''
            SELECT id, title, source, published, created_at, updated_at
            FROM posts 
            WHERE DATE(created_at) = '2025-10-29'
            ORDER BY created_at DESC
        ''')
        
        for i, row in enumerate(cursor.fetchall(), 1):
            status = '✅ Publicado' if row[3] else '📝 Rascunho'
            print(f'{i}. [{row[0]:4d}] {status}')
            print(f'   Título: {row[1][:60]}')
            print(f'   Fonte: {row[2]}')
            print(f'   Criado: {row[4]}')
            print()
    else:
        print("⚠️  Nenhum post encontrado para 29/10/2025")
        
        # Mostrar estatísticas gerais
        print(f'\n📊 ESTATÍSTICAS GERAIS DA TABELA POSTS:')
        cursor.execute('SELECT COUNT(*) FROM posts')
        total = cursor.fetchone()[0]
        print(f'  Total de posts no banco: {total}')
        
        cursor.execute('SELECT COUNT(*) FROM posts WHERE published = 1')
        pub = cursor.fetchone()[0]
        print(f'  Posts publicados: {pub}')
        
        # Mostrar datas mais recentes
        print(f'\n📅 POSTS MAIS RECENTES:')
        cursor.execute('''
            SELECT DATE(created_at) as data, COUNT(*) as qtd
            FROM posts
            GROUP BY DATE(created_at)
            ORDER BY data DESC
            LIMIT 10
        ''')
        
        for row in cursor.fetchall():
            print(f'  {row[0]}: {row[1]} posts')

finally:
    conn.close()
