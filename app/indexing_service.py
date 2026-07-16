import argparse
import html
import json
import logging
import os
import re
import sqlite3
import sys
import xmlrpc.client
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote_plus, urlparse, urlunparse
from xml.etree import ElementTree as ET

import requests
from apscheduler.schedulers.blocking import BlockingScheduler
from dotenv import load_dotenv

from .sqlite_utils import connect_sqlite

try:
    from google.auth.transport.requests import Request as GoogleAuthRequest
    from google.oauth2 import service_account
except ImportError:  # pragma: no cover - dependency is optional at runtime
    GoogleAuthRequest = None
    service_account = None

load_dotenv(override=True)

logger = logging.getLogger(__name__)

INDEXER_DB_PATH = os.getenv("INDEXER_DB_PATH", "data/indexing.db")
INDEXER_INTERVAL_MINUTES = int(os.getenv("INDEXER_INTERVAL_MINUTES", "5"))
INDEXER_POST_LIMIT = int(os.getenv("INDEXER_POST_LIMIT", "20"))
INDEXER_TIMEOUT = int(os.getenv("INDEXER_TIMEOUT", "20"))

WORDPRESS_API_URL = (os.getenv("WORDPRESS_URL") or "").rstrip("/")
WORDPRESS_USER = os.getenv("WORDPRESS_USER", "")
WORDPRESS_PASSWORD = os.getenv("WORDPRESS_PASSWORD", "")
WORDPRESS_SITE_NAME = os.getenv("WORDPRESS_SITE_NAME", "The Screen / Cinerie")

GOOGLE_INDEXING_ENABLED = os.getenv("GOOGLE_INDEXING_ENABLED", "false").lower() == "true"
GOOGLE_INDEXING_CREDENTIALS_FILE = os.getenv("GOOGLE_INDEXING_CREDENTIALS_FILE", "")
GOOGLE_INDEXING_ENDPOINT = os.getenv(
    "GOOGLE_INDEXING_ENDPOINT",
    "https://indexing.googleapis.com/v3/urlNotifications:publish",
)
GOOGLE_URL_INSPECTION_ENABLED = (
    os.getenv("GOOGLE_URL_INSPECTION_ENABLED", "false").lower() == "true"
)
GSC_PROPERTY = (os.getenv("GSC_PROPERTY") or "").strip()
GOOGLE_URL_INSPECTION_SITE_URL = (os.getenv("GOOGLE_URL_INSPECTION_SITE_URL") or "").strip()
GOOGLE_URL_INSPECTION_LANGUAGE_CODE = (
    os.getenv("GOOGLE_URL_INSPECTION_LANGUAGE_CODE", "pt-BR").strip() or "pt-BR"
)
GOOGLE_URL_INSPECTION_ENDPOINT = os.getenv(
    "GOOGLE_URL_INSPECTION_ENDPOINT",
    "https://searchconsole.googleapis.com/v1/urlInspection/index:inspect",
)

INDEXNOW_ENABLED = os.getenv("INDEXNOW_ENABLED", "false").lower() == "true"
INDEXNOW_ENDPOINT = os.getenv("INDEXNOW_ENDPOINT", "https://api.indexnow.org/indexnow")
INDEXNOW_KEY = os.getenv("INDEXNOW_KEY", "")
INDEXNOW_KEY_LOCATION = os.getenv("INDEXNOW_KEY_LOCATION", "")

SITEMAP_PING_ENABLED = os.getenv("SITEMAP_PING_ENABLED", "false").lower() == "true"
# Deprecated: Google retired sitemap ping in 2023 and Bing returns 410 for
# the legacy endpoint. Keep the flag only for non-deprecated custom endpoints.
SITEMAP_PING_ENDPOINTS = os.getenv(
    "SITEMAP_PING_ENDPOINTS",
    "",
)

RSS_PING_ENABLED = os.getenv("RSS_PING_ENABLED", "true").lower() == "true"
RSS_PING_XMLRPC_URL = os.getenv("RSS_PING_XMLRPC_URL", "https://rpc.pingomatic.com/")

INDEXER_NOTIFY_ON_PUBLISH = os.getenv("INDEXER_NOTIFY_ON_PUBLISH", "false").lower() == "true"
INDEXER_NOTIFY_PROCESS_IMMEDIATELY = (
    os.getenv("INDEXER_NOTIFY_PROCESS_IMMEDIATELY", "true").lower() == "true"
)

YOAST_NEWS_SITEMAP_ENABLED = os.getenv("YOAST_NEWS_SITEMAP_ENABLED", "false").lower() == "true"
YOAST_NEWS_SITEMAP_URL = (os.getenv("YOAST_NEWS_SITEMAP_URL") or "").strip()
YOAST_NEWS_SITEMAP_VALIDATE_RECENT_POSTS = (
    os.getenv("YOAST_NEWS_SITEMAP_VALIDATE_RECENT_POSTS", "false").lower() == "true"
)
YOAST_NEWS_SITEMAP_TIMEOUT = int(os.getenv("YOAST_NEWS_SITEMAP_TIMEOUT", "20"))

RECRAWL_ENABLED = os.getenv("RECRAWL_ENABLED", "false").lower() == "true"
RECRAWL_DELAY_MINUTES = int(os.getenv("RECRAWL_DELAY_MINUTES", "15"))
RECRAWL_MAX_ATTEMPTS = int(os.getenv("RECRAWL_MAX_ATTEMPTS", "3"))
RECRAWL_INCLUDE_INITIAL_PUBLISH = (
    os.getenv("RECRAWL_INCLUDE_INITIAL_PUBLISH", "true").lower() == "true"
)

DISCOVERY_RELATED_LINKS_ENABLED = (
    os.getenv("DISCOVERY_RELATED_LINKS_ENABLED", "false").lower() == "true"
)
DISCOVERY_RELATED_LINKS_LIMIT = int(os.getenv("DISCOVERY_RELATED_LINKS_LIMIT", "5"))

TITLE_TOKEN_STOPWORDS = {
    "para",
    "com",
    "sem",
    "sobre",
    "entre",
    "apos",
    "depois",
    "filme",
    "serie",
    "series",
    "novo",
    "nova",
    "news",
    "trailer",
    "video",
    "oficial",
    "primeiro",
    "service",
    "streaming",
}


