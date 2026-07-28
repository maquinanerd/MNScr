import gzip
import hashlib
import logging
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional
from urllib.parse import unquote, urlparse

import feedparser
import requests

logger = logging.getLogger(__name__)

NS = {"ns":"http://www.sitemaps.org/schemas/sitemap/0.9",
      "news":"http://www.google.com/schemas/sitemap-news/0.9"}

# --- New helper functions for robust date parsing and sorting ---
ISO_CLEAN_Z = re.compile(r'Z$')

def _to_iso(s: str) -> str:
    if not s:
        return ""
    s = s.strip()
    # normaliza 'Z' para +00:00 (compatível com fromisoformat)
    s = ISO_CLEAN_Z.sub("+00:00", s)
    return s

def _pick_date_from_dict(d: dict) -> str:
    # tenta chaves comuns
    for k in ("news:publication_date", "publication_date", "pubDate", "lastmod", "updated", "date"):
        v = d.get(k)
        if v:
            return _to_iso(v if isinstance(v, str) else str(v))
    # se tiver só um par, pega o valor
    if len(d) == 1:
        return _to_iso(str(next(iter(d.values()))))
    return ""

def _normalize_published(v) -> str:
    if isinstance(v, str):
        return _to_iso(v)
    if isinstance(v, dict):
        return _to_iso(_pick_date_from_dict(v))
    if isinstance(v, list):
        return _normalize_published(v[0]) if v else ""
    return ""

def _parse_dt(s: str):
    if not s:
        return None
    # tenta fromisoformat primeiro
    try:
        return datetime.fromisoformat(s)
    except Exception:
        pass
    # tenta alguns formatos comuns de sitemap/news
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%a, %d %b %Y %H:%M:%S %z"):
        try:
            return datetime.strptime(s, fmt)
        except Exception:
            continue
    return None

def _sort_key(item: dict):
    dt = _parse_dt(_normalize_published(item.get("published")))
    return dt if dt else datetime.min.replace(tzinfo=timezone.utc)
# --- End of new helper functions ---

def _stable_id_from(text: str) -> str:
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()


def canonicalize_url(url: str) -> str:
    """Normaliza URL para deduplicação entre superfeed e fallback."""
    if not url:
        return ""
    try:
        parsed = urlparse(url.strip())
    except Exception:
        return (url or "").strip().lower()

    hostname = (parsed.hostname or "").lower()
    if hostname.startswith("www."):
        hostname = hostname[4:]

    path = unquote(parsed.path or "/")
    path = re.sub(r"/{2,}", "/", path)
    if path != "/":
        path = path.rstrip("/")

    return f"{hostname}{path}".lower()


def cluster_signature(urls: list[str]) -> str:
    """
    Gera uma assinatura SHA1 a partir das URLs canonicalizadas e ordenadas.
    Usada para deduplicar clusters quando o event_key muda, mas o conjunto de URLs e o mesmo.
    """
    canonical = sorted(canonicalize_url(u) for u in urls if u)
    return hashlib.sha1("|".join(canonical).encode("utf-8")).hexdigest()


