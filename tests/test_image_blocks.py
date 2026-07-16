#!/usr/bin/env python3
"""Test image block conversion to Gutenberg format"""

from app.html_utils import html_to_gutenberg_blocks

# Teste 1: Figura com legenda (o problema original)
print("=" * 80)
print("TESTE 1: Figura com legenda")
print("=" * 80)

html_input = '''
<figure>
  <img alt="Logo do filme Star Wars: Starfighter" 
       src="https://static0.srcdn.com/wordpress/wp-content/uploads/2025/04/star-wars-starfigher-movie-logo.jpg"/>
  <figcaption>Logo do filme Star Wars: Starfighter.</figcaption>
</figure>
'''

print("\n📝 HTML de entrada:")
print(html_input)

result = html_to_gutenberg_blocks(html_input)

print("\n✅ Bloco Gutenberg gerado:")
print(result)

print("\n" + "=" * 80)
print("TESTE 2: Imagem simples (sem legenda)")
print("=" * 80)

html_input2 = '''
<img src="https://example.com/image.jpg" alt="Descrição da imagem"/>
'''

print("\n📝 HTML de entrada:")
print(html_input2)

result2 = html_to_gutenberg_blocks(html_input2)

print("\n✅ Bloco Gutenberg gerado:")
print(result2)

print("\n" + "=" * 80)
print("TESTE 3: Figura com aspas na legenda")
print("=" * 80)

html_input3 = '''
<figure>
  <img alt="Imagem com aspas" src="https://example.com/test.jpg"/>
  <figcaption>Legenda com "aspas duplas" e também 'simples'.</figcaption>
</figure>
'''

print("\n📝 HTML de entrada:")
print(html_input3)

result3 = html_to_gutenberg_blocks(html_input3)

print("\n✅ Bloco Gutenberg gerado:")
print(result3)

print("\n" + "=" * 80)
print("✅ TODOS OS TESTES CONCLUÍDOS")
print("=" * 80)
