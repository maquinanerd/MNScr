#!/usr/bin/env python3
"""Verificar artigos publicados em 29/10/2025"""

import sqlite3
from datetime import datetime

# Conectar ao banco de dados
conn = sqlite3.connect('data/app.db')
cursor = conn.cursor()

try:
    # Listar todas as tabelas
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = cursor.fetchall()
    
    print("Tabelas disponíveis:")
    for table in tables:
        cursor.execute(f'SELECT COUNT(*) FROM {table[0]}')
        count = cursor.fetchone()[0]
        print(f'  • {table[0]}: {count} registros')
    
    # Se existir tabela de artigos, consultar
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='articles'")
    if cursor.fetchone():
        print("\n" + "="*60)
        print("Artigos de 29/10/2025:")
        print("="*60 + "\n")
        
        cursor.execute('''
            SELECT COUNT(*) as total,
                   COUNT(CASE WHEN published = 1 THEN 1 END) as published,
                   COUNT(CASE WHEN published = 0 THEN 1 END) as draft
            FROM articles 
            WHERE DATE(created_at) = '2025-10-29'
        ''')
        
        resultado = cursor.fetchone()
        print(f'Total: {resultado[0]} artigos')
        print(f'  ✅ Publicados: {resultado[1]}')
        print(f'  📝 Rascunhos: {resultado[2]}')
        
        # Listar detalhes
        print(f'\nDetalhes:')
        cursor.execute('''
            SELECT id, title, source, published, created_at
            FROM articles 
            WHERE DATE(created_at) = '2025-10-29'
            ORDER BY created_at DESC
            LIMIT 20
        ''')
        
        for row in cursor.fetchall():
            status = '✅' if row[3] else '📝'
            print(f'  {status} [{row[0]:4d}] {row[1][:50]:50s} | {row[2]} | {row[4]}')
    else:
        print("\n⚠️  Tabela 'articles' não encontrada")

finally:
    conn.close()
