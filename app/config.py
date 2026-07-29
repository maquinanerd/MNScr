import logging
import os
import re
from typing import Any, Dict, List

from dotenv import load_dotenv

# Carrega variáveis de ambiente de um arquivo .env
load_dotenv(override=True)  # override=True garante que .env sobrescreve variaveis do sistema

logger = logging.getLogger(__name__)

# --- Ordem de processamento dos feeds ---
PIPELINE_ORDER: List[str] = [
    'rssprime_movies',
    'rssprime_tv',
    'screenrant_movie_lists',
    'screenrant_movie_news',
    'screenrant_tv',
]

# --- Feeds RSS (padronizados, sem "synthetic_from") ---
RSS_FEEDS: Dict[str, Dict[str, Any]] = {
    'rssprime_movies': {
        'type': 'rss',
        'urls': ['https://rss.thepeg.site/feeds/superfeed/movies/rss'],
        'category': 'Filmes',
        'source_name': 'RSSPRIME Superfeed',
        'origin': 'superfeed',
        'topic': 'movies',
        'priority': 100,
    },
    'rssprime_tv': {
        'type': 'rss',
        'urls': ['https://rss.thepeg.site/feeds/superfeed/tv/rss'],
        'category': 'Séries',
        'source_name': 'RSSPRIME Superfeed',
        'origin': 'superfeed',
        'topic': 'tv',
        'priority': 100,
    },
    'screenrant_movie_lists': {
        'urls': ['https://screenrant.com/feed/movie-lists/'],
        'category': 'movies',
        'source_name': 'ScreenRant',
        'origin': 'fallback',
        'topic': 'movies',
        'priority': 10,
    },
    'screenrant_movie_news': {
        'urls': ['https://screenrant.com/feed/movie-news/'],
        'category': 'movies',
        'source_name': 'ScreenRant',
        'origin': 'fallback',
        'topic': 'movies',
        'priority': 10,
    },
    'screenrant_tv': {
        'urls': ['https://screenrant.com/feed/tv/'],
        'category': 'tv',
        'source_name': 'ScreenRant',
        'origin': 'fallback',
        'topic': 'tv',
        'priority': 10,
    },
}

# --- HTTP ---
USER_AGENT = os.getenv(
    'MNSCR_USER_AGENT',
    'MNScr/1.0 (+editorial draft pipeline; contact: editorial@cinerie)',
)

# --- Saída editorial (MS-1) ---
# MNScr produz drafts. WordPress foi removido do caminho canônico.
OUTPUT_MODE = os.getenv('MNSCR_OUTPUT_MODE', 'local').strip().lower()
LOCAL_DRAFT_DIR = os.getenv('MNSCR_LOCAL_DRAFT_DIR', 'artifacts/local-drafts')

PAYLOAD_CONFIG: Dict[str, Any] = {
    'enabled': os.getenv('PAYLOAD_ENABLED', 'false').strip().lower() == 'true',
    'base_url': (os.getenv('PAYLOAD_BASE_URL') or '').strip(),
    'api_token': (os.getenv('PAYLOAD_API_TOKEN') or '').strip(),
    'drafts_collection': (os.getenv('PAYLOAD_DRAFTS_COLLECTION') or 'editorial-drafts').strip(),
}

# Domínio editorial usado para sugerir links internos no prompt. Não é destino
# de publicação.
PUBLISHER_DOMAIN = (os.getenv('PUBLISHER_DOMAIN') or '').strip().lower()

# --- Contrato de entrada (MS-2) ---
# Contrato canônico do RSS Prime. O feed legado ainda é aceito por um adaptador
# temporário; quando MNSCR_REQUIRED_INPUT_CONTRACT for fixado em v1, ou quando
# MNSCR_ACCEPT_LEGACY_FEED for false, itens sem contrato são rejeitados.
#
# TEMPORÁRIO: ACCEPT_LEGACY_FEED existe apenas até o RSS Prime emitir
# `rss-prime-event-v1`. Será removido — não construa nada novo sobre ele.
ACCEPT_LEGACY_FEED = os.getenv('MNSCR_ACCEPT_LEGACY_FEED', 'true').strip().lower() != 'false'
REQUIRED_INPUT_CONTRACT = (os.getenv('MNSCR_REQUIRED_INPUT_CONTRACT') or '').strip()

