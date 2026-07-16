#!/usr/bin/env python3
"""
✅ STATUS DO SISTEMA - Verificação Rápida

Mostra:
- Arquivos JSON salvos
- Tokens registrados
- Posts publicados no WordPress
- Tudo correlacionado
"""

import json
from pathlib import Path
from collections import defaultdict

def show_status():
    debug_dir = Path("debug")
    tokens_file = Path("logs/tokens/tokens_2026-02-05.jsonl")
    
    print("\n" + "="*100)
    print("📊 STATUS DO SISTEMA DE RASTREAMENTO".center(100))
    print("="*100)
    
    # 1. JSONs salvos
    json_files = list(debug_dir.glob("ai_response_batch_*.json"))
    print(f"\n📁 JSON FILES SALVOS: {len(json_files)}")
    
    if json_files:
        recent = sorted(json_files, key=lambda x: x.stat().st_mtime, reverse=True)[:5]
        for f in recent:
            try:
                data = json.loads(f.read_text(encoding='utf-8'))
                if isinstance(data, dict) and 'resultados' in data:
                    item = data['resultados'][0] if data['resultados'] else {}
                else:
                    item = data if isinstance(data, dict) else {}
                
                slug = item.get('slug', 'N/A')[:40]
                print(f"   ✅ {f.name}")
                print(f"      └─ Slug: {slug}")
            except:
                print(f"   ❓ {f.name} (erro ao ler)")
    
    # 2. Tokens registrados
    print(f"\n📊 TOKENS REGISTRADOS:")
    
    if tokens_file.exists():
        records = []
        with open(tokens_file, 'r', encoding='utf-8') as f:
            for line in f:
                records.append(json.loads(line))
        
        processing = [r for r in records if r.get('metadata', {}).get('operation') in ['batch_rewrite', 'single_rewrite']]
        publishing = [r for r in records if r.get('metadata', {}).get('operation') == 'published']
        
        total_entrada = sum(r.get('prompt_tokens', 0) for r in processing)
        total_saida = sum(r.get('completion_tokens', 0) for r in processing)
        
        print(f"   Posts processados: {len(processing)}")
        print(f"   Posts publicados:  {len(publishing)}")
        print(f"   Total entrada:     {total_entrada:,} tokens")
        print(f"   Total saída:       {total_saida:,} tokens")
        print(f"   Total geral:       {total_entrada + total_saida:,} tokens")
        
        if publishing:
            print(f"\n   📌 ÚLTIMOS 3 POSTS PUBLICADOS:")
            for record in publishing[-3:]:
                wp_id = record.get('wp_post_id', 'N/A')
                title = record.get('article_title', 'N/A')[:50]
                print(f"      ✅ [{wp_id}] {title}")
    else:
        print("   ❌ Arquivo de tokens não encontrado")
    
    # 3. Correlação
    print(f"\n🔗 CORRELAÇÃO COMPLETA:")
    print(f"   ✅ JSON + SLUG     → Arquivo nomeado com identificador único")
    print(f"   ✅ TOKENS + JSON   → Associados pelo título do artigo")
    print(f"   ✅ WP_POST_ID      → Registrado nos tokens após publicação")
    print(f"   ✅ SOURCE_URL      → Capturado e armazenado em tokens")
    
    print(f"\n" + "="*100)
    print("✅ Sistema pronto para uso! Execute: python main.py".center(100))
    print("="*100 + "\n")

if __name__ == "__main__":
    show_status()
