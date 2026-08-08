# app/pipeline.py
import json
import logging
import os
import re
import threading
import time
import unicodedata
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse, urlsplit

from bs4 import BeautifulSoup

from .ai_processor import AIProcessor
from .ai_validator import expand_article_if_too_short, validate_and_fix_ai_json
from .cleaners import clean_html_for_globo_esporte
from .cluster_extractor import build_cluster_prompt_payload, collect_cluster_images, collect_cluster_videos
from .cluster_feed_adapter import is_superfeed_item, normalize_cluster_item
from .config import (
    BLOCKED_CATEGORY_NAMES,
    BLOCKED_SOURCE_IDS,
    BLOCKED_TOPICS,
    CATEGORY_ALIASES,
    CINERIE_PUBLIC_BASE_URL,
    EDITORIAL_GATE_ENABLED,
    FACTUAL_ASSESSMENT_ENABLED,
    FACTUAL_ASSESSMENT_MODE,
    LOCAL_DRAFT_DIR,
    MNSCR_ATTRIBUTION_MODE,
    MNSCR_CINERIE_CATALOG_RESOLVE_API_KEY,
    MNSCR_CINERIE_CATALOG_RESOLVE_URL,
    MNSCR_CINERIE_ENTITY_CARD_MAX,
    MNSCR_CINERIE_ENTITY_MATCH_KINDS,
    MNSCR_CINERIE_MAX_ATTEMPTS,
    MNSCR_CINERIE_MEDIA_API_KEY,
    MNSCR_CINERIE_MEDIA_UPLOAD_ENABLED,
    MNSCR_CINERIE_RETRY_BASE_SECONDS,
    MNSCR_CINERIE_TIMEOUT_SECONDS,
    MNSCR_CONTRACT_PREFLIGHT_TTL_SECONDS,
    MNSCR_DELIVERY_MODE,
    MNSCR_ENVIRONMENT,
    MNSCR_PAYLOAD_API_KEY,
    MNSCR_PUBLIC_AUTHOR_ID,
    OUTPUT_MODE,
    PAYLOAD_CMS_ENABLED,
    PAYLOAD_INTERNAL_SERVICE_URL,
    PIPELINE_CONFIG,
    PIPELINE_ORDER,
    PUBLISHER_DOMAIN,
    RSS_FEEDS,
    SOURCE_CATEGORY_MAP,
)
from .editorial import DRAFT_FAILED, DRAFT_GENERATED, validate_draft
from .editorial.builder import build_editorial_draft
from .editorial_gate import GATE_BLOCKED, GATE_REVIEW_REQUIRED, disabled_result
from .editorial_validator import validate_editorial_quality
from .entity_validator import validate_and_fix_entities
from .event_store import EventStore
from .extractor import ContentExtractor
from .feeds import FeedReader, canonicalize_url, normalize_title_for_match, titles_are_similar
from .html_utils import (
    append_source_credit_block,
    apply_final_field_casing,
    detect_forbidden_cta,
    downgrade_h1_to_h2,
    final_pre_publish_cleanup,
    hard_filter_forbidden_html,
    merge_videos_into_content,
    normalize_meta_description,
    normalize_subtitle,
    remove_broken_image_placeholders,
    remove_source_domain_schemas,
    sanitize_final_title,
    strip_ai_tag_links,
    strip_credits_and_normalize_youtube,
    strip_forbidden_cta_sentences,
    strip_naked_internal_links,
    unescape_html_content,
    validate_and_fix_figures,
)
from .ingestion import IngestionService
from .internal_linking import add_internal_links
from .link_store import format_for_prompt as ls_format_links
from .link_store import get_link_map as ls_get_link_map
from .link_store import get_related as ls_get_related
from .link_store import save_article as ls_save_article
from .multi_source_builder import build_multi_source_payload
from .policy_engine import (
    ArticleBudget,
    calculate_dynamic_word_policy,
    classify_article_type,
    decide_draft_status,
    should_run_ai_validator,
)
from .seo_title_optimizer import optimize_title
from .store import Database
from .submitters import OUTPUT_MODE_LOCAL, build_submitter
from .superfeed_policy import check_superfeed_policy
from .task_queue import ArticleQueue
from .title_validator import TitleValidator

logger = logging.getLogger(__name__)

# --- Global instances ---
article_queue = ArticleQueue()
ai_processor: Optional[AIProcessor] = None
worker_thread: Optional[threading.Thread] = None
_runtime_lock = threading.Lock()
_worker_pause_requested = threading.Event()
_worker_idle = threading.Event()
_worker_stop_requested = threading.Event()
_worker_idle.set()
_cinerie_dispatch_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cinerie-dispatch")
_cinerie_dispatch_future = None
_cinerie_dispatch_guard = threading.Lock()

# --- Environment variables for pipeline control ---
MAX_ARTICLE_FAIL_COUNT = int(os.getenv('MAX_ARTICLE_FAIL_COUNT', 5))  # Guard: max retries before FAILED_PERMANENT
MAX_ARTICLE_WATCHDOG_TIMEOUTS = int(os.getenv('MAX_ARTICLE_WATCHDOG_TIMEOUTS', MAX_ARTICLE_FAIL_COUNT))
MAX_PER_FEED_CYCLE = int(os.getenv('MAX_PER_FEED_CYCLE', 3))
MAX_PER_CYCLE = int(os.getenv('MAX_PER_CYCLE', 10))
ARTICLE_SLEEP_S = int(os.getenv('ARTICLE_SLEEP_S', 120))  # 2 minutos entre ciclos
BETWEEN_BATCH_DELAY_S = int(os.getenv('BETWEEN_BATCH_DELAY_S', 30))  # 30s entre batches (rÃ¡pido)
BETWEEN_PUBLISH_DELAY_S = int(os.getenv('BETWEEN_PUBLISH_DELAY_S', 30))  # 30s entre publicaÃ§Ãµes
ARTICLE_WATCHDOG_TIMEOUT_S = int(os.getenv('ARTICLE_WATCHDOG_TIMEOUT_S', 300))
CLAIM_STALE_TIMEOUT_S = int(os.getenv('CLAIM_STALE_TIMEOUT_S', ARTICLE_WATCHDOG_TIMEOUT_S * 2))
CINERIE_DISPATCH_LIMIT = int(os.getenv('MNSCR_CINERIE_DISPATCH_LIMIT', 10))
CINERIE_DISPATCH_LEASE_SECONDS = int(os.getenv('MNSCR_CINERIE_DISPATCH_LEASE_SECONDS', 900))

# Exit codes are shared with the CLI entrypoint.  In particular, an unfinished
# backlog is never reported as a successful --once invocation.
EXIT_SUCCESS = 0
EXIT_INVALID_CONFIGURATION = 2
EXIT_CRITICAL_EXCEPTION = 3
EXIT_PROCESSING_FAILURE = 4
EXIT_DEADLINE_EXCEEDED = 5
EXIT_SHUTDOWN_INCOMPLETE = 6

# Versao do prompt canonico, registrada na proveniencia de cada draft.
PROMPT_VERSION = os.getenv('MNSCR_PROMPT_VERSION', 'universal_prompt-ms1')

# Link interno so existe quando ha um site para onde apontar.
#
# Em OUTPUT_MODE=local o draft nao vira pagina: o unico "endereco" que ele tem e
# o caminho do artefato em disco (artifacts/local-drafts/draft-....json). Esse
# valor nao e URL de nada. Gravado no link_store, ele volta como <a href> para um
# arquivo no corpo da materia seguinte; e no dia em que o destino for o Cinerie
# ele cai em seo.internalLinkSuggestions[].targetPath, que exige casar
# ^\/[^\s]*$ dentro de um objeto .strict() — recusa o pedido inteiro, sem strip
# silencioso.
#
# Entao em modo local nao ha link interno nenhum: nem sugestao ao writer, nem
# insercao no corpo, nem escrita no link_store. Link falso e pior que link
# ausente.
INTERNAL_LINKING_ENABLED = OUTPUT_MODE != OUTPUT_MODE_LOCAL

CLEANER_FUNCTIONS = {
    'globo.com': clean_html_for_globo_esporte,
}

# --- 3-phase AI pipeline toggle ---
USE_3PHASE_AI = os.getenv('USE_3PHASE_AI', 'false').lower() == 'true'

_PT_BR_STOPWORDS = {
    "a", "as", "ao", "aos", "e", "o", "os", "um", "uma", "uns", "umas",
    "de", "da", "das", "do", "dos", "em", "no", "nos", "na", "nas",
    "por", "para", "com", "sem", "sobre", "entre", "ate", "apos", "antes",
    "que", "se", "como", "mais", "menos", "novo", "nova", "novos", "novas",
    "noticia", "noticias", "filme", "filmes", "serie", "series", "jogo",
    "jogos", "temporada", "temporadas", "episodio", "episodios", "trailer",
    "estreia", "lancamento", "confirma", "revela", "ganha", "chega",
    "screen", "cinerie",
}


def _normalize_link_text(value: Any) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or "").lower())
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def _tokenize_link_text(value: Any) -> set[str]:
    text = _normalize_link_text(value)
    tokens = re.findall(r"[a-z0-9]+", text)
    return {token for token in tokens if len(token) >= 3 and token not in _PT_BR_STOPWORDS}


def _as_text_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [str(v) for v in value.values() if v]
    if isinstance(value, (list, tuple, set)):
        return [str(v) for v in value if v]
    return [str(value)]


def _looks_like_url(value: Any) -> bool:
    parsed = urlparse(str(value or ""))
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _entry_title_from_keywords(keywords: Any) -> str:
    options = [item.strip() for item in _as_text_list(keywords) if str(item).strip()]
    if not options:
        return ""
    return max(options, key=lambda item: (len(_tokenize_link_text(item)), len(item)))


def _iter_link_map_entries(link_map: Dict[str, Any]):
    if not isinstance(link_map, dict):
        return

    posts = link_map.get("posts")
    if isinstance(posts, list):
        for post in posts:
            if not isinstance(post, dict):
                continue
            url = post.get("url") or post.get("link") or post.get("href")
            keywords = post.get("keywords") or post.get("tags") or []
            title = (
                post.get("titulo")
                or post.get("title")
                or post.get("name")
                or _entry_title_from_keywords(keywords)
            )
            yield str(title or "").strip(), str(url or "").strip(), keywords
        return

    for key, value in link_map.items():
        if _looks_like_url(key):
            title, url = value, key
        elif _looks_like_url(value):
            title, url = key, value
        else:
            continue
        yield str(title or "").strip(), str(url or "").strip(), []


def select_internal_links(
    link_map: Dict[str, Any],
    article_title: str,
    entities: Any,
    tags: Any,
    topic: Any,
    k: int = 6,
    current_url: str = "",
) -> List[Dict[str, str]]:
    """
    Select relevant internal links from the in-memory link_map without forcing weak matches.
    Supports {'posts': [{'link': url, 'keywords': [...]}]}, {url: title}, and {title: url}.
    """
    entity_parts = _as_text_list(entities)
    tag_parts = _as_text_list(tags)
    topic_parts = _as_text_list(topic)
    query_parts = [article_title]
    query_parts.extend(entity_parts)
    query_parts.extend(tag_parts)
    query_parts.extend(topic_parts)
    query_tokens = _tokenize_link_text(" ".join(query_parts))
    if not query_tokens:
        logger.info(
            "[LINKS] writer candidates=0 reason=no_detected_terms title=%r entities=%s tags=%s topic=%s",
            (article_title or "")[:80],
            entity_parts or [],
            tag_parts or [],
            topic_parts or [],
        )
        return []

    current_url_norm = (current_url or "").strip().rstrip("/")
    scored = []
    seen_urls = set()
    for title, url, keywords in _iter_link_map_entries(link_map):
        if not title or not _looks_like_url(url):
            continue
        url_norm = url.strip().rstrip("/")
        if current_url_norm and url_norm == current_url_norm:
            continue
        if url_norm in seen_urls:
            continue

        target_text = " ".join([title] + _as_text_list(keywords))
        target_tokens = _tokenize_link_text(target_text)
        overlap = query_tokens & target_tokens
        score = len(overlap)
        if score <= 0:
            continue

        scored.append((score, len(overlap), title, url))
        seen_urls.add(url_norm)

    scored.sort(key=lambda item: (-item[0], -item[1], item[2].lower()))
    if not scored:
        reason = "entities_without_link_map_match" if entity_parts else "no_entities_or_tags_matched_link_map"
        logger.info(
            "[LINKS] writer candidates=0 reason=%s title=%r entities=%s tags=%s topic=%s",
            reason,
            (article_title or "")[:80],
            entity_parts or [],
            tag_parts or [],
            topic_parts or [],
        )
    return [{"titulo": title, "url": url} for _, _, title, url in scored[:k]]


def _is_terminal_ai_failure(reason: Optional[str]) -> bool:
    normalized = (reason or "").strip().lower()
    if not normalized:
        return False

    terminal_markers = (
        "prompt blocked by model policy",
        "ai blocked prompt",
        "blocked prompt",
        "blocked by model policy",
        "prohibited_content",
        "prompt_feedback",
    )
    return any(marker in normalized for marker in terminal_markers)


def _is_runaway_ai_failure(reason: Optional[str]) -> bool:
    return "truncated_by_token_ceiling" in (reason or "").strip().lower()


def _handle_ai_processing_failure(db: Database, article_id: int, reason: str) -> str:
    if _is_runaway_ai_failure(reason):
        db.update_article_status(
            article_id,
            'FAILED_PERMANENT',
            reason='runaway_token_ceiling',
        )
        return 'FAILED_PERMANENT'
    if _is_terminal_ai_failure(reason):
        db.update_article_status(article_id, 'FAILED', reason=reason)
        return 'FAILED'

    current_fail_count = db.get_article_fail_count(article_id)
    if current_fail_count >= MAX_ARTICLE_FAIL_COUNT:
        db.update_article_status(
            article_id,
            'FAILED_PERMANENT',
            reason=f'Max retries exceeded ({current_fail_count}): {reason}',
        )
        return 'FAILED_PERMANENT'

    db.update_article_status(article_id, 'QUEUED', reason=reason)
    return 'QUEUED'


def _image_candidate_url(candidate: Any) -> str:
    if isinstance(candidate, str):
        return candidate
    if isinstance(candidate, dict):
        return str(candidate.get("url") or candidate.get("src") or candidate.get("href") or "")
    return ""


def _image_candidate_to_html(candidate: Any) -> str:
    if isinstance(candidate, str):
        return candidate
    if not isinstance(candidate, dict):
        return ""
    image_url = _image_candidate_url(candidate)
    if not image_url:
        return ""
    alt = str(candidate.get("alt") or candidate.get("alt_text") or "").strip()
    caption = str(candidate.get("caption") or candidate.get("figcaption") or alt or "Imagem da materia").strip()
    return f'<figure><img src="{image_url}" alt="{alt}"><figcaption>{caption}</figcaption></figure>'


