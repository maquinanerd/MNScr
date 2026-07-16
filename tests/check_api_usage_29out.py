#!/usr/bin/env python3
"""Verificar uso de chaves API em 29/10/2025"""

import sqlite3
from datetime import datetime, timedelta
from collections import defaultdict

conn = sqlite3.connect('data/app.db')
cursor = conn.cursor()

try:
    print("="*80)
    print("ANÁLISE DE USO DE CHAVES API - 29/10/2025")
    print("="*80 + "\n")
    
    # Verificar se a tabela api_usage existe e tem dados
    cursor.execute("SELECT COUNT(*) FROM api_usage WHERE DATE(timestamp) = '2025-10-29'")
    total_29out = cursor.fetchone()[0]
    
    if total_29out == 0:
        print("⚠️  Nenhum registro de uso de API encontrado em 29/10/2025")
        
        # Checar se há dados de api_key_status
        cursor.execute("SELECT COUNT(*) FROM api_key_status")
        count = cursor.fetchone()[0]
        print(f"   Registros em api_key_status: {count}")
        
        # Listar colunas de api_usage
        cursor.execute("PRAGMA table_info(api_usage)")
        columns = cursor.fetchall()
        print("\n   Colunas disponíveis em api_usage:")
        for col in columns:
            print(f"     • {col[1]} ({col[2]})")
    else:
        print(f"✅ Encontrados {total_29out} registros de API em 29/10/2025\n")
    
    # Verificar schema de api_key_status
    print("Tabela api_key_status (status atual das chaves):")
    cursor.execute("PRAGMA table_info(api_key_status)")
    columns = cursor.fetchall()
    for col in columns:
        print(f"  • {col[1]} ({col[2]})")
    
    cursor.execute("SELECT COUNT(*) FROM api_key_status")
    print(f"\nRegistros: {cursor.fetchone()[0]}")
    
    # Ver o que tem em api_key_status
    cursor.execute("SELECT * FROM api_key_status LIMIT 3")
    data = cursor.fetchall()
    if data:
        print("\nPrimeiros registros:")
        for row in data:
            print(f"  {row}")
    
    # Verificar failures
    cursor.execute("SELECT COUNT(*) FROM failures WHERE DATE(created_at) = '2025-10-29'")
    failures_29 = cursor.fetchone()[0]
    print(f"\n❌ Falhas registradas em 29/10/2025: {failures_29}")
    
    if failures_29 > 0:
        cursor.execute('''
            SELECT error_type, COUNT(*) as count
            FROM failures
            WHERE DATE(created_at) = '2025-10-29'
            GROUP BY error_type
        ''')
        print("\nTipos de erro:")
        for row in cursor.fetchall():
            print(f"  • {row[0]}: {row[1]}")
    
    # Verificar feed_status para informações de requisições
    print("\n" + "="*80)
    print("Status de Feeds em 29/10/2025:")
    print("="*80)
    cursor.execute('''
        SELECT source, COUNT(*) as requisicoes, MAX(updated_at) as ultima_atualizacao
        FROM feed_status
        WHERE DATE(updated_at) = '2025-10-29'
        GROUP BY source
        ORDER BY requisicoes DESC
    ''')
    
    rows = cursor.fetchall()
    if rows:
        for row in rows:
            print(f"\n  📡 {row[0]}")
            print(f"     Requisições: {row[1]}")
            print(f"     Última atualização: {row[2]}")
    else:
        print("  ⚠️  Nenhum registro encontrado")
    
    # Estatísticas gerais
    print("\n" + "="*80)
    print("Estatísticas Gerais:")
    print("="*80)
    
    cursor.execute("SELECT COUNT(*) FROM posts WHERE DATE(created_at) = '2025-10-29'")
    posts_29 = cursor.fetchone()[0]
    print(f"\n  Posts publicados em 29/10/2025: {posts_29}")
    
    cursor.execute("SELECT COUNT(*) FROM seen_articles WHERE DATE(created_at) = '2025-10-29'")
    articles_29 = cursor.fetchone()[0]
    print(f"  Artigos vistos (seen_articles) em 29/10/2025: {articles_29}")
    
    # Calcular RPM (requisições por minuto)
    print(f"\n  Estimativa RPM: ~{posts_29 * 2 // (24 * 60):.1f} (baseado em posts)")
    
finally:
    conn.close()