def normalize_title_for_match(title: str) -> str:
    """Gera uma versão simplificada do título para match aproximado."""
    if not title:
        return ""
    normalized = title.lower().strip()
    normalized = re.sub(r"[\"'`´“”‘’]", "", normalized)
    normalized = re.sub(r"[^a-z0-9à-ÿ]+", " ", normalized, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", normalized).strip()


def titles_are_similar(left: str, right: str, threshold: float = 0.92) -> bool:
    if not left or not right:
        return False
    if left == right:
        return True
    return SequenceMatcher(None, left, right).ratio() >= threshold

# Superfeed namespaces
SF_NS_CANDIDATES = (
    "https://superfeed.rssprime/ns/1.0",
    "https://rssprime.io/superfeed/1.0",
)

def normalize_item(raw: dict) -> dict:
    """
    Aceita item vindo de RSS (feedparser) ou de sitemap e garante chaves padronizadas.
    Preferência de ID:
      1) guid/id do RSS se existir e for não-vazio
      2) link/url/loc
      3) fallback: hash de title+published
    """
    # Possíveis nomes vindos do parser
    guid = raw.get("guid") or raw.get("id")  # feedparser pode pôr 'id'
    link = raw.get("link") or raw.get("url") or raw.get("loc")
    title = raw.get("title") or raw.get("news_title") or ""
    published = raw.get("published") or raw.get("pubDate") or raw.get("lastmod")
    author = raw.get("author") or raw.get("dc_creator") or None
    summary = raw.get("summary") or raw.get("description") or None

    # Normaliza data (deixa string se não souber converter)
    def _parse_dt(dt):
        if not dt or not isinstance(dt, str):
            return dt
        for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
            try:
                return datetime.strptime(dt, fmt).isoformat()
            except (ValueError, TypeError):
                continue
        return dt  # mantém como veio

    published_iso = _parse_dt(published)

    # Monta ID estável
    if guid:
        ext_id = str(guid).strip()
    elif link:
        ext_id = _stable_id_from(link)
    else:
        ext_id = _stable_id_from(f"{title}|{published_iso or ''}")

    normalized = {
        "id":        ext_id,
        "url":       link,
        "title":     title.strip() if isinstance(title, str) else title,
        "published": published_iso,
        "author":    author,
        "summary":   summary,
        "_raw":      raw,
    }

    sf = _parse_sf_fields(raw)
    if sf["has_superfeed_meta"]:
        normalized.update(sf)

    return normalized

def _parse_sf_fields(entry: dict) -> dict:
    def _sf(key):
        for namespace in SF_NS_CANDIDATES:
            value = entry.get(f"{{{namespace}}}{key}")
            if value not in (None, ""):
                return value
        return entry.get(f"sf_{key}", "")

    multi_source_raw = str(_sf("multi_source")).strip().lower()
    is_multi_source = multi_source_raw in {"1", "true", "yes", "sim"}
    source_count_raw = _sf("source_count")
    source_count = int(source_count_raw or 0)
    urls_raw = _sf("urls")
    sep = "|" if "|" in urls_raw else ","
    all_urls = [u.strip() for u in urls_raw.split(sep) if u.strip()] if urls_raw else []
    event_key = _sf("event_key")
    is_cluster = bool(event_key and is_multi_source and source_count >= 2)
    signature = cluster_signature(all_urls) if is_cluster and len(all_urls) >= 2 else None
    return {
        "has_superfeed_meta": bool(
            event_key or urls_raw or _sf("topic") or source_count_raw or multi_source_raw
        ),
        "is_multi_source": is_multi_source,
        "multi_source": is_multi_source,
        "source_count": source_count or (len(all_urls) if all_urls else 0),
        "fonte_nome": _sf("fonte_nome"),
        "is_cluster": is_cluster,
        "event_key": event_key,
        "cluster_signature": signature,
        "primary_source": _sf("primary_source"),
        "all_sources": [s.strip() for s in re.split(r"[|,]", _sf("all_sources")) if s.strip()],
        "cluster_size": int(_sf("cluster_size") or 1),
        "urls": all_urls,
        "additional_urls": [u for u in all_urls if u != entry.get("link", "")],
        "topic": _sf("topic"),
        "first_seen": _sf("first_seen"),
        "last_seen": _sf("last_seen"),
    }


def enrich_feed_item(item: Dict[str, Any], feed_config: Dict[str, Any], source_id: str) -> Dict[str, Any]:
    """Aplica metadados operacionais do pipeline a um item normalizado."""
    origin = feed_config.get("origin") or ("superfeed" if item.get("has_superfeed_meta") else "fallback")
    topic = item.get("topic") or feed_config.get("topic") or feed_config.get("category")

    primary_url = item.get("url")
    urls = [u for u in item.get("urls", []) if u]
    if primary_url and primary_url not in urls:
        urls.insert(0, primary_url)

    source_name = feed_config.get("source_name") or source_id
    all_sources = item.get("all_sources") or [item.get("primary_source") or source_name]
    primary_source = item.get("primary_source") or all_sources[0] if all_sources else source_name

    item["origin"] = origin
    item["topic"] = topic
    item["priority"] = int(feed_config.get("priority", 10 if origin == "fallback" else 100))
    item["multi_source"] = bool(item.get("multi_source") or item.get("is_multi_source") or item.get("is_cluster"))
    item["source_count"] = int(item.get("source_count") or (len(all_sources) if all_sources else 1))
    item["fonte_nome"] = item.get("fonte_nome") or source_name
    item["primary_source"] = primary_source
    item["all_sources"] = all_sources
    item["urls"] = urls or ([primary_url] if primary_url else [])
    item["additional_urls"] = [u for u in item["urls"] if u and u != primary_url]
    if origin == "superfeed" and item.get("is_cluster") and len(item["urls"]) >= 2:
        item["cluster_signature"] = item.get("cluster_signature") or cluster_signature(item["urls"])
    item["canonical_url"] = canonicalize_url(primary_url or "")
    item["normalized_title"] = normalize_title_for_match(item.get("title", ""))
    item["source_id"] = source_id
    item["is_fallback"] = origin == "fallback"
    return item

class FeedReader:
    def __init__(self, user_agent: str):
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': user_agent})

    def _fetch_content(self, url: str) -> Optional[bytes]:
        try:
            response = self.session.get(url, timeout=20)
            response.raise_for_status()

            content = response.content
            ctype = response.headers.get("Content-Type", "").lower()

            # Decompress if it's a gzipped file
            if "gzip" in ctype or url.endswith(".gz"):
                try:
                    content = gzip.decompress(content)
                except (gzip.BadGzipFile, OSError) as e:
                    logger.warning(
                        f"Content from {url} seems to be gzipped but failed to decompress. "
                        f"Proceeding with original content. Error: {e}"
                    )
                except Exception as e:
                    logger.error(f"An unexpected error occurred during gzip decompression for {url}: {e}")
                    return None

            return content
        except requests.RequestException as e:
            logger.error(f"Failed to fetch feed/sitemap from {url}: {e}")
            return None

    def _parse_sitemap(
        self,
        xml_bytes: bytes,
        limit: int = 50,
        allow_regex: Optional[str] = None,
        deny_regex: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Parses a sitemap.xml (or sitemapindex.xml) and returns a list of article-like dicts.
        Handles nested sitemap indexes by fetching them.
        """
        try:
            root = ET.fromstring(xml_bytes)
        except ET.ParseError as e:
            logger.error(f"Failed to parse XML sitemap: {e}")
            return []

        allow = re.compile(allow_regex) if allow_regex else None
        deny  = re.compile(deny_regex)  if deny_regex  else None
        items = []

        # Handle sitemap index by fetching and parsing child sitemaps
        if root.tag.endswith("sitemapindex"):
            logger.info("Detected sitemap index. Fetching child sitemaps.")
            child_sitemap_urls = [
                sm.findtext("ns:loc", NS)
                for sm in root.findall(".//ns:sitemap", NS)
                if sm.findtext("ns:loc", NS)
            ]

            for url in child_sitemap_urls:
                if len(items) >= limit:
                    break
                logger.debug(f"Fetching child sitemap from {url}")
                child_bytes = self._fetch_content(url)
                if child_bytes:
                    # Recursive call to parse the child sitemap, passing regexes
                    items.extend(self._parse_sitemap(
                        child_bytes, limit=limit, allow_regex=allow_regex, deny_regex=deny_regex
                    ))
                    time.sleep(0.2)  # Be polite

            # Sort and limit at the very end of processing the index
            items.sort(key=_sort_key, reverse=True)
            logger.info(f"Parsed {len(items)} total items from sitemap index.")
            return items[:limit]

        # Handle regular sitemap (urlset)
        for url_element in root.findall(".//ns:url", NS):
            loc_el = url_element.find("ns:loc", NS)
            if loc_el is None or not (loc := (loc_el.text or "").strip()):
                continue

            if deny and deny.search(loc):
                continue
            if allow and not allow.search(loc):
                continue

            lastmod = url_element.findtext("ns:lastmod", NS)

            title = None
            news_block = url_element.find("news:news", NS)
            if news_block is not None:
                title_element = news_block.find("news:title", NS)
                if title_element is not None and title_element.text:
                    title = title_element.text.strip()

            if not loc:
                continue

            items.append({
                "link": loc,
                "guid": loc,
                "title": title or loc,
                "published": lastmod,
            })

        # For a single sitemap, sort and limit here.
        items.sort(key=_sort_key, reverse=True)
        logger.info(f"Parsed {len(items)} items from sitemap.")
        return items[:limit]

    def read_feeds(self, feed_config: Dict[str, Any], source_id: str) -> List[Dict[str, Any]]:
        raw_items = []
        feed_type = feed_config.get('type', 'rss')
        deny_regex = feed_config.get('deny_regex')
        require_multi_source = feed_config.get('require_multi_source', False)
        deny = re.compile(deny_regex) if deny_regex else None

        for url in feed_config.get('urls', []):
            logger.info(f"Reading {feed_type} feed from {url} for source '{source_id}'")
            content = self._fetch_content(url)
            if not content:
                continue

            if feed_type == 'sitemap':
                raw_items.extend(self._parse_sitemap(
                    content, limit=50,
                    allow_regex=feed_config.get('allow_regex'),
                    deny_regex=deny_regex
                ))
            else:  # Default to 'rss'
                feed = feedparser.parse(content)
                if feed.bozo:
                    logger.warning(f"Feed from {url} is not well-formed: {feed.bozo_exception}")
                    # Even with bozo flag, try to extract what we can
                    logger.info("Attempting to extract articles from malformed feed despite bozo flag...")

                entries = feed.entries
                if len(entries) == 0 and feed.bozo:
                    logger.warning("Feed returned 0 entries due to malformation. Trying with relaxed parsing...")
                    # Try with feedparser's built-in error recovery
                    import time
                    time.sleep(2)  # Small delay before retry
                    feed = feedparser.parse(content, sanitize_html=False, resolve_relative_uris=False)
                    entries = feed.entries
                    if entries:
                        logger.info(f"Recovered {len(entries)} entries with relaxed parsing")

                if deny:
                    entries = [e for e in entries if not deny.search(e.get('title', ''))]

                raw_items.extend(entries)

        all_items = [
            enrich_feed_item(normalize_item(item), feed_config, source_id)
            for item in raw_items
        ]
        if require_multi_source:
            all_items = [item for item in all_items if item.get('is_cluster')]

        if logger.isEnabledFor(logging.DEBUG):
            if raw_items:
                logger.debug("RAW sample for %s: %r", source_id, raw_items[0])
            if all_items:
                logger.debug("Normalized sample for %s: %r", source_id, all_items[0])

        seen_keys = set()
        unique_items = []
        for item in all_items:
            dedupe_key = item.get('event_key') or item.get('url') or item.get('id')
            if dedupe_key and dedupe_key not in seen_keys:
                unique_items.append(item)
                seen_keys.add(dedupe_key)

        unique_items.sort(
            key=lambda item: (
                int(item.get("origin") == "superfeed"),
                int(item.get("multi_source", False)),
                _sort_key(item),
            ),
            reverse=True,
        )
        logger.info(f"Found {len(unique_items)} total unique items for {source_id}.")
        return unique_items
