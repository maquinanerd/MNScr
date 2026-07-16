#!/usr/bin/env python3
"""
Test com dados reais para demonstrar a remoção de captions em inglês do ScreenRant.
"""

from bs4 import BeautifulSoup
from app.extractor import ContentExtractor
import logging

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

def test_screenrant_caption_cleaning():
    """
    Simula um artigo do ScreenRant com captions em inglês e português.
    """
    
    # HTML de exemplo simulando estrutura real do ScreenRant
    html_sample = """
    <html>
    <body>
    <article id="article-body" class="article-body">
        <h2>Título do artigo</h2>
        
        <p>Parágrafo inicial do artigo sobre o Universo Marvel.</p>
        
        <figure>
            <img src="https://static1.srcdn.com/wordpress/wp-content/uploads/2024/01/jonathan-majors-1.jpg?w=1200" alt="kang">
            <figcaption>jonathan majors as kang in ant man and the wasp quantumania</figcaption>
        </figure>
        
        <p>Mais texto do artigo explicando a importância do personagem.</p>
        
        <figure>
            <img src="https://static1.srcdn.com/wordpress/wp-content/uploads/2024/01/original-avengers.jpg?w=1200" alt="avengers">
            <figcaption>Os Vingadores originais em ação no filme de 2012</figcaption>
        </figure>
        
        <p>Conclusão do artigo com análise final.</p>
        
        <figure>
            <img src="https://static1.srcdn.com/wordpress/wp-content/uploads/2024/01/battle-ny.jpg?w=1200" alt="battle">
            <figcaption>original avengers from the battle of new york</figcaption>
        </figure>
        
    </article>
    </body>
    </html>
    """
    
    print("=" * 80)
    print("TESTE: ScreenRant Caption Cleaning com Dados Reais")
    print("=" * 80)
    
    # Antes
    soup = BeautifulSoup(html_sample, 'html.parser')
    article = soup.find('article')
    
    print("\n📋 ANTES da limpeza:")
    print("-" * 80)
    for i, fig in enumerate(article.find_all('figure'), 1):
        img = fig.find('img')
        cap = fig.find('figcaption')
        img_src = img['src'] if img else "SEM IMG"
        caption_text = cap.get_text(strip=True) if cap else "SEM LEGENDA"
        print(f"\n  Figura {i}:")
        print(f"    Imagem: {img_src}")
        print(f"    Legenda: {caption_text}")
    
    # Aplica o limpador ScreenRant
    print("\n\n⚙️  Aplicando limpador ScreenRant...")
    extractor = ContentExtractor()
    cleaned_article = extractor._clean_html_for_screenrant(soup)
    
    # Depois
    print("\n\n✨ DEPOIS da limpeza:")
    print("-" * 80)
    for i, fig in enumerate(cleaned_article.find_all('figure'), 1):
        img = fig.find('img')
        cap = fig.find('figcaption')
        img_src = img['src'] if img else "SEM IMG"
        caption_text = cap.get_text(strip=True) if cap else "VAZIA (removida)"
        is_empty = "(em branco - inglês removido)" if cap and not caption_text else ""
        print(f"\n  Figura {i}:")
        print(f"    Imagem: {img_src}")
        print(f"    Legenda: {caption_text} {is_empty}")
    
    # Validação
    print("\n\n" + "=" * 80)
    captions = [fig.find('figcaption').get_text(strip=True) for fig in cleaned_article.find_all('figure')]
    
    validation_passed = (
        captions[0] == "" and  # English caption - removed
        captions[1] == "Os Vingadores originais em ação no filme de 2012" and  # Portuguese - preserved
        captions[2] == ""  # English caption - removed
    )
    
    if validation_passed:
        print("✓ Validação PASSOU!")
        print("  ✓ Caption em inglês #1: Removida")
        print("  ✓ Caption em português: Preservada")
        print("  ✓ Caption em inglês #2: Removida")
    else:
        print("✗ Validação FALHOU!")
        print(f"  Obtidas captions: {captions}")
    
    print("=" * 80)
    
    return validation_passed


if __name__ == "__main__":
    success = test_screenrant_caption_cleaning()
    exit(0 if success else 1)
