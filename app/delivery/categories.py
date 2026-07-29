"""Explicit topic → canonical category mapping.

The AI never gets to invent a category in the CMS. Only values already present
in ``app.config.EDITORIAL_CATEGORIES`` may be sent, and the mapping between an
event topic and one of those values is written down here rather than inferred.

When a topic has no mapping the delivery is **blocked** with an explicit reason.
Falling back to a generic category would mean shipping a classification nobody
decided, and would hide the fact that a new topic needs a mapping.
"""

from __future__ import annotations

import logging
from typing import Dict, Final, List, Optional, Tuple

logger = logging.getLogger(__name__)

#: Derived from the mapping the pipeline already uses (``SOURCE_CATEGORY_MAP``
#: in ``app.config``), expressed by topic instead of by feed id. No new outlet,
#: vertical or taxonomy is introduced here.
TOPIC_TO_CATEGORY: Final[Dict[str, str]] = {
    "movies": "Filmes",
    "filmes": "Filmes",
    "movie": "Filmes",
    "tv": "Séries",
    "series": "Séries",
    "séries": "Séries",
    "shows": "Séries",
    "news": "Notícias",
    "noticias": "Notícias",
    "notícias": "Notícias",
}

MAX_TAGS: Final[int] = 12
MAX_TAG_LENGTH: Final[int] = 60


def _normalize(value: Optional[str]) -> str:
    return " ".join(str(value or "").split()).strip()


def canonical_categories() -> List[str]:
    """The only category values MNScr may ever send."""
    from app.config import EDITORIAL_CATEGORIES

    return list(EDITORIAL_CATEGORIES)


def resolve_category(
    topic: Optional[str],
    category_suggestions: Optional[List[str]] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """Resolve a canonical category.

    Returns ``(category, reason)``. ``category`` is ``None`` when nothing could
    be resolved, and ``reason`` then explains why — the caller turns that into a
    blocked delivery rather than guessing.

    A suggestion from the draft is honoured only when it already *is* a
    canonical category. Suggestions are editorial hints, not authority to create
    taxonomy.
    """
    allowed = canonical_categories()
    allowed_by_fold = {c.casefold(): c for c in allowed}

    for suggestion in category_suggestions or []:
        candidate = allowed_by_fold.get(_normalize(suggestion).casefold())
        if candidate:
            return candidate, None

    normalized_topic = _normalize(topic).casefold()
    if not normalized_topic:
        return None, "evento sem topico e sem categoria canonica sugerida"

    mapped = TOPIC_TO_CATEGORY.get(normalized_topic)
    if mapped and mapped in allowed:
        return mapped, None

    if mapped:
        # Mapped to something the runtime no longer accepts: a configuration
        # drift worth reporting rather than silently substituting.
        return None, (
            f"topico '{topic}' mapeia para '{mapped}', que nao esta em "
            f"EDITORIAL_CATEGORIES ({', '.join(allowed)})"
        )

    return None, (
        f"topico '{topic}' nao tem categoria canonica correspondente; "
        f"ampliar TOPIC_TO_CATEGORY ou classificar manualmente"
    )


def normalize_tags(tags: Optional[List[str]]) -> List[str]:
    """Clean, deduplicate and cap tag suggestions.

    Tags are freer than categories — the CMS treats them as labels — but they
    are still normalized so the destination never receives whitespace noise,
    duplicates or an unbounded list.
    """
    seen: set[str] = set()
    result: List[str] = []
    for raw in tags or []:
        tag = _normalize(raw)
        if not tag or len(tag) > MAX_TAG_LENGTH:
            continue
        key = tag.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(tag)
        if len(result) >= MAX_TAGS:
            break
    return result


__all__ = [
    "MAX_TAGS",
    "MAX_TAG_LENGTH",
    "TOPIC_TO_CATEGORY",
    "canonical_categories",
    "normalize_tags",
    "resolve_category",
]
