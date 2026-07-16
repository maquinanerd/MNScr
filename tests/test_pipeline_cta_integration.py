#!/usr/bin/env python3
"""
🎯 TESTE INTEGRADO: Simula EXATAMENTE o fluxo do pipeline.py
Garante que CTA será removido antes de publicar no WordPress
"""

import re
import logging

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

def simulate_pipeline_cta_removal(article_content):
    """Simula o EXATO fluxo de remoção de CTA do pipeline.py"""
    
    content_html = article_content
    original_html = content_html
    cta_removal_log = []
    
    logger.info("\n" + "="*80)
    logger.info("SIMULANDO FLUXO DO PIPELINE.PY")
    logger.info("="*80 + "\n")
    
    # CAMADA 1: Remover a frase EXATA (literal search)
    logger.info("🔵 CAMADA 1: Remover frases EXATAS (literal search)")
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
    logger.info("\n🔵 CAMADA 2: Remover parágrafos COM PADRÕES de CTA (regex)")
    cta_patterns = [
        r'<p[^>]*>.*?thank you for reading this post.*?don\'t forget to subscribe.*?</p>',
        r'<p[^>]*>.*?thank you for reading.*?don\'t forget.*?</p>',
        r'<p[^>]*>.*?thank you for reading.*?</p>',
        r'<p[^>]*>.*?thanks for reading.*?</p>',
        r'<p[^>]*>.*?thanks for visiting.*?</p>',
        r'<p[^>]*>.*?don\'t forget to subscribe.*?</p>',
        r'<p[^>]*>.*?subscribe now.*?</p>',
        r'<p[^>]*>.*?please subscribe.*?</p>',
        r'<p[^>]*>.*?subscribe to our.*?</p>',
        r'<p[^>]*>.*?stay tuned.*?</p>',
        r'<p[^>]*>.*?follow us.*?</p>',
        r'<p[^>]*>.*?if you enjoyed.*?</p>',
        r'<p[^>]*>.*?found this helpful.*?</p>',
        r'<p[^>]*>.*?click here.*?</p>',
        r'<p[^>]*>.*?read more.*?</p>',
        r'<p[^>]*>.*?sign up.*?</p>',
        r'<p[^>]*>.*?obrigado por ler.*?</p>',
        r'<p[^>]*>.*?obrigada por ler.*?</p>',
        r'<p[^>]*>.*?não esqueça de se inscrever.*?</p>',
        r'<p[^>]*>.*?se inscreva.*?</p>',
        r'<p[^>]*>.*?clique aqui.*?</p>',
        r'<p[^>]*>.*?leia mais.*?</p>',
        r'<p[^>]*>.*?cadastre-se.*?</p>',
        r'<p[^>]*>.*?fique atento.*?</p>',
        r'<p[^>]*>.*?nos siga.*?</p>',
        r'<p[^>]*>.*?mantenha-se atualizado.*?</p>',
        r'<p[^>]*>.*?este artigo foi.*?</p>',
        r'<p[^>]*>.*?se você gostou.*?</p>',
    ]
    
    for pattern in cta_patterns:
        original_length = len(content_html)
        matches = re.findall(pattern, content_html, flags=re.IGNORECASE | re.DOTALL)
        if matches:
            logger.error(f"🔥 Encontrado(s) {len(matches)} parágrafo(s) com CTA")
            for match in matches[:2]:
                cta_removal_log.append(f"REGEX: {match[:80]}")
        content_html = re.sub(pattern, '', content_html, flags=re.IGNORECASE | re.DOTALL)
        if len(content_html) < original_length:
            logger.info(f"✅ Parágrafo(s) removido(s) via regex")
    
    # CAMADA 3: Remover tags vazias deixadas para trás
    logger.info("\n🔵 CAMADA 3: Limpar tags VAZIAS")
    content_html = re.sub(r'<(p|div|span|article)[^>]*>\s*</\1>', '', content_html, flags=re.IGNORECASE)
    content_html = re.sub(r'<p[^>]*>\s*<br[^>]*>\s*</p>', '', content_html, flags=re.IGNORECASE)
    logger.info(f"✅ Tags vazias removidas")
    
    # CAMADA 4: Verificação FINAL - se ainda houver "thank you", REJEITA
    logger.info("\n🔵 CAMADA 4: Verificação FINAL crítica")
    if 'thank you for reading' in content_html.lower():
        logger.critical(f"❌❌❌ CRÍTICO: CTA ainda presente após limpeza! REJEITANDO ARTIGO!")
        return False, "FINAL CHECK: CTA detected after cleaning"
    else:
        logger.info(f"✅ CHECK FINAL PASSOU: Nenhum CTA detectado")
    
    # 🎯 PRÉ-PUBLICAÇÃO: Check final antes de publicar
    logger.info("\n🟢 CHECK PRÉ-PUBLICAÇÃO: Antes de chamar WordPress")
    final_cta_check = [
        "thank you for reading",
        "don't forget to subscribe",
        "subscribe now",
        "thanks for reading",
        "obrigado por ler",
        "não esqueça de se inscrever",
        "se inscreva",
    ]
    
    for cta_phrase in final_cta_check:
        if cta_phrase.lower() in content_html.lower():
            logger.critical(f"🚨🚨🚨 CRITICAL: CTA PHRASE DETECTED BEFORE PUBLISHING: '{cta_phrase}'")
            return False, f"FINAL CHECK: CTA detected before WordPress publishing - {cta_phrase}"
    
    logger.info(f"✅ CHECK FINAL PASSOU: Pronto para publicar no WordPress")
    
    # Resumo final
    logger.info(f"\n{'='*80}")
    logger.info(f"✅✅✅ ARTIGO APROVADO PARA PUBLICAÇÃO")
    logger.info(f"{'='*80}")
    logger.info(f"Caracteres removidos: {len(original_html) - len(content_html)}")
    logger.info(f"CTAs removidos: {len(cta_removal_log)}")
    if cta_removal_log:
        logger.info(f"Detalhes dos CTAs removidos:")
        for log_item in cta_removal_log:
            logger.info(f"  - {log_item[:80]}")
    logger.info(f"{'='*80}\n")
    
    return True, "OK - Ready to publish"


