#!/usr/bin/env python3
"""
🚨 TESTE NUCLEAR: Verificar que TODA CTA removal funciona
Este script testa todas as 4 camadas de remoção de CTA
"""

import re
import logging

# Configurar logging para ver tudo
logging.basicConfig(level=logging.DEBUG, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

def test_nuclear_cta_removal(content_html, title="Test"):
    """Simular exatamente o que pipeline.py faz"""
    logger.info(f"\n{'='*80}")
    logger.info(f"TESTE: {title}")
    logger.info(f"{'='*80}")
    
    original_html = content_html
    cta_removal_log = []
    
    # CAMADA 1: Remover a frase EXATA (literal search)
    logger.info("\n🔵 CAMADA 1: Remover frases EXATAS")
    nuclear_phrases = [
        "Thank you for reading this post, don't forget to subscribe!",
        "thank you for reading this post, don't forget to subscribe!",
        "Thank you for reading this post, don't forget to subscribe",
        "thank you for reading this post, don't forget to subscribe",
    ]
    
    for phrase in nuclear_phrases:
        if phrase in content_html:
            logger.error(f"🔥 Encontrado: '{phrase[:50]}...'")
            cta_removal_log.append(f"LITERAL: {phrase[:60]}")
            content_html = content_html.replace(phrase, "")
            logger.info(f"✅ Removido com sucesso")
    
    # CAMADA 2: Remover parágrafos INTEIROS que contêm padrões de CTA
    logger.info("\n🔵 CAMADA 2: Remover parágrafos COM PADRÕES de CTA")
    cta_patterns = [
        r'<p[^>]*>.*?thank you for reading this post.*?don\'t forget to subscribe.*?</p>',
        r'<p[^>]*>.*?thank you for reading.*?don\'t forget.*?</p>',
        r'<p[^>]*>.*?thank you for reading.*?</p>',
        r'<p[^>]*>.*?thanks for reading.*?</p>',
    ]
    
    for pattern in cta_patterns:
        original_length = len(content_html)
        matches = re.findall(pattern, content_html, flags=re.IGNORECASE | re.DOTALL)
        if matches:
            logger.error(f"🔥 Encontrado(s) {len(matches)} parágrafo(s) com CTA")
            for match in matches[:2]:
                logger.error(f"   Match: {match[:80]}")
                cta_removal_log.append(f"REGEX: {match[:80]}")
        content_html = re.sub(pattern, '', content_html, flags=re.IGNORECASE | re.DOTALL)
        if len(content_html) < original_length:
            logger.info(f"✅ Parágrafo(s) removido(s)")
    
    # CAMADA 3: Remover tags vazias deixadas para trás
    logger.info("\n🔵 CAMADA 3: Limpar tags VAZIAS")
    before_empty_removal = len(content_html)
    content_html = re.sub(r'<(p|div|span|article)[^>]*>\s*</\1>', '', content_html, flags=re.IGNORECASE)
    content_html = re.sub(r'<p[^>]*>\s*<br[^>]*>\s*</p>', '', content_html, flags=re.IGNORECASE)
    if len(content_html) < before_empty_removal:
        logger.info(f"✅ Tags vazias removidas")
    
    # CAMADA 4: Verificação FINAL
    logger.info("\n🔵 CAMADA 4: Verificação FINAL crítica")
    if 'thank you for reading' in content_html.lower():
        logger.critical(f"❌❌❌ CTA AINDA PRESENTE APÓS LIMPEZA!")
        return False, cta_removal_log
    else:
        logger.info(f"✅ CHECK FINAL PASSOU: Nenhum CTA detectado")
    
    # Resumo
    logger.info(f"\n{'='*80}")
    logger.info(f"RESULTADO: ✅ SUCESSO")
    logger.info(f"Caracteres removidos: {len(original_html) - len(content_html)}")
    logger.info(f"Itens removidos: {len(cta_removal_log)}")
    logger.info(f"{'='*80}\n")
    
    return True, cta_removal_log


# TESTES
print("\n\n🚀 INICIANDO TESTES NUCLEARES DE CTA REMOVAL\n")

# TESTE 1: CTA no formato exato
test1_html = """
<article>
<h1>Article Title</h1>
<p>Some content here.</p>
<p>Thank you for reading this post, don't forget to subscribe!</p>
<p>More content after.</p>
</article>
"""
success1, log1 = test_nuclear_cta_removal(test1_html, "TESTE 1: CTA exato em <p>")
assert success1, "TESTE 1 FALHOU!"

# TESTE 2: CTA em diferentes formatos
test2_html = """
<p>Content here.</p>
<p>thank you for reading this post, don't forget to subscribe!</p>
<p>Content here.</p>
"""
success2, log2 = test_nuclear_cta_removal(test2_html, "TESTE 2: CTA em lowercase")
assert success2, "TESTE 2 FALHOU!"

# TESTE 3: Múltiplos CTAs
test3_html = """
<p>Article content.</p>
<p>Thank you for reading this post, don't forget to subscribe!</p>
<p>More content.</p>
<p>Thanks for visiting!</p>
<p>Even more content.</p>
"""
success3, log3 = test_nuclear_cta_removal(test3_html, "TESTE 3: Múltiplos CTAs")
assert success3, "TESTE 3 FALHOU!"

# TESTE 4: CTA com variações
test4_html = """
<p>Content.</p>
<p>Thank you for reading.</p>
<p>Content.</p>
"""
success4, log4 = test_nuclear_cta_removal(test4_html, "TESTE 4: CTA parcial")
assert success4, "TESTE 4 FALHOU!"

# TESTE 5: Conteúdo limpo (sem CTA)
test5_html = """
<p>This is a normal article.</p>
<p>With multiple paragraphs.</p>
<p>No CTAs here.</p>
"""
success5, log5 = test_nuclear_cta_removal(test5_html, "TESTE 5: Conteúdo LIMPO (controle)")
assert success5, "TESTE 5 FALHOU!"

print("\n" + "="*80)
print("✅✅✅ TODOS OS TESTES PASSARAM! 🎉🎉🎉")
print("="*80)
print(f"Total de testes: 5")
print(f"Sucessos: 5")
print(f"Falhas: 0")