def configure_logging() -> logging.Logger:
    os.makedirs("logs", exist_ok=True)

    logger = logging.getLogger(__name__)
    logger.handlers.clear()
    logger.propagate = True
    logger.setLevel(logging.INFO)

    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)

    file_handler = logging.FileHandler("logs/indexer.log", mode="a", encoding="utf-8")
    file_handler.setLevel(logging.INFO)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)

    formatter = logging.Formatter(
        "%(asctime)s - %(levelname)s - %(module)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

    return logger


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_datetime(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    try:
        normalized = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except Exception:
        return value


def _json_dumps(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return str(value)


def _strip_html(raw: Any) -> str:
    text = str(raw or "")
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()


def _normalize_url_for_match(url: str) -> str:
    return (url or "").strip().rstrip("/")


def _safe_int_list(values: Any) -> List[int]:
    results: List[int] = []
    for value in values or []:
        try:
            results.append(int(value))
        except Exception:
            continue
    return results


def _get_conn() -> sqlite3.Connection:
    db_file = Path(INDEXER_DB_PATH)
    db_file.parent.mkdir(parents=True, exist_ok=True)
    return connect_sqlite(db_file, row_factory=sqlite3.Row)


def initialize_database() -> None:
    with _get_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS indexed_urls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL,
                date_published TEXT,
                date_sent TEXT,
                status TEXT NOT NULL,
                engine TEXT NOT NULL,
                response TEXT,
                UNIQUE(url, engine)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_indexed_urls_status ON indexed_urls(status)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_indexed_urls_date_published ON indexed_urls(date_published)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS published_posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                wp_post_id INTEGER,
                url TEXT NOT NULL UNIQUE,
                title TEXT,
                slug TEXT,
                date_published TEXT,
                categories_json TEXT,
                tags_json TEXT,
                source TEXT DEFAULT 'unknown',
                notify_status TEXT DEFAULT 'pending',
                last_error TEXT,
                notified_at TEXT,
                processed_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS recrawl_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                post_id INTEGER,
                url TEXT NOT NULL,
                scheduled_for TEXT NOT NULL,
                executed_at TEXT,
                status TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                response TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS related_links_suggestions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_post_id INTEGER,
                source_url TEXT NOT NULL,
                target_post_id INTEGER,
                target_url TEXT NOT NULL,
                anchor_text TEXT NOT NULL,
                relevance_score REAL NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(source_post_id, target_post_id)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_published_posts_notify_status ON published_posts(notify_status)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_recrawl_queue_status_scheduled ON recrawl_queue(status, scheduled_for)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_related_links_source_post ON related_links_suggestions(source_post_id)"
        )
        conn.commit()


def save_log(
    url: str,
    date_published: Optional[str],
    status: str,
    engine: str,
    response: Any,
    date_sent: Optional[str] = None,
) -> None:
    payload = response
    if not isinstance(payload, str):
        try:
            payload = json.dumps(response, ensure_ascii=False)
        except Exception:
            payload = str(response)

    with _get_conn() as conn:
        conn.execute(
            """
            INSERT INTO indexed_urls (url, date_published, date_sent, status, engine, response)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(url, engine) DO UPDATE SET
                date_published = excluded.date_published,
                date_sent = excluded.date_sent,
                status = excluded.status,
                response = excluded.response
            """,
            (url, date_published, date_sent, status, engine, payload),
        )
        conn.commit()


def _upsert_published_post(post: Dict[str, Any], source: str, notify_status: str = "pending") -> None:
    now = _utc_now_iso()
    with _get_conn() as conn:
        conn.execute(
            """
            INSERT INTO published_posts (
                wp_post_id, url, title, slug, date_published, categories_json, tags_json,
                source, notify_status, last_error, notified_at, processed_at, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, NULL, ?, ?)
            ON CONFLICT(url) DO UPDATE SET
                wp_post_id = COALESCE(excluded.wp_post_id, published_posts.wp_post_id),
                title = COALESCE(excluded.title, published_posts.title),
                slug = COALESCE(excluded.slug, published_posts.slug),
                date_published = COALESCE(excluded.date_published, published_posts.date_published),
                categories_json = COALESCE(excluded.categories_json, published_posts.categories_json),
                tags_json = COALESCE(excluded.tags_json, published_posts.tags_json),
                source = excluded.source,
                notify_status = CASE
                    WHEN published_posts.notify_status = 'sent' AND excluded.notify_status = 'pending'
                        THEN published_posts.notify_status
                    ELSE excluded.notify_status
                END,
                notified_at = COALESCE(excluded.notified_at, published_posts.notified_at),
                updated_at = excluded.updated_at
            """,
            (
                post.get("id"),
                post.get("url"),
                _strip_html(post.get("title")),
                post.get("slug"),
                _normalize_datetime(post.get("date_published")),
                _json_dumps(post.get("categories") or []),
                _json_dumps(post.get("tags") or []),
                source,
                notify_status,
                now,
                now,
                now,
            ),
        )
        conn.commit()


def _set_published_post_status(url: str, status: str, error: Optional[str] = None) -> None:
    with _get_conn() as conn:
        conn.execute(
            """
            UPDATE published_posts
            SET notify_status = ?, last_error = ?, processed_at = ?, updated_at = ?
            WHERE url = ?
            """,
            (status, error, _utc_now_iso(), _utc_now_iso(), url),
        )
        conn.commit()


def _load_pending_published_posts(limit: int = 50) -> List[Dict[str, Any]]:
    with _get_conn() as conn:
        rows = conn.execute(
            """
            SELECT wp_post_id, url, title, slug, date_published, categories_json, tags_json, source
            FROM published_posts
            WHERE notify_status IN ('pending', 'error')
            ORDER BY created_at ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    posts: List[Dict[str, Any]] = []
    for row in rows:
        posts.append(
            {
                "id": row["wp_post_id"],
                "url": row["url"],
                "title": row["title"] or "",
                "slug": row["slug"] or "",
                "date_published": row["date_published"],
                "categories": json.loads(row["categories_json"] or "[]"),
                "tags": json.loads(row["tags_json"] or "[]"),
                "source": row["source"] or "unknown",
            }
        )
    return posts


def _already_sent(url: str, engine: str) -> bool:
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM indexed_urls WHERE url = ? AND engine = ? AND status = 'sent'",
            (url, engine),
        ).fetchone()
    return row is not None


def _infer_public_site_url(api_url: str) -> str:
    if not api_url:
        return ""
    parsed = urlparse(api_url)
    path = parsed.path or ""
    if "/wp-json" in path:
        path = path.split("/wp-json", 1)[0]
    normalized = parsed._replace(path=path.rstrip("/"), params="", query="", fragment="")
    return urlunparse(normalized).rstrip("/")


def _get_site_url() -> str:
    explicit = (os.getenv("WORDPRESS_SITE_URL") or "").rstrip("/")
    if explicit:
        return explicit
    return _infer_public_site_url(WORDPRESS_API_URL)


def _get_sitemap_url() -> str:
    explicit = (os.getenv("WORDPRESS_SITEMAP_URL") or "").strip()
    if explicit:
        return explicit
    site_url = _get_site_url()
    return f"{site_url}/sitemap_index.xml" if site_url else ""


def _get_feed_url() -> str:
    explicit = (os.getenv("WORDPRESS_FEED_URL") or "").strip()
    if explicit:
        return explicit
    site_url = _get_site_url()
    return f"{site_url}/feed/" if site_url else ""


def _get_search_console_site_url() -> str:
    explicit = (GSC_PROPERTY or GOOGLE_URL_INSPECTION_SITE_URL).strip()
    if explicit:
        if explicit.startswith("sc-domain:"):
            return explicit
        if "://" not in explicit and "/" not in explicit:
            return f"sc-domain:{explicit}"
        return explicit if explicit.endswith("/") else f"{explicit}/"
    site_url = _get_site_url().strip()
    if not site_url:
        return ""
    parsed = urlparse(site_url)
    hostname = (parsed.hostname or "").lower()
    if hostname:
        if hostname.startswith("www."):
            hostname = hostname[4:]
        return f"sc-domain:{hostname}"
    return site_url if site_url.endswith("/") else f"{site_url}/"


def _requests_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": "MN26-Indexer/1.0"})
    if WORDPRESS_USER and WORDPRESS_PASSWORD:
        session.auth = (WORDPRESS_USER, WORDPRESS_PASSWORD)
    return session


def _last_published_checkpoint() -> Optional[str]:
    with _get_conn() as conn:
        row = conn.execute("SELECT MAX(date_published) AS max_date FROM indexed_urls").fetchone()
    max_date = row["max_date"] if row else None
    if not max_date:
        return None
    try:
        checkpoint = datetime.fromisoformat(max_date.replace("Z", "+00:00")) - timedelta(minutes=10)
        return checkpoint.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except Exception:
        return None


def get_new_posts(limit: int = INDEXER_POST_LIMIT) -> List[Dict[str, Any]]:
    if not WORDPRESS_API_URL:
        raise ValueError("WORDPRESS_URL nao configurada para o indexador.")

    endpoint = f"{WORDPRESS_API_URL}/posts"
    params = {
        "status": "publish",
        "orderby": "date",
        "order": "desc",
        "per_page": limit,
        "_fields": "id,date,date_gmt,modified,modified_gmt,link,slug,status,title,categories,tags",
    }
    if checkpoint := _last_published_checkpoint():
        params["after"] = checkpoint

    response = _requests_session().get(endpoint, params=params, timeout=INDEXER_TIMEOUT)
    response.raise_for_status()

    posts = []
    for item in response.json():
        link = (item.get("link") or "").strip()
        if not link:
            continue
        title_obj = item.get("title") or {}
        title = title_obj.get("rendered", "") if isinstance(title_obj, dict) else str(title_obj)
        post = {
            "id": item.get("id"),
            "url": link,
            "date_published": item.get("date_gmt") or item.get("date") or item.get("modified_gmt") or item.get("modified"),
            "title": _strip_html(title),
            "slug": item.get("slug", ""),
            "categories": item.get("categories") or [],
            "tags": item.get("tags") or [],
        }
        posts.append(post)
        _upsert_published_post(post, source="rest_polling", notify_status="pending")

    logger.info("[INDEX_DISCOVERY] WordPress retornou %s posts novos/candidatos para indexacao.", len(posts))
    return posts


def _google_access_token(scopes: Optional[List[str]] = None) -> str:
    if not GOOGLE_INDEXING_CREDENTIALS_FILE:
        raise ValueError("GOOGLE_INDEXING_CREDENTIALS_FILE nao configurado.")
    if service_account is None or GoogleAuthRequest is None:
        raise RuntimeError("Dependencia google-auth indisponivel.")

    requested_scopes = scopes or ["https://www.googleapis.com/auth/indexing"]
    logger.info(
        "[GOOGLE_AUTH] credentials_file=%s scopes=%s",
        GOOGLE_INDEXING_CREDENTIALS_FILE,
        ",".join(requested_scopes),
    )
    credentials = service_account.Credentials.from_service_account_file(
        GOOGLE_INDEXING_CREDENTIALS_FILE,
        scopes=requested_scopes,
    )
    credentials.refresh(GoogleAuthRequest())
    return credentials.token


def send_to_google_indexing_api(post: Dict[str, Any], force: bool = False) -> Tuple[bool, str]:
    url = post["url"]
    engine = "google_indexing"

    if not force and _already_sent(url, engine):
        logger.info("Google Indexing API ja enviado para %s", url)
        return True, "duplicate_skipped"

    if not GOOGLE_INDEXING_ENABLED:
        message = "Google Indexing API desabilitada por configuracao."
        save_log(url, post.get("date_published"), "disabled", engine, message, _utc_now_iso())
        return False, message

    save_log(url, post.get("date_published"), "pending", engine, {"url": url, "force": force})

    try:
        token = _google_access_token()
        payload = {"url": url, "type": "URL_UPDATED"}
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        response = requests.post(
            GOOGLE_INDEXING_ENDPOINT,
            headers=headers,
            json=payload,
            timeout=INDEXER_TIMEOUT,
        )
        status = "sent" if response.ok else "error"
        response_body = {
            "status_code": response.status_code,
            "body": response.text[:1000],
            "force": force,
            "endpoint": GOOGLE_INDEXING_ENDPOINT,
        }
        save_log(url, post.get("date_published"), status, engine, response_body, _utc_now_iso())
        if response.ok:
            logger.info(
                "[GOOGLE_INDEXING] ok=true status=%s endpoint=%s url=%s",
                response.status_code,
                GOOGLE_INDEXING_ENDPOINT,
                url,
            )
        else:
            logger.error(
                "[GOOGLE_INDEXING] ok=false status=%s endpoint=%s body=%s url=%s",
                response.status_code,
                GOOGLE_INDEXING_ENDPOINT,
                response.text[:500],
                url,
            )
        return response.ok, response.text
    except Exception as exc:
        save_log(url, post.get("date_published"), "error", engine, str(exc), _utc_now_iso())
        logger.error("Falha ao enviar %s para Google Indexing API: %s", url, exc)
        return False, str(exc)


def inspect_google_url_status(post: Dict[str, Any], force: bool = False) -> Tuple[bool, Dict[str, Any]]:
    url = post["url"]
    engine = "google_url_inspection"

    if not force and _already_sent(url, engine):
        logger.info("Google URL Inspection ja consultado para %s", url)
        return True, {"duplicate_skipped": True}

    if not GOOGLE_URL_INSPECTION_ENABLED:
        message = "Google URL Inspection desabilitado por configuracao."
        save_log(url, post.get("date_published"), "disabled", engine, message, _utc_now_iso())
        return False, {"message": message}

    site_url = _get_search_console_site_url()
    if not site_url:
        message = "GOOGLE_URL_INSPECTION_SITE_URL/WORDPRESS_SITE_URL nao configurado."
        save_log(url, post.get("date_published"), "error", engine, message, _utc_now_iso())
        return False, {"message": message}

    save_log(
        url,
        post.get("date_published"),
        "pending",
        engine,
        {"url": url, "siteUrl": site_url, "force": force},
        _utc_now_iso(),
    )

    try:
        token = _google_access_token(
            scopes=["https://www.googleapis.com/auth/webmasters.readonly"]
        )
        payload = {
            "inspectionUrl": url,
            "siteUrl": site_url,
            "languageCode": GOOGLE_URL_INSPECTION_LANGUAGE_CODE,
        }
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        response = requests.post(
            GOOGLE_URL_INSPECTION_ENDPOINT,
            headers=headers,
            json=payload,
            timeout=INDEXER_TIMEOUT,
        )
        response_json: Dict[str, Any]
        try:
            response_json = response.json() if response.text.strip() else {}
        except Exception:
            response_json = {"raw_body": response.text[:2000]}

        inspection = response_json.get("inspectionResult") or {}
        index_status = inspection.get("indexStatusResult") or {}
        parsed = {
            "status_code": response.status_code,
            "inspectionResultLink": inspection.get("inspectionResultLink"),
            "verdict": index_status.get("verdict"),
            "coverageState": index_status.get("coverageState"),
            "indexingState": index_status.get("indexingState"),
            "lastCrawlTime": index_status.get("lastCrawlTime"),
            "pageFetchState": index_status.get("pageFetchState"),
            "robotsTxtState": index_status.get("robotsTxtState"),
            "googleCanonical": index_status.get("googleCanonical"),
            "userCanonical": index_status.get("userCanonical"),
            "referringUrls": index_status.get("referringUrls") or [],
            "sitemap": index_status.get("sitemap") or [],
            "raw": response_json,
        }
        status = "sent" if response.ok else "error"
        save_log(url, post.get("date_published"), status, engine, parsed, _utc_now_iso())
        if response.ok and not index_status:
            logger.error(
                "[GOOGLE_INSPECTION] empty_index_status status=%s endpoint=%s siteUrl=%s body=%s url=%s",
                response.status_code,
                GOOGLE_URL_INSPECTION_ENDPOINT,
                site_url,
                str(response_json)[:1000],
                url,
            )
        elif not response.ok:
            logger.error(
                "[GOOGLE_INSPECTION] ok=false status=%s endpoint=%s siteUrl=%s body=%s url=%s",
                response.status_code,
                GOOGLE_URL_INSPECTION_ENDPOINT,
                site_url,
                str(response_json)[:1000],
                url,
            )
        else:
            logger.info(
                "[GOOGLE_INSPECTION] url=%s | verdict=%s | coverage=%s | indexing=%s | fetch=%s",
                url,
                parsed.get("verdict"),
                parsed.get("coverageState"),
                parsed.get("indexingState"),
                parsed.get("pageFetchState"),
            )
        return response.ok, parsed
    except Exception as exc:
        save_log(url, post.get("date_published"), "error", engine, str(exc), _utc_now_iso())
        logger.error("Falha ao inspecionar %s na URL Inspection API: %s", url, exc)
        return False, {"message": str(exc)}


def send_to_indexnow(post: Dict[str, Any], force: bool = False) -> Tuple[bool, str]:
    url = post["url"]
    engine = "indexnow"

    if not force and _already_sent(url, engine):
        logger.info("IndexNow ja enviado para %s", url)
        return True, "duplicate_skipped"

    if not INDEXNOW_ENABLED:
        message = "IndexNow desabilitado por configuracao."
        save_log(url, post.get("date_published"), "disabled", engine, message, _utc_now_iso())
        return False, message

    if not INDEXNOW_KEY:
        message = "INDEXNOW_KEY nao configurada."
        save_log(url, post.get("date_published"), "error", engine, message, _utc_now_iso())
        return False, message

    save_log(url, post.get("date_published"), "pending", engine, {"url": url, "force": force})

    try:
        parsed = urlparse(url)
        key_location = INDEXNOW_KEY_LOCATION or f"{parsed.scheme}://{parsed.netloc}/{INDEXNOW_KEY}.txt"
        payload = {
            "host": parsed.netloc,
            "key": INDEXNOW_KEY,
            "keyLocation": key_location,
            "urlList": [url],
        }
        response = requests.post(
            INDEXNOW_ENDPOINT,
            json=payload,
            headers={"Content-Type": "application/json; charset=utf-8"},
            timeout=INDEXER_TIMEOUT,
        )
        accepted = response.status_code in {200, 202}
        status = "sent" if accepted else "error"
        save_log(
            url,
            post.get("date_published"),
            status,
            engine,
            {"status_code": response.status_code, "body": response.text[:1000], "force": force},
            _utc_now_iso(),
        )
        return accepted, response.text
    except Exception as exc:
        save_log(url, post.get("date_published"), "error", engine, str(exc), _utc_now_iso())
        logger.error("Falha ao enviar %s para IndexNow: %s", url, exc)
        return False, str(exc)


def _parse_ping_endpoints(raw_value: str) -> List[Tuple[str, str]]:
    endpoints: List[Tuple[str, str]] = []
    for part in raw_value.split(";"):
        chunk = part.strip()
        if not chunk or "=" not in chunk:
            continue
        engine, template = chunk.split("=", 1)
        endpoints.append((engine.strip(), template.strip()))
    return endpoints


def _is_obsolete_sitemap_ping_endpoint(template: str) -> bool:
    parsed = urlparse(template)
    host = (parsed.netloc or "").lower()
    path = (parsed.path or "").rstrip("/")
    return (
        host in {"www.google.com", "google.com", "www.bing.com", "bing.com"}
        and path == "/ping"
    )


def ping_sitemap(post: Dict[str, Any]) -> List[Tuple[str, bool, str]]:
    results: List[Tuple[str, bool, str]] = []
    url = post["url"]
    sitemap_url = _get_sitemap_url()

    if not SITEMAP_PING_ENABLED:
        return []

    if not sitemap_url:
        save_log(url, post.get("date_published"), "error", "sitemap_ping", "WORDPRESS_SITEMAP_URL nao configurada.", _utc_now_iso())
        return [("sitemap_ping", False, "missing_sitemap_url")]

    for engine, template in _parse_ping_endpoints(SITEMAP_PING_ENDPOINTS):
        if _is_obsolete_sitemap_ping_endpoint(template):
            logger.info("[SITEMAP_PING] endpoint obsoleto ignorado | engine=%s | endpoint=%s", engine, template)
            continue
        if _already_sent(url, engine):
            results.append((engine, True, "duplicate_skipped"))
            continue

        target_url = template.format(sitemap=quote_plus(sitemap_url))
        save_log(url, post.get("date_published"), "pending", engine, {"endpoint": target_url})
        try:
            response = requests.get(target_url, timeout=INDEXER_TIMEOUT)
            status = "sent" if response.ok else "error"
            save_log(
                url,
                post.get("date_published"),
                status,
                engine,
                {"status_code": response.status_code, "body": response.text[:1000]},
                _utc_now_iso(),
            )
            results.append((engine, response.ok, response.text))
        except Exception as exc:
            save_log(url, post.get("date_published"), "error", engine, str(exc), _utc_now_iso())
            results.append((engine, False, str(exc)))

    return results


def _classify_rss_ping_response(response: Any) -> Tuple[bool, str, str]:
    if isinstance(response, dict):
        flerror = response.get("flerror")
        message = str(response.get("message") or response.get("faultString") or response)
        if flerror is True or str(flerror).lower() == "true":
            lowered = message.lower()
            if "too fast" in lowered or "slow down" in lowered or "limit" in lowered or "thrott" in lowered:
                return False, "throttled", message
            return False, "error", message
        return True, "sent", message

    message = str(response)
    lowered = message.lower()
    if "flerror" in lowered and "true" in lowered:
        if "too fast" in lowered or "slow down" in lowered or "limit" in lowered or "thrott" in lowered:
            return False, "throttled", message
        return False, "error", message
    if "pinging too fast" in lowered or "slow down" in lowered:
        return False, "throttled", message
    if "fault" in lowered or "error" in lowered:
        return False, "error", message
    return True, "sent", message


def ping_rss_feed(post: Dict[str, Any]) -> Tuple[bool, str]:
    url = post["url"]
    engine = "rss_ping"
    site_url = _get_site_url()
    feed_url = _get_feed_url()

    if _already_sent(url, engine):
        return True, "duplicate_skipped"

    if not RSS_PING_ENABLED:
        message = "RSS ping desabilitado por configuracao."
        save_log(url, post.get("date_published"), "disabled", engine, message, _utc_now_iso())
        return False, message

    if not site_url or not feed_url:
        message = "WORDPRESS_SITE_URL/WORDPRESS_FEED_URL nao configuradas."
        save_log(url, post.get("date_published"), "error", engine, message, _utc_now_iso())
        return False, message

    save_log(url, post.get("date_published"), "pending", engine, {"feed_url": feed_url})

    try:
        server = xmlrpc.client.ServerProxy(RSS_PING_XMLRPC_URL)
        try:
            response = server.weblogUpdates.extendedPing(
                WORDPRESS_SITE_NAME,
                site_url,
                url,
                feed_url,
            )
        except Exception:
            response = server.weblogUpdates.ping(WORDPRESS_SITE_NAME, site_url)

        ok, status, message = _classify_rss_ping_response(response)
        save_log(url, post.get("date_published"), status, engine, response, _utc_now_iso())
        return ok, message
    except Exception as exc:
        save_log(url, post.get("date_published"), "error", engine, str(exc), _utc_now_iso())
        logger.error("Falha ao pingar feed RSS para %s: %s", url, exc)
        return False, str(exc)


def _extract_sitemap_locs(root: ET.Element) -> List[str]:
    locs: List[str] = []
    for elem in root.iter():
        if elem.tag.split("}")[-1] == "loc" and elem.text:
            locs.append(elem.text.strip())
    return locs


def _fetch_sitemap_urls_recursive(url: str, depth: int = 0, max_depth: int = 1) -> List[str]:
    response = requests.get(url, timeout=YOAST_NEWS_SITEMAP_TIMEOUT)
    response.raise_for_status()
    root = ET.fromstring(response.content)
    root_name = root.tag.split("}")[-1]
    locs = _extract_sitemap_locs(root)

    if root_name == "urlset":
        return [_normalize_url_for_match(loc) for loc in locs if loc]

    if root_name == "sitemapindex" and depth < max_depth:
        urls: List[str] = []
        for child_url in locs[:10]:
            try:
                urls.extend(_fetch_sitemap_urls_recursive(child_url, depth + 1, max_depth))
            except Exception as exc:
                logger.warning("[YOAST_NEWS] Falha ao ler sitemap filho %s: %s", child_url, exc)
        return urls

    return [_normalize_url_for_match(loc) for loc in locs if loc]


def fetch_yoast_news_sitemap() -> Dict[str, Any]:
    if not YOAST_NEWS_SITEMAP_ENABLED:
        return {"enabled": False, "urls": [], "source": ""}
    if not YOAST_NEWS_SITEMAP_URL:
        raise ValueError("YOAST_NEWS_SITEMAP_URL nao configurada.")

    urls = _fetch_sitemap_urls_recursive(YOAST_NEWS_SITEMAP_URL)
    deduped = sorted(set(urls))
    logger.info(
        "[YOAST_NEWS] sitemap carregado | source=%s | urls=%s",
        YOAST_NEWS_SITEMAP_URL,
        len(deduped),
    )
    return {"enabled": True, "urls": deduped, "source": YOAST_NEWS_SITEMAP_URL}


def validate_post_in_yoast_news_sitemap(url: str, date_published: Optional[str] = None) -> Tuple[bool, str]:
    engine = "yoast_news_sitemap"

    if not YOAST_NEWS_SITEMAP_ENABLED:
        message = "Yoast News Sitemap desabilitado por configuracao."
        save_log(url, date_published, "disabled", engine, message, _utc_now_iso())
        return False, message

    if not YOAST_NEWS_SITEMAP_VALIDATE_RECENT_POSTS:
        message = "Validacao do Yoast News Sitemap desabilitada."
        save_log(url, date_published, "disabled", engine, message, _utc_now_iso())
        return False, message

    normalized = _normalize_url_for_match(url)
    try:
        payload = fetch_yoast_news_sitemap()
        urls = payload.get("urls") or []
        present = normalized in urls
        status = "sent" if present else "pending"
        response = {
            "present": present,
            "checked_url": normalized,
            "source": payload.get("source"),
            "url_count": len(urls),
        }
        save_log(url, date_published, status, engine, response, _utc_now_iso())
        logger.info(
            "[YOAST_NEWS] validacao | present=%s | url=%s | total_urls=%s",
            present,
            normalized,
            len(urls),
        )
        return present, "present" if present else "missing"
    except Exception as exc:
        save_log(url, date_published, "error", engine, str(exc), _utc_now_iso())
        logger.warning("[YOAST_NEWS] Falha ao validar %s no sitemap: %s", normalized, exc)
        return False, str(exc)


def schedule_recrawl(post_id: Optional[int], url: str, delay_minutes: Optional[int] = None) -> bool:
    if not RECRAWL_ENABLED or not url:
        return False

    delay = delay_minutes if delay_minutes is not None else RECRAWL_DELAY_MINUTES
    scheduled_for = (datetime.now(timezone.utc) + timedelta(minutes=delay)).isoformat()

    with _get_conn() as conn:
        existing = conn.execute(
            """
            SELECT id FROM recrawl_queue
            WHERE url = ? AND status IN ('scheduled', 'processing')
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (url,),
        ).fetchone()
        if existing:
            logger.info("[RECRAWL] ja existe fila pendente para %s", url)
            return False

        conn.execute(
            """
            INSERT INTO recrawl_queue (
                post_id, url, scheduled_for, executed_at, status, attempts, response, created_at
            ) VALUES (?, ?, ?, NULL, 'scheduled', 0, NULL, ?)
            """,
            (post_id, url, scheduled_for, _utc_now_iso()),
        )
        conn.commit()

    logger.info(
        "[RECRAWL] agendado | post_id=%s | url=%s | scheduled_for=%s",
        post_id,
        url,
        scheduled_for,
    )
    return True


def get_due_recrawls(limit: int = 50) -> List[Dict[str, Any]]:
    with _get_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, post_id, url, scheduled_for, executed_at, status, attempts, response, created_at
            FROM recrawl_queue
            WHERE status = 'scheduled' AND scheduled_for <= ? AND attempts < ?
            ORDER BY scheduled_for ASC
            LIMIT ?
            """,
            (_utc_now_iso(), RECRAWL_MAX_ATTEMPTS, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def process_recrawl_queue(limit: int = 50) -> int:
    if not RECRAWL_ENABLED:
        return 0

    due_items = get_due_recrawls(limit=limit)
    if not due_items:
        return 0

    processed = 0
    for item in due_items:
        with _get_conn() as conn:
            conn.execute(
                "UPDATE recrawl_queue SET status = 'processing', attempts = attempts + 1 WHERE id = ?",
                (item["id"],),
            )
            conn.commit()

        post = {
            "id": item.get("post_id"),
            "url": item["url"],
            "date_published": item.get("created_at"),
            "title": "",
        }
        logger.info("[RECRAWL] processando | id=%s | url=%s", item["id"], item["url"])

        google_ok, google_msg = send_to_google_indexing_api(post, force=True)
        indexnow_ok, indexnow_msg = send_to_indexnow(post, force=True)
        rss_ok, rss_msg = ping_rss_feed(post)
        present, yoast_msg = validate_post_in_yoast_news_sitemap(item["url"], item.get("created_at"))
        inspection_ok, inspection_result = inspect_google_url_status(post, force=True)

        discovery_ok = bool(present)
        supported_recrawl_ok = discovery_ok or rss_ok
        final_status = "sent" if supported_recrawl_ok else "error"
        response_payload = {
            "google": google_msg,
            "google_indexing_role": "best_effort_diagnostic",
            "indexnow": indexnow_msg,
            "rss": rss_msg,
            "rss_ok": rss_ok,
            "supported_recrawl_ok": supported_recrawl_ok,
            "discovery_ok": discovery_ok,
            "yoast_news_present": present,
            "yoast_news_result": yoast_msg,
            "google_url_inspection": inspection_result,
            "google_url_inspection_ok": inspection_ok,
        }

        with _get_conn() as conn:
            conn.execute(
                """
                UPDATE recrawl_queue
                SET status = ?, executed_at = ?, response = ?
                WHERE id = ?
                """,
                (final_status, _utc_now_iso(), _json_dumps(response_payload), item["id"]),
            )
            conn.commit()

        logger.info(
            "[RECRAWL] concluido | id=%s | status=%s | url=%s",
            item["id"],
            final_status,
            item["url"],
        )
        processed += 1

    return processed


def _tokenize_title(title: str) -> List[str]:
    tokens = re.findall(r"[A-Za-zÀ-ÿ0-9]{4,}", _strip_html(title).lower())
    return [token for token in tokens if token not in TITLE_TOKEN_STOPWORDS]


def get_related_posts_for_discovery(post: Dict[str, Any], limit: int = DISCOVERY_RELATED_LINKS_LIMIT) -> List[Dict[str, Any]]:
    if not DISCOVERY_RELATED_LINKS_ENABLED or not WORDPRESS_API_URL:
        return []

    current_id = post.get("id")
    current_url = _normalize_url_for_match(post.get("url", ""))
    current_categories = set(_safe_int_list(post.get("categories")))
    current_tags = set(_safe_int_list(post.get("tags")))
    title_tokens = _tokenize_title(post.get("title", ""))

    candidates: Dict[str, Dict[str, Any]] = {}
    session = _requests_session()
    endpoint = f"{WORDPRESS_API_URL}/posts"
    fields = "id,link,title,categories,tags"

    query_specs: List[Dict[str, Any]] = []
    if current_categories:
        query_specs.append({"categories": ",".join(str(value) for value in sorted(current_categories))})
    if current_tags:
        query_specs.append({"tags": ",".join(str(value) for value in sorted(current_tags))})
    if title_tokens:
        query_specs.append({"search": " ".join(title_tokens[:4])})

    for params in query_specs:
        request_params = {
            "status": "publish",
            "per_page": max(limit * 2, 6),
            "_fields": fields,
        }
        request_params.update(params)
        try:
            response = session.get(endpoint, params=request_params, timeout=INDEXER_TIMEOUT)
            response.raise_for_status()
            for item in response.json():
                target_url = _normalize_url_for_match(item.get("link", ""))
                target_id = item.get("id")
                if not target_url or target_url == current_url or (current_id and target_id == current_id):
                    continue
                title_obj = item.get("title") or {}
                target_title = title_obj.get("rendered", "") if isinstance(title_obj, dict) else str(title_obj)
                target_categories = set(_safe_int_list(item.get("categories")))
                target_tags = set(_safe_int_list(item.get("tags")))
                shared_categories = len(current_categories & target_categories)
                shared_tags = len(current_tags & target_tags)
                shared_title_terms = len(set(title_tokens) & set(_tokenize_title(target_title)))
                score = (shared_categories * 3.0) + (shared_tags * 2.0) + shared_title_terms
                if score <= 0:
                    continue
                existing = candidates.get(target_url)
                payload = {
                    "target_post_id": target_id,
                    "target_url": target_url,
                    "title": _strip_html(target_title),
                    "relevance_score": score,
                }
                if existing is None or payload["relevance_score"] > existing["relevance_score"]:
                    candidates[target_url] = payload
        except Exception as exc:
            logger.warning("[DISCOVERY] Falha buscando relacionados para %s: %s", current_url, exc)

    ordered = sorted(candidates.values(), key=lambda item: item["relevance_score"], reverse=True)
    return ordered[:limit]


def build_related_link_suggestions(post: Dict[str, Any], related_posts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    suggestions: List[Dict[str, Any]] = []
    created_at = _utc_now_iso()
    for item in related_posts:
        suggestions.append(
            {
                "source_post_id": post.get("id"),
                "source_url": post.get("url"),
                "target_post_id": item.get("target_post_id"),
                "target_url": item.get("target_url"),
                "anchor_text": (item.get("title") or item.get("target_url") or "")[:190],
                "relevance_score": float(item.get("relevance_score", 0.0)),
                "status": "suggested",
                "created_at": created_at,
            }
        )
    return suggestions


def save_related_link_suggestions(suggestions: List[Dict[str, Any]]) -> int:
    saved = 0
    with _get_conn() as conn:
        for item in suggestions:
            conn.execute(
                """
                INSERT INTO related_links_suggestions (
                    source_post_id, source_url, target_post_id, target_url,
                    anchor_text, relevance_score, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_post_id, target_post_id) DO UPDATE SET
                    anchor_text = excluded.anchor_text,
                    relevance_score = excluded.relevance_score,
                    status = excluded.status
                """,
                (
                    item.get("source_post_id"),
                    item.get("source_url"),
                    item.get("target_post_id"),
                    item.get("target_url"),
                    item.get("anchor_text"),
                    item.get("relevance_score"),
                    item.get("status"),
                    item.get("created_at"),
                ),
            )
            saved += 1
        conn.commit()
    return saved


def _generate_related_link_suggestions(post: Dict[str, Any]) -> int:
    if not DISCOVERY_RELATED_LINKS_ENABLED:
        return 0
    related = get_related_posts_for_discovery(post)
    suggestions = build_related_link_suggestions(post, related)
    saved = save_related_link_suggestions(suggestions)
    logger.info(
        "[DISCOVERY] sugestoes geradas | post_id=%s | url=%s | suggestions=%s",
        post.get("id"),
        post.get("url"),
        saved,
    )
    return saved


def _process_post(post: Dict[str, Any], trigger: str = "rest_polling") -> Dict[str, Any]:
    logger.info(
        "[INDEX_TRIGGER] trigger=%s | post_id=%s | url=%s",
        trigger,
        post.get("id"),
        post.get("url"),
    )

    google_ok, google_msg = send_to_google_indexing_api(post)
    indexnow_ok, indexnow_msg = send_to_indexnow(post)
    sitemap_results = ping_sitemap(post)
    rss_ok, rss_msg = ping_rss_feed(post)
    yoast_present, yoast_msg = validate_post_in_yoast_news_sitemap(
        post["url"],
        post.get("date_published"),
    )
    inspection_ok, inspection_result = inspect_google_url_status(post)

    supported_notification_ok = rss_ok or google_ok
    discovery_ok = bool(yoast_present)
    any_success = supported_notification_ok or discovery_ok
    logger.info(
        "[INDEX_STATUS] status=%s | notify_ok=%s | discovery_ok=%s | "
        "rss=%s | google_indexing_best_effort=%s | url=%s",
        "sent" if any_success else "error",
        supported_notification_ok,
        discovery_ok,
        rss_ok,
        google_ok,
        post.get("url"),
    )
    return {
        "google_ok": google_ok,
        "google_msg": google_msg,
        "google_indexing_role": "best_effort_diagnostic",
        "indexnow_ok": indexnow_ok,
        "indexnow_msg": indexnow_msg,
        "sitemap_results": sitemap_results,
        "rss_ok": rss_ok,
        "rss_msg": rss_msg,
        "yoast_news_present": yoast_present,
        "yoast_news_msg": yoast_msg,
        "discovery_ok": discovery_ok,
        "supported_notification_ok": supported_notification_ok,
        "google_url_inspection_ok": inspection_ok,
        "google_url_inspection": inspection_result,
        "status": "sent" if any_success else "error",
    }


def process_pending_notifications(limit: int = 50) -> int:
    pending = _load_pending_published_posts(limit=limit)
    if not pending:
        return 0

    processed = 0
    for post in pending:
        _set_published_post_status(post["url"], "processing")
        try:
            result = _process_post(post, trigger="publish_queue")
            _set_published_post_status(
                post["url"],
                result["status"],
                None if result["status"] == "sent" else _json_dumps(result),
            )
        except Exception as exc:
            _set_published_post_status(post["url"], "error", str(exc))
            logger.error("[INDEX_NOTIFY] Falha processando fila pendente %s: %s", post["url"], exc)
        processed += 1

    return processed


def notify_post_published(
    *,
    url: str,
    post_id: Optional[int] = None,
    title: str = "",
    date_published: Optional[str] = None,
    slug: str = "",
    categories: Optional[List[Any]] = None,
    tags: Optional[List[Any]] = None,
) -> Dict[str, Any]:
    initialize_database()

    post = {
        "id": post_id,
        "url": (url or "").strip(),
        "title": _strip_html(title),
        "date_published": _normalize_datetime(date_published) or _utc_now_iso(),
        "slug": slug or "",
        "categories": categories or [],
        "tags": tags or [],
    }

    if not post["url"]:
        return {"ok": False, "reason": "missing_url"}

    _upsert_published_post(post, source="mn26_publish", notify_status="pending")

    logger.info(
        "[INDEX_NOTIFY] publicacao recebida | post_id=%s | url=%s | title=%s",
        post.get("id"),
        post["url"],
        post.get("title", "")[:80],
    )

    suggestions_saved = _generate_related_link_suggestions(post)
    recrawl_scheduled = False
    if RECRAWL_ENABLED and RECRAWL_INCLUDE_INITIAL_PUBLISH:
        recrawl_scheduled = schedule_recrawl(post.get("id"), post["url"])

    if not INDEXER_NOTIFY_ON_PUBLISH or not INDEXER_NOTIFY_PROCESS_IMMEDIATELY:
        return {
            "ok": True,
            "queued": True,
            "processed": False,
            "suggestions_saved": suggestions_saved,
            "recrawl_scheduled": recrawl_scheduled,
        }

    try:
        result = _process_post(post, trigger="publish_notify")
        _set_published_post_status(
            post["url"],
            result["status"],
            None if result["status"] == "sent" else _json_dumps(result),
        )
        return {
            "ok": result["status"] == "sent",
            "queued": True,
            "processed": True,
            "result": result,
            "suggestions_saved": suggestions_saved,
            "recrawl_scheduled": recrawl_scheduled,
        }
    except Exception as exc:
        _set_published_post_status(post["url"], "error", str(exc))
        logger.error("[INDEX_NOTIFY] Falha no processamento imediato de %s: %s", post["url"], exc)
        return {
            "ok": False,
            "queued": True,
            "processed": False,
            "error": str(exc),
            "suggestions_saved": suggestions_saved,
            "recrawl_scheduled": recrawl_scheduled,
        }


def run_indexing_cycle() -> None:
    initialize_database()
    queued_processed = process_pending_notifications()
    recrawls_processed = process_recrawl_queue()
    logger.info(
        "[INDEX_CYCLE] fila_processada=%s | recrawls_processados=%s",
        queued_processed,
        recrawls_processed,
    )

    posts = get_new_posts()
    if not posts:
        logger.info("[INDEX_DISCOVERY] Nenhum post novo encontrado via REST.")
        return

    for post in posts:
        try:
            _generate_related_link_suggestions(post)
            if RECRAWL_ENABLED and RECRAWL_INCLUDE_INITIAL_PUBLISH:
                schedule_recrawl(post.get("id"), post["url"])
            result = _process_post(post, trigger="rest_polling")
            _set_published_post_status(
                post["url"],
                result["status"],
                None if result["status"] == "sent" else _json_dumps(result),
            )
        except Exception as exc:
            _set_published_post_status(post["url"], "error", str(exc))
            logger.error("[INDEX_DISCOVERY] Falha processando %s: %s", post["url"], exc)


def inspect_single_url(url: str, date_published: Optional[str] = None, force: bool = True) -> Dict[str, Any]:
    initialize_database()
    post = {
        "id": None,
        "url": (url or "").strip(),
        "title": "",
        "date_published": _normalize_datetime(date_published) or _utc_now_iso(),
        "slug": "",
        "categories": [],
        "tags": [],
    }
    if not post["url"]:
        raise ValueError("URL obrigatoria para inspecao.")
    ok, result = inspect_google_url_status(post, force=force)
    return {"ok": ok, "url": post["url"], "inspection": result}


def scheduler() -> None:
    logger.info("Indexador iniciado. Intervalo: %s minutos.", INDEXER_INTERVAL_MINUTES)
    run_indexing_cycle()

    job_scheduler = BlockingScheduler(timezone="America/Sao_Paulo")
    job_scheduler.add_job(run_indexing_cycle, "interval", minutes=INDEXER_INTERVAL_MINUTES, id="indexing_cycle")
    logger.info("Agendador de indexacao ativo. Pressione Ctrl+C para sair.")
    try:
        job_scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Agendador de indexacao interrompido.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Executa o indexador instantaneo do Google/IndexNow.")
    parser.add_argument("--once", action="store_true", help="Executa um ciclo unico e sai.")
    parser.add_argument("--inspect-url", help="Consulta a URL Inspection API para uma URL especifica.")
    return parser


def main(argv: Optional[List[str]] = None) -> None:
    configure_logging()
    args = build_parser().parse_args(argv)

    if args.inspect_url:
        result = inspect_single_url(args.inspect_url)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.once:
        run_indexing_cycle()
    else:
        scheduler()


if __name__ == "__main__":
    main()
