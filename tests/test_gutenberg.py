#!/usr/bin/env python3
"""Test Gutenberg blocks conversion"""

from app.html_utils import html_to_gutenberg_blocks

# Test HTML
test_html = """
<p>Este é um parágrafo de introdução.</p>
<h2>Título da Seção</h2>
<p>Segundo parágrafo com <strong>texto em negrito</strong>.</p>
<img src="https://example.com/image.jpg" alt="Descrição da imagem">
<p>Terceiro parágrafo.</p>
<blockquote>Esta é uma citação importante.</blockquote>
<ul>
<li>Item 1</li>
<li>Item 2</li>
<li>Item 3</li>
</ul>
<p>Parágrafo final.</p>
"""

print("=" * 80)
print("TESTE: HTML para Gutenberg Blocks")
print("=" * 80)

print("\nHTML ORIGINAL:")
print("-" * 80)
print(test_html)

print("\n\nGUTENBERG BLOCKS:")
print("-" * 80)
gutenberg = html_to_gutenberg_blocks(test_html)
print(gutenberg)

print("\n\n" + "=" * 80)
print("VALIDAÇÃO:")
print("=" * 80)

# Validar estrutura
blocks_count = gutenberg.count("<!-- wp:")
print(f"✅ Blocos Gutenberg encontrados: {blocks_count}")

if "<!-- wp:paragraph -->" in gutenberg:
    print("✅ Blocos de parágrafo encontrados")

if "<!-- wp:heading" in gutenberg:
    print("✅ Blocos de heading encontrados")

if "<!-- wp:image -->" in gutenberg:
    print("✅ Blocos de imagem encontrados")

if "<!-- wp:quote -->" in gutenberg:
    print("✅ Blocos de quote encontrados")

if "<!-- wp:list -->" in gutenberg:
    print("✅ Blocos de lista encontrados")

print("\n" + "=" * 80)
