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


def _domain_list(raw: str) -> List[str]:
    """Lista de hosts a partir de uma variável separada por vírgula."""
    hosts = []
    for chunk in (raw or '').replace(';', ',').split(','):
        host = chunk.strip().lower().lstrip('.')
        if host.startswith('www.'):
            host = host[4:]
        if host and host not in hosts:
            hosts.append(host)
    return hosts


# Domínios que são NOSSOS. Servem a uma pergunta só: uma URL encontrada no corpo
# do draft aponta para o nosso CMS, ou para o site da fonte?
#
# A distinção existe porque o legado do MNScr e boa parte das fontes que ele lê
# (ScreenRant, ComicBook, MovieWeb) rodam o mesmo CMS. Um caminho
# `/wp-content/uploads/` no corpo é vazamento quando o host é nosso, e é apenas
# a imagem da fonte quando o host é de terceiro. Sem essa lista a guarda mata a
# matéria pela FONTE, que foi o que aconteceu no ciclo de 05/08.
#
# Sem valor embutido: o domínio legado não volta para o código (é exatamente o
# que TestSecurity proíbe). Ele é declarado no .env, e a lista vazia é anunciada
# no startup porque significa guarda desligada.
#
# PUBLISHER_DOMAIN entra automaticamente: se ele é o nosso domínio editorial,
# ele é nosso aqui também.
OWN_CMS_DOMAINS: List[str] = _domain_list(
    (os.getenv('MNSCR_OWN_CMS_DOMAINS') or '') + ',' + PUBLISHER_DOMAIN
)

if not OWN_CMS_DOMAINS:
    logger.warning(
        '[CONFIG] MNSCR_OWN_CMS_DOMAINS vazio: a guarda de vazamento de CMS '
        'proprio nao tem dominio para reconhecer e nao vai bloquear nada.'
    )

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

# --- Editorial Gate (MS-3) ---
# Classificador técnico e versionado do draft. Não aprova e não publica.
# Desabilitar é permitido apenas em desenvolvimento: o gate desabilitado
# registra GATE_DISABLED, que nunca habilita submissão externa.
EDITORIAL_GATE_ENABLED = (
    os.getenv('MNSCR_EDITORIAL_GATE_ENABLED', 'true').strip().lower() != 'false'
)
EDITORIAL_GATE_POLICY = (
    os.getenv('MNSCR_EDITORIAL_GATE_POLICY') or 'mnscr-editorial-gate-v1'
).strip()
EDITORIAL_GATE_POLICY_DIR = (
    os.getenv('MNSCR_EDITORIAL_GATE_POLICY_DIR') or 'config/editorial_gate'
).strip()

# --- Avaliação factual (MS-4) ---
# Claims, evidências, conflitos e cobertura. Não resolve conflito, não publica
# e não verifica em fontes externas: usa apenas o material já recebido.
FACTUAL_ASSESSMENT_ENABLED = (
    os.getenv('MNSCR_FACTUAL_ASSESSMENT_ENABLED', 'true').strip().lower() != 'false'
)
FACTUAL_ASSESSMENT_VERSION = (
    os.getenv('MNSCR_FACTUAL_ASSESSMENT_VERSION') or 'mnscr-factual-assessment-v1'
).strip()
# `deterministic` não chama IA: extrai só os padrões suportados e registra a
# limitação. `hybrid` soma a camada semântica validada.
FACTUAL_ASSESSMENT_MODE = (
    os.getenv('MNSCR_FACTUAL_ASSESSMENT_MODE') or 'hybrid'
).strip().lower()
FACTUAL_PROMPT_VERSION = (
    os.getenv('MNSCR_FACTUAL_PROMPT_VERSION') or 'factual-claim-extraction-v1'
).strip()
MAX_CLAIMS_PER_DRAFT = int(os.getenv('MNSCR_MAX_CLAIMS_PER_DRAFT', 100))
MAX_EVIDENCE_PER_CLAIM = int(os.getenv('MNSCR_MAX_EVIDENCE_PER_CLAIM', 10))
MAX_EVIDENCE_EXCERPT_CHARS = int(os.getenv('MNSCR_MAX_EVIDENCE_EXCERPT_CHARS', 500))
MIN_FACTUAL_COVERAGE_RATIO = float(os.getenv('MNSCR_MIN_FACTUAL_COVERAGE_RATIO', 0.75))