def _images_to_html(images: List[Any]) -> List[str]:
    return [html for image in images or [] if (html := _image_candidate_to_html(image))]


def _plain_text_for_audit(content_html: Any) -> str:
    soup = BeautifulSoup(str(content_html or ""), "html.parser")
    return re.sub(r"\s+", " ", soup.get_text(" ", strip=True)).strip()


def _word_count_for_audit(content_html: Any) -> int:
    text = _plain_text_for_audit(content_html)
    return len(re.findall(r"\b[\wÀ-ÿ'-]+\b", text, flags=re.UNICODE))


def _safe_audit_slug(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text[:90] or "sem-slug"


def _write_cluster_prompt_input_audit(ai_payload: Dict[str, Any], art_data: Dict[str, Any]) -> None:
    if not ai_payload.get("cluster_mode"):
        return

    docs = ai_payload.get("documents") or []
    if not isinstance(docs, list):
        docs = []

    source_count_raw = (
        (art_data.get("cluster_item") or {}).get("source_count")
        or len((art_data.get("cluster_item") or {}).get("all_sources") or [])
        or len((art_data.get("cluster_item") or {}).get("urls") or [])
        or len(docs)
    )
    try:
        source_count = int(source_count_raw or 0)
    except (TypeError, ValueError):
        source_count = len(docs)

    sources = []
    total_words = 0
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        content = doc.get("content", "")
        text = _plain_text_for_audit(content)
        words = _word_count_for_audit(content)
        total_words += words
        sources.append(
            {
                "source": doc.get("source") or doc.get("source_name") or "",
                "url": doc.get("url") or "",
                "title": doc.get("title") or "",
                "words": words,
                "first_200_chars": text[:200],
            }
        )

    prompt_sources_count = len(sources)
    verdict = "MULTI_FONTE_OK" if prompt_sources_count >= 2 else "APENAS_PRINCIPAL"
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    slug = _safe_audit_slug(ai_payload.get("title") or art_data.get("title") or art_data.get("url"))
    payload = {
        "timestamp": timestamp,
        "db_id": art_data.get("db_id"),
        "cluster_event_key": ai_payload.get("event_key") or (art_data.get("cluster_item") or {}).get("event_key"),
        "source_count": source_count,
        "prompt_sources_count": prompt_sources_count,
        "sources": sources,
        "total_source_words_sent_to_writer": total_words,
        "estimated_source_tokens_sent_to_writer": int(total_words * 1.33),
        "veredito": verdict,
    }

    debug_dir = Path("debug")
    debug_dir.mkdir(exist_ok=True)
    output_path = debug_dir / f"prompt_input_{slug}_{timestamp}.json"
    with output_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)

    logger.info(
        "[CLUSTER_INPUT_AUDIT] event_key=%s source_count=%s prompt_sources_count=%s veredito=%s words=%s file=%s",
        payload["cluster_event_key"],
        source_count,
        prompt_sources_count,
        verdict,
        total_words,
        output_path.as_posix(),
    )


def _filter_body_images_against_featured(images: List[Any], featured_image_url: Optional[str]) -> List[Any]:
    if not featured_image_url:
        return images
    featured_key = canonicalize_url(featured_image_url)
    filtered = []
    for image in images or []:
        image_url = _image_candidate_url(image)
        if image_url and canonicalize_url(image_url) == featured_key:
            continue
        filtered.append(image)
    return filtered


def get_ai_processor() -> AIProcessor:
    """Lazily initialize the AI processor to avoid side effects on import."""
    global ai_processor
    if ai_processor is None:
        with _runtime_lock:
            if ai_processor is None:
                ai_processor = AIProcessor()
                logger.info("AI processor initialized lazily.")
    return ai_processor


def ensure_worker_started() -> None:
    """Start the background worker once, only when runtime actually begins."""
    global worker_thread
    if worker_thread and worker_thread.is_alive():
        return
    with _runtime_lock:
        if worker_thread and worker_thread.is_alive():
            return
        _worker_stop_requested.clear()
        worker_thread = threading.Thread(target=worker_loop, daemon=False, name="pipeline-worker")
        worker_thread.start()
        logger.info("Pipeline worker thread started.")


def stop_worker(timeout_s: float = 30) -> bool:
    """Request a coordinated worker shutdown and confirm that it joined."""
    global worker_thread
    _worker_pause_requested.clear()
    _worker_stop_requested.set()
    thread = worker_thread
    if thread is None:
        return True
    thread.join(timeout=max(0.0, timeout_s))
    stopped = not thread.is_alive()
    if stopped:
        worker_thread = None
        _worker_idle.set()
        logger.info("[WORKER_SHUTDOWN] joined=true")
    else:
        logger.error("[WORKER_SHUTDOWN] joined=false timeout_s=%s", timeout_s)
    return stopped


def pause_worker_for_maintenance(timeout_s: int = 300) -> bool:
    """Ask the background worker to become idle before maintenance work."""
    _worker_pause_requested.set()
    if worker_thread and worker_thread.is_alive():
        return _worker_idle.wait(timeout_s)
    return True


def resume_worker_after_maintenance() -> None:
    """Allow the background worker to resume normal processing."""
    _worker_pause_requested.clear()


def pipeline_has_pending_work() -> bool:
    """Return True only when Superfeed work is already queued.

    Fallback backlog must not prevent a fresh Superfeed ingestion pass.
    """
    try:
        queue_counts = article_queue.count_by_origin()
    except Exception as exc:
        logger.warning(f"[CYCLE_GUARD] Nao foi possivel contar a fila: {exc}")
        queue_counts = {}

    pending_superfeed = int(queue_counts.get("superfeed", 0))
    pending_fallback = int(queue_counts.get("fallback", 0))
    if pending_superfeed:
        logger.info(
            "[QUEUE_STATS] pending_superfeed=%s pending_fallback=%s",
            pending_superfeed,
            pending_fallback,
        )
        return True

    if pending_fallback:
        logger.info(
            "[QUEUE_STATS] pending_superfeed=0 pending_fallback=%s action=allow_superfeed_ingestion",
            pending_fallback,
        )
    return False


@dataclass
class OnceRunResult:
    """Auditable outcome of one bounded, synchronous pipeline execution."""

    initial: int = 0
    reconciled: int = 0
    ingested: int = 0
    claimed: int = 0
    completed: int = 0
    editorial_blocked: int = 0
    review_required: int = 0
    technical_failed: int = 0
    remaining: int = 0
    stop_reason: str = "completed"
    duration_ms: int = 0
    success: bool = False
    exit_code: int = EXIT_SUCCESS

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def log_summary(self) -> None:
        logger.info(
            "[ONCE_SUMMARY] initial=%s reconciled=%s ingested=%s claimed=%s completed=%s "
            "editorial_blocked=%s review_required=%s technical_failed=%s remaining=%s "
            "duration_ms=%s stop_reason=%s outcome=%s exit_code=%s",
            self.initial, self.reconciled, self.ingested, self.claimed, self.completed,
            self.editorial_blocked, self.review_required, self.technical_failed,
            self.remaining, self.duration_ms, self.stop_reason,
            "success" if self.success else "incomplete", self.exit_code,
        )


def _once_link_map() -> Dict[str, Any]:
    """Get the same SQLite-backed links that the worker uses, without a thread."""
    try:
        return ls_get_link_map() or {"posts": []}
    except Exception as exc:  # links must not make a durable queue item disappear
        logger.warning("[ONCE_LINKS] mapa de links indisponivel: %s", exc)
        return {"posts": []}


def _process_one_queued_article() -> Optional[Dict[str, Any]]:
    """Claim and process exactly one item using the canonical batch processor.

    This is intentionally shared by ``--once`` and the daemon worker: it does
    not reimplement any editorial, factual or gate rule.
    """
    worker_id = f"once:{os.getpid()}:{threading.get_ident()}:{uuid.uuid4().hex[:12]}"
    article = article_queue.pop_claimed(worker_id)
    if article is None:
        return None

    article_id = int(article.get("db_id") or article.get("id"))
    process_batch([article], _once_link_map())
    db = Database()
    try:
        row = (
            db.get_article_by_event_key(article.get("event_key"), article.get("event_revision"))
            if article.get("event_key")
            else None
        )
        if row is None:
            cursor = db._get_cursor()
            row_data = cursor.execute("SELECT * FROM seen_articles WHERE id = ?", (article_id,)).fetchone()
            row = dict(row_data) if row_data else {}
        return dict(row or {})
    finally:
        db.close()


def _record_once_outcome(result: OnceRunResult, row: Dict[str, Any]) -> None:
    status = str(row.get("status") or "")
    if status == DRAFT_GENERATED:
        result.completed += 1
        gate = row.get("latest_gate_status")
        if gate == GATE_BLOCKED:
            result.editorial_blocked += 1
        elif gate == GATE_REVIEW_REQUIRED:
            result.review_required += 1
    elif status in {DRAFT_FAILED, "FAILED", "FAILED_PERMANENT"}:
        result.technical_failed += 1


def reconcile_event_tasks() -> int:
    """Rebuild durable queue tasks for accepted RSS Prime revisions after a crash."""
    db = Database()
    try:
        tasks = db.reconcile_rssprime_event_tasks()
        if tasks:
            article_queue.push_many(tasks)
            logger.info("[EVENT_RECONCILIATION] created_tasks=%s", len(tasks))
        return len(tasks)
    finally:
        db.close()


def run_pipeline_once(*, deadline_seconds: float, max_items: int) -> OnceRunResult:
    """Run reconciliation, drain, one ingest pass and a final drain synchronously.

    The deadline is monotonic and is only checked between articles.  An active
    article is never abandoned in a daemon thread: finishing it preserves the
    claim and prevents duplicate draft creation.
    """
    if deadline_seconds <= 0 or max_items <= 0:
        raise ValueError("deadline_seconds e max_items devem ser positivos")

    started = time.monotonic()
    result = OnceRunResult(initial=len(article_queue))

    db = Database()
    try:
        recovered = db.recover_stale_processing_claims(
            stale_seconds=CLAIM_STALE_TIMEOUT_S,
            max_recoveries=MAX_ARTICLE_WATCHDOG_TIMEOUTS,
        )
        result.reconciled = sum(int(value) for key, value in recovered.items() if key != "still_alive")
    finally:
        db.close()
    result.reconciled += reconcile_event_tasks()

    def drain() -> bool:
        while len(article_queue):
            if time.monotonic() >= started + deadline_seconds:
                result.stop_reason = "deadline"
                result.exit_code = EXIT_DEADLINE_EXCEEDED
                return False
            if result.claimed >= max_items:
                result.stop_reason = "item_limit"
                result.exit_code = EXIT_DEADLINE_EXCEEDED
                return False
            row = _process_one_queued_article()
            if row is None:
                break
            result.claimed += 1
            _record_once_outcome(result, row)
        return True

    try:
        if not drain():
            return result

        before_ingest = len(article_queue)
        run_pipeline_cycle(start_worker=False, skip_pending_guard=True)
        result.ingested = max(0, len(article_queue) - before_ingest)
        drain()
    except Exception:
        logger.exception("[ONCE_CRITICAL] execucao sincrona interrompida")
        result.stop_reason = "critical_exception"
        result.exit_code = EXIT_CRITICAL_EXCEPTION
    finally:
        result.remaining = len(article_queue)
        result.duration_ms = int((time.monotonic() - started) * 1000)
        if result.exit_code == EXIT_SUCCESS and result.technical_failed:
            result.stop_reason = "processing_failure"
            result.exit_code = EXIT_PROCESSING_FAILURE
        result.success = result.exit_code == EXIT_SUCCESS and result.remaining == 0
        if result.exit_code == EXIT_SUCCESS and result.remaining:
            result.stop_reason = "shutdown_incomplete"
            result.exit_code = EXIT_SHUTDOWN_INCOMPLETE
            result.success = False
        result.log_summary()
    return result


def initialize_runtime() -> None:
    """Initialize runtime services required for the pipeline execution."""
    db = Database()
    try:
        db.initialize()
    finally:
        db.close()
    get_ai_processor()


def _new_topic_coverage() -> Dict[str, Any]:
    return {
        "event_keys": set(),
        "canonical_urls": set(),
        "normalized_titles": [],
        "normalized_title_set": set(),
        "event_key_map": {},
        "canonical_url_map": {},
        "normalized_title_map": {},
    }


def _ensure_topic_coverage(coverage_map: Dict[str, Dict[str, Any]], topic: str) -> Dict[str, Any]:
    if topic not in coverage_map:
        coverage_map[topic] = _new_topic_coverage()
    return coverage_map[topic]


def _register_superfeed_item_coverage(item: Dict[str, Any], coverage_map: Dict[str, Dict[str, Any]]) -> None:
    topic = item.get("topic")
    if not topic:
        return

    bucket = _ensure_topic_coverage(coverage_map, topic)
    summary = {
        "source_id": item.get("source_id"),
        "title": item.get("title", ""),
        "url": item.get("url"),
        "event_key": item.get("event_key"),
        "fonte_nome": item.get("fonte_nome"),
        "source_count": item.get("source_count", 1),
        "multi_source": bool(item.get("multi_source")),
        "origin": item.get("origin", "superfeed"),
    }
    if item.get("event_key"):
        bucket["event_keys"].add(item["event_key"])
        bucket["event_key_map"][item["event_key"]] = summary

    urls = item.get("urls") or [item.get("url")]
    for url in urls:
        canonical = canonicalize_url(url or "")
        if canonical:
            bucket["canonical_urls"].add(canonical)
            bucket["canonical_url_map"][canonical] = summary

    normalized_title = item.get("normalized_title") or normalize_title_for_match(item.get("title", ""))
    if normalized_title and normalized_title not in bucket["normalized_title_set"]:
        bucket["normalized_title_set"].add(normalized_title)
        bucket["normalized_titles"].append(normalized_title)
        bucket["normalized_title_map"][normalized_title] = summary


def _match_superfeed_coverage(
    item: Dict[str, Any],
    coverage_map: Dict[str, Dict[str, Any]],
) -> tuple[Optional[Dict[str, Any]], Optional[str]]:
    topic = item.get("topic")
    if not topic or topic not in coverage_map:
        return None, None

    bucket = coverage_map[topic]
    if item.get("event_key") and item["event_key"] in bucket["event_keys"]:
        return bucket["event_key_map"].get(item["event_key"]), "event_key"

    urls = item.get("urls") or [item.get("url")]
    for url in urls:
        canonical = canonicalize_url(url or "")
        if canonical and canonical in bucket["canonical_urls"]:
            return bucket["canonical_url_map"].get(canonical), "canonical_url"

    normalized_title = item.get("normalized_title") or normalize_title_for_match(item.get("title", ""))
    if normalized_title in bucket["normalized_title_set"]:
        return bucket["normalized_title_map"].get(normalized_title), "normalized_title"

    for existing_title in bucket["normalized_titles"]:
        if titles_are_similar(normalized_title, existing_title):
            return bucket["normalized_title_map"].get(existing_title), "title_similarity"

    return None, None


