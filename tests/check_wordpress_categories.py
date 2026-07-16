#!/usr/bin/env python3
"""
Script para verificar as categorias disponíveis no WordPress
e corrigir o mapeamento de "Séries"
"""

import requests
from typing import List, Dict

# Configuração
WP_SITE_URL = "https://www.maquinanerd.com.br"
WP_API_URL = f"{WP_SITE_URL}/wp-json/wp/v2/categories"

def get_all_categories() -> List[Dict]:
    """Busca todas as categorias do WordPress."""
    try:
        categories = []
        page = 1
        per_page = 100
        
        while True:
            response = requests.get(WP_API_URL, params={"page": page, "per_page": per_page})
            response.raise_for_status()
            data = response.json()
            
            if not data:
                break
            
            categories.extend(data)
            page += 1
        
        return categories
    except Exception as e:
        print(f"❌ Erro ao buscar categorias: {e}")
        return []


def main():
    """Função principal."""
    print("=" * 100)
    print("🔍 VERIFICANDO CATEGORIAS DO WORDPRESS")
    print("=" * 100)
    print()
    
    categories = get_all_categories()
    
    if not categories:
        print("❌ Nenhuma categoria encontrada!")
        return
    
    print(f"✅ Encontradas {len(categories)} categorias:")
    print()
    
    # Procurar por categorias relacionadas a Séries
    print("CATEGORIAS RELACIONADAS A 'SÉRIES' OU 'TV':")
    print("-" * 100)
    
    for cat in categories:
        name = cat.get("name", "")
        cat_id = cat.get("id", "")
        slug = cat.get("slug", "")
        
        if any(keyword in name.lower() for keyword in ["série", "tv", "show", "série tv"]):
            print(f"  ID: {cat_id:5} | Nome: {name:40} | Slug: {slug}")
    
    print()
    print("TODAS AS CATEGORIAS:")
    print("-" * 100)
    
    for cat in sorted(categories, key=lambda x: x.get("id", 0)):
        name = cat.get("name", "")
        cat_id = cat.get("id", "")
        slug = cat.get("slug", "")
        
        print(f"  ID: {cat_id:5} | Nome: {name:40} | Slug: {slug}")
    
    print()
    print("=" * 100)
    print("RESUMO PARA ATUALIZAR EM config.py:")
    print("=" * 100)
    print()
    
    print("Procure pelas linhas em WORDPRESS_CATEGORIES:")
    print()
    print("ATUAL (ERRADO):")
    print("    'Notícias': 20,")
    print("    'Filmes': 24,")
    print("    'Séries': 24,      # ❌ MESMO ID QUE FILMES!")
    print("    'Games': 73,")
    print()
    print("NECESSÁRIO CORRIGIR:")
    print("  Encontre o ID correto de 'Séries de TV' ou 'TV Series' na lista acima")
    print("  e atualize config.py com o ID correto")
    print()


if __name__ == "__main__":
    main()