# --- Entrega ao Payload CMS (MS-5) ---
# Desabilitada por padrão. O MNScr cria apenas drafts para revisão humana; não
# existe caminho de publicação. O Payload CMS vive no repositório Screen-App, e
# esta integração ainda não foi validada contra uma instância real.
PAYLOAD_CMS_ENABLED = (
    os.getenv('PAYLOAD_CMS_ENABLED', 'false').strip().lower() == 'true'
)
PAYLOAD_CMS_BASE_URL = (os.getenv('PAYLOAD_CMS_BASE_URL') or '').strip()
PAYLOAD_CMS_DRAFT_ENDPOINT = (os.getenv('PAYLOAD_CMS_DRAFT_ENDPOINT') or '').strip()
PAYLOAD_CMS_TOKEN = (os.getenv('PAYLOAD_CMS_TOKEN') or '').strip()
PAYLOAD_CMS_COLLECTION = (
    os.getenv('PAYLOAD_CMS_COLLECTION') or 'editorial-drafts'
).strip()
PAYLOAD_CMS_TIMEOUT_SECONDS = float(os.getenv('PAYLOAD_CMS_TIMEOUT_SECONDS', 15))
PAYLOAD_CMS_MAX_ATTEMPTS = int(os.getenv('PAYLOAD_CMS_MAX_ATTEMPTS', 3))
PAYLOAD_CMS_RETRY_BASE_SECONDS = float(os.getenv('PAYLOAD_CMS_RETRY_BASE_SECONDS', 2))


def get_delivery_config_issues() -> List[str]:
    """Blocking problems in the Payload CMS delivery settings.

    Só valida quando a entrega está habilitada: um MNScr rodando em modo local
    não deve falhar no startup por causa de um destino que ele não usa.
    """
    issues: List[str] = []
    if not PAYLOAD_CMS_ENABLED:
        return issues

    if not PAYLOAD_CMS_BASE_URL and not PAYLOAD_CMS_DRAFT_ENDPOINT:
        issues.append(
            'PAYLOAD_CMS_ENABLED=true exige PAYLOAD_CMS_BASE_URL ou PAYLOAD_CMS_DRAFT_ENDPOINT'
        )
    for name, value in (
        ('PAYLOAD_CMS_BASE_URL', PAYLOAD_CMS_BASE_URL),
        ('PAYLOAD_CMS_DRAFT_ENDPOINT', PAYLOAD_CMS_DRAFT_ENDPOINT),
    ):
        if value and not value.startswith(('http://', 'https://')):
            issues.append(f'{name} precisa começar com http:// ou https://')

    if not PAYLOAD_CMS_TOKEN:
        issues.append('PAYLOAD_CMS_ENABLED=true exige PAYLOAD_CMS_TOKEN')

    if PAYLOAD_CMS_TIMEOUT_SECONDS <= 0:
        issues.append(
            f'PAYLOAD_CMS_TIMEOUT_SECONDS precisa ser positivo (recebido: {PAYLOAD_CMS_TIMEOUT_SECONDS})'
        )
    if PAYLOAD_CMS_MAX_ATTEMPTS < 1:
        issues.append(
            f'PAYLOAD_CMS_MAX_ATTEMPTS precisa ser >= 1 (recebido: {PAYLOAD_CMS_MAX_ATTEMPTS})'
        )
    if PAYLOAD_CMS_RETRY_BASE_SECONDS < 0:
        issues.append(
            'PAYLOAD_CMS_RETRY_BASE_SECONDS não pode ser negativo '
            f'(recebido: {PAYLOAD_CMS_RETRY_BASE_SECONDS})'
        )
    return issues


# --- Cinerie: publicação governada (editorial-publication-request-v1) -------
#
# Canal SEPARADO do envio de rascunho acima. Dois caminhos, dois escopos, duas
# semânticas: transformar o endpoint de rascunho num endpoint de publicação faria
# um pipeline que só sabia propor passar a publicar sem mudar uma linha.

#: Origem do serviço interno do Cinerie. Apenas a origem — nunca um endpoint,
#: nunca com credencial embutida.
PAYLOAD_INTERNAL_SERVICE_URL = (os.getenv('PAYLOAD_INTERNAL_SERVICE_URL') or '').strip()
MNSCR_PAYLOAD_API_KEY = (os.getenv('MNSCR_PAYLOAD_API_KEY') or '').strip()

