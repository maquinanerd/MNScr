"""
app/tmdb_enricher.py — SHIM de compatibilidade
Delega para app/tmdb/enricher.py (pacote dedicado).
Mantido para evitar quebrar imports existentes no pipeline.
"""
from app.tmdb.enricher import (  # noqa: F401
    TMDbEnricher,
    enrich_article_with_tmdb,
    get_enricher,
    build_info_block,
)
