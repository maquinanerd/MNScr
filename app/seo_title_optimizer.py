"""
SEO Title Optimizer for Google News & Discovery

Otimiza títulos de notícias seguindo as melhores práticas para Google News,
Google Discovery e outros agregadores de notícias.

Regras implementadas:
- Tamanho: 50-70 caracteres (máximo 100)
- Palavra-chave principal nos primeiros 5 palavras
- Estrutura: [Palavra-chave] + [Verbo de ação] + [Informação específica]
- Sem caracteres HTML especiais
- Sem clickbait vago
- Com números, datas ou contexto temporal quando possível
"""

import logging
import re
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# Verbos de ação que funcionam bem em títulos de notícias
ACTION_VERBS = {
    'anuncia', 'lança', 'critica', 'vence', 'fecha', 'retorna', 'cancela',
    'adia', 'confirma', 'nega', 'revela', 'descobre', 'desenvolve', 'cria',
    'assina', 'acusa', 'investiga', 'aprova', 'rejeita', 'promete', 'ameaça',
    'realiza', 'completa', 'inaugura', 'publica', 'exibe', 'produz', 'dirige',
    'estreia', 'arrecada', 'conquista', 'bate', 'muda', 'transforma', 'rompe',
    'estabelece', 'garante', 'expõe', 'convida', 'proíbe', 'libera', 'quebra',
    'planeja',
}

# Palavras vagas que reduzem qualidade
VAGUE_WORDS = {
    'pode', 'talvez', 'alguns', 'muitos', 'diz-se', 'parece', 'supostamente',
    'provavelmente', 'possivelmente', 'alegadamente', 'aparentemente', 'supostamente',
    'segundo relatos', 'rumores', 'boatos'
}

PROTECTED_PT_BR_TOKENS = {
    "pode", "deve", "vai", "tem", "têm", "será", "serão", "está", "estão",
    "são", "foi", "faz", "fez", "podem",
}

# Caracteres HTML que precisam ser removidos
HTML_CHAR_REPLACEMENTS = {
    '&#8216;': "'",  # Left single quote
    '&#8217;': "'",  # Right single quote
    '&#8220;': '"',  # Left double quote
    '&#8221;': '"',  # Right double quote
    '&#8211;': '-',  # En dash
    '&#8212;': '-',  # Em dash
    '&quot;': '"',
    '&apos;': "'",
    '&mdash;': '-',
    '&ndash;': '-',
}


def clean_html_characters(title: str) -> str:
    """Remove HTML character entities and replace with ASCII equivalents."""
    if not title:
        return title

    cleaned = title
    for html_char, replacement in HTML_CHAR_REPLACEMENTS.items():
        cleaned = cleaned.replace(html_char, replacement)

    # Remove any remaining HTML entities
    cleaned = re.sub(r'&#\d+;', '', cleaned)
    cleaned = re.sub(r'&[a-z]+;', '', cleaned)

    return cleaned


def _tokens_for_protection(title: str) -> set[str]:
    return set(re.findall(r"\b[\wÀ-ÿ]+\b", (title or "").lower()))


def _removed_protected_tokens(original: str, candidate: str) -> set[str]:
    original_tokens = _tokens_for_protection(original)
    candidate_tokens = _tokens_for_protection(candidate)
    return (original_tokens - candidate_tokens) & PROTECTED_PT_BR_TOKENS


def remove_clickbait(title: str) -> str:
    """Remove common clickbait patterns."""
    clickbait_patterns = [
        r'^Você não vai acreditar',
        r'^Você precisa ver',
        r'^Isto é incrível',
        r'^Não sabíamos sobre',
        r'^Ninguém esperava',
        r'^O que aconteceu foi',
        r'^Prepare-se para',
    ]

    result = title
    for pattern in clickbait_patterns:
        result = re.sub(pattern, '', result, flags=re.IGNORECASE)

    return result.strip()


def extract_keyword(title: str, content: Optional[str] = None) -> Optional[str]:
    """
    Extract the main keyword from title or content.

    Returns the first significant word (noun or proper noun).
    """
    # Remove common stop words
    stop_words = {
        'o', 'a', 'um', 'uma', 'de', 'do', 'da', 'em', 'e', 'é', 'por', 'para',
        'com', 'sem', 'se', 'não', 'mas', 'ou', 'quando', 'onde', 'como', 'que'
    }

    words = title.lower().split()

    for word in words:
        # Remove punctuation
        clean_word = re.sub(r'[^\w]', '', word)

        # Skip if too short or is stop word
        if len(clean_word) < 3 or clean_word in stop_words:
            continue

        # This is likely our keyword
        return clean_word

    return None