#: Autor PÚBLICO que assina a matéria. Não é o ator técnico — esse é derivado da
#: credencial autenticada, do lado do Cinerie, e nunca sai daqui.
#:
#: É o **id numérico** da entidade `Author` no Cinerie (`"12"`), não o slug.
MNSCR_PUBLIC_AUTHOR_ID = (os.getenv('MNSCR_PUBLIC_AUTHOR_ID') or '').strip()
MNSCR_ATTRIBUTION_MODE = (os.getenv('MNSCR_ATTRIBUTION_MODE') or 'newsroom').strip()

#: DRAFT ou AUTO_PUBLISH. Sem default silencioso em produção: ver
#: `app.cinerie_service.resolve_delivery_mode`.
MNSCR_DELIVERY_MODE = (os.getenv('MNSCR_DELIVERY_MODE') or '').strip().upper()

MNSCR_CONTRACT_PREFLIGHT_TTL_SECONDS = float(
    os.getenv('MNSCR_CONTRACT_PREFLIGHT_TTL_SECONDS', 300)
)
MNSCR_CINERIE_TIMEOUT_SECONDS = float(os.getenv('MNSCR_CINERIE_TIMEOUT_SECONDS', 20))
MNSCR_CINERIE_MAX_ATTEMPTS = int(os.getenv('MNSCR_CINERIE_MAX_ATTEMPTS', 3))
MNSCR_CINERIE_RETRY_BASE_SECONDS = float(os.getenv('MNSCR_CINERIE_RETRY_BASE_SECONDS', 2))

#: `development` | `production`. Decide se um modo de entrega ausente bloqueia.
MNSCR_ENVIRONMENT = (os.getenv('MNSCR_ENVIRONMENT') or 'development').strip().lower()


def cinerie_auto_publish_enabled() -> bool:
    return MNSCR_DELIVERY_MODE == 'AUTO_PUBLISH'


def _is_public_author_id(value: str) -> bool:
    """A mesma regra que `app.cinerie.identity` aplica ao montar o pedido.

    Importado aqui dentro, e não no topo: `app.config` é carregado por quase
    todo o repositório, e uma dependência de `app.cinerie` no import faria o
    pacote de publicação subir mesmo em execução que nunca publica.
    """
    from app.cinerie.identity import is_public_author_id

    return is_public_author_id(value)


def get_cinerie_config_issues() -> List[str]:
    """Problemas bloqueantes na configuração de publicação no Cinerie.

    Só valida a fundo quando o modo é `AUTO_PUBLISH`: um MNScr rodando em modo
    local ou de rascunho não precisa de autor público nem de API key.
    """
    issues: List[str] = []

    if MNSCR_DELIVERY_MODE and MNSCR_DELIVERY_MODE not in ('DRAFT', 'AUTO_PUBLISH'):
        issues.append(
            f"MNSCR_DELIVERY_MODE desconhecido: '{MNSCR_DELIVERY_MODE}'. Aceitos: DRAFT, AUTO_PUBLISH"
        )
    if not MNSCR_DELIVERY_MODE and MNSCR_ENVIRONMENT.startswith('prod'):
        # Em produção o default do código decidiria sozinho se a matéria sai
        # publicada ou como rascunho — a decisão mais consequente do arquivo.
        issues.append(
            'MNSCR_DELIVERY_MODE é obrigatório em produção (DRAFT ou AUTO_PUBLISH)'
        )

    if not cinerie_auto_publish_enabled():
        return issues

    if not PAYLOAD_INTERNAL_SERVICE_URL:
        issues.append('MNSCR_DELIVERY_MODE=AUTO_PUBLISH exige PAYLOAD_INTERNAL_SERVICE_URL')
    elif not PAYLOAD_INTERNAL_SERVICE_URL.startswith(('http://', 'https://')):
        issues.append('PAYLOAD_INTERNAL_SERVICE_URL precisa começar com http:// ou https://')
    elif '@' in PAYLOAD_INTERNAL_SERVICE_URL.split('//', 1)[-1].split('/', 1)[0]:
        issues.append('PAYLOAD_INTERNAL_SERVICE_URL não pode conter credencial embutida')

    if not MNSCR_PAYLOAD_API_KEY:
        issues.append('MNSCR_DELIVERY_MODE=AUTO_PUBLISH exige MNSCR_PAYLOAD_API_KEY')
    if not MNSCR_PUBLIC_AUTHOR_ID:
        issues.append(
            'MNSCR_DELIVERY_MODE=AUTO_PUBLISH exige MNSCR_PUBLIC_AUTHOR_ID '
            '(a matéria sai assinada por uma entidade Author autorizada)'
        )
    elif not _is_public_author_id(MNSCR_PUBLIC_AUTHOR_ID):
        # O contrato aceita `stableId` neste campo, mas o runtime do Cinerie
        # testa `/^\d+$/` antes de procurar o autor. Um slug passa no schema e
        # reprova em toda matéria com `author_not_found`. Falhar aqui custa um
        # startup; falhar lá custa o ciclo inteiro, uma matéria por vez.
        issues.append(
            f"MNSCR_PUBLIC_AUTHOR_ID precisa ser o id numérico da entidade Author "
            f"no Cinerie (ex.: '12'), não um slug: '{MNSCR_PUBLIC_AUTHOR_ID[:40]}'"
        )
    if MNSCR_ATTRIBUTION_MODE not in ('byline', 'newsroom', 'assisted'):
        issues.append(
            f"MNSCR_ATTRIBUTION_MODE inválido: '{MNSCR_ATTRIBUTION_MODE}'. "
            'Aceitos: byline, newsroom, assisted'
        )
    if MNSCR_CONTRACT_PREFLIGHT_TTL_SECONDS < 0:
        issues.append('MNSCR_CONTRACT_PREFLIGHT_TTL_SECONDS não pode ser negativo')
    if MNSCR_CINERIE_TIMEOUT_SECONDS <= 0:
        issues.append('MNSCR_CINERIE_TIMEOUT_SECONDS precisa ser positivo')
    if MNSCR_CINERIE_MAX_ATTEMPTS < 1:
        issues.append('MNSCR_CINERIE_MAX_ATTEMPTS precisa ser >= 1')

    return issues


