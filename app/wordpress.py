import logging
import requests
import os
import time
import json
import re 
import html as _html
from .html_utils import detect_forbidden_cta, strip_forbidden_cta_sentences
from typing import Dict, Any, Optional, List
from urllib.parse import urlparse
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

MIME_TO_EXT = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}

IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif")
MIN_MEDIA_BYTES = 5000
ALLOWED_PUBLISH_IMAGE_DOMAIN = os.getenv("ALLOWED_PUBLISH_IMAGE_DOMAIN", "")
BLOCK_EXTERNAL_IMAGES_ON_PUBLISH = os.getenv(
    "BLOCK_EXTERNAL_IMAGES_ON_PUBLISH", "true"
).lower() in {"1", "true", "yes", "on"}
YOAST_INDEX_VALUE = "0"


def _forced_yoast_noindex_value(value: Any, post_ref: Any = "?") -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"1", "true", "yes", "on", "noindex"}:
        logger.error(
            "[INDEX_GUARD] noindex bloqueado post=%s requested=%s; forcing index",
            post_ref,
            value,
        )
    return YOAST_INDEX_VALUE


def _external_img_srcs(content: str, allowed_domain: str = ALLOWED_PUBLISH_IMAGE_DOMAIN) -> List[str]:
    soup = BeautifulSoup(content or "", "html.parser")
    external = []
    allowed = (allowed_domain or "").lower().lstrip(".").rstrip(".")
    for img in soup.find_all("img"):
        src = str(img.get("src") or "").strip()
        hostname = (urlparse(src).hostname or "").lower().rstrip(".")
        if src and not (hostname == allowed or hostname.endswith("." + allowed)):
            external.append(src)
    return external


def _is_local_media_url(url: str, allowed_domain: str = ALLOWED_PUBLISH_IMAGE_DOMAIN) -> bool:
    hostname = (urlparse(url or "").hostname or "").lower().rstrip(".")
    allowed = (allowed_domain or "").lower().lstrip(".").rstrip(".")
    return bool(hostname and allowed and (hostname == allowed or hostname.endswith("." + allowed)))


def _content_image_audit(content: str) -> Dict[str, int]:
    soup = BeautifulSoup(content or "", "html.parser")
    images = soup.find_all("img")
    return {
        "total": len(images),
        "external": len(_external_img_srcs(content)),
        "missing_dimensions": sum(
            1 for img in images if not img.get("width") or not img.get("height")
        ),
    }

def _slugify(name: str) -> str:
    """Creates a simple, WordPress-compatible slug from a string."""
    s = name.strip().lower()
    # Remove characters that are not alphanumeric, whitespace, or hyphen
    s = re.sub(r'[^\w\s-]', '', s, flags=re.UNICODE)
    # Replace whitespace and underscores with a hyphen
    s = re.sub(r'[\s_-]+', '-', s, flags=re.UNICODE)
    # Strip leading/trailing hyphens and limit length
    return s.strip('-')[:190] or 'tag'