def analyze_title_quality(title: str) -> Tuple[float, list]:
    """
    Analyze title quality and return score (0-100) and list of issues.

    Checks:
    - Length (50-70 optimal, 40-100 acceptable)
    - Keyword position (should be in first 5 words)
    - Action verb presence
    - Vague words absence
    - HTML characters absence
    - Clickbait patterns absence
    """
    issues = []
    score = 100.0

    # Stop words
    stop_words = {
        'o', 'a', 'um', 'uma', 'de', 'do', 'da', 'em', 'e', 'é', 'por', 'para',
        'com', 'sem', 'se', 'não', 'mas', 'ou', 'quando', 'onde', 'como', 'que'
    }

    title_clean = title.strip()
    char_count = len(title_clean)
    word_count = len(title_clean.split())

    # Check length
    if char_count < 40:
        issues.append("Título muito curto (menos de 40 caracteres)")
        score -= 15
    elif char_count < 50:
        issues.append("Título poderia ser mais longo (menos de 50 caracteres)")
        score -= 5
    elif char_count > 100:
        issues.append(f"Título muito longo ({char_count} caracteres, máximo 100)")
        score -= 15
    elif char_count > 70:
        issues.append(f"Título um pouco longo ({char_count} caracteres, ideal é 50-70)")
        score -= 3

    # Check for keyword position
    words = title_clean.lower().split()
    has_keyword_early = False
    for i, word in enumerate(words[:5]):
        if len(word) > 3 and word not in stop_words:
            has_keyword_early = True
            break

    if not has_keyword_early and word_count > 5:
        issues.append("Palavra-chave não está nos primeiros 5 palavras")
        score -= 10

    # Check for action verbs
    has_action_verb = any(verb in title.lower() for verb in ACTION_VERBS)
    if not has_action_verb:
        issues.append("Ausência de verbo de ação (recomenda-se usar: anunciou, lançou, etc)")
        score -= 5

    # Check for vague words
    vague_found = [word for word in VAGUE_WORDS if word in title.lower()]
    if vague_found:
        issues.append(f"Contém palavras vagas: {', '.join(vague_found)}")
        score -= 10

    # Check for HTML characters
    if any(char in title for char in HTML_CHAR_REPLACEMENTS.keys()):
        issues.append("Contém caracteres HTML especiais")
        score -= 10

    # Check for clickbait
    if re.search(r'Você (não|precisa|não vai acreditar)', title, re.IGNORECASE):
        issues.append("Contém padrão de clickbait")
        score -= 15

    # Check for ALL CAPS
    if title.isupper() and len(title) > 5:
        issues.append("Título em MAIÚSCULA (evitar)")
        score -= 5

    # Bonus for numbers/dates
    if re.search(r'\d{4}|2025|2024|\d+%|US\$', title):
        score += 5

    # Bonus for proper structure
    if has_action_verb and has_keyword_early:
        score += 5

    # Ensure score is between 0-100
    score = max(0, min(100, score))

    return score, issues


def optimize_title(
    original_title: str,
    content: Optional[str] = None,
    min_length: int = 50,
    max_length: int = 70,
    target_length: int = 65
) -> Tuple[str, dict]:
    """
    Optimize a news title for Google News & Discovery.

    Args:
        original_title: The original title to optimize
        content: Optional content to extract keywords/context
        min_length: Minimum character length
        max_length: Maximum character length for optimal range
        target_length: Target length for optimization

    Returns:
        Tuple of (optimized_title, optimization_report)
    """
    report = {
        'original': original_title,
        'optimized': '',
        'original_length': len(original_title),
        'optimized_length': 0,
        'original_score': 0.0,
        'optimized_score': 0.0,
        'original_issues': [],
        'optimized_issues': [],
        'changes_made': []
    }

    # Start with the original
    optimized = original_title.strip()

    # 1. Clean HTML characters
    if any(char in optimized for char in HTML_CHAR_REPLACEMENTS.keys()):
        optimized = clean_html_characters(optimized)
        report['changes_made'].append('Removidos caracteres HTML especiais')

    # 2. Remove clickbait
    original_for_clickbait = optimized
    optimized = remove_clickbait(optimized)
    if optimized != original_for_clickbait:
        report['changes_made'].append('Removido padrão de clickbait')
    # A proteção começa depois da remoção do prefixo proibido. O "vai" de
    # "você não vai acreditar" pertence ao clickbait, não à afirmação editorial.
    protected_baseline = optimized

    # 3. Remove vague words
    words = optimized.split()
    # Verbos e auxiliares protegidos carregam modalidade factual ("pode" não é
    # o mesmo que "vai"). Preserve-os e remova apenas vagueza que não muda o
    # compromisso factual da frase.
    cleaned_words = [
        w for w in words
        if w.lower() not in VAGUE_WORDS or w.lower() in PROTECTED_PT_BR_TOKENS
    ]
    protected_vague = sorted(
        {w.lower() for w in words} & VAGUE_WORDS & PROTECTED_PT_BR_TOKENS
    )
    if protected_vague:
        report['changes_made'].append(
            f"Preservado verbo/auxiliar modal: {', '.join(protected_vague)}"
        )
    if len(cleaned_words) < len(words):
        optimized = ' '.join(cleaned_words)
        report['changes_made'].append('Removidas palavras vagas')

    # 4. Ensure action verb (if not present, add one if possible)
    has_action = any(verb in optimized.lower() for verb in ACTION_VERBS)
    if not has_action and content:
        # Try to infer an action verb from context
        optimized = _infer_action_verb(optimized, content)

    # 5. Length optimization
    current_length = len(optimized)
    if current_length > 100:
        # Too long - truncate intelligently
        optimized = _truncate_title(optimized, max_length)
        report['changes_made'].append(f'Encurtado de {current_length} para {len(optimized)} caracteres')
    elif current_length < min_length:
        # Too short - try to expand if we have content
        if content:
            optimized = _expand_title(optimized, target_length, content)
            report['changes_made'].append(f'Expandido de {current_length} para {len(optimized)} caracteres')

    # 6. Fix quote marks (ensure they're straight quotes, not typographic)
    optimized = optimized.replace(''', "'").replace(''', "'")
    optimized = optimized.replace('"', '"').replace('"', '"')

    removed_protected = _removed_protected_tokens(protected_baseline, optimized)
    if removed_protected:
        logger.warning(
            "Title optimization rejected: removed protected PT-BR token(s): %s",
            ", ".join(sorted(removed_protected)),
        )
        optimized = original_title.strip()
        report['changes_made'].append(
            f"Transformação rejeitada por remover verbo/auxiliar: {', '.join(sorted(removed_protected))}"
        )

    # Analyze before and after
    original_score, original_issues = analyze_title_quality(original_title)
    optimized_score, optimized_issues = analyze_title_quality(optimized)

    report['original_length'] = len(original_title)
    report['optimized_length'] = len(optimized)
    report['original_score'] = original_score
    report['optimized_score'] = optimized_score
    report['original_issues'] = original_issues
    report['optimized_issues'] = optimized_issues
    report['optimized'] = optimized
    report['score_improvement'] = optimized_score - original_score

    logger.info(f"Título otimizado: '{original_title}' → '{optimized}'")
    logger.info(f"Score: {original_score:.1f} → {optimized_score:.1f} (melhoria: {report['score_improvement']:+.1f})")

    return optimized, report


