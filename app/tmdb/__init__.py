"""
app/tmdb/__init__.py
Pacote TMDB/Movie Hub dedicado do Máquina Nerd.

Exports públicos:
    enrich_article_with_tmdb  — função de enriquecimento segura para o pipeline
    is_available              — verifica se o hub está operacional
    get_hub                   — retorna instância singleton do HubManager
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Disponibilidade rápida (não instancia nada)
# ---------------------------------------------------------------------------

def is_available() -> bool:
    """Retorna True se TMDB está habilitado e com chave configurada."""
    enabled = os.getenv("TMDB_ENABLED", "false").lower() == "true"
    key = os.getenv("TMDB_API_KEY", "").strip()
    return enabled and bool(key)


# ---------------------------------------------------------------------------
# Exports lazy — só importa os módulos pesados quando necessário
# ---------------------------------------------------------------------------

def enrich_article_with_tmdb(
    content_html: str,
    title: str,
    category: str = "",
):
    """
    Função de alto nível para enriquecer artigo com dados TMDB.
    Safe-to-call mesmo se TMDB_ENABLED=false ou sem chave.
    Retorna (html_original, []) sem efeito colateral nesse caso.
    """
    if not is_available():
        return content_html, []

    try:
        from app.tmdb.enricher import enrich_article_with_tmdb as _enrich
        return _enrich(content_html, title, category)
    except Exception as exc:
        logger.warning("[TMDB] enrich_article_with_tmdb falhou (non-fatal): %s", exc)
        return content_html, []


def get_hub():
    """Retorna instância singleton do HubManager ou None se indisponível."""
    if not is_available():
        return None
    try:
        from app.tmdb.hub_manager import get_hub as _get_hub
        return _get_hub()
    except Exception as exc:
        logger.warning("[TMDB] get_hub falhou: %s", exc)
        return None


__all__ = [
    "is_available",
    "enrich_article_with_tmdb",
    "get_hub",
]
