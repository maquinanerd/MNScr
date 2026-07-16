"""app/tmdb/matching.py — Score engine para match título ↔ TMDB"""
from __future__ import annotations
import re
import unicodedata
import logging
from typing import Optional, Tuple, List, Dict

logger = logging.getLogger(__name__)

# Palavras a remover antes de comparar
_NOISE = re.compile(
    r'\b(o|a|os|as|um|uma|de|do|da|dos|das|em|no|na|nos|nas|'
    r'the|a|an|of|in|on|at|to|for|and|or|but|is|are|was|were)\b',
    re.IGNORECASE
)

# Sufixos editoriais comuns
_EDITORIAL_SUFFIX = re.compile(
    r'\s*[:\-–—]\s*(trailer|review|crítica|análise|estreia|temporada\s*\d+|season\s*\d+|episode\s*\d+).*$',
    re.IGNORECASE
)

# Títulos genéricos a rejeitar
_GENERIC_TITLES = {
    "filme", "filmes", "série", "séries", "temporada", "episódio",
    "movie", "series", "season", "episode", "trailer", "review",
    "lançamento", "estreia", "cinema", "streaming", "netflix",
    "hbo", "disney", "amazon", "the", "a", "an",
}


def normalize_title(title: str) -> str:
    """Normaliza título para comparação."""
    if not title:
        return ""
    # Remove acentos
    text = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    # Remove sufixos editoriais
    text = _EDITORIAL_SUFFIX.sub("", text)
    # Minúsculas
    text = text.lower()
    # Remove pontuação exceto hífen
    text = re.sub(r"[^\w\s-]", " ", text)
    # Remove noise words
    text = _NOISE.sub(" ", text)
    # Normaliza espaços
    return " ".join(text.split())


def _jaccard(a: str, b: str) -> float:
    """Similaridade de Jaccard entre conjuntos de palavras."""
    sa, sb = set(a.split()), set(b.split())
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _prefix_score(query: str, candidate: str) -> float:
    """Score se candidate começa com query ou vice-versa."""
    if not query or not candidate:
        return 0.0
    q, c = query.lower(), candidate.lower()
    if q == c:
        return 1.0
    if c.startswith(q) or q.startswith(c):
        shorter = min(len(q), len(c))
        longer = max(len(q), len(c))
        return shorter / longer * 0.9
    return 0.0


def confidence_score(query_title: str, tmdb_title: str, popularity: float = 0) -> float:
    """
    Calcula score de confiança [0.0–1.0] entre título do artigo e resultado TMDB.

    Componentes:
      - Similaridade Jaccard nas palavras normalizadas  (peso 0.5)
      - Prefix score                                    (peso 0.3)
      - Boost por popularidade (>= 10 → +0.05)         (peso 0.2)
    """
    qn = normalize_title(query_title)
    tn = normalize_title(tmdb_title)

    if not qn or not tn:
        return 0.0

    # Exato após normalização
    if qn == tn:
        return 1.0

    jaccard = _jaccard(qn, tn)
    prefix = _prefix_score(qn, tn)

    score = jaccard * 0.5 + prefix * 0.3

    # Boost leve por popularidade
    if popularity >= 50:
        score += 0.10
    elif popularity >= 10:
        score += 0.05

    return min(score, 1.0)


def is_media_related_title(title: str) -> bool:
    """Verifica se o título do artigo é sobre mídia audiovisual."""
    if not title:
        return False
    lower = title.lower()
    media_words = [
        "filme", "filmes", "série", "séries", "movie", "series",
        "temporada", "season", "episódio", "episode", "estreia",
        "trailer", "lançamento", "cinema", "netflix", "hbo", "disney+",
        "prime video", "apple tv", "streaming", "bilheteria", "oscar",
    ]
    return any(w in lower for w in media_words)


def is_media_related_category(category: str) -> bool:
    """Verifica se a categoria é elegível para enriquecimento TMDB."""
    eligible = {"Filmes", "Séries", "filmes", "séries", "movies", "tv", "cinema"}
    return category in eligible


def extract_year_from_text(text: str) -> Optional[int]:
    """Extrai o primeiro ano de 4 dígitos do texto (1900–2099)."""
    match = re.search(r'\b(19\d{2}|20\d{2})\b', text)
    return int(match.group()) if match else None


def is_generic_title(title: str) -> bool:
    """Retorna True se o título for muito genérico para buscar na TMDB."""
    norm = normalize_title(title)
    words = set(norm.split())
    return not words or words.issubset(_GENERIC_TITLES) or len(norm) < 4


def pick_best_match(
    query: str,
    results: List[Dict],
    min_confidence: float,
    title_field: str = "title",
) -> Tuple[Optional[Dict], float, str]:
    """
    Escolhe o melhor resultado de uma lista TMDB.

    Returns:
        (melhor_item, confidence, razão) ou (None, 0.0, razão_descarte)
    """
    if not results:
        return None, 0.0, "sem_resultados"

    best_item, best_score, best_reason = None, 0.0, ""

    for item in results[:5]:
        candidate_title = item.get(title_field, "")
        popularity = item.get("popularity", 0)
        score = confidence_score(query, candidate_title, popularity)

        reason = f"jaccard+prefix score={score:.2f} vs '{candidate_title}'"

        if score > best_score:
            best_score = score
            best_item = item
            best_reason = reason

    if best_score < min_confidence:
        logger.debug(
            "[TMDB/matching] Descartado (score %.2f < %.2f): '%s'",
            best_score, min_confidence, query[:60],
        )
        return None, best_score, f"confiança insuficiente ({best_score:.2f} < {min_confidence:.2f})"

    return best_item, best_score, best_reason
