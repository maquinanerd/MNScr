#!/usr/bin/env python3
# test_editorial_rules.py
"""
Script de teste: Valida títulos conforme regras editoriais.
Demonstra funcionamento do TitleValidator.
"""

import sys

import pytest

from app.title_validator import TitleValidator


@pytest.mark.xfail(
    reason=(
        "DEFEITO DE PRODUTO, nao do teste: 5 dos casos de titulo saem com "
        "severidade menor que a esperada — ha titulo que deveria ser ERRO "
        "saindo como AVISO, e titulo que deveria ser ERRO ou AVISO saindo "
        "como VALIDO. Ou seja, materia com erro de regencia ou acentuacao "
        "passa pela regra editorial. Isto ficou INVISIVEL enquanto a funcao "
        "devolvia `failed == 0` em vez de asseverar: o pytest descarta o "
        "retorno, entao a suite ficava verde com 5 casos reprovando. "
        "Marcado como falha conhecida para nao esconder de novo — corrigir "
        "as regras de portugues e trabalho proprio, com decisao editorial."
    ),
    strict=False,
)
def test_titles():
    """Testa uma bateria de títulos bons e ruins."""

    validator = TitleValidator()

    test_cases = [
        # ✅ TÍTULOS BONS (devem passar)
        {
            "title": "Batman 2 tem estreia confirmada pela DC em 2025",
            "expected": "VÁLIDO",
            "category": "✅ BOM"
        },
        {
            "title": "Marvel revela calendário completo da Fase 6",
            "expected": "VÁLIDO",
            "category": "✅ BOM"
        },
        {
            "title": "The Last of Us ganha trailer da 2ª temporada na HBO Max",
            "expected": "VÁLIDO",
            "category": "✅ BOM"
        },
        {
            "title": "Dune: Parte Dois arrecada R$ 1 bilhão em bilheteria",
            "expected": "VÁLIDO",
            "category": "✅ BOM"
        },

        # ❌ TÍTULOS COM ERROS (devem ser rejeitados)
        {
            "title": "O que causou a queda surpreendente de Batman?",
            "expected": "ERRO",
            "category": "❌ RUIM (pergunta + sensacionalismo)"
        },
        {
            "title": "BATMAN 2 TEM LANÇAMENTO CONFIRMADO!!!",
            "expected": "ERRO",
            "category": "❌ RUIM (maiúsculas + exclamações)"
        },
        {
            "title": "Vários filmes de Marvel explodem nas redes",
            "expected": "ERRO",
            "category": "❌ RUIM (vago + sensacionalismo)"
        },
        {
            "title": "Entenda por quê Star Wars foi cancelado",
            "expected": "ERRO",
            "category": "❌ RUIM (fraco + regência)"
        },
        {
            "title": "Sucessos surpreendentes de DC Studios em 2025",
            "expected": "ERRO",
            "category": "❌ RUIM (termo vazio)"
        },

        # ⚠️ TÍTULOS COM AVISOS (corrigível automaticamente)
        {
            "title": "Série de Batman é nerfada por DC Studios",
            "expected": "AVISO",
            "category": "⚠️ AVISO (gíria)"
        },
        {
            "title": "Filme de Harry Potter de gratis na streaming",
            "expected": "AVISO",
            "category": "⚠️ AVISO (acentuação)"
        },
        {
            "title": "The Mandalorian fica de lado da plataforma",
            "expected": "AVISO",
            "category": "⚠️ AVISO (regência)"
        },
    ]

    print("=" * 80)
    print("🎯 TESTE DE VALIDAÇÃO DE TÍTULOS (REGRAS EDITORIAIS)")
    print("=" * 80)
    print()

    passed = 0
    failed = 0

    for i, test in enumerate(test_cases, 1):
        title = test["title"]
        expected = test["expected"]
        category = test["category"]

        result = validator.validate(title)
        status = result['status']

        # Verificar resultado
        match = "✅ PASS" if status == expected else "❌ FAIL"
        if status == expected:
            passed += 1
        else:
            failed += 1

        print(f"{i}. {category}")
        print(f"   Título: \"{title}\"")
        print(f"   Status esperado: {expected} | Obtido: {status} | {match}")

        if result['erros']:
            print("   ❌ Erros:")
            for err in result['erros']:
                print(f"      {err}")

        if result['avisos']:
            print("   ⚠️  Avisos:")
            for warn in result['avisos']:
                print(f"      {warn}")

            # Se há avisos, mostrar correção sugerida
            corrected = validator.suggest_correction(title)
            if corrected != title:
                print(f"   ✅ Sugestão de correção: \"{corrected}\"")

        print()

    print("=" * 80)
    print(f"📊 RESULTADOS: {passed} passou ✅ | {failed} falhou ❌")
    print("=" * 80)

    # `assert`, e nao `return`: um teste que DEVOLVE o resultado nao reprova nada: o pytest ignora o retorno,
    # entao a suite ficava verde mesmo com o contador de falhas diferente de zero.
    assert failed == 0, f"{failed} caso(s) de titulo falharam"

if __name__ == "__main__":
    success = test_titles()
    sys.exit(0 if success else 1)