# O payload bruto é guardado para permitir replay fiel. Desligue apenas se o
# volume for um problema: sem ele o replay perde a evidência do que chegou.
STORE_RAW_EVENT_PAYLOAD = (
    os.getenv('MNSCR_STORE_RAW_EVENT_PAYLOAD', 'true').strip().lower() != 'false'
)
MAX_EVENT_PAYLOAD_BYTES = int(os.getenv('MNSCR_MAX_EVENT_PAYLOAD_BYTES', 1048576))

# --- Configuração da IA ---
def _is_gemini_api_key_var_name(env_name: str) -> bool:
    """Return True only for env var names that are intended to store Gemini API keys."""
    normalized = env_name.upper()
    return bool(
        normalized.startswith('GEMINI_KEY')
        or normalized.startswith('GEMINI_API_KEY')
        or re.fullmatch(r'GEMINI_\d+', normalized)
    )


def _load_ai_keys() -> List[str]:
    """
    Lê todas as chaves GEMINI_* do ambiente e as retorna em uma lista única e ordenada.
    Procura por padrões: GEMINI_*, GEMINI_KEY*, GEMINI_API*
    """
    keys = {}

    # Procurar por todas as variáveis que contenham GEMINI e sejam chaves de API
    for key, value in os.environ.items():
        if not value or not _is_gemini_api_key_var_name(key):
            continue

        # Validar que é uma chave real (começa com AIza...)
        if str(value).startswith('AIza'):
            keys[key] = value
            logger.info(f"API KEY: {key}")
        else:
            logger.warning(f"VARIAVEL: {key} encontrada mas nao eh chave de API (nao comeca com AIza)")

    if not keys:
        logger.error("ERRO: NENHUMA CHAVE DE API GEMINI ENCONTRADA! Verificar .env")

    # Sort by key name for predictable order
    sorted_key_names = sorted(keys.keys())
    result = [keys[k] for k in sorted_key_names]

    logger.info(f"CARREGADAS {len(result)} chaves de API")
    for idx, key in enumerate(result, 1):
        # Nunca registrar o prefixo da chave: apenas os 4 últimos caracteres.
        logger.info(f"  [{idx}] ****{key[-4:]}")

    return result

AI_API_KEYS = _load_ai_keys()

# Caminho para o prompt universal na raiz do projeto
PROMPT_FILE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    '..',
    'universal_prompt.txt'
)

AI_MODEL = os.getenv('AI_MODEL', 'gemini-3.1-flash-lite')

AI_GENERATION_CONFIG = {
    'temperature': 0.7,
    'top_p': 1.0,
    'max_output_tokens': 8192,
}

AI_POST_WRITER_BUDGET_TOKENS = int(os.getenv('AI_POST_WRITER_BUDGET_TOKENS', '16000'))
AI_POST_WRITER_BUDGET_PER_1K_SOURCE = int(os.getenv('AI_POST_WRITER_BUDGET_PER_1K_SOURCE', '6000'))

# Gates de indexação foram removidos na MS-1: MNScr não indexa e não publica.
# A decisão de indexação pertence ao sistema do Cinerie.

# --- Posts Pilares para Linkagem Interna ---
# URLs internas prioritárias para sugestão de links no corpo do draft.
PILAR_POSTS: List[str] = []

# Categorias sugeridas ao editor humano. São NOMES, nunca IDs de CMS.
# A taxonomia definitiva do Cinerie ainda não foi decidida (bloqueio externo).
EDITORIAL_CATEGORIES: List[str] = ['Notícias', 'Filmes', 'Séries']

