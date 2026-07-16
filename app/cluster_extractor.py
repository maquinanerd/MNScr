"""
Extração multi-fonte para itens de cluster do RSSPRIME Superfeed.
"""
import logging
import time
from typing import Optional

from .content_limiter import truncate_html_by_visible_chars
from .extractor import youtube_thumbnail_video_id

logger = logging.getLogger(__name__)

MAX_SECONDARY_DOCS = 2
MAX_CONTENT_CHARS  = 8000

DOMAIN_DELAYS: dict[str, float] = {
    "screenrant.com": 2.0,
    "collider.com":   2.0,
    "ign.com":        1.5,
    "deadline.com":   1.5,
    "variety.com":    1.5,
}
DEFAULT_DELAY = 1.0


def _get_domain_delay(url: str) -> float:
    """Retorna o delay (segundos) configurado para o domínio da URL."""
    try:
        from urllib.parse import urlparse
        host = urlparse(url).hostname or ""
        for domain, delay in DOMAIN_DELAYS.items():
            if domain in host:
                return delay
    except Exception:
        pass
    return DEFAULT_DELAY


def extract_cluster_documents(item: dict, extractor) -> list[dict]:
    """
    Extrai conteúdo da URL principal e de até MAX_SECONDARY_DOCS URLs adicionais.
    Retorna lista de dicts: [{source, url, title, content}, ...]
    Primeiro item é sempre a fonte principal.
    """
    docs = []

    primary_url = item["url"]
    primary_doc = _safe_extract(extractor, primary_url, item.get("primary_source", ""))
    if primary_doc:
        docs.append(primary_doc)
    else:
        logger.warning(f"[CLUSTER_EXTRACT] Falha na URL principal: {primary_url}")

    for url in item.get("additional_urls", [])[:MAX_SECONDARY_DOCS]:
        delay = _get_domain_delay(url)
        logger.info(f"[CLUSTER_RATE_LIMIT] sleeping={delay}s url={url}")
        time.sleep(delay)
        doc = _safe_extract(extractor, url, source_hint="")
        if doc:
            docs.append(doc)

    logger.info(
        f"[CLUSTER_EXTRACT] event_key={item.get('event_key')} "
        f"docs_extraídos={len(docs)}/{1 + len(item.get('additional_urls', []))}"
    )
    return docs


def _safe_extract(extractor, url: str, source_hint: str) -> Optional[dict]:
    """Wrapper com tratamento de erro individual por URL."""
    try:
        html = extractor._fetch_html(url)
        if not html:
            return None
        data = extractor.extract(html, url=url)
        if not data or not data.get("content"):
            return None
        return {
            "source":            source_hint or url,
            "url":               url,
            "title":             data.get("title", ""),
            "content":           truncate_html_by_visible_chars(data["content"], MAX_CONTENT_CHARS),
            "featured_image_url": data.get("featured_image_url"),
            "videos": data.get("videos", []),
        }
    except Exception as e:
        logger.error(f"[CLUSTER_EXTRACT] Erro ao extrair {url}: {e}")
        return None


def collect_cluster_videos(docs: list[dict]) -> list[dict]:
    """Consolida videos unicos de multiplas fontes do cluster."""
    unique_videos = []
    seen_ids = set()

    for doc in docs:
        for video in doc.get("videos", []):
            if not isinstance(video, dict):
                continue
            video_id = video.get("id") or video.get("watch_url") or video.get("embed_url")
            if not video_id or video_id in seen_ids:
                continue
            seen_ids.add(video_id)
            unique_videos.append(video)

    return unique_videos


def collect_cluster_images(docs: list[dict], max_images: int = 3) -> list:
    """Consolida imagens editoriais unicas de multiplas fontes do cluster."""
    unique_images = []
    seen_keys = set()

    def _image_key(candidate) -> str:
        if isinstance(candidate, str):
            if youtube_thumbnail_video_id(candidate):
                return ""
            return candidate.strip().lower()
        if isinstance(candidate, dict):
            url = str(candidate.get("url") or candidate.get("src") or "")
            if youtube_thumbnail_video_id(url):
                return ""
            return url.strip().lower()
        return ""

    for doc in docs:
        candidates = list(doc.get("images", []) or [])
        if doc.get("featured_image_url"):
            candidates.append(
                {
                    "url": doc.get("featured_image_url"),
                    "alt": "",
                    "caption": "",
                }
            )

        for image in candidates:
            key = _image_key(image)
            if not key or key in seen_keys:
                continue
            seen_keys.add(key)
            unique_images.append(image)
            if len(unique_images) >= max_images:
                return unique_images

    return unique_images


def build_cluster_prompt_payload(item: dict, docs: list[dict]) -> dict:
    """Monta o payload para o ai_processor no modo cluster."""
    cluster_videos = collect_cluster_videos(docs)
    cluster_images = collect_cluster_images(docs)
    return {
        "cluster_mode":   True,
        "event_key":      item.get("event_key"),
        "topic":          item.get("topic", ""),
        "primary_source": item.get("primary_source", ""),
        "sources":        item.get("all_sources", []),
        "documents":      docs,
        "title":          docs[0]["title"] if docs else item.get("title", ""),
        "content_html":   _merge_cluster_contents(docs),
        "source_url":     item["url"],
        "videos":         cluster_videos,
        "images":         cluster_images,
        "source_name":    item.get("fonte_nome", ""),
        "category":       item.get("topic", ""),
    }


def _merge_cluster_contents(docs: list[dict]) -> str:
    """Une textos de múltiplas fontes em um único bloco para a IA."""
    parts = []
    for i, doc in enumerate(docs):
        label = "FONTE PRINCIPAL" if i == 0 else f"FONTE SECUNDÁRIA {i}"
        parts.append(
            f"=== {label}: {doc['source']} ===\n"
            f"Título: {doc['title']}\n"
            f"URL: {doc['url']}\n\n"
            f"{doc['content']}"
        )
    return "\n\n".join(parts)