def _item_is_covered_by_superfeed(item: Dict[str, Any], coverage_map: Dict[str, Dict[str, Any]]) -> bool:
    winner, _ = _match_superfeed_coverage(item, coverage_map)
    return winner is not None


def _log_superfeed_win(
    fallback_item: Dict[str, Any],
    winner: Optional[Dict[str, Any]],
    match_reason: Optional[str],
    context: str,
) -> None:
    if not winner:
        return
    logger.info(
        "[SUPERFEED_WINS] %s | fallback=%s | source=%s | topic=%s | reason=%s | winner=%s | winner_sources=%s",
        context,
        fallback_item.get("title", "N/A")[:80],
        fallback_item.get("source_id", "N/A"),
        fallback_item.get("topic", "N/A"),
        match_reason or "unknown",
        winner.get("title", "N/A")[:80],
        winner.get("fonte_nome") or winner.get("source_id") or "N/A",
    )


def _purge_fallback_coverage(
    db: Database,
    coverage_map: Dict[str, Dict[str, Any]],
    topic: str,
) -> tuple[int, int]:
    """Supersede pending fallback rows and remove covered fallback items from the queue."""
    fallback_items = db.get_pending_fallback_articles(topic)
    if not fallback_items:
        return 0, 0

    covered_ids = []
    removed_from_queue = []
    for item in fallback_items:
        winner, match_reason = _match_superfeed_coverage(item, coverage_map)
        if not winner:
            continue
        covered_ids.append(item["id"])
        _log_superfeed_win(item, winner, match_reason, context="fallback_pending")

    superseded_count = db.mark_articles_superseded(
        covered_ids,
        reason="Fallback coberto por item do superfeed",
    )
    if covered_ids:
        removed_from_queue_count = article_queue.remove_article_ids(covered_ids)
        if removed_from_queue_count:
            for item in fallback_items:
                if item["id"] in covered_ids:
                    removed_from_queue.append(item)
            for item in removed_from_queue:
                winner, match_reason = _match_superfeed_coverage(item, coverage_map)
                _log_superfeed_win(item, winner, match_reason, context="fallback_queue_removed")
        return superseded_count, removed_from_queue_count

    return 0, 0


def _score_length_against_policy(words: int, word_policy: Optional[Dict[str, Any]] = None) -> tuple[int, str]:
    if word_policy:
        min_words = int(word_policy.get("min_acceptable_words") or 0)
        target_words = int(word_policy.get("target_words") or 0)
        if target_words > 0:
            if words >= target_words:
                return 30, f"policy_target_met:{words}/{target_words}"
            if min_words > 0 and words >= min_words:
                ratio = words / max(target_words, 1)
                points = max(25, min(30, round(30 * ratio)))
                return points, f"policy_min_met:{words}/{min_words},target={target_words}"
            ratio = words / max(target_words, 1)
            if ratio >= 0.70:
                return 10, f"near_policy_target:{words}/{target_words}"
            return 0, f"below_policy_min:{words}/{min_words or target_words}"

    if words >= 600:
        return 30, "absolute_600"
    if words >= 400:
        return 15, "absolute_400"
    if words >= 300:
        return 10, "absolute_300"
    return 0, "absolute_below_300"


def assess_content_quality(content_html: str, word_policy: Optional[Dict[str, Any]] = None) -> dict:
    """
    Score multifatorial de qualidade para diagnostico editorial.
    Fatores: palavras, estrutura H2/H3, links internos, bloco editorial.
    Score puramente diagnostico: alimenta warnings do draft, nunca aprova nada.
    """
    soup  = BeautifulSoup(content_html, "html.parser")
    text  = soup.get_text(separator=" ", strip=True)
    words = len(text.split())

    score = 0
    breakdown: Dict[str, Any] = {}
    length_score, length_reason = _score_length_against_policy(words, word_policy)
    score += length_score
    breakdown["length"] = {"points": length_score, "reason": length_reason}

    # 2. Estrutura hierÃ¡rquica (H3 dentro de H2 = profundidade real)
    structure_score = 0
    if soup.find("h3"):
        structure_score += 20
    if soup.find("h2"):
        structure_score += 10
    score += structure_score
    breakdown["structure"] = {
        "points": structure_score,
        "has_h2": bool(soup.find("h2")),
        "has_h3": bool(soup.find("h3")),
    }

    # 3. Links internos (distribuem PageRank, ajudam cluster)
    int_links = [
        a for a in soup.find_all("a", href=True)
        if PUBLISHER_DOMAIN and PUBLISHER_DOMAIN in a["href"].lower()
    ]
    link_score = 0
    if len(int_links) >= 2:
        link_score = 20
    elif len(int_links) >= 1:
        link_score = 10
    score += link_score
    breakdown["internal_links"] = {"points": link_score, "count": len(int_links)}

    reason = (
        f"score={score} | {words}w | length={length_score}({length_reason})"
        f" | structure={structure_score} | links={link_score}/{len(int_links)}"
        f" | h3={'sim' if soup.find('h3') else 'nao'}"
    )
    return {"word_count": words,
            "score": score, "reason": reason, "internal_links": len(int_links),
            "has_h2": bool(soup.find("h2")), "has_h3": bool(soup.find("h3")),
            "score_breakdown": breakdown}


def _get_article_url(article_data: Dict[str, Any]) -> Optional[str]:
    """Get article URL from various possible fields."""
    url = article_data.get("url") or article_data.get("link") or article_data.get("id")
    if not url:
        return None
    try:
        p = urlparse(url)
        if p.scheme in ("http", "https"):
            return url
    except Exception:
        return None
    return None


def _normalize_block_key(value: Any) -> str:
    text = str(value or "").strip().lower()
    normalized = unicodedata.normalize("NFKD", text)
    without_accents = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return re.sub(r"[\s_-]+", " ", without_accents).strip()


def _blocked_category_keys() -> set[str]:
    return {_normalize_block_key(name) for name in BLOCKED_CATEGORY_NAMES}


def _is_blocked_category_name(name: Any) -> bool:
    raw_name = str(name or "").strip()
    if not raw_name:
        return False
    aliased = CATEGORY_ALIASES.get(raw_name.lower(), raw_name)
    return _normalize_block_key(aliased) in _blocked_category_keys()


def _article_is_blocked_for_publication(article_data: Dict[str, Any]) -> tuple[bool, str]:
    source_id = str(article_data.get("source_id") or "").strip()
    feed_config = RSS_FEEDS.get(source_id, {})
    blocked_source_ids = {str(item).strip().lower() for item in BLOCKED_SOURCE_IDS}
    blocked_topics = {_normalize_block_key(item) for item in BLOCKED_TOPICS}

    if source_id.lower() in blocked_source_ids:
        return True, f"Source blocked: {source_id}"

    topic = article_data.get("topic") or feed_config.get("topic")
    if _normalize_block_key(topic) in blocked_topics:
        return True, f"Topic blocked: {topic}"

    category = article_data.get("category") or feed_config.get("category")
    if _is_blocked_category_name(category):
        return True, f"Category blocked: {category}"

    return False, ""


def _preserve_claim_metadata(
    source_article: Dict[str, Any],
    rebuilt_article: Dict[str, Any],
) -> Dict[str, Any]:
    """Carry internal claim metadata across article payload reconstruction."""
    for key, value in source_article.items():
        if key.startswith("_claim_"):
            rebuilt_article[key] = value
    return rebuilt_article


def _first_paragraph_text(content_html: str) -> str:
    soup = BeautifulSoup(content_html or "", "html.parser")
    paragraph = soup.find("p")
    if paragraph:
        return paragraph.get_text(separator=" ", strip=True)
    return soup.get_text(separator=" ", strip=True)

BAD_HOSTS = {"sb.scorecardresearch.com", "securepubads.g.doubleclick.net"}
IMG_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif")

def _run_3phase_batch(
    batch_data: List[Dict[str, Any]],
    processor: "AIProcessor",
) -> List[tuple]:
    """
    Run each article in batch_data through the 3-phase AI pipeline:
      1. ai_sanitize  â€” strip CTAs / junk
      2. ai_rewrite   â€” editorial rewrite
      3. ai_seo_pack  â€” SEO metadata JSON

    Returns a list of (rewritten_data | None, error_msg | None) tuples,
    one per article, matching the format returned by AIProcessor.rewrite_batch.
    """
    from .ai_rewrite import rewrite as _rewrite
    from .ai_sanitize import sanitize as _sanitize
    from .ai_seo_pack import seo_pack as _seo_pack

    client = processor._ai_client
    results = []

    for bdata in batch_data:
        article_title = bdata.get("title", "N/A")
        try:
            html_raw = bdata.get("content_html", "")

            # Phase 1 â€” SanitizaÃ§Ã£o
            logger.info(f"[3PHASE] Phase 1 sanitize: {article_title[:60]}")
            html_clean = _sanitize(html_raw, client)

            # Phase 2 â€” Reescrita editorial
            logger.info(f"[3PHASE] Phase 2 rewrite: {article_title[:60]}")
            html_rewritten = _rewrite(html_clean, {
                "domain": bdata.get("domain", ""),
                "link_block": bdata.get("link_block", ""),
                "videos": bdata.get("videos", []),
            }, client)

            # Phase 3 â€” Empacotamento SEO
            logger.info(f"[3PHASE] Phase 3 seo_pack: {article_title[:60]}")
            seo_data = _seo_pack(html_rewritten, article_title, {
                "domain": bdata.get("domain", ""),
            }, client)

            if seo_data is None:
                results.append((None, "seo_pack returned None"))
            else:
                results.append((seo_data, None))

        except Exception as exc:
            logger.error(f"[3PHASE] Failed for '{article_title}': {exc}", exc_info=True)
            results.append((None, str(exc)))

    return results