class WordPressClient:
    """A client for interacting with the WordPress REST API."""

    def __init__(self, config: Dict[str, str], categories_map: Dict[str, int]):
        self.api_url = (config.get('url') or "").rstrip('/')
        if not self.api_url:
            raise ValueError("WORDPRESS_URL is not configured.")
        self.user = config.get('user')
        self.password = config.get('password')
        self.categories_map = categories_map
        self._category_ids_cache: Optional[set[int]] = None
        self._category_name_cache: Dict[str, int] = {}
        self.last_created_post: Dict[str, Any] = {}
        self.session = requests.Session()
        if self.user and self.password:
            self.session.auth = (self.user, self.password)
        self.session.headers.update({'User-Agent': 'VocMoney-Pipeline/1.0'})

    def get_last_created_post_details(self) -> Dict[str, Any]:
        return dict(self.last_created_post or {})

    def get_domain(self) -> str:
        """Extracts the domain from the WordPress URL."""
        try:
            return urlparse(self.api_url).netloc
        except Exception:
            return ""

    def _get_existing_tag_id(self, name: str) -> Optional[int]:
        """Searches for an existing tag by name or slug and returns its ID."""
        slug = _slugify(name)
        tags_endpoint = f"{self.api_url}/tags"
        params = {"search": name, "per_page": 100}

        try:
            r = self.session.get(tags_endpoint, params=params, timeout=20)
            r.raise_for_status()
            items = r.json()
            
            # WordPress search can be broad, so we verify the match
            for item in items:
                if item.get('name', '').strip().lower() == name.strip().lower():
                    return int(item['id'])
            for item in items:
                if item.get('slug') == slug:
                    return int(item['id'])
        except requests.RequestException as e:
            logger.error(
                f"âŒ ERRO ao buscar tag '{name}' | "
                f"ExceÃ§Ã£o: {type(e).__name__} | "
                f"Mensagem: {str(e)[:200]}"
            )
        
        return None

    def _create_tag(self, name: str) -> Optional[int]:
        """Creates a new tag and returns its ID."""
        tags_endpoint = f"{self.api_url}/tags"
        payload = {"name": name, "slug": _slugify(name)}
        
        try:
            r = self.session.post(tags_endpoint, json=payload, timeout=20)
            
            if r.status_code in (200, 201):
                tag_id = int(r.json()['id'])
                logger.info(f"âœ… Tag criada com sucesso | Nome: '{name}' | ID: {tag_id} | Tempo: {r.elapsed.total_seconds():.2f}s")
                return tag_id
            
            # Handle race condition where tag was created between search and post
            if r.status_code == 400 and isinstance(r.json(), dict) and r.json().get("code") == "term_exists":
                logger.warning(f"âš ï¸  Tag '{name}' jÃ¡ existe (race condition). Re-buscando ID...")
                return self._get_existing_tag_id(name)
            
            logger.error(
                f"âŒ ERRO ao criar tag '{name}' | "
                f"Status: {r.status_code} | "
                f"Endpoint: {tags_endpoint} | "
                f"Resposta: {r.text[:300]}"
            )
            r.raise_for_status()
        except requests.RequestException as e:
            logger.error(
                f"âŒ ERRO na requisiÃ§Ã£o ao criar tag '{name}' | "
                f"ExceÃ§Ã£o: {type(e).__name__} | "
                f"Mensagem: {str(e)[:200]}"
            )
            if e.response is not None:
                logger.error(f"   Response Status: {e.response.status_code}")
                logger.error(f"   Response Body: {e.response.text[:300]}")

        return None

    def _ensure_tag_ids(self, tags: List[Any], max_tags: int = 10) -> List[int]:
        """Converts a list of tag names/IDs into a list of integer IDs, creating tags if necessary."""
        if not tags:
            return []

        # Normalize input (handles strings, ints, and comma-separated strings)
        norm_tags: List[str] = []
        for t in tags:
            if isinstance(t, int):
                norm_tags.append(str(t))
            elif isinstance(t, str):
                norm_tags.extend([p.strip() for p in t.split(',') if p.strip()])
        
        # Deduplicate and limit
        cleaned_tags = list(dict.fromkeys(norm_tags))[:max_tags]
        
        tag_ids: List[int] = []
        for tag_name in cleaned_tags:
            if tag_name.isdigit():
                tag_ids.append(int(tag_name))
            elif len(tag_name) >= 2:
                tag_id = self._get_existing_tag_id(tag_name) or self._create_tag(tag_name)
                if tag_id:
                    tag_ids.append(tag_id)
        
        logger.info(f"Resolved tags {tags} to IDs: {tag_ids}")
        return tag_ids

    def _get_existing_category_id(self, name: str) -> Optional[int]:
        """Searches for an existing category by name or slug and returns its ID."""
        slug = _slugify(name)
        cached = self._category_name_cache.get(name.strip().lower()) or self._category_name_cache.get(slug)
        if cached and self._category_exists(cached):
            return cached

        endpoint = f"{self.api_url}/categories"
        # WordPress category search is not as reliable as tag search, so we get more results and filter
        params = {"search": name, "per_page": 100}

        try:
            r = self.session.get(endpoint, params=params, timeout=20)
            r.raise_for_status()
            items = r.json()
            
            # Exact match on name (case-insensitive)
            for item in items:
                if item.get('name', '').strip().lower() == name.strip().lower():
                    cat_id = int(item['id'])
                    self._remember_category(item)
                    return cat_id
            # Match on slug
            for item in items:
                if item.get('slug') == slug:
                    cat_id = int(item['id'])
                    self._remember_category(item)
                    return cat_id
        except requests.RequestException as e:
            logger.error(
                f"âŒ ERRO ao buscar categoria '{name}' | "
                f"ExceÃ§Ã£o: {type(e).__name__} | "
                f"Mensagem: {str(e)[:200]}"
            )
        
        return None

    def _remember_category(self, item: Dict[str, Any]) -> None:
        try:
            cat_id = int(item["id"])
        except (KeyError, TypeError, ValueError):
            return
        if self._category_ids_cache is not None:
            self._category_ids_cache.add(cat_id)
        name = (item.get("name") or "").strip().lower()
        slug = (item.get("slug") or "").strip().lower()
        if name:
            self._category_name_cache[name] = cat_id
        if slug:
            self._category_name_cache[slug] = cat_id

    def _get_all_category_ids(self, force_refresh: bool = False) -> set[int]:
        # Heavy fallback: do not call in normal publish path.
        # Use _category_exists() (direct GET) or _get_existing_category_id() (search) instead.
        if self._category_ids_cache is not None and not force_refresh:
            return set(self._category_ids_cache)

        endpoint = f"{self.api_url}/categories"
        category_ids: set[int] = set()
        page = 1
        _t_all = time.perf_counter()
        logger.info(f"[WP_TAXONOMY_TIMING] start _get_all_category_ids force_refresh={force_refresh}")

        while True:
            _t_page = time.perf_counter()
            response = self.session.get(
                endpoint,
                params={"per_page": 100, "page": page, "orderby": "id", "order": "asc"},
                timeout=30,
            )
            logger.info(f"[WP_TAXONOMY_TIMING] _get_all_category_ids page={page} status={response.status_code} elapsed={time.perf_counter()-_t_page:.2f}s")
            if response.status_code == 400 and page > 1:
                break
            response.raise_for_status()
            items = response.json()
            if not items:
                break
            for item in items:
                self._remember_category(item)
                try:
                    category_ids.add(int(item["id"]))
                except (KeyError, TypeError, ValueError):
                    continue
            total_pages = int(response.headers.get("X-WP-TotalPages", "0") or 0)
            if total_pages and page >= total_pages:
                break
            if len(items) < 100:
                break
            page += 1

        logger.info(f"[WP_TAXONOMY_TIMING] done _get_all_category_ids pages={page} total={len(category_ids)} elapsed={time.perf_counter()-_t_all:.2f}s")
        self._category_ids_cache = category_ids
        return set(category_ids)

    def _category_exists(self, category_id: int, force_refresh: bool = False) -> bool:
        """Check if a category ID exists. Uses in-memory cache; falls back to direct GET."""
        try:
            category_id = int(category_id)
        except (TypeError, ValueError):
            return False

        # 1. Fast path: already in our in-memory set (populated when categories are found/created)
        if not force_refresh and self._category_ids_cache is not None and category_id in self._category_ids_cache:
            logger.info(f"[WP_TAXONOMY_TIMING] category_id_cache_hit id={category_id}")
            return True

        # 2. Direct GET for the specific category — no full pagination needed
        endpoint = f"{self.api_url}/categories/{category_id}"
        logger.info(f"[WP_TAXONOMY_TIMING] direct_category_get id={category_id}")
        try:
            _t = time.perf_counter()
            r = self.session.get(endpoint, timeout=30)
            elapsed = time.perf_counter() - _t
            if r.status_code == 200:
                # Cache the hit so future checks are instant
                if self._category_ids_cache is None:
                    self._category_ids_cache = set()
                self._category_ids_cache.add(category_id)
                self._remember_category(r.json())
                logger.info(f"[WP_TAXONOMY_TIMING] direct_category_get id={category_id} found=True elapsed={elapsed:.2f}s")
                return True
            if r.status_code == 404:
                logger.info(f"[WP_TAXONOMY_TIMING] direct_category_get id={category_id} found=False elapsed={elapsed:.2f}s")
                return False
            # Unexpected status: log and treat as not-found
            logger.warning(f"[WP_TAXONOMY_TIMING] direct_category_get id={category_id} status={r.status_code} elapsed={elapsed:.2f}s")
            return False
        except requests.RequestException as exc:
            logger.warning(f"Falha ao validar categoria {category_id} via GET direto: {exc}")
            return False

    def _create_category(self, name: str) -> Optional[int]:
        """Creates a new category and returns its ID."""
        endpoint = f"{self.api_url}/categories"
        payload = {"name": name, "slug": _slugify(name)}
        
        try:
            r = self.session.post(endpoint, json=payload, timeout=20)
            
            if r.status_code in (200, 201):
                cat_id = int(r.json()['id'])
                self._remember_category(r.json())
                logger.info(f"Categoria criada com sucesso | Nome: '{name}' | ID: {cat_id} | Tempo: {r.elapsed.total_seconds():.2f}s")
                return cat_id
            
            if r.status_code == 400 and isinstance(r.json(), dict) and r.json().get("code") == "term_exists":
                logger.warning(f"âš ï¸  Categoria '{name}' jÃ¡ existe (race condition). Re-buscando ID...")
                return self._get_existing_category_id(name)
            
            logger.error(
                f"âŒ ERRO ao criar categoria '{name}' | "
                f"Status: {r.status_code} | "
                f"Endpoint: {endpoint} | "
                f"Resposta: {r.text[:300]}"
            )
            r.raise_for_status()
        except requests.RequestException as e:
            logger.error(
                f"âŒ ERRO na requisiÃ§Ã£o ao criar categoria '{name}' | "
                f"ExceÃ§Ã£o: {type(e).__name__} | "
                f"Mensagem: {str(e)[:200]}"
            )
            if e.response is not None:
                logger.error(f"   Response Status: {e.response.status_code}")
                logger.error(f"   Response Body: {e.response.text[:300]}")

        return None

    def resolve_category_names_to_ids(self, category_names: List[str]) -> List[int]:
        """Converts a list of category names into a list of integer IDs, creating categories if necessary."""
        if not category_names:
            return []

        # Deduplicate while preserving order (for logging)
        cleaned_names = list(dict.fromkeys([name.strip() for name in category_names if name.strip() and len(name) >= 1]))
        
        cat_ids: List[int] = []
        for name in cleaned_names:
            _t_cat = time.perf_counter()
            cache_key = name.strip().lower()

            # 1. Fast path: in-memory name cache (populated on previous lookups)
            cached_id = self._category_name_cache.get(cache_key)
            if cached_id:
                logger.info(f"[WP_TAXONOMY_TIMING] category_name_cache_hit name={name!r} id={cached_id}")
                cat_ids.append(cached_id)
                continue

            logger.info(f"[WP_TAXONOMY_TIMING] start resolve_category name={name!r}")

            # 2. Check static local map first (from config)
            cat_id = self.categories_map.get(name)
            if not cat_id:
                for map_name, map_id in self.categories_map.items():
                    if map_name.lower() == cache_key:
                        cat_id = map_id
                        break

            # 3. If not in local map, query WordPress by search (direct, no pagination)
            if not cat_id:
                cat_id = self._get_existing_category_id(name) or self._create_category(name)

            if cat_id and self._category_exists(cat_id):
                cat_ids.append(cat_id)
            elif cat_id:
                logger.warning(f"Categoria resolvida mas nao validada no WordPress | Nome='{name}' | ID={cat_id}")
            logger.info(f"[WP_TAXONOMY_TIMING] done resolve_category name={name!r} id={cat_id} elapsed={time.perf_counter()-_t_cat:.2f}s")

        logger.info(f"Resolved category names {cleaned_names} to IDs: {cat_ids}")
        return cat_ids

    def upload_media_from_url(self, image_url: str, alt_text: str = "", max_attempts: int = 2) -> Optional[Dict[str, Any]]:
        """
        Downloads an image and uploads it to WordPress with a retry mechanism.
        """
        last_err = None
        filename = (urlparse(image_url).path.split('/')[-1] or "image").split("?")[0]
        for attempt in range(1, max_attempts + 1):
            try:
                # 1. Download the image with a reasonable timeout
                img_response = requests.get(image_url, timeout=25)
                img_response.raise_for_status()
                content_type = img_response.headers.get('Content-Type', 'image/jpeg').split(';')[0].strip()
                if not content_type.startswith("image/"):
                    raise ValueError(f"Invalid media content-type: {content_type}")
                img_size = len(img_response.content)
                if img_size < MIN_MEDIA_BYTES:
                    raise ValueError(f"Image too small: {img_size} bytes")
                # Sanitize filename
                if not any(filename.lower().endswith(ext) for ext in IMAGE_EXTS):
                    filename = filename + MIME_TO_EXT.get(content_type, ".jpg")

                # 2. Upload to WordPress
                media_endpoint = f"{self.api_url}/media"
                headers = {
                    'Content-Disposition': f'attachment; filename="{filename}"',
                    'Content-Type': content_type,
                }
                wp_response = self.session.post(media_endpoint, headers=headers, data=img_response.content, timeout=40)
                wp_response.raise_for_status()
                media_id = wp_response.json().get('id')
                logger.info(f"MEDIA OK: ID {media_id} | {filename} ({img_size} bytes)")
                logger.debug(f"  Content-Type: {content_type}")
                logger.debug(f"  Tempo: {wp_response.elapsed.total_seconds():.2f}s")
                return wp_response.json() # Success

            except (requests.Timeout, requests.ConnectionError) as e:
                last_err = e
                logger.warning(f"MEDIA RETRY {attempt}/{max_attempts}: {type(e).__name__} | Aguardando {2*attempt}s...")
                time.sleep(2 * attempt)  # Simple backoff
            except Exception as e:
                last_err = e
                logger.error(f"MEDIA ERRO ({attempt}/{max_attempts}): {type(e).__name__}: {str(e)[:150]}")
                if hasattr(e, 'response') and e.response is not None:
                    logger.error(f"  Status: {e.response.status_code}")
                    try:
                        resp_json = e.response.json()
                        logger.error(f"  Erro WordPress: {resp_json.get('code', 'N/A')} - {resp_json.get('message', 'N/A')[:200]}")
                    except:
                        logger.error(f"  Response: {e.response.text[:300]}")
                break # Don't retry on WP errors (4xx, 5xx) or other issues

        logger.error(f"MEDIA FALHOU: {filename} apos {attempt} tentativa(s) | Erro: {type(last_err).__name__}")
        return None

    def set_media_alt_text(self, media_id: int, alt_text: str) -> bool:
        """Sets the alt text for a media item in WordPress."""
        if not alt_text:
            return False
        try:
            endpoint = f"{self.api_url}/media/{media_id}"
            payload = {"alt_text": alt_text}
            r = self.session.post(endpoint, json=payload, timeout=20)
            r.raise_for_status()
            logger.info(f"Successfully set alt text for media ID {media_id}.")
            return True
        except requests.RequestException as e:
            logger.warning(f"Failed to set alt_text on media {media_id}: {e}")
            if e.response is not None:
                logger.warning(f"Response body: {e.response.text}")
            return False

    def find_media_by_search(self, keyword: str) -> Optional[int]:
        """
        Busca na biblioteca de mÃ­dia do WordPress uma imagem cujo tÃ­tulo contenha
        a keyword informada. Retorna o ID da primeira imagem encontrada, ou None.
        Usado pelos evergreens para reaproveitar imagens jÃ¡ enviadas pelo pipeline.
        """
        if not keyword:
            return None
        try:
            endpoint = f"{self.api_url}/media"
            params = {"search": keyword, "media_type": "image", "per_page": 5, "orderby": "date", "order": "desc"}
            r = self.session.get(endpoint, params=params, timeout=15)
            r.raise_for_status()
            items = r.json()
            if items:
                media_id = int(items[0]["id"])
                logger.info(f"[EVERGREEN] Imagem reutilizada da biblioteca | keyword='{keyword}' | media_id={media_id}")
                return media_id
        except Exception as exc:
            logger.debug(f"[EVERGREEN] find_media_by_search('{keyword}'): {exc}")
        return None

    def wait_media_available(self, media_id: int, max_attempts: int = 6, delay: float = 0.75) -> bool:
        """Wait until a freshly uploaded media item is readable by the WP REST API."""
        if not media_id:
            return False
        ready = self._wait_for_media_ready(media_id, max_attempts=max_attempts)
        if ready:
            logger.info("[POST_FEATURED_TIMING] media_available media_id=%s", media_id)
            return True
        logger.warning(
            "[POST_FEATURED_TIMING] media_availability_unconfirmed media_id=%s attempts=%s action=continue_create_with_post_create_reconcile",
            media_id,
            max_attempts,
        )
        return False

    def find_related_posts(self, term: str, limit: int = 3) -> List[Dict[str, str]]:
        """Searches for posts on the site and returns their title and URL."""
        if not term:
            return []
        try:
            endpoint = f"{self.api_url}/search"
            params = {"search": term, "per_page": limit, "_embed": "self"}
            resp = self.session.get(endpoint, params=params, timeout=15)
            resp.raise_for_status()
            # The 'url' in the search result is the API URL, we need the 'link' from the embedded post object
            return [{"title": i.get("title", ""), "url": i.get("_embedded", {}).get("self", [{}])[0].get("link", "")} for i in resp.json()]
        except requests.RequestException as e:
            logger.error(f"Error searching for related posts with term '{term}': {e}")
            return []

    def create_post(self, payload: Dict[str, Any]) -> Optional[int]:
        """Creates a new post in WordPress."""
        try:
            # Validar tamanho do payload ANTES de enviar
            payload_size = len(json.dumps(payload))
            post_title = payload.get('title', 'SEM TITULO')[:60]
            
            # Se exceder 30KB, tentar reduzir conteÃºdo
            if payload_size > 30000:  # 30KB limit (mais realista que 15KB)
                logger.warning(f"POST GRANDE: {payload_size} bytes (limite: 30KB). Tentando reduzir conteÃºdo...")
                
                # EstratÃ©gia 1: Remover parÃ¡grafos repetitivos ou muito curtos
                if 'content' in payload and payload['content']:
                    content = payload['content']
                    # Remove mÃºltiplos parÃ¡grafos vazios
                    content = re.sub(r'<!-- /wp:paragraph -->\s*<!-- wp:paragraph -->', '', content)
                    # Remove parÃ¡grafos com menos de 30 chars
                    content = re.sub(r'<!-- wp:paragraph -->\s*<p>\s*.{0,25}\s*<\/p>\s*<!-- /wp:paragraph -->', '', content, flags=re.IGNORECASE)
                    payload['content'] = content
                    
                    # Recalcular tamanho apÃ³s reduÃ§Ã£o
                    new_payload_size = len(json.dumps(payload))
                    logger.info(f"ConteÃºdo reduzido: {payload_size} â†’ {new_payload_size} bytes")
                    payload_size = new_payload_size
            
            if payload_size > 30000:  # Ainda muito grande
                logger.error(f"POST GRANDE DEMAIS: {payload_size} bytes (limite: 30KB)")
                logger.error(f"  Titulo: {post_title}")
                logger.error(f"  Content: {len(payload.get('content', ''))} chars")
                logger.error(f"  Featured media: {payload.get('featured_media')}")
                return None
            
            # Resolve tag names to integer IDs before sending
            if 'tags' in payload and payload['tags']:
                payload['tags'] = self._ensure_tag_ids(payload['tags'])

            posts_endpoint = f"{self.api_url}/posts"
            payload.setdefault('status', 'publish')
            
            # Clean up payload: remove fields WordPress REST API doesn't accept
            # Only send recognized fields to avoid 500 errors
            safe_fields = ['title', 'slug', 'content', 'excerpt', 'categories', 'tags', 'featured_media', 'status']
            clean_payload = {k: v for k, v in payload.items() if k in safe_fields}
            
            # Remove featured_media if it's 0 or None (invalid)
            if 'featured_media' in clean_payload:
                if not clean_payload['featured_media'] or clean_payload['featured_media'] == 0:
                    logger.warning("Featured media ID is invalid (0 or None). Removing from payload.")
                    del clean_payload['featured_media']
                else:
                    # Ensure it's an integer
                    clean_payload['featured_media'] = int(clean_payload['featured_media'])
                    self.wait_media_available(clean_payload['featured_media'])
            
            # Ensure title is plain text (no HTML tags)
            if 'title' in clean_payload and clean_payload['title']:
                title = clean_payload['title']
                # Remove any HTML tags that might have leaked into the title
                title = re.sub(r'<[^>]+>', '', title).strip()
                clean_payload['title'] = title
            
            # Sanitize content to prevent malformed HTML
            if 'content' in clean_payload and clean_payload['content']:
                content = clean_payload['content']
                # Replace problematic whitespace characters
                content = content.replace('\u00a0', ' ')  # Non-breaking space
                content = content.replace('\u2002', ' ')  # En space
                content = content.replace('\u2003', ' ')  # Em space
                content = content.replace('\u200b', '')   # Zero-width space (remove)
                # Remove only truly problematic control characters (not whitespace)
                content = ''.join(char for char in content if ord(char) >= 32 or char in '\n\r\t' or ord(char) in [0x0B])
                # Clean up multiple consecutive spaces (but keep single spaces)
                content = re.sub(r' {2,}', ' ', content)
                clean_payload['content'] = content
            
            # Sanitize excerpt
            if 'excerpt' in clean_payload and clean_payload['excerpt']:
                excerpt = clean_payload['excerpt']
                excerpt = excerpt.replace('\u00a0', ' ')
                excerpt = ''.join(char for char in excerpt if ord(char) >= 32 or char in '\n\r\t')
                clean_payload['excerpt'] = excerpt
            
            # Sanitize title
            if 'title' in clean_payload and clean_payload['title']:
                title = clean_payload['title']
                title = title.replace('\u00a0', ' ')
                title = ''.join(char for char in title if ord(char) >= 32 or char in '\n\r\t')
                clean_payload['title'] = title
            
            # Ensure minimum content length
            if 'content' not in clean_payload or not clean_payload.get('content', '').strip():
                logger.error("POST ERRO: Conteudo vazio. Cancelando publicacao")
                return None

            external_images = _external_img_srcs(clean_payload.get("content", ""))
            if external_images:
                post_ref = clean_payload.get("slug") or clean_payload.get("title") or "?"
                for image_url in external_images:
                    logger.warning(
                        "[EXTERNAL_IMAGE_GUARD] post=%s url=%s block=%s",
                        post_ref,
                        image_url,
                        BLOCK_EXTERNAL_IMAGES_ON_PUBLISH,
                    )
                if BLOCK_EXTERNAL_IMAGES_ON_PUBLISH:
                    logger.error(
                        "[EXTERNAL_IMAGE_GUARD] publication_blocked post=%s count=%s",
                        post_ref,
                        len(external_images),
                    )
                    return None

            image_audit = _content_image_audit(clean_payload.get("content", ""))
            logger.info(
                "[IMAGE_PUBLISH_AUDIT] post=%s featured=local_attachment:%s "
                "img_total=%s img_external=%s img_missing_dimensions=%s",
                clean_payload.get("slug") or clean_payload.get("title") or "?",
                clean_payload.get("featured_media") or "none",
                image_audit["total"],
                image_audit["external"],
                image_audit["missing_dimensions"],
            )
            
            content_length = len(clean_payload['content'])
            if content_length < 100:
                logger.error(f"POST ERRO: Conteudo muito curto ({content_length} chars). Minimo: 100 chars")
                return None
            
            # Validar tÃ­tulo
            if 'title' not in clean_payload or not clean_payload.get('title', '').strip():
                logger.error("POST ERRO: Titulo vazio. Cancelando publicacao")
                return None
            
            title_length = len(clean_payload['title'])
            if title_length < 3:
                logger.error(f"POST ERRO: Titulo muito curto ({title_length} chars). Minimo: 3 chars")
                return None
            
            # Ensure categories is a valid list of integers
            if 'categories' in clean_payload and clean_payload['categories']:
                try:
                    clean_payload['categories'] = [int(c) for c in clean_payload['categories'] if c]
                except (ValueError, TypeError) as e:
                    logger.warning(f"Categorias invalidas, removendo: {e}")
                    del clean_payload['categories']
            
            # VALIDAR CATEGORIAS - usar cache + GET direto por ID (sem paginacao global)
            if 'categories' in clean_payload and clean_payload['categories']:
                try:
                    valid_categories = []
                    for cat_id in clean_payload['categories']:
                        if self._category_exists(cat_id):
                            valid_categories.append(cat_id)
                        else:
                            logger.warning(f"Categoria {cat_id} nao existe no WordPress, removendo")

                    if valid_categories:
                        clean_payload['categories'] = valid_categories
                        logger.info(f"Categorias validadas: {valid_categories}")
                    else:
                        # Nenhuma categoria valida — tentar novamente com force_refresh do cache
                        valid_categories = [
                            cat_id for cat_id in clean_payload['categories']
                            if self._category_exists(cat_id, force_refresh=True)
                        ]
                        if valid_categories:
                            clean_payload['categories'] = valid_categories
                            logger.info(f"Categorias validadas apos retry: {valid_categories}")
                        else:
                            logger.warning("Nenhuma categoria valida encontrada, removendo todas")
                            del clean_payload['categories']
                        
                except Exception as cat_err:
                    logger.warning(f"Erro ao validar categorias: {cat_err}, enviando assim mesmo")
            
            # Log ANTES da requisiÃ§Ã£o - informaÃ§Ã£o resumida
            post_title = clean_payload.get('title', 'SEM TITULO')[:80]
            logger.info(f"POST CRIAR: '{post_title}'")
            logger.info(
                "  WP payload: title_len=%s content_len=%s cat=%s tags=%s featured_media=%s",
                len(clean_payload.get('title', '')),
                len(clean_payload.get('content', '')),
                clean_payload.get('categories', []),
                clean_payload.get('tags', []),
                clean_payload.get('featured_media') or 'nenhuma',
            )
            
            # ValidaÃ§Ã£o extra: tentar serializar para JSON e validar
            try:
                test_json = json.dumps(clean_payload, ensure_ascii=False)
                logger.debug(f"JSON vÃ¡lido: {len(test_json)} bytes")
            except Exception as json_err:
                logger.error(f"ERRO: Payload nÃ£o Ã© JSON vÃ¡lido: {str(json_err)[:200]}")
                logger.error(f"  TÃ­tulo: {clean_payload.get('title')[:100]}")
                logger.error(f"  ConteÃºdo (primeiros 100): {clean_payload.get('content', '')[:100]}")
                return None
            logger.debug(f"  - Tamanho conteudo: {len(clean_payload.get('content', ''))} chars")
            logger.debug(f"  - Featured image: {clean_payload.get('featured_media', 'nenhuma')}")
            logger.debug(f"  - Categorias: {clean_payload.get('categories', [])}")
            logger.debug(f"  - Tags: {clean_payload.get('tags', [])}")
            
            # Log do payload completo APENAS em DEBUG mode
            if logger.isEnabledFor(logging.DEBUG):
                try:
                    log_payload = json.dumps(clean_payload, indent=2, ensure_ascii=False)[:2000]
                    logger.debug(f"PAYLOAD JSON:\n{log_payload}")
                except Exception as log_e:
                    logger.warning(f"Nao conseguiu serializar payload: {log_e}")

            response = self.session.post(posts_endpoint, json=clean_payload, timeout=60)
            
            # Log DEPOIS da resposta
            response_json = response.json() if response.ok else {}
            post_id = response_json.get('id') if response.ok else None
            
            if response.ok and post_id and post_id > 0:  # Validar que tem ID valido
                requested_featured_media = clean_payload.get('featured_media')
                observed_featured_media = response_json.get('featured_media')
                if requested_featured_media and observed_featured_media != requested_featured_media:
                    observed_after_get = self._get_post_featured_media(post_id)
                    logger.warning(
                        "Featured media nao confirmado na resposta de criacao | post_id=%s requested=%s observed_response=%s observed_get=%s status=%s.",
                        post_id,
                        requested_featured_media,
                        observed_featured_media,
                        observed_after_get,
                        clean_payload.get("status"),
                    )
                    if observed_after_get == int(requested_featured_media):
                        response_json['featured_media'] = requested_featured_media
                        self.last_created_post = response_json if isinstance(response_json, dict) else {}
                        logger.info(
                            "Featured media confirmado por GET pos-criacao | post_id=%s media_id=%s",
                            post_id,
                            requested_featured_media,
                        )
                        logger.info(f"POST OK: ID {post_id} criado com sucesso em {response.elapsed.total_seconds():.2f}s")
                        return post_id
                    logger.warning(
                        "Featured media ausente apos GET pos-criacao | post_id=%s requested=%s observed_response=%s observed_get=%s status=%s. Tentando ajuste direto.",
                        post_id,
                        requested_featured_media,
                        observed_featured_media,
                        observed_after_get,
                        clean_payload.get("status"),
                    )
                    if self.set_post_featured_media(post_id, requested_featured_media, max_attempts=10):
                        response_json['featured_media'] = requested_featured_media
                    else:
                        logger.error(
                            "POST criado, mas featured_media nao foi aplicado | post_id=%s media_id=%s",
                            post_id,
                            requested_featured_media,
                        )
                self.last_created_post = response_json if isinstance(response_json, dict) else {}
                logger.info(f"POST OK: ID {post_id} criado com sucesso em {response.elapsed.total_seconds():.2f}s")
                return post_id
            elif response.status_code == 500:
                # ERRO 500 - CAPTURAR TUDO PARA DEBUG DETALHADO
                logger.error("=" * 100)
                logger.error("ðŸ”´ ERRO 500 DO WORDPRESS - ANÃLISE COMPLETA")
                logger.error("=" * 100)
                
                # 1. InformaÃ§Ãµes da requisiÃ§Ã£o
                logger.error(f"REQUISIÃ‡ÃƒO ENVIADA:")
                logger.error(f"  URL: {posts_endpoint}")
                logger.error(f"  MÃ©todo: POST")
                logger.error(f"  Auth: {'Sim (Basic Auth)' if self.user else 'NÃ£o'}")
                logger.error(f"  Headers: {dict(self.session.headers)}")
                logger.error(f"  Tamanho do payload: {len(json.dumps(clean_payload))} bytes")
                
                # 2. ConteÃºdo enviado (payload completo)
                logger.error(f"\nPAYLOAD ENVIADO (JSON COMPLETO):")
                try:
                    payload_json = json.dumps(clean_payload, indent=2, ensure_ascii=False)
                    logger.error(payload_json)
                except Exception as e:
                    logger.error(f"Erro ao serializar payload: {e}")
                
                # 3. Resposta do WordPress (TUDO)
                logger.error(f"\nRESPOSTA DO WORDPRESS:")
                logger.error(f"  Status Code: {response.status_code}")
                logger.error(f"  Headers Response: {dict(response.headers)}")
                logger.error(f"  Content-Type: {response.headers.get('content-type')}")
                logger.error(f"  Content-Length: {len(response.text)} bytes")
                
                # 4. Corpo da resposta (COMPLETO, nÃ£o truncado)
                logger.error(f"\nCORPO DA RESPOSTA (COMPLETO):")
                logger.error(f"{response.text}")
                
                # 5. Tentar parsear como JSON
                logger.error(f"\nANÃLISE JSON DA RESPOSTA:")
                try:
                    resp_json = response.json()
                    logger.error(json.dumps(resp_json, indent=2, ensure_ascii=False))
                except Exception as json_err:
                    logger.error(f"  NÃ£o Ã© JSON vÃ¡lido: {json_err}")
                    logger.error(f"  Tipo MIME: {response.headers.get('content-type')}")
                
                # 6. Salvar em arquivo de debug
                debug_file = f"debug/wordpress_error_500_{int(time.time())}.txt"
                try:
                    import os
                    os.makedirs('debug', exist_ok=True)
                    with open(debug_file, 'w', encoding='utf-8') as f:
                        f.write(f"ERRO 500 - {post_title}\n")
                        f.write("=" * 100 + "\n\n")
                        f.write(f"PAYLOAD ENVIADO:\n{json.dumps(clean_payload, indent=2, ensure_ascii=False)}\n\n")
                        f.write(f"RESPOSTA WORDPRESS:\n{response.text}\n")
                    logger.error(f"  âœ… Debug salvo em: {debug_file}")
                except Exception as file_err:
                    logger.error(f"  âŒ Erro ao salvar debug: {file_err}")
                
                logger.error("=" * 100)
                logger.error("âŒ TENTANDO NOVAMENTE...")
                logger.error("=" * 100)
                
                # Tentar novamente
                logger.info(f"  Tentativa 2: Reenviando artigo completo (sem remoÃ§Ã£o de conteÃºdo)")
                
                try:
                    response2 = self.session.post(posts_endpoint, json=clean_payload, timeout=60)
                    post_id2 = response2.json().get('id') if response2.ok else None
                    
                    if response2.ok and post_id2 and post_id2 > 0:
                        response_json2 = response2.json()
                        requested_featured_media = clean_payload.get('featured_media')
                        observed_featured_media = response_json2.get('featured_media')
                        if requested_featured_media and observed_featured_media != requested_featured_media:
                            logger.warning(
                                "Featured media nao confirmado na tentativa 2 | post_id=%s requested=%s observed=%s. Tentando ajuste direto.",
                                post_id2,
                                requested_featured_media,
                                observed_featured_media,
                            )
                            if self.set_post_featured_media(post_id2, requested_featured_media, max_attempts=10):
                                response_json2['featured_media'] = requested_featured_media
                        self.last_created_post = response_json2 if isinstance(response_json2, dict) else {}
                        logger.info(f"âœ… POST CRIADO NA TENTATIVA 2: ID {post_id2}")
                        return post_id2
                    else:
                        logger.error(f"âŒ Tentativa 2 tambÃ©m falhou com status {response2.status_code}")
                        logger.error(f"  Resposta: {response2.text}")
                except Exception as retry_err:
                    logger.error(f"âŒ Tentativa 2 exception: {str(retry_err)[:500]}")
            
            # ERRO na criacao - log detalhado
            logger.error(f"WordPress post creation failed with status {response.status_code}: {response.text[:500]}")
            
            # Log a resposta de erro detalhada
            try:
                error_data = response.json()
                error_msg = error_data.get('message', 'sem mensagem')
                error_code = error_data.get('code', 'sem codigo')
                logger.error(f"ERROR - wordpress - WordPress post creation failed with status {response.status_code}")
                logger.error(f"ERROR - wordpress - Failed to create WordPress post: {response.status_code} Server Error: Internal Server Error for url: {posts_endpoint}")
            except:
                pass
                logger.error(f"  Resposta: {response.text[:300]}")
            
            response.raise_for_status()

        except requests.RequestException as e:
            logger.error(f"POST ERRO: Excecao {type(e).__name__}: {str(e)[:200]}")
            if hasattr(e, 'response') and e.response is not None:
                logger.error(f"  Status: {e.response.status_code}")
                try:
                    logger.error(f"  Response JSON: {json.dumps(e.response.json(), indent=2)[:500]}")
                except:
                    logger.error(f"  Response: {e.response.text[:300]}")
            return None

    def _attach_media_to_post(self, post_id: int, media_id: int) -> bool:
        """Attach a media item to a post before using it as featured media."""
        try:
            response = self.session.post(
                f"{self.api_url}/media/{int(media_id)}",
                json={"post": int(post_id)},
                timeout=30,
            )
            if response.ok:
                logger.info(
                    "Media anexada ao post | post_id=%s media_id=%s",
                    post_id,
                    media_id,
                )
                return True
            logger.warning(
                "Falha ao anexar media ao post | post_id=%s media_id=%s status=%s response=%s",
                post_id,
                media_id,
                response.status_code,
                response.text[:300],
            )
        except requests.RequestException as exc:
            logger.warning(
                "Excecao ao anexar media ao post | post_id=%s media_id=%s error=%s",
                post_id,
                media_id,
                exc,
            )
        return False

    def _wait_for_media_ready(self, media_id: int, max_attempts: int = 4) -> bool:
        """Wait until WordPress exposes the uploaded media as an image."""
        endpoint = f"{self.api_url}/media/{int(media_id)}"
        for attempt in range(1, max_attempts + 1):
            try:
                response = self.session.get(
                    endpoint,
                    params={"_fields": "id,media_type,mime_type,source_url,media_details"},
                    timeout=30,
                )
                if response.ok:
                    data = response.json() or {}
                    media_type = str(data.get("media_type") or "").lower()
                    mime_type = str(data.get("mime_type") or "").lower()
                    source_url = data.get("source_url")
                    if mime_type.startswith("image/") and source_url:
                        return True
                    logger.warning(
                        "Media ainda nao pronta para destaque | media_id=%s attempt=%s/%s media_type=%s mime_type=%s",
                        media_id,
                        attempt,
                        max_attempts,
                        media_type,
                        mime_type,
                    )
                else:
                    logger.warning(
                        "Media readiness check falhou | media_id=%s attempt=%s/%s status=%s response=%s",
                        media_id,
                        attempt,
                        max_attempts,
                        response.status_code,
                        response.text[:300],
                    )
            except requests.RequestException as exc:
                logger.warning(
                    "Media readiness check exception | media_id=%s attempt=%s/%s error=%s",
                    media_id,
                    attempt,
                    max_attempts,
                    exc,
                )
            time.sleep(min(2 * attempt, 6))
        return False

    def set_post_featured_media(self, post_id: int, media_id: int, max_attempts: int = 6) -> bool:
        """Sets and verifies the WordPress featured image for a post."""
        if not post_id or not media_id:
            logger.warning(
                "Featured media update ignorado por IDs invalidos | post_id=%s media_id=%s",
                post_id,
                media_id,
            )
            return False

        endpoint = f"{self.api_url}/posts/{post_id}"
        payload = {"featured_media": int(media_id)}
        attached = False

        for attempt in range(1, max_attempts + 1):
            try:
                if attempt == 1:
                    self._wait_for_media_ready(media_id, max_attempts=2)
                elif not attached:
                    attached = self._attach_media_to_post(post_id, media_id)
                    self._wait_for_media_ready(media_id, max_attempts=2)

                response = self.session.post(
                    endpoint,
                    params={"_fields": "id,featured_media"},
                    json=payload,
                    timeout=30,
                )
                if not response.ok:
                    logger.warning(
                        "Featured media update falhou | post_id=%s media_id=%s attempt=%s/%s status=%s response=%s",
                        post_id,
                        media_id,
                        attempt,
                        max_attempts,
                        response.status_code,
                        response.text[:300],
                    )
                    time.sleep(attempt)
                    continue

                observed = (response.json() or {}).get("featured_media")
                if observed == int(media_id):
                    if isinstance(self.last_created_post, dict) and self.last_created_post.get("id") == post_id:
                        self.last_created_post["featured_media"] = int(media_id)
                    logger.info(
                        "Featured media aplicado | post_id=%s media_id=%s attempt=%s",
                        post_id,
                        media_id,
                        attempt,
                    )
                    return True

                logger.warning(
                    "Featured media divergente apos update | post_id=%s requested=%s observed=%s attempt=%s/%s",
                    post_id,
                    media_id,
                    observed,
                    attempt,
                    max_attempts,
                )
                time.sleep(min(2 * attempt, 10))
            except requests.RequestException as exc:
                logger.warning(
                    "Featured media update exception | post_id=%s media_id=%s attempt=%s/%s error=%s",
                    post_id,
                    media_id,
                    attempt,
                    max_attempts,
                    exc,
                )
                time.sleep(min(2 * attempt, 10))

        observed = self._get_post_featured_media(post_id)
        if observed == int(media_id):
            if isinstance(self.last_created_post, dict) and self.last_created_post.get("id") == post_id:
                self.last_created_post["featured_media"] = int(media_id)
            logger.info(
                "Featured media confirmado em verificacao final | post_id=%s media_id=%s",
                post_id,
                media_id,
            )
            return True

        return False

    def _get_post_featured_media(self, post_id: int) -> Optional[int]:
        """Fetch the current featured media ID for a post."""
        try:
            response = self.session.get(
                f"{self.api_url}/posts/{int(post_id)}",
                params={"_fields": "id,featured_media"},
                timeout=30,
            )
            if not response.ok:
                logger.warning(
                    "Featured media final check falhou | post_id=%s status=%s response=%s",
                    post_id,
                    response.status_code,
                    response.text[:300],
                )
                return None
            observed = (response.json() or {}).get("featured_media")
            return int(observed) if observed else 0
        except (requests.RequestException, TypeError, ValueError) as exc:
            logger.warning(
                "Featured media final check exception | post_id=%s error=%s",
                post_id,
                exc,
            )
            return None

    def get_post_content(self, post_id: int) -> Optional[str]:
        """Fetches the raw content of a post for sanitization checks."""
        endpoint = f"{self.api_url}/posts/{post_id}"
        params = {"context": "edit", "_fields": "content"}
        try:
            r = self.session.get(endpoint, params=params, timeout=30)
            r.raise_for_status()
            data = r.json() or {}
            content = data.get("content", {})
            result = content.get("raw") or content.get("rendered")
            logger.debug(f"âœ… ConteÃºdo do post {post_id} recuperado | Tamanho: {len(result) if result else 0} chars | Tempo: {r.elapsed.total_seconds():.2f}s")
            return result
        except requests.RequestException as e:
            logger.error(
                f"âŒ ERRO ao buscar conteÃºdo do post {post_id} | "
                f"ExceÃ§Ã£o: {type(e).__name__} | "
                f"Mensagem: {str(e)[:200]} | "
                f"Endpoint: {endpoint}"
            )
            if getattr(e, "response", None) is not None:
                logger.error(f"   Response Status: {e.response.status_code}")
                logger.error(f"   Response Body: {e.response.text[:500]}")
            return None

    def update_post_content(self, post_id: int, content: str) -> bool:
        """Updates the content of an existing post."""
        endpoint = f"{self.api_url}/posts/{post_id}"
        payload = {"content": content}
        try:
            r = self.session.post(endpoint, json=payload, timeout=40)
            if not r.ok:
                logger.error(
                    f"âŒ ERRO ao atualizar conteÃºdo do post {post_id} | "
                    f"Status: {r.status_code} | "
                    f"Tamanho do conteÃºdo: {len(content)} chars | "
                    f"Tempo: {r.elapsed.total_seconds():.2f}s | "
                    f"Resposta: {r.text[:500]}"
                )
                r.raise_for_status()
            logger.info(f"âœ… ConteÃºdo do post {post_id} atualizado com sucesso | Tamanho: {len(content)} chars | Tempo: {r.elapsed.total_seconds():.2f}s")
            return True
        except requests.RequestException as e:
            logger.error(
                f"âŒ ERRO ao atualizar post {post_id} | "
                f"ExceÃ§Ã£o: {type(e).__name__} | "
                f"Mensagem: {str(e)[:200]} | "
                f"Endpoint: {endpoint}"
            )
            if hasattr(e, 'response') and e.response is not None:
                logger.error(f"   Response Status: {e.response.status_code}")
                logger.error(f"   Response Body: {e.response.text[:500]}")
            if getattr(e, "response", None) is not None:
                logger.error(f"Response body: {e.response.text}")
            return False

    def update_post_yoast_seo(self, post_id: int, featured_media_id: int, seo_data: Dict[str, Any]) -> bool:
        """
        Updates Yoast SEO metadata for a post including OG images, meta descriptions, etc.
        This fixes the og:image pointing to external URLs by forcing it to use featured_media.
        
        Args:
            post_id: WordPress post ID
            featured_media_id: ID of the featured image (media ID)
            seo_data: Dictionary containing SEO fields:
                - title: SEO title (60-70 chars)
                - description: Meta description (120-160 chars)  
                - focuskw: Focus keyword
                - title_pt: Portuguese SEO title
                - description_pt: Portuguese meta description
                
        Returns:
            True if update was successful, False otherwise
        """
        try:
            endpoint = f"{self.api_url}/posts/{post_id}"
            
            # Prepare Yoast SEO fields
            # Note: WordPress REST API uses underscore prefix for meta fields in Yoast
            yoast_fields = {
                '_yoast_wpseo_title': seo_data.get('title', '')[:70] or seo_data.get('title_pt', '')[:70],
                '_yoast_wpseo_metadesc': seo_data.get('description', '')[:160] or seo_data.get('description_pt', '')[:160],
                '_yoast_wpseo_focuskw': seo_data.get('focuskw', '')[:30],
                '_yoast_wpseo_opengraph-image': '',  # Will be set below
                '_yoast_wpseo_opengraph-image-id': str(featured_media_id),
                '_yoast_wpseo_content_score': '90',  # Assumed good content
                '_yoast_wpseo_primary_category': '',  # Can be set if needed
                '_yoast_wpseo_meta-robots-noindex': _forced_yoast_noindex_value(seo_data.get('noindex', '0'), post_id),
                '_yoast_wpseo_meta-robots-nofollow': '0',  # SEMPRE "0": links internos distribuem PageRank mesmo em noindex
            }
            
            # Get featured image URL from WordPress
            if featured_media_id:
                try:
                    media_endpoint = f"{self.api_url}/media/{featured_media_id}"
                    media_resp = self.session.get(media_endpoint, timeout=20)
                    if media_resp.ok:
                        media_data = media_resp.json()
                        image_url = media_data.get('source_url', '')
                        if image_url and _is_local_media_url(image_url):
                            yoast_fields['_yoast_wpseo_opengraph-image'] = image_url
                            logger.debug(f"OG Image URL set: {image_url}")
                        elif image_url:
                            logger.warning(
                                "[EXTERNAL_IMAGE_GUARD] post=%s field=og:image url=%s",
                                post_id,
                                image_url,
                            )
                except Exception as e:
                    logger.warning(f"Could not fetch media URL for ID {featured_media_id}: {e}")
            
            # Build the update payload
            # Yoast fields are sent as meta in WordPress REST API v2
            update_payload = {
                'meta': yoast_fields
            }
            
            # Make the update request
            response = self.session.post(endpoint, json=update_payload, timeout=40)
            
            if response.ok:
                logger.info(f"âœ… Yoast SEO metadata atualizado | Post ID: {post_id} | Tempo: {response.elapsed.total_seconds():.2f}s")
                logger.debug(f"   Yoast fields updated: {list(yoast_fields.keys())}")
                return True
            else:
                logger.error(
                    f"âŒ ERRO ao atualizar Yoast SEO para post {post_id} | "
                    f"Status: {response.status_code} | "
                    f"Resposta: {response.text[:500]}"
                )
                return False
                
        except requests.RequestException as e:
            logger.error(
                f"âŒ ERRO ao atualizar Yoast SEO do post {post_id} | "
                f"ExceÃ§Ã£o: {type(e).__name__} | "
                f"Mensagem: {str(e)[:200]}"
            )
            return False

    def apply_noindex(self, post_id: int) -> bool:
        """
        Bloqueia aplicacao de noindex.

        A regra operacional atual e que todos os posts publicados fiquem
        indexaveis; manter este metodo como rejeicao explicita protege scripts
        legados que ainda tentem aplicar noindex.
        """
        logger.error(
            "[INDEX_GUARD] apply_noindex blocked post_id=%s; all published posts must be index",
            post_id,
        )
        return False

    def verify_noindex(self, post_id: int) -> tuple[bool, str]:
        """
        Verifica se o post esta como noindex.
        Retorna (ok, observed_value).
        """
        endpoint = f"{self.api_url}/posts/{post_id}"
        params = {"context": "edit"}
        try:
            response = self.session.get(endpoint, params=params, timeout=30)
            response.raise_for_status()
            data = response.json() or {}
        except requests.RequestException as exc:
            logger.error("[NOINDEX_GUARD] exception verifying noindex post_id=%s error=%s", post_id, exc)
            return False, f"verify_error:{type(exc).__name__}"

        meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
        meta_value = str(meta.get("_yoast_wpseo_meta-robots-noindex", "") or "")
        if meta_value in {"1", "true", "True"}:
            return True, "noindex"

        yoast_head_json = data.get("yoast_head_json") if isinstance(data.get("yoast_head_json"), dict) else {}
        robots = yoast_head_json.get("robots") if isinstance(yoast_head_json.get("robots"), dict) else {}
        robots_index = str(robots.get("index", "") or "").lower()
        if robots_index:
            return robots_index == "noindex", robots_index

        yoast_head = str(data.get("yoast_head") or "").lower()
        if 'name="robots"' in yoast_head and "noindex" in yoast_head:
            return True, "noindex"
        if 'name="robots"' in yoast_head and "index" in yoast_head:
            return False, "index"

        return False, meta_value or "not_available"

    def add_google_news_meta(self, post_id: int, news_data: Dict[str, Any]) -> bool:
        """
        Adds Google News optimized meta tags to a post.
        Improves search rankings for Google News and news aggregators.
        
        Args:
            post_id: WordPress post ID
            news_data: Dictionary with news metadata:
                - keywords: List of keywords (comma-separated, max 10 words)
                - genres: News genres (Blog, Opinion, Review, etc.)
                - standout: Whether this is a standout article (True/False)
                - access: 'Subscription', 'Registration', or 'Free'
                
        Returns:
            True if update was successful, False otherwise
        """
        try:
            endpoint = f"{self.api_url}/posts/{post_id}"
            
            # Prepare Google News meta fields
            keywords = news_data.get('keywords', '')
            if isinstance(keywords, list):
                keywords = ', '.join(keywords)
            keywords = keywords[:256]  # Max length for Google News keywords
            
            genres = news_data.get('genres', 'Blog')
            standout = news_data.get('standout', False)
            access = news_data.get('access', 'Free')
            
            # Google News meta tags (as custom meta)
            news_fields = {
                'googleNews_keywords': keywords,
                'googleNews_genres': genres,
                'googleNews_standout': 'yes' if standout else 'no',
                'googleNews_access': access,
                'article_news_keywords': keywords,  # Alternativa para alguns temas
            }
            
            # Build update payload
            update_payload = {
                'meta': news_fields
            }
            
            # Make the update request
            response = self.session.post(endpoint, json=update_payload, timeout=40)
            
            if response.ok:
                logger.info(f"âœ… Google News meta tags adicionadas | Post ID: {post_id} | Keywords: {keywords[:50]}...")
                logger.debug(f"   Genres: {genres}")
                return True
            else:
                logger.warning(
                    f"âš ï¸  Aviso ao adicionar Google News meta para post {post_id} | "
                    f"Status: {response.status_code}"
                )
                # NÃ£o retorna False pois isso nÃ£o Ã© crÃ­tico
                return True
                
        except requests.RequestException as e:
            logger.warning(f"âš ï¸  Aviso ao adicionar Google News meta: {type(e).__name__}")
            return True  # NÃ£o bloqueia se falhar

    def sanitize_published_post(self, post_id: int, max_attempts: int = 2, backoff_s: int = 2) -> bool:
        """
        Fetches the published post's content and excerpt, detects forbidden CTAs,
        attempts to sanitize them (content + excerpt) and updates the post via API.

        Returns True if an update was applied (or no CTA present). False if update failed.
        """
        endpoint = f"{self.api_url}/posts/{post_id}"
        params = {"context": "edit", "_fields": "content,excerpt"}

        try:
            r = self.session.get(endpoint, params=params, timeout=30)
            r.raise_for_status()
            data = r.json() or {}
        except requests.RequestException as e:
            logger.error(f"Failed to fetch post {post_id} for sanitation: {e}")
            if getattr(e, 'response', None) is not None:
                logger.error(f"Response body: {e.response.text}")
            return False

        content_obj = data.get('content') or {}
        excerpt_obj = data.get('excerpt') or {}
        content_raw = content_obj.get('raw') or content_obj.get('rendered') or ''
        excerpt_raw = excerpt_obj.get('raw') or excerpt_obj.get('rendered') or ''

        # Normalize HTML entities to increase detection coverage
        content_raw = _html.unescape(content_raw)
        excerpt_raw = _html.unescape(excerpt_raw)

        content_cta = detect_forbidden_cta(content_raw)
        excerpt_cta = detect_forbidden_cta(excerpt_raw)

        if not content_cta and not excerpt_cta:
            logger.debug(f"No CTA detected for post {post_id} (content/excerpt).")
            return True

        logger.warning(f"CTA detected in published post {post_id}: content_cta={content_cta} excerpt_cta={excerpt_cta}")

        # Attempt sanitization
        new_content, content_removed = strip_forbidden_cta_sentences(content_raw)
        new_excerpt, excerpt_removed = strip_forbidden_cta_sentences(excerpt_raw)

        # Fallback: aggressive simple string replacements for common variants
        def aggressive_fallback(s: str) -> str:
            if not s:
                return s
            s = s.replace("don\'t forget to subscribe", "")
            s = s.replace("donâ€™t forget to subscribe", "")
            s = s.replace("don't forget to subscribe", "")
            s = s.replace("Thank you for reading this post, don't forget to subscribe!", "")
            s = s.replace("Thank you for reading this post, dont forget to subscribe!", "")
            # remove common English CTA fragments
            s = re.sub(r"(?is)thank\s+you\s+for\s+reading[^<]{0,200}?subscribe[^<]{0,200}?", "", s)
            s = re.sub(r"(?is)thanks\s+for\s+reading[^<]{0,200}?subscribe[^<]{0,200}?", "", s)
            return s

        if not content_removed:
            fallback_content = aggressive_fallback(new_content or content_raw)
            if fallback_content != (new_content or content_raw):
                new_content = fallback_content
                content_removed = True

        if not excerpt_removed:
            fallback_excerpt = aggressive_fallback(new_excerpt or excerpt_raw)
            if fallback_excerpt != (new_excerpt or excerpt_raw):
                new_excerpt = fallback_excerpt
                excerpt_removed = True

        if not content_removed and not excerpt_removed:
            logger.error(f"Sanitization attempted for post {post_id} but no removals applied. Will still attempt update if user requested.")

        payload = {}
        if content_removed and new_content is not None:
            payload['content'] = new_content
        if excerpt_removed and new_excerpt is not None:
            payload['excerpt'] = new_excerpt

        if not payload:
            # Nothing to update
            logger.info(f"No payload to update for post {post_id} after sanitation attempt.")
            return True

        # Try updating with retries
        attempt = 0
        while attempt < max_attempts:
            attempt += 1
            try:
                r = self.session.post(endpoint, json=payload, timeout=40)
                if r.ok:
                    logger.info(f"Sanitation update applied to post {post_id} (attempt {attempt}).")
                    return True
                else:
                    logger.error(f"Failed to apply sanitation update to post {post_id} (attempt {attempt}): {r.status_code} - {r.text}")
            except requests.RequestException as e:
                logger.error(f"Error applying sanitation update to post {post_id} (attempt {attempt}): {e}")
            time.sleep(backoff_s * attempt)

        logger.error(f"All attempts to sanitize post {post_id} failed.")
        return False

    def get_published_posts(self, fields: List[str], max_posts: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Fetches published posts, handling pagination, with an optional limit.

        Args:
            fields: A list of fields to retrieve for each post.
            max_posts: Optional limit on the total number of posts to fetch.
        """
        all_posts = []
        page = 1
        per_page = 100
        
        fields_str = ','.join(fields)

        while True:
            # Exit if we have reached the desired number of posts
            if max_posts and len(all_posts) >= max_posts:
                logger.info(f"Reached max_posts limit of {max_posts}. Stopping fetch.")
                break

            endpoint = f"{self.api_url}/posts"
            params = {
                "status": "publish",
                "per_page": per_page,
                "page": page,
                "_fields": fields_str,
            }
            try:
                logger.info(f"Fetching page {page} of published posts...")
                r = self.session.get(endpoint, params=params, timeout=30)
                r.raise_for_status()
                
                posts = r.json()
                if not posts:
                    logger.info("No more posts found. Finished fetching.")
                    break
                
                all_posts.extend(posts)
                
                if len(posts) < per_page:
                    logger.info(f"Last page reached ({len(posts)} posts). Finished fetching.")
                    break
                    
                page += 1

            except requests.RequestException as e:
                logger.error(f"Error fetching published posts (page {page}): {e}")
                if e.response is not None:
                    logger.error(f"Response body: {e.response.text}")
                break
        
        # Trim the list to the exact number if max_posts is set
        if max_posts:
            all_posts = all_posts[:max_posts]

        logger.info(f"Successfully fetched a total of {len(all_posts)} posts.")
        return all_posts

    def get_tags_map_by_ids(self, tag_ids: List[int]) -> Dict[int, str]:
        """ 
        Fetches tag details from a list of IDs and returns a map of {id: name}.
        Handles pagination for large lists of IDs.
        """
        if not tag_ids:
            return {}

        tag_map = {}
        unique_ids = list(set(tag_ids))
        endpoint = f"{self.api_url}/tags"
        
        # The 'include' parameter can take a list of up to 100 IDs.
        # We chunk the requests to handle more than 100.
        for i in range(0, len(unique_ids), 100):
            chunk = unique_ids[i:i + 100]
            params = {
                "include": ",".join(map(str, chunk)),
                "per_page": 100, # Ensure we get all requested items in the chunk
                "_fields": "id,name"
            }
            try:
                logger.info(f"Fetching names for {len(chunk)} tag IDs...")
                r = self.session.get(endpoint, params=params, timeout=30)
                r.raise_for_status()
                tags_data = r.json()
                for tag in tags_data:
                    tag_map[tag['id']] = tag['name']
            except requests.RequestException as e:
                logger.error(f"Error fetching tag details: {e}")
                # Continue to next chunk even if one fails
                continue
        
        logger.info(f"Successfully mapped {len(tag_map)} tag IDs to names.")
        return tag_map

    def close(self):
        """Closes the requests session."""
        self.session.close()

