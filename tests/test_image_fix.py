#!/usr/bin/env python3
"""
Script de teste para validar as correções de imagens HTML.

Uso:
    python test_image_fix.py
"""

import sys
import json
from pathlib import Path

# Adicionar o diretório pai ao path
sys.path.insert(0, str(Path(__file__).parent))

from app.html_utils import unescape_html_content, validate_and_fix_figures, merge_images_into_content

def test_unescape():
    """Teste 1: Função de unescape"""
    print("=" * 60)
    print("TESTE 1: unescape_html_content()")
    print("=" * 60)
    
    escaped = "&lt;p&gt;Olá &amp; bem-vindo!&lt;/p&gt;"
    unescaped = unescape_html_content(escaped)
    
    print(f"Entrada escapada: {escaped}")
    print(f"Saída desescapada: {unescaped}")
    print(f"✓ Passou\n" if "<p>" in unescaped else "✗ Falhou\n")
    

def test_validate_figures_with_embedded_html():
    """Teste 2: Validação com HTML embutido no src"""
    print("=" * 60)
    print("TESTE 2: validate_and_fix_figures() - HTML embutido no src")
    print("=" * 60)
    
    problematic = '''<figure><img decoding="async" src="&lt;figure&gt;&lt;img src=&quot;https://comicbook.com/wp-content/uploads/sites/4/2024/10/Marvel-MCU-Young-Avengers-1.jpg?w=1024&quot; alt=&quot;&quot;&gt;&lt;figcaption&gt;&lt;/figcaption&gt;&lt;/figure&gt;"></figure>'''
    
    print(f"Entrada problemática:")
    print(f"  {problematic[:100]}...")
    print()
    
    fixed = validate_and_fix_figures(problematic)
    
    print(f"Saída corrigida:")
    print(f"  {fixed}")
    print()
    
    # Validações
    checks = [
        ("URL extraída", "https://comicbook.com" in fixed),
        ("Sem HTML no src", "<figure><img src=\"<" not in fixed),
        ("Tem figcaption", "<figcaption>" in fixed),
        ("Tem alt text", 'alt=' in fixed),
    ]
    
    for check_name, result in checks:
        print(f"  {'✓' if result else '✗'} {check_name}")
    
    print()
    return all(result for _, result in checks)


def test_validate_figures_missing_caption():
    """Teste 3: Adição de figcaption faltante"""
    print("=" * 60)
    print("TESTE 3: validate_and_fix_figures() - Adicionar figcaption")
    print("=" * 60)
    
    without_caption = '<figure><img src="https://example.com/image.jpg" alt="Test image"></figure>'
    
    print(f"Entrada sem figcaption:")
    print(f"  {without_caption}")
    print()
    
    fixed = validate_and_fix_figures(without_caption)
    
    print(f"Saída com figcaption:")
    print(f"  {fixed}")
    print()
    
    has_caption = "<figcaption>" in fixed
    print(f"  {'✓' if has_caption else '✗'} Figcaption adicionada")
    print()
    
    return has_caption


def test_merge_images():
    """Teste 4: Merge de imagens no conteúdo"""
    print("=" * 60)
    print("TESTE 4: merge_images_into_content()")
    print("=" * 60)
    
    html_content = "<p>Este é um parágrafo.</p><p>Este é outro parágrafo.</p>"
    image_urls = [
        "https://example.com/image1.jpg",
        "https://example.com/image2.png"
    ]
    
    print(f"HTML original:")
    print(f"  {html_content}")
    print()
    print(f"URLs de imagens a adicionar:")
    for url in image_urls:
        print(f"  - {url}")
    print()
    
    result = merge_images_into_content(html_content, image_urls, max_images=2)
    
    print(f"Resultado após merge:")
    print(f"  {result[:200]}...")
    print()
    
    # Validações
    checks = [
        ("Imagens adicionadas", "https://example.com/image1.jpg" in result),
        ("Tem figure tags", "<figure>" in result),
        ("Tem figcaption", "<figcaption>" in result),
    ]
    
    for check_name, result_check in checks:
        print(f"  {'✓' if result_check else '✗'} {check_name}")
    
    print()
    return all(r for _, r in checks)


def main():
    print("\n" + "=" * 60)
    print("SUITE DE TESTES - CORREÇÃO DE IMAGENS HTML")
    print("=" * 60 + "\n")
    
    results = []
    
    try:
        test_unescape()
        results.append(("Unescape", True))
    except Exception as e:
        print(f"✗ Erro: {e}\n")
        results.append(("Unescape", False))
    
    try:
        results.append(("Validate + HTML embutido", test_validate_figures_with_embedded_html()))
    except Exception as e:
        print(f"✗ Erro: {e}\n")
        results.append(("Validate + HTML embutido", False))
    
    try:
        results.append(("Validate + Figcaption", test_validate_figures_missing_caption()))
    except Exception as e:
        print(f"✗ Erro: {e}\n")
        results.append(("Validate + Figcaption", False))
    
    try:
        results.append(("Merge images", test_merge_images()))
    except Exception as e:
        print(f"✗ Erro: {e}\n")
        results.append(("Merge images", False))
    
    # Resumo
    print("=" * 60)
    print("RESUMO DOS TESTES")
    print("=" * 60)
    
    for test_name, passed in results:
        status = "✓ PASSOU" if passed else "✗ FALHOU"
        print(f"{test_name:.<40} {status}")
    
    total_passed = sum(1 for _, p in results if p)
    total = len(results)
    
    print()
    print(f"Total: {total_passed}/{total} testes passaram")
    print()
    
    if total_passed == total:
        print("🎉 TODOS OS TESTES PASSARAM!")
        return 0
    else:
        print(f"❌ {total - total_passed} teste(s) falharam")
        return 1


if __name__ == "__main__":
    sys.exit(main())