# Mapeia o source_id para uma lista de nomes de categorias sugeridas
SOURCE_CATEGORY_MAP: Dict[str, List[str]] = {
    'rssprime_tv': ['Séries'],
    'rssprime_movies': ['Filmes'],
    'screenrant_movie_lists': ['Filmes'],
    'screenrant_movie_news': ['Filmes'],
    'screenrant_tv': ['Séries'],
}


# --- Sinônimos de Categorias ---
# Mapeia nomes alternativos (em minúsculas) para o nome canônico sugerido
CATEGORY_ALIASES: Dict[str, str] = {}

# Editorial blocks: keep games out of ingestion and publishing.
BLOCKED_CATEGORY_NAMES = {
    'games',
    'game',
    'gaming',
    'jogos',
    'videogames',
    'video games',
    'jogos eletronicos',
}
BLOCKED_SOURCE_IDS = {'rssprime_games'}
BLOCKED_TOPICS = {'games'}


# --- Agendador / Pipeline ---
SCHEDULE_CONFIG = {
    'check_interval_minutes': int(os.getenv('CHECK_INTERVAL_MINUTES', 15)),
    'max_articles_per_feed': int(os.getenv('MAX_ARTICLES_PER_FEED', 3)),
    'per_article_delay_seconds': int(os.getenv('PER_ARTICLE_DELAY_SECONDS', 8)),
    'per_feed_delay_seconds': int(os.getenv('PER_FEED_DELAY_SECONDS', 15)),
    'cleanup_after_hours': int(os.getenv('CLEANUP_AFTER_HOURS', 72)),
}

PIPELINE_CONFIG = {
    'attribution_policy': 'Fonte: {domain}',
    'publisher_name': os.getenv('PUBLISHER_NAME', 'Cinerie'),
}

# --- Configuração TMDb (The Movie Database) ---
TMDB_CONFIG = {
    'enabled': os.getenv('TMDB_ENABLED', 'false').lower() == 'true',
    'api_key': os.getenv('TMDB_API_KEY', ''),
    'max_enrichments_per_article': int(os.getenv('TMDB_MAX_ENRICHMENTS', 3)),
    'extract_trending': os.getenv('TMDB_EXTRACT_TRENDING', 'false').lower() == 'true',
    'extract_upcoming': os.getenv('TMDB_EXTRACT_UPCOMING', 'false').lower() == 'true',
}


# --- AI Validator (segunda camada de IA) ---
AI_VALIDATOR_CONFIG = {
    'enabled': os.getenv('AI_VALIDATOR_ENABLED', 'true').lower() == 'true',
    'model': os.getenv('AI_VALIDATOR_MODEL', 'gemini-2.5-flash-lite'),
    'max_retries': int(os.getenv('AI_VALIDATOR_MAX_RETRIES', '1')),
    'fail_open': os.getenv('AI_VALIDATOR_FAIL_OPEN', 'true').lower() == 'true',
}


def get_runtime_config_issues() -> List[str]:
    """
    Return a list of blocking configuration issues for the main pipeline runtime.

    The critical path is now: ingest feeds -> generate an editorial draft ->
    hand it to a submitter. WordPress credentials are no longer required and
    ``MNSCR_OUTPUT_MODE=wordpress`` is rejected outright.
    """
    issues: List[str] = []

    from app.submitters import OutputModeError, resolve_output_mode

    try:
        resolve_output_mode(OUTPUT_MODE)
    except OutputModeError as exc:
        issues.append(str(exc))

    if not AI_API_KEYS:
        issues.append("Nenhuma chave GEMINI válida foi encontrada")

    for source_id in PIPELINE_ORDER:
        feed = RSS_FEEDS.get(source_id)
        if not feed:
            issues.append(f"Feed '{source_id}' está na PIPELINE_ORDER mas não existe em RSS_FEEDS")
            continue
        if not feed.get('urls'):
            issues.append(f"Feed '{source_id}' não possui URLs configuradas")

    return issues


def validate_runtime_config() -> None:
    """Raise a ValueError when a blocking runtime configuration issue is found."""
    issues = get_runtime_config_issues()
    if issues:
        raise ValueError("Configuração inválida:\n- " + "\n- ".join(issues))