def _infer_action_verb(title: str, content: str) -> str:
    """Infer appropriate action verb based on content.

    IMPORTANTE: Esta função está DESABILITADA porque estava adicionando
    "lança" incorretamente a títulos. A detecção de action verbs deve
    ser mais conservadora. Retorna o título original.
    """
    # Desabilitado por enquanto - estava adicionando "lança" de forma incorreta
    # Mantém a função para compatibilidade com código existente, mas apenas retorna
    # o título original sem modificações
    return title


def _truncate_title(title: str, max_length: int) -> str:
    """Intelligently truncate title to max length."""
    if len(title) <= max_length:
        return title

    # Try to cut at word boundary
    truncated = title[:max_length]
    last_space = truncated.rfind(' ')

    if last_space > max_length * 0.7:  # Only cut at space if reasonable
        truncated = truncated[:last_space]

    # Remove trailing punctuation
    truncated = re.sub(r'[,;:.]$', '', truncated)

    return truncated.strip()


def _expand_title(title: str, target_length: int, content: str) -> str:
    """Try to expand title to target length using content."""
    if len(title) >= target_length:
        return title

    # Extract keywords or context from content
    words = content.split()
    nouns = [w for w in words if len(w) > 4 and w not in VAGUE_WORDS]

    if nouns:
        # Add relevant context
        additional_context = ' '.join(nouns[:2])
        expanded = f"{title} em {additional_context}"[:target_length]
        return expanded

    return title


def batch_optimize_titles(titles: list, content_list: list = None) -> list:
    """Optimize multiple titles at once."""
    results = []

    content_list = content_list or [None] * len(titles)

    for title, content in zip(titles, content_list):
        optimized, report = optimize_title(title, content)
        results.append({
            'original': title,
            'optimized': optimized,
            'report': report
        })

    return results


# Exemplo de uso
if __name__ == "__main__":
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(name)s - %(levelname)s - %(message)s'
    )

    # Test examples
    test_titles = [
        "Você não vai acreditar no que a Marvel anunciou para 2025",
        "Game of Thrones criador anuncia novo projeto com Netflix",
        "Disney+ possível cancelamento de série popular segundo fontes",
        "2024: Ano de recordes para indústria de streaming",
    ]

    print("=" * 70)
    print("SEO TITLE OPTIMIZER - TEST RESULTS")
    print("=" * 70)
    print()

    for title in test_titles:
        optimized, report = optimize_title(title)

        print(f"Original:  {title}")
        print(f"Otimizado: {optimized}")
        print(f"Score:     {report['original_score']:.1f} → {report['optimized_score']:.1f}")
        print(f"Tamanho:   {report['original_length']} → {report['optimized_length']} caracteres")

        if report['changes_made']:
            print("Mudanças:")
            for change in report['changes_made']:
                print(f"  - {change}")

        if report['optimized_issues']:
            print("Questões restantes:")
            for issue in report['optimized_issues']:
                print(f"  - {issue}")

        print()
