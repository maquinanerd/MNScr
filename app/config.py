import os
import re
import logging
from dotenv import load_dotenv
from typing import Dict, List, Any

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
USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
    'AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/91.0.4472.124 Safari/537.36'
)

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
        logger.info(f"  [{idx}] {key[:15]}...{key[-4:]}")
    
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

INDEX_GATE_ENABLED = os.getenv('INDEX_GATE_ENABLED', 'true').lower() == 'true'
INDEX_GATE_MIN_SCORE = int(os.getenv('INDEX_GATE_MIN_SCORE', '45'))
INDEX_GATE_MIN_INTERNAL_LINKS = int(os.getenv('INDEX_GATE_MIN_INTERNAL_LINKS', '1'))
INDEX_GATE_REQUIRE_WORDCOUNT_MIN = os.getenv('INDEX_GATE_REQUIRE_WORDCOUNT_MIN', 'true').lower() == 'true'

# --- QA-LLM de originalidade ---
# QA_LLM_ENABLED=true   → roda o check para todos os artigos; resultado alimenta gate de indexação
# QA_LLM_ENABLED=false  → comportamento idêntico ao anterior (nenhuma chamada extra)
# QA_LLM_MIN_ORIGINALITY → score mínimo 0-100 para receber index,follow (régua inicial frouxa)
QA_LLM_ENABLED: bool = os.getenv('QA_LLM_ENABLED', 'true').lower() == 'true'
QA_LLM_MIN_ORIGINALITY: int = int(os.getenv('QA_LLM_MIN_ORIGINALITY', '50'))

# --- WordPress ---
WORDPRESS_CONFIG = {
    'url': os.getenv('WORDPRESS_URL'),
    'user': os.getenv('WORDPRESS_USER'),
    'password': os.getenv('WORDPRESS_PASSWORD'),
}

# --- Posts Pilares para Linkagem Interna ---
# Adicione aqui as URLs completas dos seus posts mais importantes.
# A lógica de linkagem interna dará prioridade máxima a links que apontam para estes artigos.
PILAR_POSTS: List[str] = [
    # Ex: "https://seusite.com/guia-completo-de-futebol",
    # Ex: "https://seusite.com/historia-das-copas-do-mundo",
]

# IDs das categorias no WordPress (ajuste os IDs conforme o seu WP)
WORDPRESS_CATEGORIES: Dict[str, int] = {
    'Notícias': 20,
    'Filmes': 24,
    'Séries': 21,  # ID correto de Séries (era 24 antes, mesmo ID de Filmes!)
}

# Mapeia o source_id para uma lista de nomes de categorias
SOURCE_CATEGORY_MAP: Dict[str, List[str]] = {
    'rssprime_tv': ['Séries'],
    'rssprime_movies': ['Filmes'],
    'screenrant_movie_lists': ['Filmes'],
    'screenrant_movie_news': ['Filmes'],
    'screenrant_tv': ['Séries'],
}


# --- Sinônimos de Categorias ---
# Mapeia nomes alternativos (em minúsculas) para o slug canônico em WORDPRESS_CATEGORIES
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
    'images_mode': os.getenv('IMAGES_MODE', 'hotlink'),  # 'hotlink' ou 'download_upload'
    'attribution_policy': 'Fonte: {domain}',
    'publisher_name': os.getenv('PUBLISHER_NAME', 'The Screen / Cinerie'),
    'publisher_logo_url': os.getenv(
        'PUBLISHER_LOGO_URL',
        'https://exemplo.com/logo.png'  # TODO: atualizar para a URL real do logo
    ),
}

# --- Configuração TMDb (The Movie Database) ---
TMDB_CONFIG = {
    'enabled': os.getenv('TMDB_ENABLED', 'false').lower() == 'true',
    'api_key': os.getenv('TMDB_API_KEY', ''),
    'max_enrichments_per_article': int(os.getenv('TMDB_MAX_ENRICHMENTS', 3)),
    'extract_trending': os.getenv('TMDB_EXTRACT_TRENDING', 'false').lower() == 'true',
    'extract_upcoming': os.getenv('TMDB_EXTRACT_UPCOMING', 'false').lower() == 'true',
}


# --- Forçar indexação de todos os posts ---
# Quando True, todos os posts publicados recebem index/follow independente do score editorial.
# Para forçar mesmo abaixo do score, use FORCE_INDEX_ALL_POSTS=true + ALLOW_MANUAL_INDEX_OVERRIDE=true
FORCE_INDEX_ALL_POSTS: bool = True

# --- Policy Engine ---
# Limiar mínimo de score para indexação (padrão 30)
# (não precisam de linha aqui, mas documentamos para referência):
#   PUBLISH_ALL_PROCESSABLE=true → publicar tudo que for processável
#   ALLOW_LOW_SCORE_PUBLISH=true → permite publicar com score baixo
#   ALLOW_MANUAL_INDEX_OVERRIDE=false → proteção extra contra force_index
#   QA_LLM_ENABLED=true             → QA-LLM de originalidade habilitado (resultado alimenta gate de indexação, nunca bloqueia publicação)
#   QA_LLM_MIN_ORIGINALITY=50       → score mínimo 0-100 para index,follow (régua frouxa inicial)

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

    This is intentionally focused on the current production-critical path:
    WordPress publishing and Gemini processing.
    """
    issues: List[str] = []

    if not WORDPRESS_CONFIG.get('url'):
        issues.append("WORDPRESS_URL não configurada")
    if not WORDPRESS_CONFIG.get('user'):
        issues.append("WORDPRESS_USER não configurado")
    if not WORDPRESS_CONFIG.get('password'):
        issues.append("WORDPRESS_PASSWORD não configurada")
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