def process_batch(articles: List[Dict[str, Any]], link_map: Dict[str, Any]):
    """Process a batch of articles."""
    if not articles:
        return

    processor = get_ai_processor()
    db = Database()
    extractor = ContentExtractor()
    submitter = build_submitter(OUTPUT_MODE, local_dir=LOCAL_DRAFT_DIR)

    try:
        # Extract content for all articles first
        extracted_articles = []
        for article_data in articles:
            article_db_id = article_data['db_id']
            source_id = article_data['source_id']
            feed_config = RSS_FEEDS.get(source_id, {})
            is_blocked, block_reason = _article_is_blocked_for_publication(article_data)
            category = feed_config.get('category', 'NotÃ­cias')

            try:
                if is_blocked:
                    logger.warning(
                        "[CATEGORY_BLOCK] skipped db_id=%s source_id=%s topic=%s reason=%s",
                        article_db_id,
                        source_id,
                        article_data.get("topic") or feed_config.get("topic"),
                        block_reason,
                    )
                    db.update_article_status(article_db_id, 'SKIPPED', reason=block_reason)
                    continue

                article_url = _get_article_url(article_data)
                if not article_url:
                    logger.warning(f"Skipping article {article_data.get('id')} - missing/invalid URL")
                    db.update_article_status(article_db_id, 'FAILED', reason="Missing/invalid URL")
                    continue

                logger.info(f"Processing article: {article_data.get('title', 'N/A')} (DB ID: {article_db_id}) from {source_id}")
                logger.info(f"  Original URL: {article_url}")

                # â”€â”€ MODO CLUSTER (RSSPRIME Superfeed) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
                if is_superfeed_item(article_data):
                    allow_single_source = os.getenv("ALLOW_SINGLE_SOURCE", "false").lower() == "true"
                    policy_status, policy_reason = check_superfeed_policy(
                        article_data,
                        allow_single_source=allow_single_source,
                    )
                    if policy_status != "OK":
                        logger.info(
                            "[SUPERFEED_POLICY] blocked event_key=%s status=%s reason=%s",
                            article_data.get("event_key"),
                            policy_status,
                            policy_reason,
                        )
                        db.update_article_status(
                            article_db_id,
                            'FAILED',
                            reason=f"{policy_status}: {policy_reason}",
                        )
                        continue
                    logger.info(f"[CLUSTER_ITEM] event_key={article_data.get('event_key')} sources={article_data.get('all_sources')}")
                    cluster_item = normalize_cluster_item(article_data)
                    cluster_item['db_id'] = article_db_id
                    cluster_item['urls'] = article_data.get('urls') or [article_url]
                    cluster_item['source_count'] = article_data.get('source_count') or len(cluster_item.get('urls') or [])
                    multi_source_payload = build_multi_source_payload(cluster_item, extractor)
                    if not multi_source_payload:
                        db.update_article_status(
                            article_db_id,
                            'FAILED',
                            reason="Cluster: fontes validas insuficientes",
                        )
                        logger.info(
                            f"[CLUSTER_HEALTH] event_key={cluster_item.get('event_key')} "
                            f"extraction_rate=0/{1 + len(cluster_item.get('additional_urls', []))} "
                            f"(DEGRADED)"
                        )
                        continue
                    cluster_docs = multi_source_payload['cluster_docs']
                    cluster_videos = collect_cluster_videos(cluster_docs)
                    extra_videos = article_data.get("extra_videos") or []
                    if extra_videos:
                        seen_video_ids = {
                            (video.get("id") or video.get("watch_url") or video.get("embed_url"))
                            for video in cluster_videos
                            if isinstance(video, dict)
                        }
                        added_extra_videos = 0
                        for video in extra_videos:
                            if not isinstance(video, dict):
                                continue
                            video_id = video.get("id") or video.get("watch_url") or video.get("embed_url")
                            if not video_id or video_id in seen_video_ids:
                                continue
                            cluster_videos.append(video)
                            seen_video_ids.add(video_id)
                            added_extra_videos += 1
                        logger.info(
                            "[CLUSTER_VIDEOS] extra_videos_added=%s db_id=%s",
                            added_extra_videos,
                            article_db_id,
                        )
                    cluster_images = collect_cluster_images(cluster_docs)
                    featured_image_url = cluster_docs[0].get('featured_image_url')
                    cluster_body_images = _filter_body_images_against_featured(cluster_images, featured_image_url)
                    if not cluster_body_images and cluster_images:
                        cluster_body_images = cluster_images[:1]
                    cluster_body_images_html = _images_to_html(cluster_body_images)
                    total_urls = 1 + len(cluster_item.get("additional_urls", []))
                    extracted_ok = len(cluster_docs)
                    cluster_content_html = "\n\n".join(doc.get("content", "") for doc in cluster_docs)
                    cluster_source_words = len(
                        BeautifulSoup(cluster_content_html, "html.parser").get_text(" ", strip=True).split()
                    )
                    # Determinar o tamanho alvo
                    _cluster_policy = calculate_dynamic_word_policy(
                        source_words=cluster_source_words,
                        article_type="news",
                        origin="superfeed",
                        source_id=source_id,
                        db_id=article_db_id,
                    )
                    cluster_target_words = _cluster_policy['target_words']
                    cluster_policy = _cluster_policy
                    logger.info(
                        f"[CLUSTER_HEALTH] event_key={cluster_item.get('event_key')} "
                        f"extraction_rate={extracted_ok}/{total_urls} "
                        f"({'OK' if extracted_ok >= 2 else 'DEGRADED'})"
                    )
                    logger.info(
                        "[SOURCE_LENGTH] mode=cluster words=%s docs=%s images=%s db_id=%s",
                        cluster_source_words,
                        len(cluster_docs),
                        len(cluster_body_images_html),
                        article_db_id,
                    )
                    logger.info(
                        "[TARGET_LENGTH] mode=cluster min_words=%s db_id=%s",
                        cluster_target_words,
                        article_db_id,
                    )
                    if not cluster_docs:
                        db.update_article_status(article_db_id, 'FAILED', reason="Cluster: nenhum documento extraÃ­do")
                        continue
                    extracted_articles.append(_preserve_claim_metadata(article_data, {
                        'db_id':       article_db_id,
                        'url':         article_url,
                        'source_id':   source_id,
                        'category':    category,
                        'feed_config': feed_config,
                        'title':       article_data.get('title', ''),
                        'is_cluster':  True,
                        'cluster_item': cluster_item,
                        'cluster_docs': cluster_docs,
                        'sources_used': multi_source_payload.get('sources_used', []),
                        'sources_skipped': multi_source_payload.get('sources_skipped', []),
                        'publication_basis': multi_source_payload.get('publication_basis', ''),
                        'source_word_count': cluster_source_words,
                        'target_words': cluster_target_words,
                        'min_acceptable_words': _cluster_policy['min_acceptable_words'],
                        'max_recommended_words': _cluster_policy['max_recommended_words'],
                        'word_policy': cluster_policy,
                        'content_html': cluster_content_html,
                        'extracted':   {
                            'title':             cluster_docs[0]['title'],
                            'content':           cluster_content_html,
                            'images':            cluster_body_images_html,
                            'videos':            cluster_videos,
                            'schema_original':   None,
                            'featured_image_url': featured_image_url,
                        },
                    }))
                    continue
                # â”€â”€ MODO SIMPLES (comportamento atual) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

                # Extract content
                html_content = extractor._fetch_html(article_url)
                if not html_content:
                    db.update_article_status(article_db_id, 'FAILED', reason="Failed to fetch HTML")
                    continue

                # Apply cleaners if needed
                soup = BeautifulSoup(html_content, 'lxml')
                domain = urlparse(article_url).netloc.lower()
                for cleaner_domain, cleaner_func in CLEANER_FUNCTIONS.items():
                    if cleaner_domain in domain:
                        soup = cleaner_func(soup)
                        logger.info(f"Applied cleaner for {cleaner_domain}")
                        break

                # Extract data
                extracted_data = extractor.extract(str(soup), url=article_url)
                if not extracted_data or not extracted_data.get('content'):
                    logger.warning(f"Failed to extract content from {article_url}")
                    db.update_article_status(article_db_id, 'FAILED', reason="Extraction failed")
                    continue

                # Add to batch
                extracted_articles.append(_preserve_claim_metadata(article_data, {
                    'db_id': article_db_id,
                    'url': article_url,
                    'source_id': source_id,
                    'category': category,
                    'extracted': extracted_data,
                    'feed_config': feed_config,
                    'title': article_data.get('title', '')
                }))

            except Exception as e:
                logger.error(f"Error extracting article {article_data.get('title', 'N/A')}: {e}", exc_info=True)
                db.update_article_status(article_db_id, 'FAILED', reason=str(e))

        # Process all extracted articles individually via AI (batch size 1)
        batch_count = 0
        for batch in [extracted_articles[i:i+1] for i in range(0, len(extracted_articles), 1)]:
            # Aguardar entre batches para garantir qualidade SEO
            if batch_count > 0:
                logger.info(f"Aguardando {BETWEEN_BATCH_DELAY_S}s entre batches (garantindo processamento de qualidade)...")
                time.sleep(BETWEEN_BATCH_DELAY_S)

            batch_data = []
            for art in batch:
                # â”€â”€ MODO CLUSTER: payload jÃ¡ montado â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
                if art.get('is_cluster'):
                    ai_payload = build_cluster_prompt_payload(art['cluster_item'], art['cluster_docs'])
                    ai_payload['db_id'] = art.get('db_id')
                    ai_payload['source_word_count'] = art.get('source_word_count', 0)
                    ai_payload['target_words'] = art.get('target_words', 650)
                    ai_payload['word_policy'] = art.get('word_policy', {})
                    ai_payload['images'] = art['extracted'].get('images', [])
                    ai_payload['videos'] = art['extracted'].get('videos', [])
                    ai_payload['domain'] = PUBLISHER_DOMAIN
                    link_candidates = select_internal_links(
                        link_map=link_map,
                        article_title=ai_payload.get('title') or art.get('title', ''),
                        entities=art.get('cluster_item', {}).get('entity', ''),
                        tags=art.get('cluster_item', {}).get('tags', []),
                        topic=art.get('cluster_item', {}).get('topic', art.get('category', '')),
                        k=6,
                        current_url=art.get('url', ''),
                    ) if INTERNAL_LINKING_ENABLED else []
                    ai_payload['internal_link_candidates'] = link_candidates
                    logger.info(
                        "[LINKS] writer candidates=%s db_id=%s",
                        len(link_candidates), art.get('db_id', '?'),
                    )
                    _write_cluster_prompt_input_audit(ai_payload, art)
                    logger.info(f"[CLUSTER_MERGE] {len(art['cluster_docs'])} docs mesclados para IA | event_key={art['cluster_item']['event_key']}")
                    batch_data.append(ai_payload)
                    continue
                # â”€â”€ MODO SIMPLES â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

                extracted = art['extracted']
                main_text = extracted.get('content', '')
                content_for_ai = main_text

                # Calcular tamanho alvo proporcional à fonte
                _source_word_count = len(main_text.split())

                _article_type_pre = classify_article_type(
                    title=extracted.get('title', ''),
                    source_words=_source_word_count,
                    db_id=art.get('db_id', '?')
                )

                _word_policy = calculate_dynamic_word_policy(
                    source_words=_source_word_count,
                    article_type=_article_type_pre,
                    origin=art.get('feed_config', {}).get('origin', 'fallback'),
                    source_id=art.get('source_id', ''),
                    db_id=art.get('db_id', '?'),
                )
                _target_words = _word_policy['target_words']
                _min_acceptable = _word_policy['min_acceptable_words']
                _max_recommended = _word_policy['max_recommended_words']

                logger.info(
                    "[SOURCE_LENGTH] words=%s db_id=%s",
                    _source_word_count, art.get('db_id', '?'),
                )
                logger.info(
                    "[WORD_POLICY] source_words=%s article_type=%s min=%s target=%s max=%s allow_expansion=%s db_id=%s",
                    _source_word_count, _article_type_pre, _min_acceptable, _target_words, _max_recommended, _word_policy['allow_expansion'], art.get('db_id', '?')
                )
                logger.info(
                    "[TARGET_LENGTH] source=policy_engine target_words=%s min_acceptable=%s db_id=%s",
                    _target_words, _min_acceptable, art.get('db_id', '?')
                )
                art['source_word_count'] = _source_word_count
                art['target_words'] = _target_words
                art['word_policy'] = _word_policy

                from .cluster_engine import score_event as _score_event_pre
                _pre_event = _score_event_pre({
                    "title": extracted.get('title', ''),
                    "content": main_text,
                    "tags": [],
                })
                _related = ls_get_related(
                    entity=_pre_event.get("entity", ""),
                    category=art['category'],
                    limit=3,
                ) if INTERNAL_LINKING_ENABLED else []
                _link_block = ls_format_links(_related)
                link_candidates = select_internal_links(
                    link_map=link_map,
                    article_title=extracted.get('title') or art.get('title', ''),
                    entities=[_pre_event.get("entity", "")],
                    tags=extracted.get('tags', []),
                    topic=art.get('feed_config', {}).get('topic') or art.get('category', ''),
                    k=6,
                    current_url=art.get('url', ''),
                ) if INTERNAL_LINKING_ENABLED else []
                logger.info(
                    "[LINKS] writer candidates=%s db_id=%s",
                    len(link_candidates), art.get('db_id', '?'),
                )

                batch_data.append({
                    'db_id': art.get('db_id'),
                    'title': extracted.get('title'),
                    'content_html': content_for_ai,
                    'source_url': art['url'],
                    'category': art['category'],
                    'videos': extracted.get('videos', []),
                    'images': extracted.get('images', []),
                    'source_name': art['feed_config'].get('source_name', ''),
                    'domain': PUBLISHER_DOMAIN,
                    'schema_original': extracted.get('schema_original'),
                    'link_block': _link_block,
                    'source_word_count': _source_word_count,
                    'target_words': _target_words,
                    'word_policy': _word_policy,
                    'min_acceptable_words': _min_acceptable,
                    'max_recommended_words': _max_recommended,
                    'internal_link_candidates': link_candidates,
                })

            try:
                if USE_3PHASE_AI:
                    batch_results = _run_3phase_batch(batch_data, processor)
                else:
                    batch_results = processor.rewrite_batch(batch_data)
                batch_count += 1

                for art_data, (rewritten_data, failure_reason) in zip(batch, batch_results):
                    _article_started_at = time.perf_counter()
                    try:
                        if not rewritten_data:
                            reason = failure_reason or 'AI processing failed'
                            _handle_ai_processing_failure(db, art_data['db_id'], reason)
                            continue

                        # --- CLASSIFICAR TIPO EDITORIAL ---
                        _source_words_for_policy = art_data.get('source_word_count', 0)
                        _origin = art_data.get('feed_config', {}).get('origin', 'fallback')
                        _article_type = classify_article_type(
                            title=art_data.get('title', ''), # Use original title, not rewritten, for proper classification
                            source_words=_source_words_for_policy,
                            db_id=art_data['db_id'],
                        )
                        art_data['article_type'] = _article_type
                        art_data['origin'] = _origin

                        _word_policy = calculate_dynamic_word_policy(
                            source_words=_source_words_for_policy,
                            article_type=_article_type,
                            origin=_origin,
                            db_id=art_data['db_id'],
                        )

                        _budget = ArticleBudget(
                            article_type=_article_type,
                            origin=_origin,
                            db_id=art_data['db_id'],
                            source_words=_source_words_for_policy,
                        )

                        # Main Writer Tokens
                        _main_in = rewritten_data.get('_input_tokens', 0)
                        _main_out = rewritten_data.get('_output_tokens', 0)
                        _main_tot = rewritten_data.get('_total_tokens', 0)

                        _budget.input_tokens = _main_in
                        _budget.output_tokens = _main_out
                        _budget.consume(tokens=_main_tot, stage='main_writer')
                        _output_words = len(rewritten_data.get('conteudo_final', '').split())

                        # --- AI VALIDATOR: condicional ---
                        _validator_decision = should_run_ai_validator(
                            output_words=_output_words, policy=_word_policy, structural_score=None,
                            title=rewritten_data.get('titulo_final', ''),
                            meta=rewritten_data.get('meta_description', ''),
                            content_html=rewritten_data.get('conteudo_final', ''),
                            article_type=_article_type, origin=_origin, db_id=art_data['db_id'],
                        )
                        if _validator_decision['run'] and _budget.has_budget('ai_validator', 3000):
                            logger.info('[GEMINI_CALL] stage=ai_validator db_id=%s reason=%s',
                                art_data['db_id'], _validator_decision['reason'])
                            try:
                                rewritten_data = validate_and_fix_ai_json(
                                    ai_json=rewritten_data, original_article=art_data,
                                    api_client=processor._ai_client if processor else None,
                                )
                                _val_in = rewritten_data.get('_validator_input_tokens', 0)
                                _val_out = rewritten_data.get('_validator_output_tokens', 0)
                                _val_tot = rewritten_data.get('_validator_total_tokens') or (_val_in + _val_out) or 2000
                                _budget.input_tokens += _val_in
                                _budget.output_tokens += _val_out
                                _budget.consume(tokens=_val_tot, stage='ai_validator')
                            except Exception as _ve:
                                logger.warning('[AI_VALIDATOR] exception=%s db_id=%s', _ve, art_data['db_id'])
                        else:
                            try:
                                from .ai_validator import _apply_local_fixes
                                rewritten_data = _apply_local_fixes(rewritten_data, db_id=art_data['db_id'])
                            except Exception:
                                pass

                        # --- EXPANSÃO: condicional ---
                        if _budget.has_budget('expansion', 4000):
                            try:
                                rewritten_data = expand_article_if_too_short(
                                    rewritten_data=rewritten_data, original_article=art_data,
                                    word_policy=_word_policy,
                                    api_client=processor._ai_client if processor else None,
                                )
                                _exp_in = rewritten_data.get('_expansion_input_tokens', 0)
                                _exp_out = rewritten_data.get('_expansion_output_tokens', 0)
                                _exp_tot = rewritten_data.get('_expansion_total_tokens') or (_exp_in + _exp_out) or 3000
                                _budget.input_tokens += _exp_in
                                _budget.output_tokens += _exp_out
                                _budget.consume(tokens=_exp_tot, stage='expansion')
                            except Exception as _ee:
                                logger.warning('[EXPANSION] exception=%s db_id=%s', _ee, art_data['db_id'])

                        # --- ENTITY VALIDATOR: corrige nomes próprios/datas contra a fonte original ---
                        try:
                            source_for_entities = (art_data.get('extracted') or {}).get('content', '')
                            entity_corrections = []
                            for entity_field in (
                                'titulo_final',
                                'conteudo_final',
                                'meta_description',
                                'subtitle',
                            ):
                                if not rewritten_data.get(entity_field):
                                    continue
                                corrected_text, corrections = validate_and_fix_entities(
                                    texto_fonte=source_for_entities,
                                    texto_reescrito=rewritten_data.get(entity_field, ''),
                                )
                                rewritten_data[entity_field] = corrected_text
                                entity_corrections.extend(
                                    {**correction, 'field': entity_field}
                                    for correction in corrections
                                )
                            if entity_corrections:
                                rewritten_data['_entity_corrections'] = entity_corrections
                                logger.info(
                                    '[ENTITY_VALIDATOR] applied=%s db_id=%s',
                                    len(entity_corrections),
                                    art_data['db_id'],
                                )
                        except Exception as _entity_exc:
                            logger.warning(
                                '[ENTITY_VALIDATOR] exception=%s db_id=%s',
                                _entity_exc,
                                art_data['db_id'],
                            )

                        raw_content_html = rewritten_data.get('conteudo_final', '').strip()
                        title = rewritten_data.get('titulo_final', '').strip()
                        if not title or not raw_content_html:
                            db.update_article_status(art_data['db_id'], 'FAILED',
                                reason='AI output missing required fields')
                            continue

                        content_html, _ = strip_forbidden_cta_sentences(raw_content_html)
                        for _phrase in [
                            "Thank you for reading this post, don't forget to subscribe!",
                            "thank you for reading this post, don't forget to subscribe!",
                        ]:
                            content_html = content_html.replace(_phrase, '')
                        if 'thank you for reading' in content_html.lower():
                            db.update_article_status(art_data['db_id'], 'FAILED',
                                reason='CTA persisted after cleaning')
                            continue

                        title_validator = TitleValidator()
                        validation_result = title_validator.validate(title)
                        if validation_result.get('status') == 'AVISO':
                            corrected = title_validator.suggest_correction(title)
                            if corrected != title:
                                title = corrected
                        title, title_optimization_report = optimize_title(title, content_html)
                        _fallback_title = rewritten_data.get('titulo_final', '') or art_data.get('title', '')
                        title, _title_status = sanitize_final_title(title, fallback_title=_fallback_title)
                        if _title_status == 'INVALID':
                            db.update_article_status(art_data['db_id'], 'FAILED',
                                reason='TITULO_INVALIDO_APOS_SANITIZACAO')
                            continue

                        content_html = unescape_html_content(content_html)
                        content_html = validate_and_fix_figures(content_html)
                        extracted = art_data.get('extracted', {})
                        content_html = remove_broken_image_placeholders(content_html)
                        content_html = downgrade_h1_to_h2(content_html)
                        content_html = strip_ai_tag_links(content_html)
                        content_html = strip_naked_internal_links(content_html)
                        content_html = merge_videos_into_content(content_html, extracted.get('videos', []))
                        content_html = validate_and_fix_figures(content_html, ensure_figcaption=False)
                        content_html = strip_credits_and_normalize_youtube(content_html)
                        content_html = remove_source_domain_schemas(content_html)
                        # Sanitizacao defensiva: remove script/style/form/handlers e
                        # iframes nao-YouTube antes de o HTML sair do MNScr.
                        content_html = hard_filter_forbidden_html(content_html)

                        # --- CATEGORIAS: apenas sugestoes (nomes), nunca IDs de CMS ---
                        category_suggestions: List[str] = []

                        def _suggest_category(name: Any) -> None:
                            clean_name = str(name or '').strip()
                            if not clean_name or _is_blocked_category_name(clean_name):
                                return
                            if clean_name not in category_suggestions:
                                category_suggestions.append(clean_name)

                        for _name in SOURCE_CATEGORY_MAP.get(art_data['source_id'], []):
                            _suggest_category(_name)
                        for _suggested in rewritten_data.get('categorias', []) or []:
                            if isinstance(_suggested, dict) and _suggested.get('nome'):
                                _raw_name = str(_suggested['nome'])
                                _suggest_category(CATEGORY_ALIASES.get(_raw_name.lower(), _raw_name))
                        rewritten_data['categorias'] = [
                            {'nome': name} for name in category_suggestions
                        ]

                        if INTERNAL_LINKING_ENABLED and link_map:
                            content_html = add_internal_links(
                                html_content=content_html, link_map_data=link_map,
                                current_post_categories=[])

                        credit_primary_url = art_data.get('url')
                        credit_additional_urls = []
                        if art_data.get('is_cluster'):
                            cluster_item = art_data.get('cluster_item', {})
                            cluster_docs = art_data.get('cluster_docs', [])
                            credit_primary_url = cluster_item.get('url') or credit_primary_url
                            if cluster_docs:
                                credit_primary_url = cluster_docs[0].get('url') or credit_primary_url
                                credit_additional_urls = [d.get('url') for d in cluster_docs[1:] if d.get('url')]
                            else:
                                credit_additional_urls = cluster_item.get('additional_urls', [])
                        content_html = append_source_credit_block(content_html,
                            primary_url=credit_primary_url, additional_urls=credit_additional_urls)

                        # --- QA (diagnostico; warnings nunca viram aprovacao) ---
                        quality = assess_content_quality(content_html, word_policy=_word_policy)
                        logger.info('[QA] %s | %s', title[:50], quality["reason"])
                        logger.info(
                            "[QA_SCORE_BREAKDOWN] db_id=%s breakdown=%s",
                            art_data["db_id"],
                            quality.get("score_breakdown"),
                        )

                        draft_warnings: List[str] = []
                        final_cta_match = detect_forbidden_cta(content_html)
                        if final_cta_match:
                            draft_warnings.append('CTA_RESIDUAL: ' + str(final_cta_match))
                            logger.warning('[CTA_CHECK] CTA residual: %s db_id=%s',
                                final_cta_match, art_data['db_id'])

                        meta_description_final, meta_desc_status = normalize_meta_description(
                            rewritten_data.get('meta_description', ''), title,
                            _first_paragraph_text(content_html),
                            rewritten_data.get('focus_keyphrase') or rewritten_data.get('focus_keyword') or '',
                        )
                        rewritten_data['meta_description'] = meta_description_final
                        db.update_article_meta_description_status(art_data['db_id'], meta_desc_status)
                        logger.info('[META_DESC] status=%s chars=%s', meta_desc_status, len(meta_description_final))

                        subtitle_final, subtitle_status = normalize_subtitle(
                            rewritten_data.get('subtitle', ''), title,
                            _first_paragraph_text(content_html), meta_description_final,
                        )
                        rewritten_data['subtitle'] = subtitle_final
                        db.update_article_subtitle(art_data['db_id'], subtitle_final, subtitle_status)

                        _editorial_payload = {
                            'title': title, 'content_html': content_html,
                            'meta': meta_description_final, 'subtitle': subtitle_final,
                            'sources_skipped': art_data.get('sources_skipped') or [],
                            'subtitle_status': subtitle_status, 'meta_status': meta_desc_status,
                            'article_type': _article_type,
                        }
                        _editorial_result = validate_editorial_quality(_editorial_payload)
                        logger.info('[EDITORIAL_QA] ok=%s status=%s type=%s words=%s issues=%s',
                            _editorial_result['ok'], _editorial_result['status'],
                            _editorial_result['content_type'], _editorial_result['word_count'],
                            _editorial_result['issues'] or 'none')
                        draft_warnings.extend(_editorial_result.get('issues') or [])

                        if not _editorial_result['ok']:
                            db.record_draft_failure(
                                art_data['db_id'],
                                reason='EDITORIAL_QA: ' + '; '.join(_editorial_result['issues']),
                            )
                            continue

                        # --- DECISAO DE DRAFT (nunca publicacao, nunca aprovacao) ---
                        _draft_decision = decide_draft_status(
                            content_html=content_html, title=title,
                            db_id=art_data['db_id'], structural_score=quality['score'],
                        )
                        if not _draft_decision['allowed']:
                            db.record_draft_failure(
                                art_data['db_id'],
                                reason='DRAFT_BLOCKED: ' + str(_draft_decision["reason"]),
                            )
                            continue

                        # --- NORMALIZACAO FINAL DE TEXTO ---
                        content_html = final_pre_publish_cleanup(
                            content_html,
                            title=title,
                            db_id=art_data["db_id"],
                        )
                        title = apply_final_field_casing(title)
                        subtitle_final = apply_final_field_casing(subtitle_final)
                        meta_description_final = apply_final_field_casing(meta_description_final)

                        final_slug = str(rewritten_data.get('slug') or '').strip().lower() or None
                        rewritten_data['slug'] = final_slug
                        logger.info("[SLUG_SUGGESTION] slug=%s db_id=%s", final_slug, art_data["db_id"])

                        # As sugestoes de SEO cruas da IA seguem para a camada
                        # do Cinerie, que as normaliza e reconstroi. Guardadas em
                        # `art_data` para nao alargar a assinatura do draft com um
                        # campo que so a entrega usa.
                        art_data['_seo_source'] = rewritten_data

                        # --- CONSTRUCAO DO DRAFT EDITORIAL ---
                        draft = build_editorial_draft(
                            art_data=art_data,
                            rewritten=rewritten_data,
                            body_html=content_html,
                            title=title,
                            subtitle=subtitle_final,
                            meta_description=meta_description_final,
                            warnings=draft_warnings,
                            model_provider='google',
                            model_name=(
                                processor._ai_client.get_last_used_model()
                                if processor and processor._ai_client else None
                            ),
                            prompt_version=PROMPT_VERSION,
                            duration_ms=int((time.perf_counter() - _article_started_at) * 1000),
                            input_event=_resolve_input_event(art_data),
                        )
                        draft.seo_source = _persistent_cinerie_seo_source(
                            art_data.get('_seo_source')
                        )

                        blocking_errors = validate_draft(draft)
                        if blocking_errors:
                            draft.blocking_errors = blocking_errors
                            draft.status = DRAFT_FAILED
                            logger.error(
                                '[DRAFT_INVALID] db_id=%s draft_id=%s errors=%s',
                                art_data['db_id'], draft.draft_id, blocking_errors,
                            )
                            db.record_draft_failure(
                                art_data['db_id'],
                                reason='DRAFT_VALIDATION: ' + '; '.join(blocking_errors),
                                draft_id=draft.draft_id,
                                draft_output_hash=draft.provenance.output_hash,
                                draft_revision=draft.revision,
                            )
                            continue

                        _budget.report(
                            draft_id=draft.draft_id,
                            draft_generated=True,
                            score=quality['score'],
                        )

                        # --- AVALIACAO FACTUAL (antes do gate) ---
                        factual_assessment = _run_factual_assessment(draft, art_data)
                        draft.factual_assessment = factual_assessment

                        # --- EDITORIAL GATE (classifica; nao aprova) ---
                        gate_result = _run_editorial_gate(
                            draft,
                            db=db,
                            article_db_id=art_data['db_id'],
                            input_event=_resolve_input_event(art_data),
                            factual_assessment=factual_assessment,
                        )
                        draft.editorial_gate = gate_result

                        # --- SUBMISSAO (nunca publicacao) ---
                        claim_owner = art_data.get("_claim_owner")
                        if claim_owner and not db.article_claim_is_current(art_data["db_id"], claim_owner):
                            logger.warning(
                                "[CLAIM_LOST] artigo %s nao pertence mais a este worker; abortando antes da submissao",
                                art_data["db_id"],
                            )
                            continue

                        result = submitter.submit(draft)
                        logger.info(
                            '[DRAFT_SUBMIT] draft_id=%s destino=%s status=%s ok=%s path=%s',
                            draft.draft_id, result.destination, result.status,
                            result.success, result.artifact_path,
                        )

                        if not result.success:
                            db.record_draft_failure(
                                art_data['db_id'],
                                reason='SUBMISSION_' + str(result.status) + ': ' + str(result.error or ''),
                                draft_id=draft.draft_id,
                                draft_output_hash=draft.provenance.output_hash,
                                draft_revision=draft.revision,
                                submission_destination=result.destination,
                                submission_status=result.status,
                                submission_error=result.error,
                                artifact_path=result.artifact_path,
                            )
                            continue

                        recorded = db.record_draft_generated(
                            art_data['db_id'],
                            draft_id=draft.draft_id,
                            draft_output_hash=draft.provenance.output_hash,
                            draft_revision=draft.revision,
                            artifact_path=result.artifact_path,
                            submission_destination=result.destination,
                            submission_status=result.status,
                            claimed_by=claim_owner,
                        )
                        if not recorded:
                            logger.error(
                                "[CLAIM_LOST] artigo %s perdeu o claim antes de registrar o draft %s",
                                art_data["db_id"], draft.draft_id,
                            )
                            continue

                        logger.info("DRAFT GERADO: %s | %s", draft.draft_id, title[:70])

                        # --- ENTREGA AO CMS (somente draft, apos o gate) ---
                        _deliver_draft(draft, art_data)

                        # --- PUBLICACAO GOVERNADA NO CINERIE (AUTO_PUBLISH) ---
                        # Canal separado do envio de rascunho: outro contrato,
                        # outro endpoint, outro escopo.
                        publicacao = _publish_to_cinerie(draft, art_data)

                        # Link store: alimenta sugestoes de links internos futuros.
                        #
                        # O unico endereco publico desta materia e o que o
                        # Cinerie devolveu na publicacao (`canonicalSlug`).
                        # `artifact_path` e um caminho de arquivo e `draft_id`
                        # nao e endereco de nada — gravar qualquer um dos dois
                        # transformaria a proxima materia em portadora de um
                        # link falso, e a barreira do link_store os recusaria de
                        # qualquer forma, deixando o link interno morto com um
                        # WARNING por artigo como unico sinal.
                        slug_publicado = getattr(
                            getattr(publicacao, "record", None), "canonical_slug", None
                        )
                        url_publica = _public_url_for_slug(
                            slug_publicado, base=CINERIE_PUBLIC_BASE_URL
                        )
                        if INTERNAL_LINKING_ENABLED and url_publica:
                            from .cluster_engine import score_event
                            event = score_event({
                                "title":   title,
                                "content": content_html,
                                "tags":    rewritten_data.get("tags_sugeridas", []),
                            })
                            ls_save_article(
                                title=title,
                                url=url_publica,
                                category=art_data['category'],
                                entity=event.get("entity", ""),
                            )
                        elif INTERNAL_LINKING_ENABLED:
                            logger.info(
                                "[LINKS] link_store ignorado draft_id=%s motivo=%s: "
                                "sem endereco publico para esta materia",
                                draft.draft_id,
                                "sem_canonical_slug" if not slug_publicado else "base_publica_ausente",
                            )
                        else:
                            logger.info(
                                "[LINKS] link_store ignorado draft_id=%s motivo=output_mode=%s "
                                "sem endereco publico para o draft",
                                draft.draft_id, OUTPUT_MODE,
                            )

                        if art_data.get('is_cluster'):
                            event_key = (art_data.get('cluster_item') or {}).get('event_key', '')
                            cluster_urls = (art_data.get('cluster_item') or {}).get('urls') or [art_data.get('url')]
                            db.register_covered_urls(event_key, cluster_urls, art_data['db_id'])
                            logger.info(
                                "[SUPERFEED_COVERAGE] URLs registradas apos draft "
                                "event_key=%s seen_article_id=%s draft_id=%s",
                                event_key, art_data['db_id'], draft.draft_id,
                            )

                        logger.info("Aguardando %ss para o proximo artigo...", BETWEEN_PUBLISH_DELAY_S)
                        time.sleep(BETWEEN_PUBLISH_DELAY_S)

                    except Exception as e:
                        logger.error(f"Error processing article result {art_data['title']}: {e}", exc_info=True)
                        db.update_article_status(art_data['db_id'], 'FAILED', reason=str(e))

            except Exception as e:
                logger.error(f"Error processing batch: {e}", exc_info=True)
                # Se o lote falhar (ex: JSON malformado da IA), tente processar individualmente
                for art in batch:
                    db.record_draft_failure(
                        art['db_id'],
                        reason='BATCH_FAILURE: ' + str(e),
                    )

    finally:
        db.close()


