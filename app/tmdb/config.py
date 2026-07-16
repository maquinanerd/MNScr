"""
app/tmdb/config.py
Configuração centralizada do pacote TMDB/Movie Hub.
Todas as variáveis de ambiente lidas aqui — nunca expostas em logs.
"""

from __future__ import annotations

import os
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Raiz do projeto (2 níveis acima de app/tmdb/)
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).parent.parent.parent


# ---------------------------------------------------------------------------
# Habilitação e autenticação
# ---------------------------------------------------------------------------
TMDB_ENABLED: bool = os.getenv("TMDB_ENABLED", "false").lower() == "true"

# Chave e token — NUNCA logar os valores completos
_TMDB_API_KEY_RAW: str = os.getenv("TMDB_API_KEY", "").strip()
_TMDB_ACCESS_TOKEN_RAW: str = os.getenv("TMDB_ACCESS_TOKEN", "").strip()

TMDB_API_KEY: str = _TMDB_API_KEY_RAW
TMDB_ACCESS_TOKEN: str = _TMDB_ACCESS_TOKEN_RAW

# Verificações de disponibilidade
HAS_API_KEY: bool = bool(_TMDB_API_KEY_RAW)
HAS_ACCESS_TOKEN: bool = bool(_TMDB_ACCESS_TOKEN_RAW)

# ---------------------------------------------------------------------------
# Localização
# ---------------------------------------------------------------------------
TMDB_LANGUAGE: str = os.getenv("TMDB_LANGUAGE", "pt-BR")
TMDB_REGION: str = os.getenv("TMDB_REGION", "BR")

# ---------------------------------------------------------------------------
# Banco de dados dedicado
# ---------------------------------------------------------------------------
_db_path_raw = os.getenv("TMDB_DB_PATH", "data/tmdb_hub.sqlite")
TMDB_DB_PATH: Path = (
    Path(_db_path_raw)
    if os.path.isabs(_db_path_raw)
    else _PROJECT_ROOT / _db_path_raw
)

# ---------------------------------------------------------------------------
# Sincronização
# ---------------------------------------------------------------------------
TMDB_SYNC_TRENDING: bool = os.getenv("TMDB_SYNC_TRENDING", "true").lower() == "true"
TMDB_SYNC_UPCOMING: bool = os.getenv("TMDB_SYNC_UPCOMING", "true").lower() == "true"
TMDB_SYNC_WATCH_PROVIDERS: bool = os.getenv("TMDB_SYNC_WATCH_PROVIDERS", "true").lower() == "true"

# ---------------------------------------------------------------------------
# Enriquecimento de artigos
# ---------------------------------------------------------------------------
TMDB_ARTICLE_ENRICHMENT: bool = os.getenv("TMDB_ARTICLE_ENRICHMENT", "true").lower() == "true"
TMDB_MAX_ARTICLE_BLOCKS: int = int(os.getenv("TMDB_MAX_ARTICLE_BLOCKS", "1"))
TMDB_MIN_MATCH_CONFIDENCE: float = float(os.getenv("TMDB_MIN_MATCH_CONFIDENCE", "0.82"))

# ---------------------------------------------------------------------------
# Hub de filmes/séries
# ---------------------------------------------------------------------------
TMDB_HUB_ENABLED: bool = os.getenv("TMDB_HUB_ENABLED", "true").lower() == "true"
TMDB_HUB_DRAFT_MODE: bool = os.getenv("TMDB_HUB_DRAFT_MODE", "true").lower() == "true"
TMDB_AUTO_PUBLISH_HUB: bool = os.getenv("TMDB_AUTO_PUBLISH_HUB", "false").lower() == "true"

# ---------------------------------------------------------------------------
# Rate limiting / timeouts
# ---------------------------------------------------------------------------
TMDB_REQUEST_TIMEOUT: int = int(os.getenv("TMDB_REQUEST_TIMEOUT", "10"))
TMDB_RATE_LIMIT_SLEEP: float = float(os.getenv("TMDB_RATE_LIMIT_SLEEP", "0.25"))

# ---------------------------------------------------------------------------
# Categorias WordPress para Hub
# ---------------------------------------------------------------------------
TMDB_WP_CATEGORIES: dict = {
    "movies": "Filmes",
    "tv": "Séries",
    "streaming": "Onde Assistir",
    "upcoming": "Lançamentos",
    "trending": "Em Alta",
}

# ---------------------------------------------------------------------------
# Helpers de diagnóstico (sem expor chaves)
# ---------------------------------------------------------------------------

def masked_key(key: str, show: int = 4) -> str:
    """Retorna chave mascarada para logs seguros."""
    if not key:
        return "(vazia)"
    return f"{key[:show]}...{key[-2:]}" if len(key) > show + 2 else "***"


def log_config_status() -> None:
    """Loga status da configuração TMDB (sem expor credenciais)."""
    logger.info("[TMDB/config] enabled=%s", TMDB_ENABLED)
    logger.info("[TMDB/config] api_key=%s", masked_key(_TMDB_API_KEY_RAW))
    logger.info("[TMDB/config] access_token=%s", masked_key(_TMDB_ACCESS_TOKEN_RAW, 6))
    logger.info("[TMDB/config] language=%s region=%s", TMDB_LANGUAGE, TMDB_REGION)
    logger.info("[TMDB/config] db_path=%s", TMDB_DB_PATH)
    logger.info("[TMDB/config] enrichment=%s confidence=%.2f", TMDB_ARTICLE_ENRICHMENT, TMDB_MIN_MATCH_CONFIDENCE)
    logger.info("[TMDB/config] hub_enabled=%s draft_mode=%s auto_publish=%s",
                TMDB_HUB_ENABLED, TMDB_HUB_DRAFT_MODE, TMDB_AUTO_PUBLISH_HUB)


def validate_config() -> list[str]:
    """
    Valida configuração e retorna lista de problemas encontrados.
    Não levanta exceções — permite que o pipeline decida o que fazer.
    """
    issues = []
    if TMDB_ENABLED and not HAS_API_KEY:
        issues.append("TMDB_ENABLED=true mas TMDB_API_KEY está vazia")
    if TMDB_AUTO_PUBLISH_HUB:
        issues.append("TMDB_AUTO_PUBLISH_HUB=true — publicação automática habilitada (verifique se intencional)")
    return issues
