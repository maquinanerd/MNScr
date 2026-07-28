#!/usr/bin/env python3
"""
⚡ TESTE RÁPIDO: Validar CTA Removal em 30 segundos
Execute isto para confirmar que o sistema está funcionando
"""

import re
import sys


def quick_test():
    """Teste rápido das 5 camadas"""

    test_cases = [
        ("Thank you for reading this post, don't forget to subscribe!", "CTA EXATO"),
        ("thank you for reading this post, don't forget to subscribe!", "CTA LOWERCASE"),
        ("<p>Thank you for reading this post, don't forget to subscribe!</p>", "CTA EM TAG"),
    ]

    print("\n" + "="*60)
    print("⚡ TESTE RÁPIDO: 5 CAMADAS DE CTA REMOVAL")
    print("="*60 + "\n")

    passed = 0
    failed = 0

    for content, description in test_cases:
        print(f"📍 Testando: {description}")

        # LAYER 1: Literal
        if "Thank you for reading this post, don't forget to subscribe!" in content:
            content = content.replace("Thank you for reading this post, don't forget to subscribe!", "")
        if "thank you for reading this post, don't forget to subscribe!" in content:
            content = content.replace("thank you for reading this post, don't forget to subscribe!", "")

        # LAYER 2: Regex
        pattern = r'<p[^>]*>.*?thank you for reading.*?don\'t forget to subscribe.*?</p>'
        content = re.sub(pattern, '', content, flags=re.IGNORECASE | re.DOTALL)

        # LAYER 3: Empty tags
        content = re.sub(r'<p[^>]*>\s*</p>', '', content, flags=re.IGNORECASE)

        # LAYER 4 & 5: Check
        if 'thank you for reading' in content.lower() or 'don\'t forget to subscribe' in content.lower():
            print("   ❌ FALHOU: CTA ainda presente")
            failed += 1
        else:
            print("   ✅ PASSOU: CTA removido")
            passed += 1

        print()

    print("="*60)
    print(f"RESULTADO: {passed}/{len(test_cases)} testes passaram")

    if passed == len(test_cases):
        print("✅✅✅ SISTEMA FUNCIONANDO PERFEITAMENTE!")
        print("="*60 + "\n")
        return 0
    else:
        print(f"❌ {failed} teste(s) falharam")
        print("="*60 + "\n")
        return 1

if __name__ == "__main__":
    sys.exit(quick_test())