def worker_loop():
    """Continuously process articles from the queue in batches.

    Respects:
    - Max 10 AI requests per cycle (to avoid RPM violations)
    - 5-minute pause after hitting request limit
    """
    # Carregar link_map estÃ¡tico (fallback) + dados dinÃ¢micos do SQLite
    static_link_map = {}
    try:
        with open('data/internal_links.json', 'r', encoding='utf-8') as f:
            static_link_map = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        pass  # JSON vazio Ã© esperado; DB Ã© a fonte primÃ¡ria

    def _refresh_link_map() -> dict:
        """Monta link_map fresco: SQLite (primÃ¡rio) + JSON estÃ¡tico (fallback)."""
        db_map = ls_get_link_map()
        json_posts = static_link_map.get('posts', [])
        db_urls = {p['link'] for p in db_map['posts']}
        extra = [p for p in json_posts if p['link'] not in db_urls]
        all_posts = db_map['posts'] + extra
        logger.info(f"[LINKS] link_map atualizado: {len(db_map['posts'])} do DB + {len(extra)} do JSON = {len(all_posts)} total")
        return {'posts': all_posts}

    link_map = _refresh_link_map()
    worker_id = f"{os.getpid()}:{threading.get_ident()}:{uuid.uuid4().hex[:12]}"
    requests_in_cycle = 0
    MAX_REQUESTS_PER_CYCLE = 10
    PAUSE_ON_LIMIT_S = 300  # 5 minutos
    last_pause_time = time.time()
    QUEUE_TIMEOUT_S = 30  # 30 segundos timeout para esperar artigo

    while not _worker_stop_requested.is_set():
        if _worker_pause_requested.is_set():
            _worker_idle.set()
            _worker_stop_requested.wait(1)
            continue

        _worker_idle.set()
        # Check request limit before claiming an article, so a pause does not
        # leave a freshly claimed row idle in PROCESSING.
        if requests_in_cycle >= MAX_REQUESTS_PER_CYCLE:
            logger.warning(
                f"[RPM PROTECTION] Atingido limite de {MAX_REQUESTS_PER_CYCLE} requisiÃƒÂ§ÃƒÂµes por ciclo. "
                f"Pausando pipeline por {PAUSE_ON_LIMIT_S}s (5 minutos)."
            )
            last_pause_time = time.time()
            _worker_stop_requested.wait(PAUSE_ON_LIMIT_S)
            if _worker_stop_requested.is_set():
                break
            requests_in_cycle = 0
            logger.info("[RPM PROTECTION] Resumindo pipeline apÃƒÂ³s pausa de 5 minutos.")
            continue

        # Get articles from queue (batch size 1)
        articles = []
        start_wait = time.time()

        # Wait up to QUEUE_TIMEOUT_S for an article to appear in queue
        while time.time() - start_wait < QUEUE_TIMEOUT_S:
            if _worker_pause_requested.is_set():
                break
            claim_owner = f"{worker_id}:{uuid.uuid4().hex[:12]}"
            if article := article_queue.pop_claimed(claim_owner):
                _worker_idle.clear()
                logger.info(
                    "[TOKEN_TRACE] stage=worker_start db_id=%s token_in_payload=%r",
                    article.get("db_id") or article.get("id"),
                    article.get("_claim_owner"),
                )
                logger.info(
                    "[QUEUE_POP] origin=%s | source=%s | topic=%s | title=%s",
                    article.get("origin", "unknown"),
                    article.get("source_id", "unknown"),
                    article.get("topic", "unknown"),
                    (article.get("title") or "N/A")[:80],
                )
                articles.append(article)
                break
            else:
                # Esperar um pouco antes de tentar novamente
                _worker_stop_requested.wait(1)

        if not articles:
            # Still no articles after timeout, reset cycle counter and continue
            logger.debug("[WORKER] Nenhum artigo na fila apÃ³s timeout de 30s")

            # Reset cycle counter after quiet period (2 minutos sem processar)
            if time.time() - last_pause_time > 120:
                logger.info("[RPM PROTECTION] PerÃ­odo de inatividade detectado. Resetando contador de requisiÃ§Ãµes.")
                requests_in_cycle = 0
                last_pause_time = time.time()

            _worker_stop_requested.wait(5)  # Esperar 5s antes de tentar novamente
            continue
        # Refresh link_map before processing to pick up articles published this session
        link_map = _refresh_link_map()

        # Process batch and count requests
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="article-watchdog")
        future = executor.submit(process_batch, articles, link_map)
        try:
            future.result(timeout=ARTICLE_WATCHDOG_TIMEOUT_S)
        except TimeoutError:
            article_id = articles[0].get("db_id") or articles[0].get("id") or "unknown"
            logger.error(
                "[WATCHDOG] artigo %s estourou %ss — abortando",
                article_id,
                ARTICLE_WATCHDOG_TIMEOUT_S,
            )
            _handle_watchdog_timeout(articles[0])
            executor.shutdown(wait=False, cancel_futures=True)
            _worker_idle.set()
            continue
        finally:
            if future.done():
                executor.shutdown(wait=True)
        requests_in_cycle += len(articles)
        last_pause_time = time.time()  # Atualizar timestamp de Ãºltima atividade
        _worker_idle.set()

        logger.info(
            f"Worker: {len(articles)} artigos processados. "
            f"Total requisiÃ§Ãµes neste ciclo: {requests_in_cycle}/{MAX_REQUESTS_PER_CYCLE}. "
            f"Dormindo por {ARTICLE_SLEEP_S}s."
        )
        _worker_stop_requested.wait(ARTICLE_SLEEP_S)

    _worker_idle.set()
    logger.info("[WORKER_SHUTDOWN] loop_exited=true")