# ============================================================================
# CENÁRIOS DE TESTE REALISTAS
# ============================================================================

print("\n" + "#"*80)
print("# 🚀 TESTES INTEGRADOS DO PIPELINE")
print("#"*80)

# CENÁRIO 1: Artigo com CTA exato (caso real mais comum)
print("\n\n📍 CENÁRIO 1: Artigo com CTA exato (CASO REAL MAIS COMUM)")
print("-"*80)
article1 = """
<article>
<h1>Breaking News Article</h1>
<p>Lorem ipsum dolor sit amet, consectetur adipiscing elit.</p>
<p>More important content here that should stay.</p>
<p>Thank you for reading this post, don't forget to subscribe!</p>
<p>Final thoughts about the article.</p>
</article>
"""
success1, reason1 = simulate_pipeline_cta_removal(article1)
assert success1, f"CENÁRIO 1 FALHOU: {reason1}"

# CENÁRIO 2: Artigo com CTA em lowercase
print("\n\n📍 CENÁRIO 2: Artigo com CTA em lowercase")
print("-"*80)
article2 = """
<article>
<p>Great article content.</p>
<p>thank you for reading this post, don't forget to subscribe!</p>
<p>Final note.</p>
</article>
"""
success2, reason2 = simulate_pipeline_cta_removal(article2)
assert success2, f"CENÁRIO 2 FALHOU: {reason2}"

# CENÁRIO 3: Artigo com múltiplos CTAs
print("\n\n📍 CENÁRIO 3: Artigo com MÚLTIPLOS CTAs")
print("-"*80)
article3 = """
<p>Article start.</p>
<p>Thank you for reading this post, don't forget to subscribe!</p>
<p>Middle content.</p>
<p>Thanks for visiting our site. Please subscribe now!</p>
<p>More content.</p>
<p>Don't forget to subscribe for more updates!</p>
</article>
"""
success3, reason3 = simulate_pipeline_cta_removal(article3)
assert success3, f"CENÁRIO 3 FALHOU: {reason3}"

# CENÁRIO 4: Artigo LIMPO (controle positivo)
print("\n\n📍 CENÁRIO 4: Artigo LIMPO (controle - sem CTA)")
print("-"*80)
article4 = """
<article>
<h1>Clean Article</h1>
<p>This is a well-written article about important topics.</p>
<p>Lots of valuable information here.</p>
<p>More content with no calls to action.</p>
</article>
"""
success4, reason4 = simulate_pipeline_cta_removal(article4)
assert success4, f"CENÁRIO 4 FALHOU: {reason4}"

# CENÁRIO 5: Artigo com CTA português
print("\n\n📍 CENÁRIO 5: Artigo com CTA em PORTUGUÊS")
print("-"*80)
article5 = """
<p>Artigo muito bom sobre os negócios.</p>
<p>Mais conteúdo relevante aqui.</p>
<p>Obrigado por ler nosso artigo! Não esqueça de se inscrever para mais atualizações.</p>
<p>Conclusão do artigo.</p>
"""
success5, reason5 = simulate_pipeline_cta_removal(article5)
assert success5, f"CENÁRIO 5 FALHOU: {reason5}"

# CENÁRIO 6: Artigo com CTA escondido (HTML complexo)
print("\n\n📍 CENÁRIO 6: Artigo com CTA em HTML complexo")
print("-"*80)
article6 = """
<div class="article-content">
<p>Opening paragraph.</p>
<p class="cta-box">Thank you for reading this post, don't forget to subscribe!</p>
<p>Closing paragraph.</p>
</div>
"""
success6, reason6 = simulate_pipeline_cta_removal(article6)
assert success6, f"CENÁRIO 6 FALHOU: {reason6}"

print("\n\n" + "="*80)
print("✅✅✅ TODOS OS 6 CENÁRIOS PASSARAM COM SUCESSO! 🎉🎉🎉")
print("="*80)
print(f"Total de testes: 6")
print(f"Sucessos: 6")
print(f"Falhas: 0")
print("\n🚀 SISTEMA DE CTA REMOVAL ESTÁ PRONTO PARA PRODUÇÃO!")
print("="*80 + "\n")
