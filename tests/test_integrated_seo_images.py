#!/usr/bin/env python3
"""
Teste integrado: SEO Title Optimizer + Image Fix

Valida que os dois sistemas funcionam juntos corretamente.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from app.html_utils import unescape_html_content, validate_and_fix_figures
from app.seo_title_optimizer import optimize_title


@pytest.mark.xfail(
    reason=(
        "DEFEITO DE PRODUTO, nao do teste: o fluxo integrado reprova porque "
        "o otimizador de titulo recusa a otimizacao ao remover tokens "
        "protegidos de PT-BR (`pode`, `vai`) — ver o WARNING "
        "`seo_title_optimizer.py:301`. O teste ficava verde porque devolvia "
        "`return 1` em vez de asseverar, e o pytest descarta o retorno. "
        "Marcado como falha conhecida para nao esconder de novo."
    ),
    strict=False,
)
def test_integrated_flow():
    """Teste o fluxo completo de processamento."""
    print("=" * 80)
    print("TESTE INTEGRADO: SEO TITLES + IMAGE FIXING")
    print("=" * 80)
    print()

    # Simular resposta da IA
    ai_response = {
        "titulo_final": "Você não vai acreditar no que a Marvel pode estar planejando para Homem-Aranha",
        "conteudo_final": """<p>A Marvel pode estar desenvolvendo algo grande.</p>
<figure><img src="&lt;figure&gt;&lt;img src=&quot;https://example.com/spider-man.jpg&quot;&gt;&lt;/figure&gt;">
<figcaption>Imagem do Homem-Aranha</figcaption></figure>
<p>Segundo relatos não confirmados, talvez haja novidades em breve.</p>"""
    }

    print("ENTRADA (Resposta da IA)")
    print("-" * 80)
    print(f"Título: {ai_response['titulo_final']}")
    print(f"Conteúdo: {ai_response['conteudo_final'][:150]}...")
    print()

    # Passo 1: Otimizar Título
    print("PASSO 1: Otimizar Título para Google News")
    print("-" * 80)
    original_title = ai_response['titulo_final']
    optimized_title, title_report = optimize_title(original_title, ai_response['conteudo_final'])

    print(f"Título Original:  {original_title}")
    print(f"Título Otimizado: {optimized_title}")
    print(f"Score SEO: {title_report['original_score']:.1f} → {title_report['optimized_score']:.1f}")
    print(f"Tamanho: {title_report['original_length']} → {title_report['optimized_length']} caracteres")

    if title_report['changes_made']:
        print("Mudanças realizadas:")
        for change in title_report['changes_made']:
            print(f"  • {change}")

    if title_report['optimized_issues']:
        print("Questões restantes:")
        for issue in title_report['optimized_issues']:
            print(f"  ⚠ {issue}")

    print()

    # Passo 2: Desescapar HTML
    print("PASSO 2: Desescapar HTML do Conteúdo")
    print("-" * 80)
    content = ai_response['conteudo_final']
    unescaped_content = unescape_html_content(content)

    print("Conteúdo Original (primeiros 100 chars):")
    print(f"  {content[:100]}...")
    print()
    print("Após Unescape (primeiros 100 chars):")
    print(f"  {unescaped_content[:100]}...")
    print()

    # Passo 3: Validar e Corrigir Figuras
    print("PASSO 3: Validar e Corrigir Estruturas de Figura")
    print("-" * 80)
    fixed_content = validate_and_fix_figures(unescaped_content)

    print("Conteúdo Final (após fix):")
    print()
    # Extrair apenas as figuras para visualização
    import re
    figures = re.findall(r'<figure>.*?</figure>', fixed_content, re.DOTALL)
    for fig in figures:
        print(f"  {fig}")
    print()

    # Validações finais
    print("VALIDAÇÕES FINAIS")
    print("-" * 80)

    checks = {
        "Título sem clickbait": "Você não vai acreditar" not in optimized_title,
        "Título entre 50-70 chars": 50 <= len(optimized_title) <= 70,
        "Título com verbo de ação": any(verb in optimized_title.lower() for verb in
                                         ['anuncia', 'lança', 'critica', 'vence', 'fecha']),
        "HTML desescapado": "&lt;" not in fixed_content and "&gt;" not in fixed_content,
        "Figuras com estrutura correta": "<figure><img" in fixed_content,
        "Imagens com alt text": 'alt=' in fixed_content,
        "Figcaptions presentes": "<figcaption>" in fixed_content,
        "URLs válidas": "https://example.com/spider-man.jpg" in fixed_content,
    }

    all_passed = True
    for check_name, result in checks.items():
        status = "✓ PASSOU" if result else "✗ FALHOU"
        print(f"{check_name:.<50} {status}")
        if not result:
            all_passed = False

    print()
    print("=" * 80)
    print("RESULTADO FINAL:")
    print(f"  Título: {optimized_title}")
    print(f"  Conteúdo: {fixed_content[:200]}...")

    # `assert`, e não `return`: o pytest descarta o valor devolvido, então este
    # teste ficava verde mesmo devolvendo 1.
    assert all_passed, "o fluxo integrado de SEO + imagens reprovou"


if __name__ == "__main__":
    sys.exit(test_integrated_flow())