def get_factual_config_issues() -> List[str]:
    """Blocking problems in the factual settings. Sem fallback silencioso."""
    from app.factual.states import ASSESSMENT_MODES, ASSESSMENT_VERSIONS

    issues: List[str] = []
    if FACTUAL_ASSESSMENT_VERSION not in ASSESSMENT_VERSIONS:
        issues.append(
            f"MNSCR_FACTUAL_ASSESSMENT_VERSION desconhecida: '{FACTUAL_ASSESSMENT_VERSION}'. "
            f"Conhecidas: {', '.join(sorted(ASSESSMENT_VERSIONS))}"
        )
    if FACTUAL_ASSESSMENT_MODE not in ASSESSMENT_MODES:
        issues.append(
            f"MNSCR_FACTUAL_ASSESSMENT_MODE desconhecido: '{FACTUAL_ASSESSMENT_MODE}'. "
            f"Aceitos: {', '.join(sorted(ASSESSMENT_MODES))}"
        )
    for name, value in (
        ('MNSCR_MAX_CLAIMS_PER_DRAFT', MAX_CLAIMS_PER_DRAFT),
        ('MNSCR_MAX_EVIDENCE_PER_CLAIM', MAX_EVIDENCE_PER_CLAIM),
        ('MNSCR_MAX_EVIDENCE_EXCERPT_CHARS', MAX_EVIDENCE_EXCERPT_CHARS),
    ):
        if value <= 0:
            issues.append(f"{name} precisa ser positivo (recebido: {value})")
    if not (0.0 <= MIN_FACTUAL_COVERAGE_RATIO <= 1.0):
        issues.append(
            "MNSCR_MIN_FACTUAL_COVERAGE_RATIO precisa estar entre 0 e 1 "
            f"(recebido: {MIN_FACTUAL_COVERAGE_RATIO})"
        )
    return issues

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

    if FACTUAL_ASSESSMENT_ENABLED:
        issues.extend(get_factual_config_issues())

    issues.extend(get_delivery_config_issues())
    issues.extend(get_cinerie_config_issues())

    # Uma política de gate inexistente precisa falhar no startup: usar um
    # fallback silencioso tornaria todo veredito armazenado inexplicável.
    if EDITORIAL_GATE_ENABLED:
        from app.editorial_gate.errors import EditorialGateError
        from app.editorial_gate.policy import available_policies, load_policy

        try:
            load_policy(EDITORIAL_GATE_POLICY, EDITORIAL_GATE_POLICY_DIR)
        except EditorialGateError as exc:
            found = available_policies(EDITORIAL_GATE_POLICY_DIR)
            issues.append(
                f"{exc} Políticas disponíveis: {', '.join(found) if found else 'nenhuma'}"
            )

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