def _handle_watchdog_timeout(article: Dict[str, Any]) -> bool:
    """Keep ownership while the timed-out Python thread may still be running.

    ThreadPoolExecutor cannot kill a running thread. Releasing its claim here
    would let another worker publish the same article while the original
    thread can still be writing the draft artifact and the database.
    """
    article_id = article.get("db_id") or article.get("id")
    if article_id is None:
        count = int(article.get("_watchdog_timeout_count") or 0) + 1
        article["_watchdog_timeout_count"] = count
        if count >= MAX_ARTICLE_WATCHDOG_TIMEOUTS:
            logger.error(
                "[WATCHDOG] artigo sem db_id descartado apos %s timeout(s)",
                count,
            )
        return False

    db = Database()
    try:
        status = db.get_article_status(int(article_id))
        if status in (DRAFT_GENERATED, DRAFT_FAILED):
            logger.warning(
                "[WATCHDOG] artigo %s ja possui draft registrado; nao re-enfileirando",
                article_id,
            )
            return False
        if status != "PROCESSING":
            logger.warning(
                "[WATCHDOG] artigo %s esta em status %s; claim nao sera alterado",
                article_id,
                status,
            )
            return False

        logger.warning(
            "[WATCHDOG] artigo %s manteve claim PROCESSING; thread ainda pode estar ativa "
            "e somente recovery com pid dono morto pode liberar a posse",
            article_id,
        )
        return False
    finally:
        db.close()

def _deliver_draft(draft, art_data):
    """Hand an approved draft to the external CMS, if delivery is enabled.

    Runs last on purpose: the artifact is already written and the draft already
    recorded, so a delivery failure never costs the local copy. The service
    refuses blocked drafts before opening a socket, so this call is safe even
    when the gate said no.
    """
    from .delivery_service import DeliveryService

    if not PAYLOAD_CMS_ENABLED:
        logger.info(
            "[CONFIG_FEATURE_SKIPPED] feature=draft_delivery "
            "variable=PAYLOAD_CMS_ENABLED state=disabled"
        )
        return None

    service = None
    try:
        service = DeliveryService()
        outcome = service.deliver_draft(draft, topic=art_data.get("topic"))
        logger.info(
            "[DRAFT_DELIVERY] draft_id=%s status=%s remote_draft_id=%s tentativas=%s",
            draft.draft_id, outcome.result.status,
            outcome.result.remote_draft_id or "-", outcome.result.attempts,
        )
        return outcome
    except Exception as exc:  # noqa: BLE001 - o draft local nunca pode ser perdido
        logger.exception(
            "[DRAFT_DELIVERY] draft_id=%s falha inesperada na entrega (%s); "
            "o draft local permanece intacto",
            draft.draft_id, type(exc).__name__,
        )
        return None
    finally:
        if service is not None:
            service.close()


def _public_url_for_slug(slug, *, base):
    """Endereço público a partir do ``canonicalSlug`` devolvido pela publicação.

    O link_store existe para virar ``<a href>`` no corpo da próxima matéria,
    então o que entra ali precisa ser endereço de verdade. O pipeline oferecia
    ``result.artifact_path or draft.draft_id`` — um caminho em disco e um
    identificador —, a barreira do link_store recusava os dois, e o link interno
    simplesmente nunca existia, com um WARNING por artigo como único sinal.

    Faltando slug ou base, devolve ``None`` em vez de chutar. Endereço errado é
    pior que endereço nenhum: ele vira link quebrado publicado na matéria
    seguinte.
    """
    slug_text = str(slug or "").strip().strip("/")
    base_text = str(base or "").strip()
    if not slug_text:
        return None
    if not base_text:
        logger.info(
            "[CONFIG_FEATURE_SKIPPED] feature=internal_link_skipped "
            "variable=CINERIE_PUBLIC_BASE_URL state=empty"
        )
        return None

    parsed = urlsplit(base_text)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        # Base mal configurada não vira endereço: o link_store recusaria de
        # qualquer forma, e recusar aqui deixa o motivo mais perto da causa.
        logger.warning(
            "[CONFIG_FEATURE_SKIPPED] feature=internal_link_skipped "
            "variable=CINERIE_PUBLIC_BASE_URL state=invalid",
        )
        return None

    return f"{base_text.rstrip('/')}/{slug_text}"


def _persistent_cinerie_seo_source(source: Any) -> Dict[str, Any]:
    """Persiste só a entrada SEO que o contrato consome, nunca o JSON cru da IA."""
    from .cinerie.seo import migrate_legacy_seo

    if not isinstance(source, dict):
        return {}
    persisted = migrate_legacy_seo(source)
    schema_type = source.get("schemaTypeRecommendation")
    if isinstance(schema_type, str) and schema_type.strip():
        persisted["schemaTypeRecommendation"] = schema_type.strip()
    alt_texts = source.get("image_alt_texts")
    if isinstance(alt_texts, (dict, list)):
        persisted["image_alt_texts"] = alt_texts
    return persisted


def _build_entity_resolver():
    """O resolvedor de entidade, ou ``None`` quando o recurso nao esta configurado.

    ``None`` NAO e erro, e essa e a regra: a resolucao e enriquecimento, e a
    materia sai sem ficha do mesmo jeito que sai sem foto. Uma variavel ausente
    vira WARNING no log e segue; virar `issue` de startup faria uma credencial
    que ninguem pediu impedir a publicacao de tudo.

    A credencial e NOVA e separada das duas do CMS (`draft_ingest` e
    `editorial_media_ingest`): a rota vive no `screen-app`, e reaproveitar uma
    chave do CMS faria um unico vazamento abrir os dois lados da fronteira.
    """
    from .config import cinerie_entity_resolve_enabled

    if not cinerie_entity_resolve_enabled():
        faltando = [
            nome
            for nome, valor in (
                ("MNSCR_CINERIE_CATALOG_RESOLVE_URL", MNSCR_CINERIE_CATALOG_RESOLVE_URL),
                (
                    "MNSCR_CINERIE_CATALOG_RESOLVE_API_KEY",
                    MNSCR_CINERIE_CATALOG_RESOLVE_API_KEY,
                ),
            )
            if not valor
        ]
        logger.warning(
            "[CINERIE_ENTITY] resolucao de entidade DESLIGADA (ausente: %s); "
            "as materias saem sem entityCard",
            ", ".join(faltando),
        )
        return None

    try:
        from .cinerie.client import CatalogResolveClient, CatalogResolveConfig

        cliente = CatalogResolveClient(
            CatalogResolveConfig(
                base_url=MNSCR_CINERIE_CATALOG_RESOLVE_URL,
                api_key=MNSCR_CINERIE_CATALOG_RESOLVE_API_KEY,
                timeout_seconds=MNSCR_CINERIE_TIMEOUT_SECONDS,
            )
        )
    except Exception as exc:  # noqa: BLE001 - ficha nunca custa a materia
        logger.warning(
            "[CINERIE_ENTITY] resolvedor nao pode ser construido (%s): %s; "
            "as materias saem sem entityCard",
            type(exc).__name__, exc,
        )
        return None

    logger.info(
        "[CINERIE_ENTITY] resolucao LIGADA base=%s max_cards=%s casamentos_aceitos=%s",
        MNSCR_CINERIE_CATALOG_RESOLVE_URL,
        MNSCR_CINERIE_ENTITY_CARD_MAX,
        ",".join(MNSCR_CINERIE_ENTITY_MATCH_KINDS),
    )
    return cliente.resolve


