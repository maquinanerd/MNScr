#!/usr/bin/env python3
"""
Teste de limpeza de CTAs/Junk do conteúdo final.
Valida que "Thank you for reading" e similares serão removidos.
"""
import re

# Simular o código da pipeline que Remove CTAs
def remove_ctas(content_html):
    """Remove CTAs e junk content."""
    dangerous_patterns = [
        r'<p[^>]*>.*?thank you for reading.*?</p>',
        r'<p[^>]*>.*?thanks for reading.*?</p>',
        r'<p[^>]*>.*?thanks for visiting.*?</p>',
        r'<p[^>]*>.*?don\'t forget to subscribe.*?</p>',
        r'<p[^>]*>.*?subscribe now.*?</p>',
        r'<p[^>]*>.*?click here.*?</p>',
        r'<p[^>]*>.*?read more.*?</p>',
        r'<p[^>]*>.*?sign up.*?</p>',
        r'<p[^>]*>.*?please subscribe.*?</p>',
        r'<p[^>]*>.*?subscribe to our.*?</p>',
        r'<p[^>]*>.*?stay tuned.*?</p>',
        r'<p[^>]*>.*?follow us.*?</p>',
        r'<p[^>]*>.*?newsletter.*?</p>',
        r'<p[^>]*>.*?author box.*?</p>',
    ]
    
    removed_count = 0
    for pattern in dangerous_patterns:
        original_length = len(content_html)
        content_html = re.sub(pattern, '', content_html, flags=re.IGNORECASE | re.DOTALL)
        if len(content_html) < original_length:
            removed_count += 1
            print(f"✅ Removido: {pattern[:50]}...")
    
    return content_html, removed_count

# Testes
test_cases = [
    {
        'name': '1. CTA simples',
        'input': '<p>Great article!</p><p>Thank you for reading this post, don\'t forget to subscribe!</p><p>More content</p>',
        'should_remove': True,
    },
    {
        'name': '2. Subscribe com maiúsculas',
        'input': '<p>Content here</p><p>SUBSCRIBE NOW to our newsletter</p>',
        'should_remove': True,
    },
    {
        'name': '3. Stay tuned',
        'input': '<p>Article content</p><p>Stay tuned for more updates!</p>',
        'should_remove': True,
    },
    {
        'name': '4. Thank you variação',
        'input': '<p>Story text</p><p>Thanks for visiting our site!</p>',
        'should_remove': True,
    },
    {
        'name': '5. Follow us',
        'input': '<p>News</p><p>Follow us on Twitter for updates</p>',
        'should_remove': True,
    },
    {
        'name': '6. Conteúdo legítimo (não deve remover)',
        'input': '<p>The movie received thanks from critics.</p><p>This article provides subscriber information.</p>',
        'should_remove': False,
    },
]

print("=" * 80)
print("🧪 TESTE DE LIMPEZA DE CTAs")
print("=" * 80)

passed = 0
failed = 0

for test in test_cases:
    print(f"\n{test['name']}")
    print(f"Input: {test['input'][:60]}...")
    
    result, removed = remove_ctas(test['input'])
    was_removed = len(result) < len(test['input'])
    
    if test['should_remove']:
        if was_removed:
            print(f"✅ PASS - CTA removido (esperado)")
            passed += 1
        else:
            print(f"❌ FAIL - CTA não foi removido (inesperado!)")
            failed += 1
    else:
        if not was_removed:
            print(f"✅ PASS - Conteúdo preservado (esperado)")
            passed += 1
        else:
            print(f"❌ FAIL - Conteúdo removido por engano!")
            print(f"   Original: {test['input'][:60]}...")
            print(f"   Resultado: {result[:60]}...")
            failed += 1

print("\n" + "=" * 80)
print(f"📊 RESULTADOS: {passed} passou ✅ | {failed} falhou ❌")
print("=" * 80)

if failed == 0:
    print("✅ Todos os testes de limpeza passaram!")
else:
    print("❌ Alguns testes falharam!")