def _ingest_hero_media_best_effort(draft, result, client) -> None:
    """Os tres passos da imagem de destaque. Nenhum deles pode falhar a materia.

        1. a materia ja foi publicada             -> article_id   (quem chama)
        2. a foto entra no acervo                 -> mediaId
        3. a foto vira a CAPA daquela materia     -> PATCH .../hero

    Os tres sao idempotentes e sao repetidos na integra a cada revisao: o passo
    2 devolve ``unchanged`` quando os bytes nao mudaram, e o passo 3 devolve
    ``unchanged`` quando a capa ja e aquela — sem escrever, sem criar versao
    nova. Quando a fonte troca a foto no mesmo endereco, os dois devolvem
    ``replaced`` e a capa acompanha.

    O passo 3 fechou um no que era de ORDEM, nao de permissao: a foto so pode
    ser ingerida depois de a materia existir, e a materia so poderia declarar
    ``media[]`` antes de a foto existir. A rota nova nao le nem escreve
    ``sourceRevision`` e nao passa por ``staleRevision``, entao ela nao mexe na
    reconciliacao de eventos — que era exatamente o efeito colateral que
    impedia fechar isto pela resubmissao.

    ``client`` e o MESMO ``CinerieClient`` ja usado para publicar o texto — so
    a credencial de midia e separada, passada por ``api_key`` na propria
    chamada, e o passo 3 usa a MESMA do passo 2 (escopo
    ``editorial_media_ingest``, nenhuma variavel nova).

    NADA ESCAPA DAQUI, e a razao e a POSICAO da chamada, nao a educacao do
    codigo chamado. Quem chama esta funcao e ``_publish_to_cinerie``, DENTRO do
    mesmo ``try`` que trata falha de publicacao — e o ``except`` de la devolve
    ``None``. Uma excecao que vazasse desta funcao seria lida como "a
    publicacao falhou" DEPOIS de o Cinerie ja ter aceitado a materia: o
    ``article_id`` real seria descartado, o `CinerieStore` nao registraria o
    desfecho e a materia publicada viraria uma falha no relatorio. Os modulos
    chamados ja engolem tudo; os ``except`` daqui existem para que a garantia
    nao dependa de eles continuarem se comportando — e o passo 3 ganhou o SEU
    proprio, pelo mesmo motivo que o passo 2 tem o dele.
    """
    if not MNSCR_CINERIE_MEDIA_UPLOAD_ENABLED:
        return
    article_id = getattr(result, "article_id", None)
    if not article_id:
        return
    if not MNSCR_CINERIE_MEDIA_API_KEY:
        logger.warning(
            "[CINERIE_MEDIA] MNSCR_CINERIE_MEDIA_UPLOAD_ENABLED=true mas "
            "MNSCR_CINERIE_MEDIA_API_KEY ausente; publicando sem hero"
        )
        return

    try:
        from .cinerie.media_selection import ingest_hero_image
        from .cinerie.request import primary_source_display_name

        media_result = ingest_hero_image(
            article_id=str(article_id),
            image_candidates=getattr(draft, "media_candidates", None) or [],
            source_name=primary_source_display_name(draft) or "",
            client=client,
            media_api_key=MNSCR_CINERIE_MEDIA_API_KEY,
            alt_fallback=getattr(getattr(draft, "content", None), "title", None),
        )
    except Exception as exc:  # noqa: BLE001 - imagem NUNCA custa a materia publicada
        logger.warning(
            "[CINERIE_MEDIA] ingestao levantou %s para article_id=%s: %s; "
            "a materia publicada segue valida, sem hero",
            type(exc).__name__, article_id, exc,
        )
        return

    if media_result is None:
        return

    try:
        from .cinerie.media_selection import point_hero_media

        hero = point_hero_media(
            article_id=str(article_id),
            media_id=media_result.media_id,
            client=client,
            media_api_key=MNSCR_CINERIE_MEDIA_API_KEY,
        )
    except Exception as exc:  # noqa: BLE001 - a capa NUNCA custa a materia publicada
        logger.warning(
            "[CINERIE_MEDIA] passo da capa levantou %s para article_id=%s mediaId=%s: %s; "
            "a materia publicada segue valida, sem capa",
            type(exc).__name__, article_id, media_result.media_id, exc,
        )
        return

    if hero is None:
        # O motivo ja saiu classificado em `point_hero_media` — conflito de
        # ESTADO ou recusa de outra natureza. Repetir aqui so somaria ruido.
        return

    logger.info(
        "[CINERIE_MEDIA] capa apontada mediaId=%s -> article_id=%s outcome=%s previous=%s",
        hero.media_id, hero.article_id, hero.outcome, hero.previous_media_id or "-",
    )


def _publish_to_cinerie(draft, art_data):
    """Pede publicacao governada ao Cinerie, quando o modo e AUTO_PUBLISH.

    Canal SEPARADO do envio de rascunho acima: outro contrato, outro endpoint,
    outro escopo. Roda por ultimo pelo mesmo motivo — o artefato local ja esta
    gravado, entao uma falha aqui nunca custa a copia local.

    O servico recusa antes de qualquer socket quando o gate bloqueou, quando o
    contrato divergiu ou quando o pedido nao passa na validacao local.
    """
    from .cinerie_service import MODE_AUTO_PUBLISH, resolve_delivery_mode

    try:
        mode = resolve_delivery_mode(MNSCR_DELIVERY_MODE, environment=MNSCR_ENVIRONMENT)
    except Exception as exc:  # noqa: BLE001 - configuracao invalida nunca publica
        logger.error("[CINERIE_PUBLICATION] modo de entrega invalido (%s); nada foi enviado", exc)
        return None

    if mode != MODE_AUTO_PUBLISH:
        return None

    service = None
    try:
        from .cinerie.client import CinerieClient, CinerieConfig
        from .cinerie.preflight import ContractPreflight
        from .cinerie_service import CinerieService
        from .cinerie_store import CinerieStore
        from .delivery.retry import RetryPolicy

        client = CinerieClient(
            CinerieConfig(
                base_url=PAYLOAD_INTERNAL_SERVICE_URL,
                api_key=MNSCR_PAYLOAD_API_KEY,
                timeout_seconds=MNSCR_CINERIE_TIMEOUT_SECONDS,
            )
        )
        service = CinerieService(
            store=CinerieStore(),
            client=client,
            public_author_id=MNSCR_PUBLIC_AUTHOR_ID,
            attribution_mode=MNSCR_ATTRIBUTION_MODE,
            delivery_mode=mode,
            preflight=ContractPreflight(
                client, ttl_seconds=MNSCR_CONTRACT_PREFLIGHT_TTL_SECONDS
            ),
            retry_policy=RetryPolicy(
                max_attempts=MNSCR_CINERIE_MAX_ATTEMPTS,
                base_seconds=MNSCR_CINERIE_RETRY_BASE_SECONDS,
            ),
            entity_resolver=_build_entity_resolver(),
            entity_card_max=MNSCR_CINERIE_ENTITY_CARD_MAX,
            entity_match_kinds=MNSCR_CINERIE_ENTITY_MATCH_KINDS,
        )
        result = service.publish_draft(
            draft,
            seo_source=getattr(draft, "seo_source", None) or art_data.get("_seo_source"),
        )
        logger.info("[CINERIE_PUBLICATION] draft_id=%s %s", draft.draft_id, result.safe_log_fields())
        log_cinerie_quota_deferrals([result])
        _ingest_hero_media_best_effort(draft, result, client)
        return result
    except Exception as exc:  # noqa: BLE001 - o draft local nunca pode ser perdido
        logger.exception(
            "[CINERIE_PUBLICATION] draft_id=%s falha inesperada (%s); "
            "o draft local permanece intacto",
            draft.draft_id, type(exc).__name__,
        )
        return None
    finally:
        if service is not None:
            service.store.close()


def log_cinerie_quota_deferrals(results) -> Dict[str, int]:
    """Registra o que o servidor adiou, sem criar uma cota local concorrente."""
    counts: Dict[str, int] = {}
    deferred_total = 0
    for result in results:
        if getattr(result, "outcome", None) != "DEFERRED":
            continue
        deferred_total += 1
        dimensions = [
            code for code in (getattr(result, "reason_codes", None) or [])
            if re.fullmatch(r"[A-Z0-9_]{1,80}_DAILY_LIMIT_REACHED", str(code))
        ] or ["DIMENSION_NOT_DECLARED"]
        for dimension in dimensions:
            counts[dimension] = counts.get(dimension, 0) + 1

    fields = " ".join(f"{name}={count}" for name, count in sorted(counts.items()))
    logger.info(
        "[CINERIE_QUOTA_DEFERRALS] deferred_total=%s dimensions=%s",
        deferred_total,
        fields or "none",
    )
    return counts


def _load_local_draft(draft_id: str):
    from .gate_service import draft_from_artifact

    path = Path(LOCAL_DRAFT_DIR) / f"{draft_id}.json"
    if not path.is_file():
        return None
    return draft_from_artifact(json.loads(path.read_text(encoding="utf-8")))


@contextmanager
def cinerie_dispatch_lock_heartbeat(
    db_path: str,
    owner: str,
    *,
    lease_seconds: int,
):
    """Renova o lock de processo enquanto uma passada pode estar em I/O remoto."""
    from .cinerie_store import CinerieStore

    stop = threading.Event()
    lost = threading.Event()
    interval = max(0.1, float(lease_seconds) / 3.0)

    def maintain() -> None:
        heartbeat_store = None
        try:
            heartbeat_store = CinerieStore(db_path)
            while not stop.wait(interval):
                try:
                    renewed = heartbeat_store.acquire_dispatch_lock(
                        owner=owner,
                        lease_seconds=max(1, lease_seconds),
                    )
                except Exception:
                    logger.exception("[CINERIE_DISPATCH_LOCK] state=heartbeat_failed")
                    lost.set()
                    return
                if not renewed:
                    lost.set()
                    return
        except Exception:
            logger.exception("[CINERIE_DISPATCH_LOCK] state=heartbeat_start_failed")
            lost.set()
        finally:
            if heartbeat_store is not None:
                heartbeat_store.close()

    thread = threading.Thread(target=maintain, daemon=True, name="cinerie-lock-heartbeat")
    thread.start()
    try:
        yield lost
    finally:
        stop.set()
        thread.join(timeout=interval + 5.0)


def _dispatch_cinerie_pending_once() -> None:
    """Executa uma passada finita fora da thread de ingestão."""
    from .cinerie.client import CinerieClient, CinerieConfig
    from .cinerie.preflight import ContractPreflight
    from .cinerie_service import CinerieService
    from .cinerie_store import CinerieStore
    from .delivery.retry import RetryPolicy

    owner = f"{os.getpid()}:{uuid.uuid4().hex}"
    store = CinerieStore()
    lock_acquired = False
    try:
        lock_acquired = store.acquire_dispatch_lock(
            owner=owner,
            lease_seconds=CINERIE_DISPATCH_LEASE_SECONDS,
        )
        if not lock_acquired:
            logger.info("[CINERIE_DISPATCH] process_lock=busy action=skipped")
            return

        client = CinerieClient(
            CinerieConfig(
                base_url=PAYLOAD_INTERNAL_SERVICE_URL,
                api_key=MNSCR_PAYLOAD_API_KEY,
                timeout_seconds=MNSCR_CINERIE_TIMEOUT_SECONDS,
            )
        )
        service = CinerieService(
            store=store,
            client=client,
            public_author_id=MNSCR_PUBLIC_AUTHOR_ID,
            attribution_mode=MNSCR_ATTRIBUTION_MODE,
            delivery_mode="AUTO_PUBLISH",
            preflight=ContractPreflight(
                client, ttl_seconds=MNSCR_CONTRACT_PREFLIGHT_TTL_SECONDS
            ),
            retry_policy=RetryPolicy(
                max_attempts=MNSCR_CINERIE_MAX_ATTEMPTS,
                base_seconds=MNSCR_CINERIE_RETRY_BASE_SECONDS,
            ),
            entity_resolver=_build_entity_resolver(),
            entity_card_max=MNSCR_CINERIE_ENTITY_CARD_MAX,
            entity_match_kinds=MNSCR_CINERIE_ENTITY_MATCH_KINDS,
        )
        with cinerie_dispatch_lock_heartbeat(
            store.db_path,
            owner,
            lease_seconds=max(1, CINERIE_DISPATCH_LEASE_SECONDS),
        ) as lock_lost:
            results = service.dispatch_pending(
                worker_id=owner,
                draft_loader=_load_local_draft,
                limit=CINERIE_DISPATCH_LIMIT,
                lease_seconds=CINERIE_DISPATCH_LEASE_SECONDS,
            )
            if lock_lost.is_set():
                logger.error("[CINERIE_DISPATCH_LOCK] state=lost owner=%s", owner)
        log_cinerie_quota_deferrals(results)
    except Exception as exc:  # noqa: BLE001 - despacho não derruba ingestão
        logger.exception("[CINERIE_DISPATCH] status=failed error=%s", type(exc).__name__)
    finally:
        if lock_acquired:
            store.release_dispatch_lock(owner=owner)
        store.close()


def schedule_cinerie_dispatch() -> bool:
    """Agenda sem bloquear o ciclo; não acumula passadas neste processo."""
    from .cinerie_service import MODE_AUTO_PUBLISH, resolve_delivery_mode

    try:
        mode = resolve_delivery_mode(MNSCR_DELIVERY_MODE, environment=MNSCR_ENVIRONMENT)
    except Exception as exc:  # noqa: BLE001 - startup valida; aqui falha fechado
        logger.error(
            "[CINERIE_DISPATCH] action=skipped reason=invalid_delivery_mode error=%s",
            type(exc).__name__,
        )
        return False
    if mode != MODE_AUTO_PUBLISH:
        logger.info("[CINERIE_DISPATCH] action=skipped reason=delivery_mode_not_auto_publish")
        return False

    global _cinerie_dispatch_future
    with _cinerie_dispatch_guard:
        if _cinerie_dispatch_future is not None and not _cinerie_dispatch_future.done():
            logger.info("[CINERIE_DISPATCH] process_lock=local_busy action=skipped")
            return False
        _cinerie_dispatch_future = _cinerie_dispatch_executor.submit(
            _dispatch_cinerie_pending_once
        )
    return True


def _run_factual_assessment(draft, art_data):
    """Build and persist the factual picture for this draft.

    Runs before the Editorial Gate, which consumes it. A technical failure here
    never costs the draft: the assessment is skipped, the reason recorded, and
    the gate still produces a coherent verdict from what it does have.
    """
    from .factual_builder import build_factual_assessment
    from .factual_store import FactualStore

    if not FACTUAL_ASSESSMENT_ENABLED:
        logger.info(
            "[FACTUAL_ASSESSMENT_COMPLETED] draft_id=%s motivo=desabilitado_por_configuracao",
            draft.draft_id,
        )
        return None

    store = None
    try:
        assessment = build_factual_assessment(
            draft,
            _source_material_for(art_data),
            claim_response=_claim_response_for(draft, art_data),
        )
        store = FactualStore()
        store.save_assessment(assessment)
        return assessment
    except Exception as exc:  # noqa: BLE001 - o draft local nunca pode ser perdido
        logger.exception(
            "[FACTUAL_ASSESSMENT_FAILED] draft_id=%s erro=%s: o draft segue para o gate",
            draft.draft_id, type(exc).__name__,
        )
        return None
    finally:
        if store is not None:
            store.close()


def _claim_response_for(draft, art_data):
    """Resposta bruta da camada semântica de claims, quando o modo pede uma.

    Um ``_claim_response`` já presente em ``art_data`` vence: é assim que o
    replay reprocessa com a resposta original e que os testes injetam uma
    resposta fixa sem tocar a rede.

    Em ``deterministic`` não há chamada — e a ausência é registrada pelo próprio
    ``build_factual_assessment`` como ``DETERMINISTIC_MODE_ONLY_PATTERN_CLAIMS``,
    não aqui: um único lugar decide o que a ausência significa.
    """
    injected = art_data.get("_claim_response")
    if injected is not None:
        return injected

    from .factual.states import MODE_DETERMINISTIC

    if FACTUAL_ASSESSMENT_MODE == MODE_DETERMINISTIC:
        return None

    from .factual_ai import request_claim_extraction

    return request_claim_extraction(draft)


def _source_material_for(art_data):
    """Source documents already extracted for this article.

    Uses only what the pipeline received: cluster documents when the event had
    several sources, otherwise the single extracted article. Nothing is fetched.
    """
    material = []
    for doc in art_data.get("cluster_docs") or []:
        if not isinstance(doc, dict):
            continue
        material.append(
            {
                "url": doc.get("url") or doc.get("source_url"),
                "content": doc.get("content") or doc.get("text") or "",
                "title": doc.get("title"),
                "source_name": doc.get("source_name") or doc.get("fonte_nome"),
                "source_domain": doc.get("domain"),
                "published_at": doc.get("published_at"),
                "is_primary": bool(doc.get("is_primary")),
                "canonical_url": doc.get("canonical_url"),
            }
        )
    # No caminho de fonte unica o texto extraido vive em art_data['extracted'],
    # nao no topo: o dicionario montado em worker_loop guarda a extracao inteira
    # sob essa chave. Ler so o topo devolvia lista vazia para TODO artigo nao
    # clusterizado, e a avaliacao factual saia com zero evidencia e cobertura
    # 0.0 — sem erro nenhum no caminho, porque lista vazia e um valor legitimo.
    extracted = art_data.get("extracted")
    extracted = extracted if isinstance(extracted, dict) else {}
    single_content = (
        extracted.get("content")
        or art_data.get("content")
        or art_data.get("content_html")
        or ""
    )
    if not material and single_content:
        material.append(
            {
                "url": art_data.get("url") or art_data.get("canonical_url"),
                "content": single_content,
                "title": extracted.get("title") or art_data.get("title"),
                "source_name": art_data.get("fonte_nome"),
                "source_domain": art_data.get("domain"),
                "is_primary": True,
            }
        )
    return [m for m in material if m.get("url") and m.get("content")]


def _run_editorial_gate(draft, *, db, article_db_id, input_event=None, factual_assessment=None):
    """Classify the draft and persist the verdict.

    The gate never turns a correctly generated draft into a failure: a blocked
    draft is still written to disk so an operator can see *why* it was blocked.
    Blocking stops a future external submission, not local preservation.

    A gate that itself fails is reported and the draft still proceeds to local
    storage — losing the artifact would destroy the only evidence available.
    """
    from .editorial_gate import EditorialGate
    from .gate_store import GateStore

    if not EDITORIAL_GATE_ENABLED:
        logger.warning(
            "[GATE_COMPLETED] draft_id=%s outcome=GATE_DISABLED "
            "motivo=MNSCR_EDITORIAL_GATE_ENABLED=false",
            draft.draft_id,
        )
        return disabled_result(draft)

    context = {
        "output_mode": OUTPUT_MODE,
        "input_event": input_event,
        "ingestion_warnings": list(draft.warnings or []),
        "factual_assessment": factual_assessment,
    }

    gate_store = None
    try:
        result = EditorialGate().evaluate(draft, context=context)
        gate_store = GateStore()
        gate_store.save_result(result)
        db.record_gate_result(
            article_db_id,
            gate_id=result.gate_id,
            gate_status=result.outcome,
            policy_version=result.policy_version,
            gate_hash=result.gate_hash,
        )
        if result.outcome == GATE_BLOCKED:
            logger.error(
                "[GATE_BLOCKED] draft_id=%s db_id=%s regras=%s",
                draft.draft_id, article_db_id,
                ",".join(r.rule_code for r in result.blocking_rules),
            )
        elif result.outcome == GATE_REVIEW_REQUIRED:
            logger.info(
                "[GATE_REVIEW_REQUIRED] draft_id=%s db_id=%s regras=%s",
                draft.draft_id, article_db_id,
                ",".join(r.rule_code for r in result.warning_rules),
            )
        return result
    except Exception as exc:  # noqa: BLE001 - o draft local nunca pode ser perdido
        logger.exception(
            "[GATE_COMPLETED] draft_id=%s db_id=%s erro=%s: o draft segue para gravacao local",
            draft.draft_id, article_db_id, type(exc).__name__,
        )
        return None
    finally:
        if gate_store is not None:
            gate_store.close()


def _resolve_input_event(art_data: Dict[str, Any]):
    """The normalized event behind this article, when there is one.

    Prefers the object attached during this cycle. Falls back to the event store
    when the article came back through the database (a re-enqueue loses the
    in-memory object). Returns ``None`` for anything that never went through the
    contract layer — the builder treats that as the pre-MS-2 path.
    """
    attached = art_data.get("_input_event")
    if attached is not None:
        return attached

    event_ref = art_data.get("_input_event_ref")
    if event_ref is not None:
        if not isinstance(event_ref, dict) or event_ref.get("schema") != "rssprime-event-reference-v1":
            logger.warning("[EVENT_REFERENCE] referencia de fila invalida ou desconhecida")
            return None
        event_key = event_ref.get("event_key")
        revision = event_ref.get("revision")
    else:
        # Legacy payloads predate the event-reference queue format.  They are
        # still consumable without rewriting their persisted JSON.
        event_key = art_data.get("event_key") or (art_data.get("cluster_item") or {}).get("event_key")
        revision = None
    if not event_key:
        return None

    store = None
    try:
        store = EventStore()
        stored = store.get_event(str(event_key), int(revision) if revision is not None else None)
        if stored is None or not stored.normalized_payload:
            logger.warning("[EVENT_REFERENCE] evento ausente event_key=%s revision=%s", event_key, revision)
            return None
        return stored.to_event()
    except Exception as exc:  # noqa: BLE001 - provenance is best-effort, never fatal
        logger.warning("[CONTRACT_VALIDATION] falha ao recuperar evento %s: %s", event_key, exc)
        return None
    finally:
        if store is not None:
            store.close()


def apply_input_contract(
    feed_items: List[Dict[str, Any]],
    source_id: str,
    event_store: Optional[EventStore] = None,
) -> List[Dict[str, Any]]:
    """Filter feed items through the versioned input contract.

    Only VALIDATED events continue into the pipeline. Duplicates, conflicts,
    stale revisions and invalid payloads are persisted with their reason and
    dropped here, so nothing downstream has to re-derive why an item vanished.

    Items that carry no event identity at all (plain fallback feeds, which are
    not acontecimentos) pass through untouched: the contract governs RSS Prime
    events, not every RSS item MNScr can read.
    """
    from .feeds import extract_event_envelope

    owns_store = event_store is None
    store = event_store or EventStore()
    service = IngestionService(store)

    accepted: List[Dict[str, Any]] = []
    try:
        for item in feed_items:
            envelope = extract_event_envelope(item)
            if not envelope.get("event_key") and not envelope.get("contract_version"):
                accepted.append(item)
                continue

            outcome = service.ingest(envelope, source_id=source_id)
            if outcome.accepted and outcome.event is not None:
                enriched = dict(item)
                enriched["_input_event"] = outcome.event
                enriched["event_key"] = outcome.event.event_key
                enriched["event_revision"] = outcome.event.revision
                enriched.setdefault("topic", outcome.event.topic)
                accepted.append(enriched)
                continue

            logger.info(
                "[EVENT_RECEIVED] source_id=%s event_key=%s revision=%s status=%s "
                "decision=%s errors=%s dropped=true",
                source_id,
                outcome.event.event_key if outcome.event else "-",
                outcome.event.revision if outcome.event else "-",
                outcome.validation_status,
                outcome.decision or "-",
                ",".join(outcome.error_codes) or "-",
            )
    finally:
        if owns_store:
            store.close()

    return accepted


def process_stored_event(event) -> Dict[str, Any]:
    """Replay processor: rebuild the draft for one already-stored revision.

    Reads nothing from the network and nothing from the feed. The event is
    re-enqueued for the worker under its own revision, so the draft id it
    produces is the deterministic id of that revision — replaying revision 2
    can never overwrite revision 1.
    """
    db = Database()
    try:
        article = db.get_article_by_event_key(event.event_key, event.revision)
        if article is None:
            raise RuntimeError(
                f"Evento '{event.event_key}' nao tem artigo correspondente em seen_articles; "
                "nada a reprocessar."
            )
        article = dict(article)
        article["db_id"] = article.get("id")
        article["_input_event"] = event
        article["event_key"] = event.event_key
        article["event_revision"] = event.revision
        db.reset_article_for_replay(int(article["db_id"]))
    finally:
        db.close()

    link_map = _build_link_map_for_replay()
    process_batch([article], link_map)

    db = Database()
    try:
        refreshed = db.get_article_by_event_key(event.event_key, event.revision) or {}
    finally:
        db.close()
    return {
        "draft_id": refreshed.get("draft_id"),
        "output_hash": refreshed.get("draft_output_hash"),
        "status": refreshed.get("draft_status"),
    }


def _build_link_map_for_replay() -> Dict[str, Any]:
    """Internal-link map for a replay run; empty is acceptable."""
    try:
        return ls_get_link_map() or {}
    except Exception as exc:  # noqa: BLE001 - links are a nicety, not a blocker
        logger.warning("[REPLAY_STARTED] mapa de links indisponivel: %s", exc)
        return {}


def run_pipeline_cycle(*, start_worker: bool = True, skip_pending_guard: bool = False):
    """Read feeds and enqueue articles for the worker or a bounded synchronous run."""
    initialize_runtime()
    reconcile_event_tasks()
    schedule_cinerie_dispatch()
    if start_worker:
        ensure_worker_started()
    # A persistent backlog is data, not proof that a worker is alive.  Queue
    # uniqueness and claims make a fresh ingestion pass safe while the daemon
    # drains; skipping here was the false-success path of --once.
    if not skip_pending_guard:
        pipeline_has_pending_work()

    logger.info("Starting new pipeline ingestion cycle.")

    db = Database()
    stale_result = db.recover_stale_processing_claims(
        stale_seconds=CLAIM_STALE_TIMEOUT_S,
        max_recoveries=MAX_ARTICLE_WATCHDOG_TIMEOUTS,
    )
    if any(stale_result.values()):
        logger.info("[CLAIM_STALE] recovery=%s", stale_result)

    feed_reader = FeedReader(user_agent=PIPELINE_CONFIG.get('publisher_name', 'Bot'))

    processed_total_in_cycle = 0
    superfeed_coverage: Dict[str, Dict[str, Any]] = {}

    for topic in ("movies", "tv"):
        for item in db.get_recent_superfeed_items(topic):
            _register_superfeed_item_coverage(item, superfeed_coverage)
        superseded_count, removed_queue_count = _purge_fallback_coverage(db, superfeed_coverage, topic)
        if superseded_count or removed_queue_count:
            logger.info(
                "[SUPERFEED_CLEANUP] topic=%s | superseded=%s | removed_from_queue=%s",
                topic,
                superseded_count,
                removed_queue_count,
            )

    for source_id in PIPELINE_ORDER:
        if processed_total_in_cycle >= MAX_PER_CYCLE:
            logger.info(f"Max articles per cycle ({MAX_PER_CYCLE}) reached. Ending ingestion cycle.")
            break

        consecutive_failures = db.get_consecutive_failures(source_id)
        if consecutive_failures >= 3:
            logger.warning(f"Circuit open for feed {source_id} ({consecutive_failures} fails) -> skipping.")
            db.reset_consecutive_failures(source_id)
            continue

        feed_config = RSS_FEEDS.get(source_id)
        if not feed_config:
            logger.warning(f"No configuration found for feed source: {source_id}")
            continue

        logger.info(f"Ingesting feed: {source_id}")
        try:
            limit = min(MAX_PER_FEED_CYCLE, MAX_PER_CYCLE - processed_total_in_cycle)
            if limit <= 0:
                logger.info(f"No remaining capacity in this cycle for {source_id}.")
                break

            origin = feed_config.get("origin", "fallback")
            topic = feed_config.get("topic")

            pending_articles = db.get_articles_to_process(
                source_id,
                limit,
                clusters_only=feed_config.get('require_multi_source', False),
            )
            for article in pending_articles:
                article.setdefault("origin", origin)
                article.setdefault("topic", topic)
                article.setdefault("canonical_url", canonicalize_url(article.get("url") or ""))
            if origin == "fallback" and topic and pending_articles:
                covered_pending = []
                for article in pending_articles:
                    winner, match_reason = _match_superfeed_coverage(article, superfeed_coverage)
                    if winner:
                        covered_pending.append(article)
                        _log_superfeed_win(article, winner, match_reason, context="fallback_reenqueue_blocked")
                if covered_pending:
                    superseded_count = db.mark_articles_superseded(
                        [article["id"] for article in covered_pending],
                        reason="Fallback coberto por item do superfeed",
                    )
                    logger.info(
                        f"Marked {superseded_count} pending fallback articles from {source_id} as superseded by superfeed."
                    )
                pending_articles = [
                    article for article in pending_articles
                    if not _item_is_covered_by_superfeed(article, superfeed_coverage)
                ]
            if pending_articles:
                for article in pending_articles:
                    article['db_id'] = article['id']
                article_queue.push_many(pending_articles)
                if origin == "superfeed":
                    for article in pending_articles:
                        _register_superfeed_item_coverage(article, superfeed_coverage)
                    if topic:
                        superseded_count, removed_queue_count = _purge_fallback_coverage(db, superfeed_coverage, topic)
                        if superseded_count or removed_queue_count:
                            logger.info(
                                "[SUPERFEED_CLEANUP] topic=%s | superseded=%s | removed_from_queue=%s",
                                topic,
                                superseded_count,
                                removed_queue_count,
                            )
                processed_total_in_cycle += len(pending_articles)
                logger.info(
                    f"Re-enqueued {len(pending_articles)} pending articles from {source_id}. "
                    f"Total in cycle: {processed_total_in_cycle}."
                )
                db.reset_consecutive_failures(source_id)
                time.sleep(int(os.getenv('FEED_STAGGER_S', 45)))
                continue

            feed_items = feed_reader.read_feeds(feed_config, source_id)
            feed_items = apply_input_contract(feed_items, source_id)
            if origin == "fallback" and topic:
                filtered_feed_items = []
                skipped_count = 0
                for item in feed_items:
                    winner, match_reason = _match_superfeed_coverage(item, superfeed_coverage)
                    if winner:
                        skipped_count += 1
                        _log_superfeed_win(item, winner, match_reason, context="fallback_feed_blocked")
                        continue
                    filtered_feed_items.append(item)
                feed_items = filtered_feed_items
                if skipped_count:
                    logger.info(
                        f"Skipped {skipped_count} fallback items from {source_id} already represented in superfeed."
                    )
            new_articles = db.filter_new_articles(source_id, feed_items, limit=limit)

            if not new_articles:
                logger.info(f"No new articles found for {source_id}.")
                continue

            for article in new_articles:
                article['source_id'] = source_id

            article_queue.push_many(new_articles)
            if origin == "superfeed":
                for article in new_articles:
                    _register_superfeed_item_coverage(article, superfeed_coverage)
                if topic:
                    superseded_count, removed_queue_count = _purge_fallback_coverage(db, superfeed_coverage, topic)
                    if superseded_count or removed_queue_count:
                        logger.info(
                            "[SUPERFEED_CLEANUP] topic=%s | superseded=%s | removed_from_queue=%s",
                            topic,
                            superseded_count,
                            removed_queue_count,
                        )

            processed_total_in_cycle += len(new_articles)
            logger.info(f"Enqueued {len(new_articles)} articles from {source_id}. Total in cycle: {processed_total_in_cycle}.")

            db.reset_consecutive_failures(source_id)

        except Exception as e:
            logger.error(f"Error processing feed {source_id}: {e}", exc_info=True)
            db.increment_consecutive_failures(source_id)

        # Stagger feed processing
        time.sleep(int(os.getenv('FEED_STAGGER_S', 45)))

    db.close()
    logger.info("Pipeline ingestion cycle finished.")
