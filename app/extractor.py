import html  # New import for html.unescape
import json
import logging
import re
import time
from typing import Any, Dict, List, Optional, Set, Tuple, Union
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from .config import USER_AGENT
from .safe_http import UnsafeUrlError, safe_get

#: Teto de bytes de uma pagina de artigo.
ARTICLE_MAX_BYTES = 8 * 1024 * 1024

logger = logging.getLogger(__name__)

try:
    import trafilatura
    from trafilatura.metadata import extract_metadata as trafilatura_extract_metadata  # New import
    _TRAFILATURA_IMPORT_ERROR: Optional[Exception] = None
except Exception as exc:
    trafilatura = None
    trafilatura_extract_metadata = None
    _TRAFILATURA_IMPORT_ERROR = exc

_TRAFILATURA_WARNING_EMITTED = False

def _coerce_url(candidate: Any) -> Optional[str]:
    """
    Aceita str, dict (ex.: {'url': ...} / {'src': ...} / {'href': ...} / {'content': ...})
    e listas (pega o primeiro válido). Retorna URL (str) ou None.
    """
    if not candidate:
        return None

    # já é string?
    if isinstance(candidate, str):
        u = candidate.strip()
        return u or None

    # lista/tupla: pega o primeiro válido
    if isinstance(candidate, (list, tuple)):
        for item in candidate:
            u = _coerce_url(item)
            if u:
                return u
        return None

    # dict: tenta chaves comuns
    if isinstance(candidate, dict):
        for k in ('url', 'src', 'href', 'content'):
            v = candidate.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
            # às vezes vem lista dentro do dict
            if isinstance(v, (list, tuple)):
                u = _coerce_url(v)
                if u:
                    return u
        # às vezes o dict tem só uma key com a URL
        for v in candidate.values():
            u = _coerce_url(v)
            if u:
                return u
        return None

    # desconhecido
    return None

def _dedupe_preserve(urls: List[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out

BAD_IMAGE_KEYWORDS = {
    'author', 'autor', 'avatar', 'byline', 'perfil', 'profile',
    'placeholder', 'logo', 'logomarca', 'brand', 'marca',
    'icon', 'favicon', 'sprite', 'comment', 'user', 'usuario', 'usuário'
}

BAD_IMAGE_DOMAINS = {
    'gravatar.com', 'twimg.com', 'facebook.com', 'fbcdn.net',
    'gstatic.com', 'googleusercontent.com',
    # Adicionados conforme sugestão para bloquear trackers e placeholders
    "schema.org", "scorecardresearch.com", "doubleclick.net",
    "quantserve.com", "chartbeat.com", "google-analytics.com"
}

# aceita query ?width=1200&height=630 e sufixos -1200x630.jpg
DIM_SUFFIX_RE = re.compile(r'-(\d{2,5})x(\d{2,5})(?=\.[a-z]{3,4})(?:\?.*)?$', re.IGNORECASE)

def _guess_dimensions_from_url(url: str) -> Tuple[Optional[int], Optional[int]]:
    try:
        p = urlparse(url)
        q = parse_qs(p.query or '')
        w = q.get('width') or q.get('w')
        h = q.get('height') or q.get('h')
        if w and h:
            return int(w[0]), int(h[0])
        m = DIM_SUFFIX_RE.search(p.path)
        if m:
            return int(m.group(1)), int(m.group(2))
    except Exception:
        pass
    return None, None

def _is_bad_domain(url: str) -> bool:
    try:
        host = urlparse(url).hostname or ''
        return any(host.endswith(d) for d in BAD_IMAGE_DOMAINS)
    except Exception:
        return False


YOUTUBE_DOMAINS = (
    "youtube.com", "www.youtube.com", "m.youtube.com",
    "youtu.be", "www.youtu.be",
)

_YT_PATTERNS = (
    r"(?:youtube\.com/(?:embed/|shorts/|v/)|youtu\.be/)([A-Za-z0-9_-]{11})",
)
_YT_THUMBNAIL_RE = re.compile(
    r"(?:https?:)?//(?:i\.ytimg\.com|img\.youtube\.com)/vi(?:_webp)?/"
    r"([A-Za-z0-9_-]{11})/(?:default|mqdefault|hqdefault|sddefault|maxresdefault|"
    r"hq720|mq[1-3]|hq[1-3])\.(?:jpg|jpeg|webp)",
    re.I,
)
_X_STATUS_RE = re.compile(r"https?://(?:www\.)?(?:x|twitter)\.com/[^/\s]+/status/(\d+)", re.I)

PRIORITY_CDN_DOMAINS = (
    "static1.srcdn.com",              # ScreenRant
    "static1.colliderimages.com",     # Collider
    "static1.cbrimages.com",          # CBR
    "static1.moviewebimages.com",     # MovieWeb
    "static0.gamerantimages.com", "static1.gamerantimages.com",
    "static2.gamerantimages.com", "static3.gamerantimages.com",  # GameRant
    "static1.thegamerimages.com",     # TheGamer
)

FORBIDDEN_TEXT_EXACT: Set[str] = {
    "Your comment has not been saved",
}

FORBIDDEN_LABELS: Set[str] = {
    "Release Date", "Runtime", "Director", "Directors", "Writer", "Writers",
    "Producer", "Producers", "Cast"
}

JUNK_IMAGE_PATTERNS = (
    "placeholder", "sprite", "icon", "emoji", ".svg",
    # From user suggestion to filter out non-content images
    "cta", "read-more", "share", "logo", "banner",
    "lazyload", "fallback", "blank.gif", "spacer.gif", "1x1",
)
MIN_ACCEPTABLE_EXTRACT_WORDS = 150

# Blocos a ignorar (relacionados/sidebars/galerias etc.)
_BAD_SECTION_RX = re.compile(
    r"(related|trending|more|sidebar|aside|recommend|recommended|"
    r"gallery|carousel|slideshow|video|playlist|social|share|"
    r"footer|header|nav|subscribe|newsletter|ad|advert|sponsor|"
    # From user suggestion
    r"cta|banner|paid|outbrain|taboola|"
    r"screen-hub|screenhub|hub|most-popular|popular)",
    re.I
)


def _parse_srcset(srcset: str):
    """Retorna a URL com maior largura declarada em um srcset."""
    best = None
    best_w = -1
    for part in (srcset or "").split(","):
        part = part.strip()
        if not part:
            continue
        tokens = part.split()
        url = tokens[0]
        w = 0
        if len(tokens) > 1 and tokens[1].endswith("w"):
            try:
                w = int(tokens[1][:-1])
            except Exception:
                w = 0
        if w >= best_w:
            best_w = w
            best = url
    return best


def _has_bad_keyword(url: str) -> bool:
    u = url.lower()
    return any(k in u for k in BAD_IMAGE_KEYWORDS)

def _is_junk_filename(url: str) -> bool:
    """Checks if the image filename suggests it's a non-content image."""
    try:
        name = urlparse(url).path.rsplit("/", 1)[-1].lower()
        return any(snippet in name for snippet in JUNK_IMAGE_PATTERNS)
    except Exception:
        return False # Fail safe

def _has_small_thumbnail_width(url: str) -> bool:
    u = (url or "").lower()
    return '?w=300' in u or '?w=400' in u or '&w=300' in u or '&w=400' in u

def _passes_min_size(url: str, min_w: int = 600, min_h: int = 315) -> bool:
    w, h = _guess_dimensions_from_url(url)
    if w is None or h is None:
        # Sem dimensão explícita: aceita provisoriamente (muitos sites não expõem)
        return True
    if w < min_w or h < min_h:
        return False
    # evita quase-quadradas/estranhas como avatar 150x150
    ar = w / h if h else 0
    return 0.6 <= ar <= 2.2

def is_valid_article_image(url: str) -> bool:
    if not url or url.startswith('data:') :
        return False
    if _is_bad_domain(url):
        return False
    if _has_bad_keyword(url):
        return False
    if _is_junk_filename(url):
        return False
    if _has_small_thumbnail_width(url):
        return False
    if not _passes_min_size(url):
        return False
    return True


def _html_word_count(value: str) -> int:
    text = BeautifulSoup(value or "", "lxml").get_text(" ", strip=True)
    return len(re.findall(r"\b[\wÀ-ÿ]+\b", text))

def pick_featured_image(candidates: list[str]) -> Optional[str]:
    """Retorna a primeira imagem que passa no filtro."""
    for u in candidates:
        if is_valid_article_image(u):
            return u
    return None


def _abs(u: str, base: str) -> Optional[str]:
    if not u:
        return None
    u = html.unescape(u).strip()
    if not u or u.startswith("data:"):
        return None
    embedded_urls = list(re.finditer(r"https?://", u, flags=re.IGNORECASE))
    if len(embedded_urls) > 1:
        malformed = u
        u = u[embedded_urls[-1].start():]
        logger.warning(
            "[IMAGE_URL_REPAIRED] malformed=%s repaired=%s",
            malformed,
            u,
        )
    return urljoin(base, u)

def _extract_from_style(style_attr: str) -> Optional[str]:
    if not style_attr:
        return None
    m = re.search(r"url\((.*?)\)", style_attr)
    if not m:
        return None
    url = m.group(1).strip()
    if url.startswith("'") and url.endswith("'"):
        return url[1:-1]
    if url.startswith('"') and url.endswith('"'):
        return url[1:-1]
    return url

def _select_first_by_priority(soup: BeautifulSoup, selectors: List[str]):
    """Return the first element matching the highest-priority selector.

    ``soup.select_one("a, b")`` returns whichever element comes first in the
    *document*, not the first selector that matches. Site cleaners rely on
    selector priority (a specific article body must win over a generic
    wrapper), so the list is evaluated in order.
    """
    for selector in selectors:
        found = soup.select_one(selector)
        if found is not None:
            return found
    return None


def _find_article_body(soup: BeautifulSoup) -> BeautifulSoup:
    """
    Tenta localizar o nó raiz do corpo do artigo.
    - Prefere a tag <article>
    - Fallback para seletores comuns (article body/content)
    - Evita nós com classes/ids que casem _BAD_SECTION_RX
    - Fallback final: nó com mais <p> + <figure>
    """
    # 1. Encontre o contêiner principal do artigo
    article_body = soup.find('article')
    if article_body:
        return article_body

    logger.info("Could not find <article> tag, falling back to other selectors.")

    # Enhanced with user suggestions for more specific content containers
    candidates = soup.select(
        "article .entry-content, article .content, article [itemprop='articleBody'], "
        ".post-content, .single-content, .post-body, "
        "[itemprop='articleBody'], .article-body, .article-content"  # Original selectors
    )
    if not candidates:
        candidates = soup.find_all(True)

    best, best_score = None, -1
    for c in candidates:
        classes = " ".join(c.get("class", [])) + " " + (c.get("id") or "")
        if _BAD_SECTION_RX.search(classes):
            continue
        # Evita wrappers muito genéricos do site
        if c.name in ("header", "footer", "nav", "aside"):
            continue
        score = len(c.find_all("p")) + len(c.find_all("figure"))
        if score > best_score:
            best, best_score = c, score
    return best or soup

def collect_images_from_article(soup: BeautifulSoup, base_url: str) -> list[str]:
    """
    Coleta URLs de imagens relevantes SOMENTE DO CORPO DO ARTIGO.
    Fontes consideradas:
      - <img> (src, data-*, srcset)
      - <picture><source srcset="..."/>
      - nós com atributos data-*
      - estilos inline: background-image
      - <figure> contendo <img>
    Aplica filtros de junk/thumb e prioriza CDNs conhecidas.
    """
    root = _find_article_body(soup)
    urls: list[str] = []

    def _push(candidate: Optional[str]) -> None:
        if not candidate:
            return
        abs_u = _abs(candidate, base_url)
        if not abs_u:
            return
        if youtube_thumbnail_video_id(abs_u):
            return
        if not is_valid_article_image(abs_u):
            return
        urls.append(abs_u.rstrip("/"))

    # 1) <img> tags
    for img in root.select("img:not([aria-hidden='true'])"):
        cand = None
        for attr in ("src", "data-src", "data-original", "data-lazy-src", "data-image", "data-img-url"):
            if img.get(attr):
                cand = img.get(attr)
                break
        if not cand and img.get("srcset"):
            cand = _parse_srcset(img.get("srcset"))
        _push(cand)

    # 2) <picture><source>
    for source in root.select("picture source[srcset]"):
        _push(_parse_srcset(source.get("srcset", "")))

    # 2.5) <noscript> com <img> (fallback de lazy-load)
    for ns in root.find_all("noscript"):
        try:
            inner = BeautifulSoup(ns.string or "", "html.parser")
        except Exception:
            continue
        for img in inner.find_all("img"):
            _push(img.get("src") or img.get("data-src") or img.get("data-original"))

    # 3) nós com data-* comuns
    for node in root.select('[data-img-url], [data-image], [data-src], [data-original]') :
        cand = node.get("data-img-url") or node.get("data-image") or node.get("data-src") or node.get("data-original")
        _push(cand)

    # 4) estilos inline background-image
    for node in root.select('[style*="background-image"]') :
        _push(_extract_from_style(node.get("style", "")))

    # 5) <figure> contendo <img> (ou srcset)
    for fig in root.find_all("figure"):
        img = fig.find("img")
        if img:
            if img.get("src"):
                _push(img.get("src"))
            elif img.get("srcset"):
                _push(_parse_srcset(img.get("srcset", "")))

    # de-dup preservando preferência das CDNs
    dedup: dict[str, int] = {}
    for u in urls:
        host = urlparse(u).netloc
        pref = 0 if host in PRIORITY_CDN_DOMAINS else 1
        dedup[u] = min(dedup.get(u, pref), pref)
    ordered = sorted(dedup.items(), key=lambda kv: (kv[1], kv[0]))
    return [u for u, _ in ordered]


def youtube_thumbnail_video_id(value: str) -> str:
    """Return the YouTube video ID when value contains a known thumbnail URL."""
    match = _YT_THUMBNAIL_RE.search(str(value or ""))
    return match.group(1) if match else ""


def _x_embed_key(blockquote) -> str:
    for link in blockquote.find_all("a", href=True):
        if match := _X_STATUS_RE.search(link.get("href", "")):
            return match.group(1)
    return re.sub(r"\s+", " ", blockquote.get_text(" ", strip=True)).lower()


def _collect_x_embeds(soup: BeautifulSoup) -> list[str]:
    embeds = []
    seen = set()
    for blockquote in soup.select("blockquote.twitter-tweet"):
        key = _x_embed_key(blockquote)
        if not key or key in seen:
            continue
        seen.add(key)
        clean = BeautifulSoup(str(blockquote), "html.parser")
        for script in clean.find_all("script"):
            script.decompose()
        preserved = clean.find("blockquote")
        if preserved:
            embeds.append(str(preserved))
    return embeds


def _append_missing_x_embeds(container: BeautifulSoup, embeds: list[str]) -> None:
    target = container.body if getattr(container, "body", None) else container
    existing = {
        _x_embed_key(blockquote)
        for blockquote in target.select("blockquote.twitter-tweet")
    }
    for embed_html in embeds:
        fragment = BeautifulSoup(embed_html, "html.parser")
        blockquote = fragment.find("blockquote")
        if not blockquote:
            continue
        key = _x_embed_key(blockquote)
        if not key or key in existing:
            continue
        target.append(blockquote)
        existing.add(key)


def _html_word_count_without_x_embeds(content_html: str) -> int:
    soup = BeautifulSoup(content_html or "", "html.parser")
    for blockquote in soup.select("blockquote.twitter-tweet"):
        blockquote.decompose()
    return _html_word_count(str(soup))

# --- New helper functions from user prompt ---
def _get(url, timeout=25, tries=2):
    """Busca a pagina do artigo com destino verificado e corpo limitado.

    A URL vem do feed, ou seja, de fora. `requests.get(..., allow_redirects=True)`
    entregava o redirect sem exame nenhum: a primeira URL podia ser inocente e o
    `Location` apontar para a rede interna. Ver `app/safe_http.py`.

    `UnsafeUrlError` NAO e retentavel — insistir num destino proibido nao muda a
    resposta, so repete a tentativa de alcancar a rede interna.
    """
    last_err = None
    for _ in range(tries):
        try:
            resultado = safe_get(
                url,
                headers={"User-Agent": USER_AGENT},
                read_timeout=timeout,
                max_bytes=ARTICLE_MAX_BYTES,
            )
            tipo = (resultado.headers.get("Content-Type") or "")
            if 200 <= resultado.status_code < 300 and "text/html" in tipo:
                return _RespostaCompativel(resultado)
        except UnsafeUrlError:
            raise
        except Exception as e:
            last_err = e
        time.sleep(0.6)
    if last_err:
        raise last_err
    raise RuntimeError(f"HTTP error fetching {url}")


class _RespostaCompativel:
    """Casca com a forma de `requests.Response` que o resto do modulo espera.

    Trocar o cliente nao deveria obrigar a reescrever quem consome o resultado;
    aqui so os tres atributos usados a jusante sao expostos.
    """

    __slots__ = ("_resultado",)

    def __init__(self, resultado):
        self._resultado = resultado

    @property
    def content(self) -> bytes:
        return self._resultado.content

    @property
    def text(self) -> str:
        return self._resultado.content.decode("utf-8", errors="replace")

    @property
    def status_code(self) -> int:
        return self._resultado.status_code

    @property
    def headers(self) -> dict:
        return self._resultado.headers

    @property
    def url(self) -> str:
        return self._resultado.url

def _clean_text(s):
    if not s:
        return ""
    return re.sub(r"[ \t]+", " ", html.unescape(s)).strip()

def _collapse_ws(s: str) -> str:
    """Espacos em branco viram UM espaco. Complemento de `get_text(" ")`.

    `get_text(" ")` poe um separador entre cada pedaco de texto; onde a marcacao
    ja tinha espaco, sobram dois. Colapsar depois e o que deixa a legenda com o
    espacamento que o leitor veria.
    """
    if not s:
        return ""
    return re.sub(r"\s+", " ", str(s)).strip()

def _warn_trafilatura_unavailable(context: str) -> None:
    global _TRAFILATURA_WARNING_EMITTED
    if _TRAFILATURA_IMPORT_ERROR and not _TRAFILATURA_WARNING_EMITTED:
        logger.warning(
            "Trafilatura indisponivel por bloqueio/import (%s). Usando fallback em BeautifulSoup para %s.",
            _TRAFILATURA_IMPORT_ERROR,
            context,
        )
        _TRAFILATURA_WARNING_EMITTED = True

def _extract_content_html_fallback(html_text: str, url: str) -> Optional[str]:
    """
    Fallback generico quando o Trafilatura nao pode ser usado.
    Preserva o melhor container editorial em HTML.
    """
    soup = BeautifulSoup(html_text, "lxml")
    root = _find_article_body(soup)
    root_soup = BeautifulSoup(str(root), "lxml")
    container = root_soup.find("article") or root_soup.find("main") or root_soup.body or root_soup

    for el in container.select("script, style, noscript, form, button, svg, canvas"):
        el.decompose()

    for el in list(container.find_all(["header", "footer", "nav", "aside"])):
        el.decompose()

    for el in list(container.find_all(True)):
        attrs = " ".join(el.get("class", [])) + " " + (el.get("id") or "")
        if _BAD_SECTION_RX.search(attrs):
            el.decompose()
            continue
        if el.name in {"div", "section"}:
            keepable = el.find(["p", "img", "figure", "iframe", "blockquote", "ul", "ol", "h2", "h3", "h4"])
            if not keepable and not el.get_text(" ", strip=True):
                el.decompose()

    content_html = container.decode_contents() if hasattr(container, "decode_contents") else str(container)
    content_html = (content_html or "").strip()
    return content_html or None

def _trafilatura_extract_core(url, html_text): # Renamed to avoid conflict with class method
    if trafilatura is None or trafilatura_extract_metadata is None:
        _warn_trafilatura_unavailable(url)
        return None
    downloaded = trafilatura.extract(
        filecontent=html_text,
        url=url,
        include_images=False,
        include_links=False,
        with_metadata=True,
    )
    if not downloaded:
        return None
    meta = trafilatura_extract_metadata(html_text, url) # Use the imported metadata extractor
    return {
        "title": (meta.title if meta and meta.title else None),
        "text": downloaded.strip(),
        "author": (", ".join(meta.author) if meta and meta.author else None),
        "date": (meta.date if meta and meta.date else None),
        "top_image": None, # This will be filled by _pick_featured_image later
    }

def _wp_fallback(soup):
    # WordPress common selectors: title, content, author, date, image
    title = soup.select_one("h1.asset-title, h1.entry-title, h1.post-title, header h1") # Added asset-title for infomoney
    content = soup.select_one("div.article-content, div.entry-content, .single-post-content, .post-content, article .content")
    author = soup.select_one('[rel="author"], .author-name, .byline .author a, .byline a[rel="author"]')
    date = soup.select_one("time[datetime], .post-date, .entry-date")
    img = soup.select_one("article figure img, .wp-block-image img, .post-thumbnail img")
    return {
        "title": _clean_text(title.get_text()) if title else None,
        "text": _clean_text("\n".join([p.get_text(" ", strip=True) for p in content.select("p")])) if content else None,
        "author": _clean_text(author.get_text()) if author else None,
        "date": (date.get("datetime") if date and date.has_attr("datetime") else _clean_text(date.get_text()) if date else None),
        "top_image": (img.get("src") if img and img.has_attr("src") else None),
    }

def _estadao_arc_fallback(soup):
    # Estadão (Arc): title and body are often in <article> with specific blocks
    title = soup.select_one("h1.n--noticia__title, h1, header h1") # Added n--noticia__title for estadao
    paras = soup.select("[data-qa='body-text']") or soup.select("article p")
    text = _clean_text("\n".join(p.get_text(" ", strip=True) for p in paras)) if paras else None
    author = soup.select_one("[data-qa='author-name'], .author-name, a[rel='author']")
    date = soup.select_one("time[datetime]")
    img = soup.select_one("figure img, .lead-media img")
    return {
        "title": _clean_text(title.get_text()) if title else None,
        "text": text,
        "author": _clean_text(author.get_text()) if author else None,
        "date": date.get("datetime") if date and date.has_attr("datetime") else None,
        "top_image": img.get("src") if img and img.has_attr("src") else None,
    }

def _choose_best(a, b):
    # Fills empty fields in A with values from B
    if not a:
        return b
    if not b:
        return a
    out = {}
    for k in {"title","text","author","date","top_image"}:
        out[k] = a.get(k) or b.get(k)
    return out

def _extract_site_specific(soup: BeautifulSoup, url: str, selectors: Dict[str, Union[str, List[str]]]) -> Optional[Dict[str, Any]]:
    """
    Helper for site-specific extraction using a dictionary of CSS selectors.
    Falls back gracefully by returning None if key elements are not found.
    """
    try:
        # Find title
        title_tag = soup.select_one(str(selectors['title']))
        title = title_tag.get_text(strip=True) if title_tag else None

        # Find content body
        content_tag = soup.select_one(str(selectors['content']))

        if not title or not content_tag:
            logger.warning(f"Specific extractor failed to find title/content for {url}. Will fall back to generic.")
            return None

        # Basic cleanup inside content
        for junk_selector in selectors.get('junk', []):
            for junk_tag in content_tag.select(str(junk_selector)):
                junk_tag.decompose()

        content_html = str(content_tag)

        # Use existing helpers for media and metadata
        # Note: These helpers operate on the *original* soup object to find meta tags, etc.
        extractor = ContentExtractor() # Temporary instance to access helpers
        featured_image_url = extractor._pick_featured_image(soup, url)
        images = collect_images_from_article(soup, url) # This also uses its own logic to find the body
        videos = extractor._extract_youtube_videos(soup)

        excerpt_tag = soup.select_one('meta[name="description"], meta[property="og:description"]')
        excerpt = excerpt_tag['content'].strip() if excerpt_tag and excerpt_tag.get('content') else ''

        # Ensure the featured image isn't duplicated in the body images list
        other_images = [img for img in images if img != featured_image_url]

        result = {
            "title": title,
            "content": content_html,
            "excerpt": excerpt,
            "featured_image_url": featured_image_url,
            "images": other_images,
            "videos": videos,
            "source_url": url,
        }

        logger.info(f"Successfully extracted content using specific extractor for {url}. Title: {result['title'][:50]}...")
        return result

    except Exception as e:
        # Log with exc_info=False to avoid a huge traceback for a common fallback case
        logger.error(f"Error in site-specific extractor for {url}: {e}. Falling back to generic.", exc_info=False)
        return None

def _extract_json_ld(soup: BeautifulSoup) -> List[Dict[str, Any]]:
    """
    Encontra e parseia todos os scripts do tipo ld+json da página.
    """
    json_ld_data = []
    scripts = soup.find_all('script', type='application/ld+json')
    for script in scripts:
        if script.string:
            try:
                # Corrigir JSONs malformados com vírgulas extras
                clean_str = re.sub(r',\s*([\}\]])', r'\1', script.string)
                data = json.loads(clean_str)
                if isinstance(data, dict):
                    json_ld_data.append(data)
                elif isinstance(data, list):
                    json_ld_data.extend([d for d in data if isinstance(d, dict)])
            except json.JSONDecodeError:
                logger.warning("Falha ao parsear script JSON-LD.", exc_info=False)
    return json_ld_data

def _find_news_article_in_json_ld(json_ld_data: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Busca nos dados JSON-LD parseados por um objeto NewsArticle, Article ou BlogPosting.
    """
    for data in json_ld_data:
        graph = data.get('@graph', [data])
        for item in graph:
            if isinstance(item, dict) and item.get('@type') in ('NewsArticle', 'Article', 'BlogPosting'):
                return item
    return None

# --- New constants for related content removal ---
LEIA_HEADING_RE = re.compile(r"(leia também|veja também|relacionad[oa]s|recomendad[oa]s|tópicos relacionados)", re.I)

# Site-specific rules for related content
SITE_SPECIFIC_RELATED_SELECTORS = {
    "infomoney.com.br": [
        ".single__related", ".article__related", ".post-related", ".related-posts",
        ".rm-related", ".block-related", ".single__sidebar", ".article__sidebar",
        "section.single__see-also", ".wp-block-infomoney-blocks-infomoney-read-more",
    ],
    "estadao.com.br": [
        ".links-relacionados", ".mat-relacionadas", ".es-relacionadas",
        ".stories-related", ".see-also", ".link-relacionado", ".box-relacionadas",
    ],
}

# English common words for simple caption language detection
ENGLISH_COMMON_WORDS = {
    'the', 'and', 'in', 'as', 'from', 'to', 'at', 'by', 'for', 'with',
    'is', 'are', 'was', 'been', 'be', 'have', 'has', 'had', 'do', 'does',
    'did', 'will', 'would', 'could', 'should', 'may', 'can', 'might',
    'of', 'or', 'a', 'an', 'on', 'it', 'that', 'this', 'which', 'who',
    'what', 'where', 'when', 'why', 'how', 'all', 'each', 'every', 'both',
}

# Portuguese common words
PORTUGUESE_COMMON_WORDS = {
    'o', 'a', 'os', 'as', 'e', 'é', 'em', 'de', 'da', 'do', 'das', 'dos',
    'um', 'uma', 'uns', 'umas', 'para', 'por', 'que', 'com', 'seu', 'sua',
    'seus', 'suas', 'mais', 'como', 'mas', 'foi', 'é', 'está', 'estão',
    'são', 'não', 'tem', 'temos', 'tenho', 'tinha', 'ser', 'estar', 'ir',
    'vou', 'vai', 'vão', 'foram', 'ele', 'ela', 'eles', 'elas', 'nós',
}

def _is_likely_english_caption(text: str) -> bool:
    """
    Heuristic to detect if caption text is likely in English.
    Returns True if it seems to be English, False otherwise.
    """
    if not text or len(text.strip()) < 3:
        return False

    text_lower = text.lower().strip()
    words = re.findall(r'\b\w+\b', text_lower)

    if len(words) < 2:
        return False

    # Count how many words are common English vs Portuguese words
    english_word_count = sum(1 for w in words if w in ENGLISH_COMMON_WORDS)
    portuguese_word_count = sum(1 for w in words if w in PORTUGUESE_COMMON_WORDS)

    # If more Portuguese words than English, likely Portuguese
    if portuguese_word_count > english_word_count:
        return False

    # Count capitalized words (likely proper nouns) - if many, ignore them
    capitalized_words = sum(1 for w in re.findall(r'\b\w+\b', text) if w[0].isupper())

    # If text is mostly proper nouns (e.g., "Tom Holland in Spider-Man"), check other signals
    if capitalized_words > len(words) * 0.4:  # More than 40% capitalized
        # Check for common prepositions "in", "as", "from" which are very English
        if any(prep in text_lower for prep in [' in ', ' as ', ' from ']):
            # Check if these prepositions connect English common words
            if english_word_count >= 1:  # At least one English common word
                return True

    # If more than 30% of words are common English words, likely English
    if len(words) > 0 and english_word_count / len(words) > 0.3:
        return True

    # Also check for typical English article structure (starts with article + noun)
    if text_lower.startswith(('the ', 'a ', 'an ', 'this ', 'that ')):
        return True

    return False

def _image_caption_keys(image_url: str) -> list[str]:
    parsed = urlparse(html.unescape(image_url or ""))
    path = unquote(parsed.path or image_url or "").strip().lower()
    basename = path.rsplit("/", 1)[-1]
    return [key for key in (basename, path) if key]


def _store_image_caption(captions: Dict[str, str], image_url: str, caption: str) -> None:
    for key in _image_caption_keys(image_url):
        captions.setdefault(key, caption)


def _capture_figure_caption(figcaption, caption_text: str, base_url: str = "") -> Dict[str, str]:
    captions: Dict[str, str] = {}
    figure = figcaption.find_parent("figure") or figcaption.parent
    if not figure:
        return captions
    for img in figure.find_all("img"):
        candidate = (
            img.get("src") or img.get("data-src") or img.get("data-original")
            or img.get("data-lazy-src") or img.get("data-image") or img.get("data-img-url")
        )
        if not candidate and img.get("srcset"):
            candidate = _parse_srcset(img.get("srcset", ""))
        if candidate:
            _store_image_caption(captions, urljoin(base_url, candidate), caption_text)
    return captions


def _merge_image_captions(*caption_maps: Dict[str, str]) -> Dict[str, str]:
    merged: Dict[str, str] = {}
    for caption_map in caption_maps:
        for key, value in (caption_map or {}).items():
            if key and value:
                merged.setdefault(key, value)
    return merged


def collect_image_captions_from_article(soup: BeautifulSoup, base_url: str = "") -> Dict[str, str]:
    try:
        root = _find_article_body(soup)
    except Exception as exc:
        logger.warning("collect_image_captions_from_article: _find_article_body failed (%s) — returning empty", exc)
        return {}
    captions: Dict[str, str] = {}
    try:
        for figure in root.find_all("figure"):
            figcaption = figure.find("figcaption")
            caption_text = figcaption.get_text(" ", strip=True) if figcaption else ""
            if not caption_text:
                continue
            for img in figure.find_all("img"):
                candidate = (
                    img.get("src") or img.get("data-src") or img.get("data-original")
                    or img.get("data-lazy-src") or img.get("data-image") or img.get("data-img-url")
                )
                if not candidate and img.get("srcset"):
                    candidate = _parse_srcset(img.get("srcset", ""))
                if candidate:
                    _store_image_caption(captions, urljoin(base_url, candidate), caption_text)
    except Exception as exc:
        logger.warning("collect_image_captions_from_article: error iterating figures (%s) — returning partial", exc)
    return captions


def _clean_english_captions(soup: BeautifulSoup, domain: str, base_url: str = "") -> Dict[str, str]:
    """
    Remove or blank out figcaption elements that are in English.
    This preserves the figure structure but removes non-Portuguese text.
    """
    captured: Dict[str, str] = {}
    for figcaption in soup.find_all('figcaption'):
        # COM separador. Sem ele o BeautifulSoup emenda os pedacos: a legenda
        # `<span>'Made in Korea' Season 2</span><span>Disney/Hulu</span>` saia
        # como `Season 2Disney/Hulu`, duas palavras coladas, e essa legenda vira
        # texto publicado (`alt` da imagem, credito). Ver
        # `collect_image_captions_from_article`, que ja fazia certo.
        caption_text = _collapse_ws(figcaption.get_text(" ", strip=True))
        if caption_text and _is_likely_english_caption(caption_text):
            captured.update(_capture_figure_caption(figcaption, caption_text, base_url))
            logger.info(f"INFO ({domain}): Removendo legenda em inglês: {caption_text[:60]}")
            # Blank out the caption instead of removing it to preserve structure
            figcaption.string = ""
    setattr(soup, "_mn_image_captions", captured)
    return captured


class ContentExtractor:
    """Extrai e limpa conteúdo para o pipeline."""
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': USER_AGENT})

    def _fetch_html(self, url: str) -> Optional[str]:
        try:
            resp = self.session.get(url, timeout=20.0, allow_redirects=True)
            resp.raise_for_status()
            return resp.text
        except requests.RequestException as e:
            logger.error(f"Failed to fetch HTML from {url}: {e}")
            return None

    def _pre_clean_html(self, soup: BeautifulSoup, url: str):
        """Remove widgets/ads/blocos óbvios ANTES da extração."""
        # --- New robust related content removal logic from user patch ---
        try:
            # 1. Remove sections by heading text (e.g., "Leia também")
            # Iterate backwards to avoid issues with modifying the list while iterating
            for h in reversed(soup.find_all(re.compile("^h[1-6]$"))):
                heading_text = h.get_text(" ", strip=True)
                if heading_text and LEIA_HEADING_RE.search(heading_text):
                    parent_container = h.find_parent(('section', 'aside', 'div'))
                    if parent_container and len(parent_container.find_all(re.compile("^h[1-6]$"))) <= 2:
                        logger.debug(f"Decomposing parent container '{parent_container.name}' of related heading: {heading_text}")
                        parent_container.decompose()
                    else:
                        logger.debug(f"Decomposing related heading and its sibling: {heading_text}")
                        next_sibling = h.find_next_sibling()
                        if next_sibling and next_sibling.name in ("div", "ul", "section", "ol"):
                            next_sibling.decompose()
                        h.decompose()

            # 2. Remove by site-specific selectors
            source_host = (urlparse(url).hostname or "").replace("www.", "")
            if source_host in SITE_SPECIFIC_RELATED_SELECTORS:
                for sel in SITE_SPECIFIC_RELATED_SELECTORS[source_host]:
                    for el in soup.select(sel):
                        el.decompose()

            # 3. Remove links that are likely related content wrappers
            for a in soup.select("a"):
                cls = " ".join(a.get("class", [])).lower()
                if any(k in cls for k in ["relacion", "related", "leia", "veja"]) or a.get("data-gtm-cta") in ("related", "see_more"):
                    a.decompose()

        except Exception as e:
            logger.warning(f"Error during advanced related content removal for {url}: {e}", exc_info=False)
        # --- End of new logic ---

        # Merged list from original and user suggestions for more robust cleaning
        selectors_to_remove = {
            # User-suggested selectors for CTAs, ads, and social sharing
            ".cta-middle", ".infomoney-read-more", ".read-more", ".post__related",
            ".sharing", ".share", ".social", ".banner", ".ads", ".advertisement",
            "[data-ad]", "[data-ad-slot]",
            ".sponsored", ".paid-content", ".partner", ".outbrain", ".taboola",

            # Original selectors
            '[class*="srdb"]', '[class*="rating"]', '.review', '.score', '.meter',
            'header', 'footer', 'nav', 'aside',
            '[class*="related"]', '[id*="related"]',
            # From user's patch (GENERIC_REL_SELECTORS)
            "[class*='relacionad']", "[class*='relaciona']", "[class*='recommend']",
            "[class*='veja-tambem']", "[class*='leia-tambem']", "[id*='relacionad']",
            "[id*='leia']", "section[aria-label*='Leia']", "section[aria-label*='Relacionad']",
            '[class*="trending"]', '[id*="trending"]', 'div.widget',
            '[class*="sidebar"]',  '[id*="sidebar"]',
            '[class*="recommend"]','[class*="recommended"]',
            '[class*="screen-hub"]','[class*="screenhub"]',
            '[class*="most-popular"]','[id*="most-popular"]',
            '[class*="popular"]','[id*="popular"]',
            '[class*="newsletter"]','[id*="newsletter"]',
            '[class*="ad-"]','[id*="ad-"]','[class*="advert"]','[id*="advert"]',
            '.comments', '#comments',
            '.author', '.author-box', '.post-author', '.byline', '.entry-author',
            '.avatar', '.author__image', '.author-profile',
            '.subscribe',
        }
        for sel in selectors_to_remove:
            for el in soup.select(sel):
                try:
                    el.decompose()
                except Exception:
                    pass

        # remover texto "powered by srdb"
        for text_node in soup.find_all(string=lambda t: isinstance(t, str) and "powered by srdb" in t.lower()):
            p = text_node.find_parent()
            if p:
                try:
                    p.decompose()
                except Exception:
                    pass

        logger.info("Pre-cleaned HTML, removing unwanted widgets and blocks.")

    def _remove_forbidden_blocks(self, soup: BeautifulSoup) -> None:
        """Remove infobox técnica e mensagens indesejadas do html extraído."""
        for t in soup.find_all(string=True):
            s = (t or "").strip()
            if s and s in FORBIDDEN_TEXT_EXACT:
                try:
                    t.parent.decompose()
                except Exception:
                    pass

        candidates = []
        for tag in soup.find_all(["div", "section", "aside", "ul", "ol"]):
            text = " ".join(tag.get_text(separator="\n").split())
            lbl_count = sum(1 for lbl in FORBIDDEN_LABELS
                            if re.search(rf"(^|\n)\s*{re.escape(lbl)}\s*(\n|$|:)", text, flags=re.I))
            if lbl_count >= 2:
                candidates.append(tag)
        for c in candidates:
            try:
                c.decompose()
            except Exception:
                pass

        for tag in soup.find_all(["p", "li", "span", "h3", "h4"]):
            if not tag.parent:
                continue
            s = (tag.get_text() or "").strip().rstrip(':').strip()
            if s in FORBIDDEN_TEXT_EXACT or s in FORBIDDEN_LABELS:
                try:
                    tag.decompose()
                except Exception:
                    pass

    def _convert_data_img_to_figure(self, soup: BeautifulSoup):
        """
        Converte divs com 'data-img-url' em <figure><img>.
        Faz APENAS dentro do corpo do artigo para não pegar sidebar.
        """
        root = _find_article_body(soup)
        converted = 0
        for div in root.select('div[data-img-url]') :
            img_url = div['data-img-url']
            fig = soup.new_tag('figure')
            img = soup.new_tag('img', src=img_url)
            cap = soup.new_tag('figcaption')
            # Mesmo defeito de `_clean_english_captions`: o `div` costuma ter
            # legenda e credito em elementos irmaos, e sem separador os dois
            # saem colados — aqui direto no `alt` da imagem.
            caption_text = _collapse_ws(div.get_text(" ", strip=True))
            if caption_text:
                cap.string = caption_text
                img['alt'] = caption_text
            fig.append(img)
            if caption_text:
                fig.append(cap)
            try:
                div.replace_with(fig)
                converted += 1
            except Exception:
                pass
        if converted:
            logger.info(f"Converted {converted} 'data-img-url' divs to <figure> tags.")

    def _pick_featured_image(self, soup: BeautifulSoup, base_url: str) -> Optional[str]:
        """
        Encontra a melhor imagem destacada de um artigo seguindo uma ordem de prioridade.
        1. Metatag Open Graph (og:image) - Mais confiável.
        2. Dados Estruturados JSON-LD - Muito confiável.
        3. Maior imagem dentro da tag <article> - Bom fallback.
        """

        # --- Prioridade 1: Metatag Open Graph (og:image) ---
        # Quase todos os sites usam isso para compartilhar em redes sociais.
        og_image = soup.find('meta', property='og:image')
        if og_image and og_image.get('content'):
            logger.info("[SUCCESS] Imagem encontrada com sucesso via Open Graph (og:image).")
            return urljoin(base_url, og_image['content'])

        # --- Prioridade 2: Dados Estruturados (JSON-LD) ---
        # Muitos sites usam isso para SEO e para o Google.
        json_ld_script = soup.find('script', type='application/ld+json')
        if json_ld_script and json_ld_script.string:
            try:
                data = json.loads(json_ld_script.string)

                # O JSON-LD pode ser uma lista (com @graph) ou um objeto.
                if isinstance(data, list):
                    data = data[0]

                image_data = data.get('image')
                if image_data:
                    # A imagem pode ser uma lista, um objeto ou uma string.
                    image_url = None
                    if isinstance(image_data, list):
                        image_data = image_data[0]

                    if isinstance(image_data, dict) and image_data.get('url'):
                        image_url = image_data['url']
                    elif isinstance(image_data, str):
                        image_url = image_data

                    if image_url:
                        logger.info("[SUCCESS] Imagem encontrada com sucesso via JSON-LD.")
                        return urljoin(base_url, image_url)

            except (json.JSONDecodeError, KeyError, TypeError, IndexError):
                # Ignora erros se o JSON-LD estiver mal formatado ou não tiver a imagem.
                pass

        # --- Prioridade 3: Maior imagem dentro da tag <article> (Fallback) ---
        # Se os métodos acima falharem, procura a maior imagem dentro do corpo do artigo.
        article_tag = soup.find('article')
        if article_tag:
            max_area = 0
            best_image_url = None
            for img in article_tag.find_all('img'):
                src = img.get('src') or img.get('data-src')
                if not src:
                    continue

                width_str = img.get('width', '0')
                height_str = img.get('height', '0')
                # Garante que width e height são numéricos antes de converter
                if width_str.isdigit() and height_str.isdigit():
                    width = int(width_str)
                    height = int(height_str)
                    area = width * height
                    if area > max_area:
                        max_area = area
                        best_image_url = src

            if best_image_url:
                logger.warning("[WARNING] Imagem encontrada via método de fallback (maior imagem no artigo).")
                return urljoin(base_url, best_image_url)

        logger.warning(f"[ERROR] Nenhuma imagem destacada confiável foi encontrada para {base_url}.")
        return None

    def _extract_youtube_id(self, url: str, soup: Optional[BeautifulSoup] = None) -> Optional[str]:
        """
        Extracts a YouTube video ID from a URL using various patterns.
        Optionally uses the soup object to find a fallback ID in meta tags.
        """
        if not url:
            return None

        # 1) Common patterns (embed/shorts/youtu.be)
        for pattern in _YT_PATTERNS:
            m = re.search(pattern, url)
            if m:
                return m.group(1)

        # 2) watch?v=ID
        try:
            pu = urlparse(url)
            if "youtube.com" in pu.netloc and pu.path == "/watch":
                q = parse_qs(pu.query)
                if "v" in q and len(q["v"][0]) == 11:
                    return q["v"][0]
        except Exception:
            pass

        # 3) Optional fallback using og:image (only if soup is provided)
        if soup is not None:
            try:
                og = soup.find("meta", property="og:image")
                if og and og.get("content"):
                    # og:image often ends with .../<ID>/hqdefault.jpg
                    mm = re.search(r"/([A-Za-z0-9_-]{11})/hqdefault", og["content"])
                    if mm:
                        return mm.group(1)
            except Exception:
                pass

        return None

    def _extract_youtube_videos(self, soup: BeautifulSoup) -> list[dict]:
        ids = []
        for iframe in soup.find_all("iframe"):
            vid = self._extract_youtube_id(iframe.get("src", ""), soup=soup)
            if vid:
                ids.append(vid)
        for div in soup.select('.w-youtube[id], .youtube[id], [data-youtube-id]') :
            vid = div.get("id") or div.get("data-youtube-id")
            if vid:
                ids.append(vid)
        seen, ordered = set(), []
        for v in ids:
            if v and v not in seen:
                seen.add(v)
                ordered.append(v)
        if ordered:
            logger.info(f"Found {len(ordered)} unique YouTube videos.")
        return [{"id": v, "embed_url": f"https://www.youtube.com/embed/{v}",
                 "watch_url": f"https://www.youtube.com/watch?v={v}"} for v in ordered]

    def _extract_generic_content_html(self, soup: BeautifulSoup, url: str) -> Optional[str]:
        cleaned_html_str = str(soup)
        if trafilatura is None:
            _warn_trafilatura_unavailable(url)
            return _extract_content_html_fallback(cleaned_html_str, url)

        try:
            content_html = trafilatura.extract(
                cleaned_html_str,
                include_images=False,
                include_links=True,
                include_comments=False,
                include_tables=False,
                output_format='html'
            )
        except Exception as exc:
            logger.warning(f"Trafilatura falhou durante a extracao de {url}: {exc}. Usando fallback em BeautifulSoup.")
            return _extract_content_html_fallback(cleaned_html_str, url)

        if content_html:
            return content_html

        logger.warning(f"Trafilatura retornou conteudo vazio para {url}. Usando fallback em BeautifulSoup.")
        return _extract_content_html_fallback(cleaned_html_str, url)

    def _extract_with_trafilatura(self, html: str, url: str) -> Optional[Dict[str, Any]]:
        """
        Generic extraction method using Trafilatura as the core engine.
        This was the original `extract` method.
        """
        logger.debug(f"Using generic (trafilatura) extractor for {url}")
        try:
            soup = BeautifulSoup(html, 'lxml')

            # Preserve twitter embeds
            twitter_embeds = soup.find_all('blockquote', class_='twitter-tweet')
            featured_image_url = self._pick_featured_image(soup, url)

            # 1) Tenta extrair metadados de JSON-LD primeiro, pois é a fonte mais confiável
            all_json_ld = _extract_json_ld(soup)
            news_article_schema = _find_news_article_in_json_ld(all_json_ld)

            # 2) limpeza prévia pesada
            self._pre_clean_html(soup, url)

            # 5) Extrai imagens do corpo do artigo
            image_captions = _merge_image_captions(
                getattr(soup, "_mn_image_captions", {}) or {},
                collect_image_captions_from_article(soup, base_url=url),
            )
            body_images = collect_images_from_article(soup, base_url=url)

            # 6) vídeos
            videos = self._extract_youtube_videos(soup)

            # 7) metadados: Prioriza JSON-LD, com fallback para tags meta
            title = 'No Title Found'
            excerpt = ''
            if news_article_schema:
                logger.info(f"Usando metadados do JSON-LD para {url}")
                title = news_article_schema.get('headline') or news_article_schema.get('name') or title
                excerpt = news_article_schema.get('description') or excerpt
                if not featured_image_url:
                    featured_image_url = _coerce_url(news_article_schema.get('image'))
            else: # Fallback
                title = (og_title.get('content') if (og_title := soup.find('meta', property='og:title')) else None) or (soup.title.string if soup.title else title)
                excerpt = (meta_desc.get('content') if (meta_desc := soup.find('meta', attrs={'name': 'description'})) else None) or \
                          (og_desc.get('content') if (og_desc := soup.find('meta', property='og:description')) else '')

            # 8) extrair corpo com trafilatura ou fallback em BeautifulSoup
            content_html = self._extract_generic_content_html(soup, url)
            if not content_html:
                logger.warning(f"Generic extraction returned empty content for {url}")
                return None

            # Append twitter embeds back
            if twitter_embeds:
                for embed in twitter_embeds:
                    content_html += str(embed)

            # 9) pós-processar corpo
            article_soup = BeautifulSoup(content_html, 'lxml')
            self._remove_forbidden_blocks(article_soup)

            # 9.5) REMOVER CTAs AGRESSIVAMENTE (NOVO)
            # Remove qualquer parágrafo/div que contenha frases de CTA
            cta_phrases = [
                'thank you for reading',
                "don't forget to subscribe",
                'subscribe now',
                'click here',
                'read more',
                'sign up',
                'thanks for reading',
                'thanks for visiting',
                'please subscribe',
                'subscribe to our',
                'stay tuned',
                'keep up to date',
                'follow us',
                'obrigado por ler',
                'obrigada por ler',
                'não esqueça de se inscrever',
                'se inscreva',
                'clique aqui',
                'leia mais',
                'cadastre-se',
            ]

            for elem in list(article_soup.find_all(['p', 'div', 'span', 'article', 'blockquote', 'section'])):
                if not elem.parent:
                    continue
                text = (elem.get_text(strip=True) or "").lower()
                if any(cta in text for cta in cta_phrases):
                    logger.warning(f"CTA REMOVIDO: {text[:60]}")
                    elem.decompose()

            # 10) Seleciona imagens do corpo (excluindo a destacada)
            # A `collect_images_from_article` já aplica `is_valid_article_image`
            other_valid_images = [
                u for u in body_images if u != featured_image_url
            ]

            # Create figure tags for body images
            body_images_html_list = [f'<figure><img src="{url}" alt=""><figcaption></figcaption></figure>' for url in other_valid_images]


            logger.info(f"Selected featured image: {featured_image_url}. Found {len(other_valid_images)} other valid images.")

            # Conteúdo final: só o conteúdo interno do <body>, se existir
            if article_soup.body:
                final_content_html = article_soup.body.decode_contents()
            else:
                final_content_html = str(article_soup)
            result = {
                "title": title.strip(),
                "content": final_content_html,
                "excerpt": (excerpt or "").strip(),
                "featured_image_url": featured_image_url,
                "images": body_images_html_list,
                "image_captions": image_captions,
                "videos": videos,
                "source_url": url,
                "schema_original": news_article_schema # Passa o schema extraído adiante
            }
            logger.info(f"Successfully extracted and cleaned content from {url}. Title: {result['title'][:50]}...")
            return result

        except Exception as e:
            logger.error(f"An unexpected error occurred during extraction for {url}: {e}", exc_info=True)
            return None

    def _clean_html_for_collider(self, soup: BeautifulSoup) -> Optional[BeautifulSoup]:
        """
        Limpador MUITO AGRESSIVO para COLLIDER.COM.
        Remove widgets, CTAs, figuras indesejadas, SVGs decorativos, e outros blocos.
        Mantém APENAS paragrafos, headings, blockquotes e figuras legítimas com contexto.
        """
        # 1. Isolar o article-body específico do Collider
        article_body = _select_first_by_priority(
            soup, ['#article-body', '.article-body', '[itemprop="articleBody"]']
        )
        if not article_body:
            article_container = soup.find('article')
            if article_container:
                article_body = article_container
            else:
                logger.error("ERRO CRÍTICO (Collider): Nenhum contêiner de artigo encontrado.")
                return None

        logger.info("INFO (Collider): Iniciando limpeza AGRESSIVA de widgets, CTAs, e figuras indesejadas...")

        # 2. Remove scripts e styles PRIMEIRO
        for element in article_body.find_all(['script', 'style']):
            element.decompose()

        # 3. Remove TODOS os elementos com padrões de classe indesejados (MUITO AGRESSIVO)
        class_patterns_to_remove = [
            r'tag-interaction',           # Tag interaction widgets
            r'w-display-card',            # Collider display cards
            r'w-quick-action-sidebar',    # Quick action sidebars
            r'display-card',              # Any display card
            r'sidebar',                   # Sidebars
            r'related',                   # Related content
            r'recommended',               # Recommended sections
            r'trending',                  # Trending
            r'widget',                    # Widgets
            r'ad-',                       # Ads
            r'banner',                    # Banners
            r'promoted',                  # Promoted content
            r'sponsored',                 # Sponsored content
            r'carousel',                  # Carousels
            r'gallery',                   # Galleries
        ]

        for elem in list(article_body.find_all(True)):
            if not elem.parent:
                continue

            elem_classes = " ".join(elem.get('class', []))
            elem_id = elem.get('id', '')

            # Verificar se algum padrão de classe ou ID bate
            for pattern in class_patterns_to_remove:
                if re.search(pattern, elem_classes, re.I) or re.search(pattern, elem_id, re.I):
                    logger.info(f"INFO (Collider): Removendo widget/bloco com classe/id: '{elem_classes}' / '{elem_id}'")
                    elem.decompose()
                    break

        # 4. Remove elementos com atributos data-is-tag-interaction
        for elem in list(article_body.find_all(True)):
            if not elem.parent:
                continue

            if elem.get('data-is-tag-interaction'):
                logger.info("INFO (Collider): Removendo elemento com data-is-tag-interaction")
                elem.decompose()

        # 5. Remove aside tags e sidebars
        for elem in list(article_body.find_all('aside')):
            logger.info("INFO (Collider): Removendo tag <aside>")
            elem.decompose()

        # 6. Remove figuras indesejadas (SVGs, logos, imagens sem contexto)
        for fig in list(article_body.find_all('figure')):
            if not fig.parent:
                continue

            img = fig.find('img')
            if not img:
                logger.info("INFO (Collider): Removendo figura vazia (sem <img>)")
                fig.decompose()
                continue

            src = img.get('src', '').lower()
            alt = img.get('alt', '').lower()

            # Remove SVGs decorativos, logos, e imagens com padrões ruins
            if '.svg' in src or 'logo' in src or 'icon' in src or 'sr-db' in src or 'sr-db' in alt:
                logger.info(f"INFO (Collider): Removendo figura decorativa/logo: {src}")
                fig.decompose()
                continue

            # CRUCIAL: Remove imagens de THUMBNAIL/PEQUENO TAMANHO
            # Sites usam ?w=300, ?w=400 para widgets, carousels, e blocos relacionados
            if '?w=300' in src or '?w=400' in src or '&w=300' in src or '&w=400' in src:
                logger.info(f"INFO (Collider): Removendo figura de thumbnail (?w=300/400): {src}")
                fig.decompose()
                continue

            # Remove figuras órfãs (sem parágrafo antes ou depois)
            prev_p = fig.find_previous(['p', 'h2', 'h3', 'blockquote'])
            next_p = fig.find_next(['p', 'h2', 'h3', 'blockquote'])

            if not prev_p and not next_p:
                logger.info("INFO (Collider): Removendo figura órfã (sem contexto textual)")
                fig.decompose()

        # 7. Remove CTAs e "Thank you" messages
        for elem in list(article_body.find_all(['p', 'div', 'span', 'article', 'blockquote', 'section'])):
            if not elem.parent:
                continue

            text = (elem.get_text(strip=True) or "").lower()

            # Remove CTAs - lista mais agressiva
            if any(cta in text for cta in [
                'thank you for reading',
                "don't forget to subscribe",
                'subscribe now',
                'click here',
                'read more',
                'sign up',
                'thanks for reading',
                'thanks for visiting',
                'please subscribe',
                'subscribe to our',
                'stay tuned',
                'keep up to date',
                'follow us',
            ]):
                logger.info(f"INFO (Collider): Removendo CTA: {text[:60]}")
                elem.decompose()

        # 8. Remove English captions from images
        _clean_english_captions(article_body, "Collider")

        logger.info("INFO (Collider): Limpeza agressiva concluída. Retornando HTML final.")
        return article_body

    def _clean_html_for_gamerant(self, soup: BeautifulSoup) -> Optional[BeautifulSoup]:
        """
        VERSÃO FINAL para GAMERANT.COM.
        Remove tag-interaction widgets, display-card widgets, e outros blocos indesejados.
        Mantém APENAS o conteúdo textual do artigo.
        """
        # 1. Isolar o <article> ou article-body
        article_container = soup.find('article')
        if not article_container:
            # Tentar encontrar por classe article-body
            article_container = soup.select_one('[id*="article"], [class*="article-body"], .article-body')

        if not article_container:
            logger.error("ERRO CRÍTICO (GameRant): Nenhum contêiner de artigo encontrado.")
            return None

        # 2. Encontrar o body do artigo
        article_body = article_container.select_one('#article-body, .article-body, [itemprop="articleBody"]')
        if not article_body:
            article_body = article_container

        logger.info("INFO (GameRant): Iniciando limpeza de widgets e blocos indesejados...")

        # 3. Remove scripts e styles PRIMEIRO
        for element in article_body.find_all(['script', 'style']):
            element.decompose()

        # 4. Remove elementos com padrões de classe específicos (usando regex) - AGRESSIVO
        class_patterns_to_remove = [
            r'tag-interaction',        # Tag interaction widgets (like/follow/rating)
            r'display-card',           # Display cards (sidebars, related content)
            r'quick-action-sidebar',   # Quick action sidebars
            r'sidebar',                # Any sidebar
            r'related',                # Related content
            r'recommended',            # Recommended sections
            r'author-profile',         # Author profile cards
            r'trending',               # Trending sections
            r'widget',                 # Widget containers
            r'ad-',                    # Ad containers
            r'banner',                 # Banners
            r'gallery',                # Gallery containers
            r'carousel',               # Carousel containers
            r'promoted',               # Promoted content
            r'sponsored',              # Sponsored content
        ]

        for elem in list(article_body.find_all(True)):
            if not elem.parent:
                continue

            elem_classes = " ".join(elem.get('class', []))
            elem_id = elem.get('id', '')

            # Verificar se algum padrão de classe ou ID bate
            for pattern in class_patterns_to_remove:
                if re.search(pattern, elem_classes, re.I) or re.search(pattern, elem_id, re.I):
                    logger.info(f"INFO (GameRant): Removendo elemento com classe/id '{elem_classes}' / '{elem_id}'")
                    elem.decompose()
                    break

        # 5. Remove elementos com atributos data-is-tag-interaction
        for elem in list(article_body.find_all(True)):
            if not elem.parent:
                continue

            if elem.get('data-is-tag-interaction'):
                logger.info("INFO (GameRant): Removendo elemento com data-is-tag-interaction")
                elem.decompose()

        # 6. Remove aside tags
        for elem in article_body.find_all('aside'):
            logger.info("INFO (GameRant): Removendo tag <aside>")
            elem.decompose()

        # 7. Remove figuras indesejadas (SVGs, logos, imagens sem contexto)
        for fig in list(article_body.find_all('figure')):
            if not fig.parent:
                continue

            img = fig.find('img')
            if not img:
                logger.info("INFO (GameRant): Removendo figura vazia (sem <img>)")
                fig.decompose()
                continue

            src = img.get('src', '').lower()
            alt = img.get('alt', '').lower()

            # Remove SVGs decorativos, logos, e imagens com padrões ruins
            if '.svg' in src or 'logo' in src or 'icon' in src or 'sr-db' in src or 'sr-db' in alt:
                logger.info(f"INFO (GameRant): Removendo figura decorativa/logo: {src}")
                fig.decompose()
                continue

            # CRUCIAL: Remove imagens de THUMBNAIL/PEQUENO TAMANHO
            # Sites usam ?w=300, ?w=400 para widgets, carousels, e blocos relacionados
            if '?w=300' in src or '?w=400' in src or '&w=300' in src or '&w=400' in src:
                logger.info(f"INFO (GameRant): Removendo figura de thumbnail (?w=300/400): {src}")
                fig.decompose()
                continue

            # Remove figuras órfãs (sem parágrafo antes ou depois)
            prev_p = fig.find_previous(['p', 'h2', 'h3', 'blockquote'])
            next_p = fig.find_next(['p', 'h2', 'h3', 'blockquote'])

            if not prev_p and not next_p:
                logger.info("INFO (GameRant): Removendo figura órfã (sem contexto textual)")
                fig.decompose()

        # 8. Remove parágrafos com CTAs
        for elem in list(article_body.find_all(['p', 'div', 'span', 'article', 'blockquote', 'section'])):
            if not elem.parent:
                continue
            text = (elem.get_text(strip=True) or "").lower()

            # Remove CTAs - lista mais agressiva
            if any(cta in text for cta in [
                'thank you for reading',
                "don't forget to subscribe",
                'subscribe now',
                'click here',
                'read more',
                'sign up',
                'thanks for reading',
                'thanks for visiting',
                'please subscribe',
                'subscribe to our',
                'stay tuned',
                'keep up to date',
                'follow us',
            ]):
                logger.info(f"INFO (GameRant): Removendo CTA: {text[:60]}")
                elem.decompose()

        # 9. Remove English captions from images
        _clean_english_captions(article_body, "GameRant")

        logger.info("INFO (GameRant): Limpeza agressiva concluída. Retornando HTML final.")
        return article_body

    def _clean_html_for_comicbook(self, soup: BeautifulSoup) -> Optional[BeautifulSoup]:
        """
        Limpador específico para COMICBOOK.COM.
        Remove display-card widgets, tag-interaction widgets, CTAs, sidebars, e blocos de relacionados.
        Muito mais agressivo que ScreenRant por ter mais poluição de imagens.
        """
        # 1. Isolar o <article>
        article_container = soup.find('article')
        if not article_container:
            logger.error("ERRO CRÍTICO (ComicBook): A tag <article> principal não foi encontrada.")
            return None

        # 2. Seletor específico para o corpo do artigo
        article_body = article_container.select_one('.article-body')
        if not article_body:
            logger.warning("AVISO (ComicBook): Contêiner '.article-body' não encontrado, usando <article> inteiro.")
            article_body = article_container

        logger.info("INFO (ComicBook): Iniciando limpeza agressiva de widgets, imagens indesejadas e blocos...")

        # 3. Remove scripts e styles PRIMEIRO
        for element in article_body.find_all(['script', 'style']):
            element.decompose()

        # 4. Remove elementos com padrões de classe específicos (MUITO AGRESSIVO para ComicBook)
        class_patterns_to_remove = [
            r'display-card',           # Qualquer classe com 'display-card'
            r'tag-interaction',        # Qualquer classe com 'tag-interaction'
            r'author-info',            # Author info blocks
            r'related-content',        # Related content blocks
            r'recommended',            # Recommended articles
            r'trending',               # Trending sections
            r'sidebar',                # Sidebars
            r'widget',                 # Widget containers
            r'ad-',                    # Ad containers
            r'banner',                 # Banners
            r'gallery-',               # Gallery containers
            r'carousel',               # Carousel containers
            r'promoted',               # Promoted content
            r'sponsored',              # Sponsored content
        ]

        for elem in list(article_body.find_all(True)):  # Converter para list para evitar issues de iteração
            if not elem.parent:  # Já foi removido
                continue

            elem_classes = " ".join(elem.get('class', []))
            elem_id = elem.get('id', '')

            # Verificar se algum padrão de classe ou ID bate
            for pattern in class_patterns_to_remove:
                if re.search(pattern, elem_classes, re.I) or re.search(pattern, elem_id, re.I):
                    logger.info(f"INFO (ComicBook): Removendo elemento com classe/id '{elem_classes}' ou '{elem_id}'")
                    elem.decompose()
                    break

        # 5. Remove elementos com atributos data-is-tag-interaction ou data-stnl-*
        for elem in list(article_body.find_all(True)):
            if not elem.parent:  # Já foi removido
                continue

            # Remover por atributo data-is-tag-interaction
            if elem.get('data-is-tag-interaction'):
                logger.info("INFO (ComicBook): Removendo elemento com data-is-tag-interaction")
                elem.decompose()
                continue

            # Remover por atributo data-stnl-*
            for attr in list(elem.attrs.keys()):
                if attr.startswith('data-stnl-'):
                    logger.info(f"INFO (ComicBook): Removendo elemento com atributo {attr}")
                    elem.decompose()
                    break

        # 6. Remove aside tags
        for elem in article_body.find_all('aside'):
            logger.info("INFO (ComicBook): Removendo tag <aside>")
            elem.decompose()

        # 8. Remove figuras soltas (imagens sem contexto) - MUITO ESPECÍFICO PARA COMICBOOK
        for fig in list(article_body.find_all('figure')):
            if not fig.parent:
                continue

            img = fig.find('img')
            if not img:
                logger.info("INFO (ComicBook): Removendo figura vazia (sem <img>)")
                fig.decompose()
                continue

            src = img.get('src', '').lower()
            alt = img.get('alt', '').lower()

            # Remove SVGs decorativos, logos, e imagens com padrões ruins
            if '.svg' in src or 'logo' in src or 'icon' in src or 'sr-db' in src or 'sr-db' in alt:
                logger.info(f"INFO (ComicBook): Removendo figura decorativa/logo: {src}")
                fig.decompose()
                continue

            # CRUCIAL: Remove imagens de THUMBNAIL/PEQUENO TAMANHO
            # ComicBook usa ?w=300 para imagens de widgets, carousels, e blocos relacionados
            # Imagens de conteúdo principal geralmente têm w=600+ ou sem parâmetro de tamanho
            if '?w=300' in src or '?w=400' in src or '&w=300' in src or '&w=400' in src:
                logger.info(f"INFO (ComicBook): Removendo figura de thumbnail (?w=300/400): {src}")
                fig.decompose()
                continue

            # Se a figura não tem um parágrafo antes ou depois dela, é provavelmente decoration
            prev_p = fig.find_previous(['p', 'h2', 'h3', 'blockquote'])
            next_p = fig.find_next(['p', 'h2', 'h3', 'blockquote'])

            # Se tiver um parágrafo relacionado, manter. Caso contrário, remover.
            # Figuras que vêm de display-card removem muito lixo
            if not prev_p and not next_p:
                logger.info("INFO (ComicBook): Removendo figura órfã (sem contexto textual)")
                fig.decompose()

        # 9. Remove parágrafos com "Thank you for reading" ou "Subscribe"
        for elem in list(article_body.find_all(['p', 'div', 'span', 'article', 'blockquote', 'section'])):
            if not elem.parent:
                continue
            text = (elem.get_text(strip=True) or "").lower()

            # Remove CTAs - lista mais agressiva
            if any(cta in text for cta in [
                'thank you for reading',
                "don't forget to subscribe",
                'subscribe now',
                'click here',
                'read more',
                'sign up',
                'thanks for reading',
                'thanks for visiting',
                'please subscribe',
                'subscribe to our',
                'stay tuned',
                'keep up to date',
                'follow us',
            ]):
                logger.info(f"INFO (ComicBook): Removendo CTA: {text[:60]}")
                elem.decompose()

        # 10. Remove English captions from images
        _clean_english_captions(article_body, "ComicBook")

        logger.info("INFO (ComicBook): Limpeza concluída. Retornando HTML final.")
        return article_body

    def _clean_html_for_screenrant(self, soup: BeautifulSoup) -> Optional[BeautifulSoup]:
        """
        VERSÃO FINAL para SCREENRANT.COM.
        Remove display-card widgets, tag-interaction widgets, CTAs, e outros blocos indesejados.
        Mantém APENAS o conteúdo textual do artigo.
        """
        # 1. Isolar o <article>
        article_container = soup.find('article')
        if not article_container:
            logger.error("ERRO CRÍTICO (ScreenRant): A tag <article> principal não foi encontrada.")
            return None

        # 2. Seletor específico para o corpo do artigo
        article_body = article_container.select_one('.article-body')
        if not article_body:
            logger.warning("AVISO (ScreenRant): Contêiner '.article-body' não encontrado, usando <article> inteiro.")
            article_body = article_container

        logger.info("INFO (ScreenRant): Iniciando limpeza de widgets e blocos indesejados...")

        # 3. Remove scripts e styles PRIMEIRO
        for element in article_body.find_all(['script', 'style']):
            element.decompose()

        # 4. Remove elementos com padrões de classe específicos (usando regex) - AGRESSIVO
        class_patterns_to_remove = [
            r'display-card',           # Qualquer classe com 'display-card'
            r'tag-interaction',        # Qualquer classe com 'tag-interaction'
            r'author-info',            # Author info blocks
            r'related-content',        # Related content blocks
            r'recommended',            # Recommended articles
            r'trending',               # Trending sections
            r'sidebar',                # Sidebars
            r'widget',                 # Widget containers
            r'ad-',                    # Ad containers
            r'banner',                 # Banners
            r'gallery',                # Gallery containers
            r'carousel',               # Carousel containers
            r'promoted',               # Promoted content
            r'sponsored',              # Sponsored content
        ]

        for elem in list(article_body.find_all(True)):  # Converter para list para evitar issues de iteração
            if not elem.parent:  # Já foi removido
                continue

            elem_classes = " ".join(elem.get('class', []))
            elem_id = elem.get('id', '')

            # Verificar se algum padrão de classe ou ID bate
            for pattern in class_patterns_to_remove:
                if re.search(pattern, elem_classes, re.I) or re.search(pattern, elem_id, re.I):
                    logger.info(f"INFO (ScreenRant): Removendo elemento com classe/id '{elem_classes}' / '{elem_id}'")
                    elem.decompose()
                    break

        # 5. Remove elementos com atributos data-is-tag-interaction ou data-stnl-*
        for elem in list(article_body.find_all(True)):
            if not elem.parent:  # Já foi removido
                continue

            # Remover por atributo data-is-tag-interaction
            if elem.get('data-is-tag-interaction'):
                logger.info("INFO (ScreenRant): Removendo elemento com data-is-tag-interaction")
                elem.decompose()
                continue

            # Remover por atributo data-stnl-*
            for attr in list(elem.attrs.keys()):
                if attr.startswith('data-stnl-'):
                    logger.info(f"INFO (ScreenRant): Removendo elemento com atributo {attr}")
                    elem.decompose()
                    break

        # 6. Remove aside tags
        for elem in article_body.find_all('aside'):
            logger.info("INFO (ScreenRant): Removendo tag <aside>")
            elem.decompose()

        # 7. Remove figuras indesejadas (SVGs, logos, imagens sem contexto)
        for fig in list(article_body.find_all('figure')):
            if not fig.parent:
                continue

            img = fig.find('img')
            if not img:
                logger.info("INFO (ScreenRant): Removendo figura vazia (sem <img>)")
                fig.decompose()
                continue

            src = img.get('src', '').lower()
            alt = img.get('alt', '').lower()

            # Remove SVGs decorativos, logos, e imagens com padrões ruins
            if '.svg' in src or 'logo' in src or 'icon' in src or 'sr-db' in src or 'sr-db' in alt:
                logger.info(f"INFO (ScreenRant): Removendo figura decorativa/logo: {src}")
                fig.decompose()
                continue

            # CRUCIAL: Remove imagens de THUMBNAIL/PEQUENO TAMANHO
            # Sites usam ?w=300, ?w=400 para widgets, carousels, e blocos relacionados
            if '?w=300' in src or '?w=400' in src or '&w=300' in src or '&w=400' in src:
                logger.info(f"INFO (ScreenRant): Removendo figura de thumbnail (?w=300/400): {src}")
                fig.decompose()
                continue

            # Remove figuras órfãs (sem parágrafo antes ou depois)
            prev_p = fig.find_previous(['p', 'h2', 'h3', 'blockquote'])
            next_p = fig.find_next(['p', 'h2', 'h3', 'blockquote'])

            if not prev_p and not next_p:
                logger.info("INFO (ScreenRant): Removendo figura órfã (sem contexto textual)")
                fig.decompose()

        # 8. Remove parágrafos com "Thank you for reading" ou "Subscribe" e outras CTAs
        # Busca TUDO - p, div, span, article, blockquote, section
        for elem in list(article_body.find_all(['p', 'div', 'span', 'article', 'blockquote', 'section'])):
            if not elem.parent:
                continue
            text = (elem.get_text(strip=True) or "").lower()

            # Remove CTAs - lista mais agressiva
            if any(cta in text for cta in [
                'thank you for reading',
                "don't forget to subscribe",
                'subscribe now',
                'click here',
                'read more',
                'sign up',
                'thanks for reading',
                'thanks for visiting',
                'please subscribe',
                'subscribe to our',
                'stay tuned',
                'keep up to date',
                'follow us',
            ]):
                logger.info(f"INFO (ScreenRant): Removendo CTA: {text[:60]}")
                elem.decompose()

        # 9. Remove English captions from images
        _clean_english_captions(article_body, "ScreenRant")

        logger.info("INFO (ScreenRant): Limpeza agressiva concluída. Retornando HTML final.")
        return article_body

    def _clean_html_for_lance_definitivo(self, soup: BeautifulSoup) -> Optional[BeautifulSoup]:
        """
        VERSÃO FINAL v3 para o LANCE! - Whitelist Hiper Específico.
        Ignora <figure> que contêm ícones .svg e mantém embeds do Twitter.
        """
        # 1. Isolar o <article>. Tudo fora dele é 100% ignorado.
        article_container = soup.find('article')
        if not article_container:
            logger.error("ERRO CRÍTICO (Lance!): A tag <article> principal não foi encontrada.")
            return None

        # 2. Destruir a barra lateral (se existir) para garantir.
        sidebar = soup.find('aside', class_='tab-m:hidden')
        if sidebar:
            sidebar.decompose()

        # 3. Lista de elementos válidos que vamos extrair.
        good_elements = []

        # 4. Iterar e capturar apenas o que está na nossa lista de permissão.
        for element in article_container.find_all(['p', 'h2', 'figure', 'blockquote']):

            # Pega parágrafos e subtítulos
            if element.name in ['p', 'h2']:
                good_elements.append(str(element))
                continue

            # Pega o embed do Twitter
            if element.name == 'blockquote' and 'twitter-tweet' in element.get('class', []):
                logger.info("INFO (Lance!): Embed de Twitter encontrado e MANTIDO.")
                good_elements.append(str(element))
                continue

            # REGRA REFINADA PARA <figure>
            if element.name == 'figure':
                # Procura por uma tag <img> dentro da figura
                img_tag = element.find('img')

                # Se não houver tag <img>, ou se a imagem for um ícone .svg, IGNORA a figura.
                if not img_tag or (img_tag.get('src') and img_tag.get('src').endswith('.svg')):
                    logger.info(f"INFO (Lance!): Ignorando <figure> de ícone SVG: {img_tag.get('src') if img_tag else 'Figura vazia'}")
                    continue # Pula para o próximo elemento do loop

                # Se passou no teste, é uma figura de conteúdo válida.
                good_elements.append(str(element))
                continue

        if not good_elements:
            logger.warning("AVISO (Lance!): Nenhum conteúdo válido foi encontrado.")
            return None

        # 5. Juntar apenas os elementos bons para formar o HTML final.
        final_html = "".join(good_elements)

        # 6. Retorna o novo <article> contendo apenas os elementos bons.
        return BeautifulSoup(final_html, 'lxml')

    def _clean_html_for_ge(self, soup: BeautifulSoup) -> Optional[BeautifulSoup]:
        """
        VERSÃO RESILIENTE E FINALÍSSIMA v2 para o GE.
        Tenta múltiplos seletores e remove blocos indesejados, incluindo o do Cartola FC.
        """
        # 1. Encontrar o contêiner principal do artigo (lógica resiliente).
        possible_selectors = [
            {'tag': 'div', 'class_': 'materia-conteudo'},
            {'tag': 'article', 'class_': 'post-content'},
            {'tag': 'div', 'class_': 'mc-article-body'},
        ]

        main_container = None
        for selector in possible_selectors:
            main_container = soup.find(selector['tag'], class_=selector.get('class_'))
            if main_container:
                logger.info(f"INFO (GE): Contêiner principal encontrado com o seletor: {selector}")
                break

        if not main_container:
            logger.error("ERRO CRÍTICO (GE): Nenhum contêiner principal válido foi encontrado.")
            return None

        # 2. LISTA DE EXTERMÍNIO: Todos os seletores de blocos indesejados.
        selectors_to_destroy = [
            # Blocos de vídeo
            {'tag': 'div', 'class_': 'video-player'},
            {'tag': 'article', 'class_': 'content-video'},
            {'tag': 'div', 'class_': 'show-multicontent-playlist-container'},

            # Blocos de "Relacionados"
            {'tag': 'div', 'class_': 'related-materia'},

            # NOVO BLOCO DO CARTOLA FC (MAIS ESCALADOS)
            {'tag': 'div', 'id': 'gm-widget-mais-escalados-root'},
        ]

        logger.info("INFO (GE): Iniciando limpeza agressiva de blocos indesejados...")
        for selector in selectors_to_destroy:
            # Busca tanto por 'class_' quanto por 'id'
            elements_to_remove = main_container.find_all(
                selector['tag'],
                class_=selector.get('class_'),
                id=selector.get('id')
            )

            for element in elements_to_remove:
                logger.info(f"INFO (GE): Removendo bloco indesejado com seletor: {selector}")
                element.decompose()

        # Remove scripts e styles para limpeza final.
        for element in main_container.find_all(['script', 'style']):
            element.decompose()

        logger.info("INFO (GE): Limpeza concluída. Retornando HTML final.")
        return main_container

    def _clean_html_for_variety(self, soup: BeautifulSoup) -> Optional[BeautifulSoup]:
        """
        Limpador específico para VARIETY.COM (Penske Media).
        Remove widgets, CTAs, sidebar, related, ads e outros blocos indesejados.
        Mantém apenas parágrafos e headings do corpo do artigo.
        """
        # 1. Isolar o container do artigo
        article_body = _select_first_by_priority(soup, [
            '[data-testid="article-body"]', '.article-body', '.entry-content',
            '.c-entry-content', '[itemprop="articleBody"]', '.c-content',
        ])
        if not article_body:
            article_container = soup.find('article')
            if article_container:
                article_body = article_container
            else:
                logger.error("ERRO CRÍTICO (Variety): Nenhum contêiner de artigo encontrado.")
                return None

        logger.info("INFO (Variety): Iniciando limpeza de widgets, CTAs e blocos indesejados...")

        # 2. Remove scripts e styles
        for element in article_body.find_all(['script', 'style', 'noscript', 'iframe', 'form', 'button']):
            element.decompose()

        # 3. Remove elementos com padrões de classe indesejados
        class_patterns_to_remove = [
            r'related',
            r'recommended',
            r'sidebar',
            r'widget',
            r'ad-',
            r'advertisement',
            r'banner',
            r'promoted',
            r'sponsored',
            r'newsletter',
            r'social-share',
            r'share-bar',
            r'tags',
            r'trending',
            r'gallery',
            r'carousel',
        ]

        for elem in list(article_body.find_all(True)):
            if not elem.parent:
                continue
            elem_classes = " ".join(elem.get('class', []))
            elem_id = elem.get('id', '')
            for pattern in class_patterns_to_remove:
                if re.search(pattern, elem_classes, re.I) or re.search(pattern, elem_id, re.I):
                    elem.decompose()
                    break

        # 4. Remove aside e nav
        for elem in list(article_body.find_all(['aside', 'nav', 'footer'])):
            elem.decompose()

        # 5. Remove figuras indesejadas (SVGs, logos, thumbnails)
        for fig in list(article_body.find_all('figure')):
            if not fig.parent:
                continue
            img = fig.find('img')
            if not img:
                fig.decompose()
                continue
            src = img.get('src', '').lower()
            if '.svg' in src or 'logo' in src or 'icon' in src:
                fig.decompose()
                continue
            if '?w=300' in src or '?w=400' in src or '&w=300' in src or '&w=400' in src:
                fig.decompose()
                continue

        # 6. Remove CTAs
        for elem in list(article_body.find_all(['p', 'div', 'span'])):
            if not elem.parent:
                continue
            text = (elem.get_text(strip=True) or "").lower()
            if any(cta in text for cta in [
                'thank you for reading',
                "don't forget to subscribe",
                'subscribe now',
                'subscribe to variety',
                'sign up for',
                'sign up to',
                'click here',
                'read more',
                'thanks for reading',
                'please subscribe',
            ]):
                elem.decompose()

        # 7. Remove captions em inglês
        _clean_english_captions(article_body, "Variety")

        logger.info("INFO (Variety): Limpeza concluída. Retornando HTML final.")
        return article_body

    def _clean_html_for_hollywoodreporter(self, soup: BeautifulSoup) -> Optional[BeautifulSoup]:
        """
        Limpador específico para HOLLYWOODREPORTER.COM (Penske Media).
        Remove widgets, CTAs, sidebar, related, ads e outros blocos indesejados.
        Mantém apenas parágrafos e headings do corpo do artigo.
        """
        # 1. Isolar o container do artigo
        article_body = _select_first_by_priority(soup, [
            '[data-testid="article-body"]', '.article-content', '.article-body',
            '.entry-content', '[itemprop="articleBody"]', '.c-content',
        ])
        if not article_body:
            article_container = soup.find('article')
            if article_container:
                article_body = article_container
            else:
                logger.error("ERRO CRÍTICO (HollywoodReporter): Nenhum contêiner de artigo encontrado.")
                return None

        logger.info("INFO (HollywoodReporter): Iniciando limpeza de widgets, CTAs e blocos indesejados...")

        # 2. Remove scripts e styles
        for element in article_body.find_all(['script', 'style', 'noscript', 'iframe', 'form', 'button']):
            element.decompose()

        # 3. Remove elementos com padrões de classe indesejados
        class_patterns_to_remove = [
            r'related',
            r'recommended',
            r'sidebar',
            r'widget',
            r'ad-',
            r'advertisement',
            r'banner',
            r'promoted',
            r'sponsored',
            r'newsletter',
            r'social-share',
            r'share-bar',
            r'tags',
            r'trending',
            r'gallery',
            r'carousel',
        ]

        for elem in list(article_body.find_all(True)):
            if not elem.parent:
                continue
            elem_classes = " ".join(elem.get('class', []))
            elem_id = elem.get('id', '')
            for pattern in class_patterns_to_remove:
                if re.search(pattern, elem_classes, re.I) or re.search(pattern, elem_id, re.I):
                    elem.decompose()
                    break

        # 4. Remove aside e nav
        for elem in list(article_body.find_all(['aside', 'nav', 'footer'])):
            elem.decompose()

        # 5. Remove figuras indesejadas (SVGs, logos, thumbnails)
        for fig in list(article_body.find_all('figure')):
            if not fig.parent:
                continue
            img = fig.find('img')
            if not img:
                fig.decompose()
                continue
            src = img.get('src', '').lower()
            if '.svg' in src or 'logo' in src or 'icon' in src:
                fig.decompose()
                continue
            if '?w=300' in src or '?w=400' in src or '&w=300' in src or '&w=400' in src:
                fig.decompose()
                continue

        # 6. Remove CTAs
        for elem in list(article_body.find_all(['p', 'div', 'span'])):
            if not elem.parent:
                continue
            text = (elem.get_text(strip=True) or "").lower()
            if any(cta in text for cta in [
                'thank you for reading',
                "don't forget to subscribe",
                'subscribe now',
                'sign up for',
                'sign up to',
                'click here',
                'read more',
                'thanks for reading',
                'please subscribe',
            ]):
                elem.decompose()

        # 7. Remove captions em inglês
        _clean_english_captions(article_body, "HollywoodReporter")

        logger.info("INFO (HollywoodReporter): Limpeza concluída. Retornando HTML final.")
        return article_body

    def _clean_html_for_movieweb(self, soup: BeautifulSoup) -> Optional[BeautifulSoup]:
        """
        Limpador especifico para MOVIEWEB.COM.
        Usa whitelist editorial para evitar CTAs, ads e blocos de navegacao.
        """
        article_body = _select_first_by_priority(soup, [
            '[itemprop="articleBody"]', '#article-body', '.article-body',
            '.w-article-body', 'article .article-body', '.article-content',
            '.entry-content', '.post-content', 'main article', 'article', 'main',
        ])
        if not article_body:
            logger.error("ERRO CRITICO (MovieWeb): Nenhum container de artigo encontrado.")
            return None

        for element in article_body.find_all(
            [
                'script', 'style', 'noscript', 'iframe', 'form', 'button',
                'nav', 'footer', 'aside', 'header'
            ]
        ):
            element.decompose()

        for selector in [
            '.video',
            '.gallery',
            '.carousel',
            '.newsletter',
            '.related',
            '.social-share',
            '.share',
            '.ad',
            '.advertisement',
            '.sponsored',
            '.w-login',
            '.w-valnet-login',
            '.valnet-login',
            '.vs-video-modal',
            '.vertical-short',
            '.display-card',
            '.w-display-card',
            '.w-display-card-info',
            '.w-display-card-media',
            '.w-display-card-cast',
            '.tag-interaction-widget',
            '.w-tag-interaction-popup-menu',
            '.ww-sensa-quiz-widget',
            '.w-sensa-quiz-widget',
            '.article-tags',
            '.article-options',
        ]:
            for elem in list(article_body.select(selector)):
                elem.decompose()

        class_patterns_to_remove = [
            r'login',
            r'related',
            r'preference',
            r'popup',
            r'modal',
            r'captcha',
            r'thread',
            r'comment',
            r'newsletter',
            r'\bad\b',
            r'ad-',
            r'advertisement',
            r'social-share',
            r'share',
            r'gallery',
            r'carousel',
            r'sponsored',
            r'promo',
            r'video',
            r'trending',
            r'recommended',
            r'sidebar',
            r'widget',
            r'display-card',
            r'w-display-card',
            r'tag-interaction',
            r'sensa',
            r'quiz',
            r'\bcast\b',
            r'srdb',
            r'screenrant',
            r'powered-by',
            r'extra-content',
            r'interaction',
        ]
        for elem in list(article_body.find_all(True)):
            if not elem.parent:
                continue
            elem_classes = " ".join(elem.get('class', []))
            elem_id = elem.get('id', '')
            if any(
                re.search(pattern, elem_classes, re.I) or re.search(pattern, elem_id, re.I)
                for pattern in class_patterns_to_remove
            ):
                elem.decompose()

        blocked_text_signals = [
            'advertisement',
            'subscribe',
            'subscription',
            'sign up',
            'newsletter',
            'read more',
            'more from',
            'follow us',
            'keep reading',
            'create an account',
            'log in',
            'continue with',
            'by creating an account',
            'terms of use',
            'privacy policy',
            'release date',
            'runtime',
            'director',
            'writers',
            'producers',
            'powered by',
            'see all',
            'report error',
            'start quiz',
        ]
        good_elements = []
        for elem in article_body.find_all(['p', 'h2', 'h3', 'blockquote', 'ul', 'ol', 'figure']):
            if not elem.parent:
                continue
            ancestor_classes = " ".join(
                " ".join(parent.get('class', []))
                for parent in elem.parents
                if getattr(parent, 'get', None)
            )
            ancestor_id = " ".join(
                str(parent.get('id', ''))
                for parent in elem.parents
                if getattr(parent, 'get', None)
            )
            if any(
                re.search(pattern, ancestor_classes, re.I) or re.search(pattern, ancestor_id, re.I)
                for pattern in class_patterns_to_remove
            ):
                continue
            text = elem.get_text(" ", strip=True)
            lowered = text.lower()
            if any(signal in lowered for signal in blocked_text_signals):
                continue
            if elem.name == 'figure':
                img = elem.find('img')
                img_url = img.get('src') or img.get('data-src') or img.get('data-img-url') if img else ''
                if not is_valid_article_image(img_url or ''):
                    continue
            if elem.name in {'p', 'h2', 'h3', 'blockquote'} and len(text) < 20:
                continue
            good_elements.append(str(elem))

        if not good_elements:
            logger.warning("AVISO (MovieWeb): Nenhum elemento editorial valido encontrado.")
            return None

        article = BeautifulSoup(f"<article>{''.join(good_elements)}</article>", 'lxml')
        final_article = article.find('article')
        _clean_english_captions(final_article, "MovieWeb")
        logger.info("INFO (MovieWeb): Limpeza concluida. Retornando HTML final.")
        return final_article

    def extract(self, html: str, url: str) -> Optional[Dict[str, Any]]:
        """
        Main extraction flow. Uses a modular, site-specific cleaning method.
        If no specific rule is found, it falls back to a generic extractor.
        """
        soup = BeautifulSoup(html, 'lxml')
        domain = urlparse(url).netloc.lower().replace('www.', '')
        x_embeds = _collect_x_embeds(soup)

        # --- Step 1: Get metadata from the full page ---
        featured_image_url = self._pick_featured_image(soup, url)
        title = (og_title.get('content') if (og_title := soup.find('meta', property='og:title')) else None) or (soup.title.string if soup.title else 'No Title Found')
        if 'movieweb.com' in domain:
            h1_title = soup.select_one('article h1, h1')
            if h1_title:
                title = h1_title.get_text(" ", strip=True)
        excerpt = (meta_desc.get('content') if (meta_desc := soup.find('meta', attrs={'name': 'description'})) else None) or \
                  (og_desc.get('content') if (og_desc := soup.find('meta', property='og:description')) else '')
        videos_full_page = self._extract_youtube_videos(soup)

        def _generic_result(reason: str) -> Optional[Dict[str, Any]]:
            logger.warning("INFO (%s): Usando fallback generico (%s).", domain, reason)
            generic_soup = BeautifulSoup(html, 'lxml')
            self._pre_clean_html(generic_soup, url)
            self._convert_data_img_to_figure(generic_soup)
            image_captions = _merge_image_captions(
                getattr(generic_soup, "_mn_image_captions", {}) or {},
                collect_image_captions_from_article(generic_soup, base_url=url),
            )
            body_images = collect_images_from_article(generic_soup, base_url=url)
            content_html = self._extract_generic_content_html(generic_soup, url)
            if not content_html:
                logger.warning(f"Generic extraction returned empty content for {url}")
                return None

            article_soup = BeautifulSoup(content_html, 'lxml')
            self._remove_forbidden_blocks(article_soup)
            _append_missing_x_embeds(article_soup, x_embeds)
            final_html = article_soup.body.decode_contents() if article_soup.body else str(article_soup)
            other_valid_images = [u for u in body_images if u != featured_image_url]
            body_images_html_list = [
                f'<figure><img src="{img_url}" alt=""><figcaption></figcaption></figure>'
                for img_url in other_valid_images
            ]
            logger.info(
                f"Selected featured image: {featured_image_url}. Found {len(other_valid_images)} other valid images."
            )
            return {
                "title": title.strip(),
                "content": final_html,
                "excerpt": (excerpt or "").strip(),
                "featured_image_url": featured_image_url,
                "images": body_images_html_list,
                "image_captions": image_captions,
                "videos": videos_full_page,
                "source_url": url,
                "schema_original": _find_news_article_in_json_ld(_extract_json_ld(generic_soup))
            }

        # --- Step 2: Route to the correct site-specific cleaner ---
        cleaned_container = None
        if 'lance.com.br' in domain:
            cleaned_container = self._clean_html_for_lance_definitivo(soup)
        elif 'ge.globo.com' in domain:
            cleaned_container = self._clean_html_for_ge(soup)
        elif 'comicbook.com' in domain:
            # Usa o limpador específico para ComicBook (mais agressivo)
            cleaned_container = self._clean_html_for_comicbook(soup)
            logger.info(f"INFO ({domain}): Usando limpador específico para ComicBook")
        elif 'screenrant.com' in domain:
            # Usa o limpador específico para ScreenRant
            cleaned_container = self._clean_html_for_screenrant(soup)
            logger.info(f"INFO ({domain}): Usando limpador específico para ScreenRant")
        elif 'gamerant.com' in domain:
            # Usa o limpador específico para GameRant
            cleaned_container = self._clean_html_for_gamerant(soup)
            logger.info(f"INFO ({domain}): Usando limpador específico para GameRant")
        elif 'collider.com' in domain:
            # Usa o limpador específico para Collider
            cleaned_container = self._clean_html_for_collider(soup)
            logger.info(f"INFO ({domain}): Usando limpador específico para Collider")
        elif 'variety.com' in domain:
            # Usa o limpador específico para Variety
            cleaned_container = self._clean_html_for_variety(soup)
            logger.info(f"INFO ({domain}): Usando limpador específico para Variety")
        elif 'hollywoodreporter.com' in domain:
            # Usa o limpador específico para Hollywood Reporter
            cleaned_container = self._clean_html_for_hollywoodreporter(soup)
            logger.info(f"INFO ({domain}): Usando limpador específico para HollywoodReporter")
        elif 'movieweb.com' in domain:
            cleaned_container = self._clean_html_for_movieweb(soup)
            logger.info(f"INFO ({domain}): Usando limpador especifico para MovieWeb")

        # --- Step 3: Process content if a cleaned container was returned ---
        if cleaned_container:
            logger.info(f"Successfully cleaned content for {domain} using specific extractor.")

            # Extract images and videos from WITHIN the cleaned container
            image_captions = _merge_image_captions(
                collect_image_captions_from_article(cleaned_container, base_url=url),
                getattr(cleaned_container, "_mn_image_captions", {}) or {},
            )
            body_images = []
            for img in cleaned_container.find_all('img'):
                candidate = img.get('src') or img.get('data-src') or img.get('data-img-url')
                if not candidate and img.get('srcset'):
                    candidate = _parse_srcset(img.get('srcset', ''))
                if not candidate:
                    continue
                image_url = _abs(candidate, url)
                if is_valid_article_image(image_url):
                    body_images.append(image_url)
            videos_in_container = self._extract_youtube_videos(cleaned_container)

            _append_missing_x_embeds(cleaned_container, x_embeds)
            final_content_html = str(cleaned_container)
            specific_words = _html_word_count_without_x_embeds(final_content_html)
            raw_words = _html_word_count(html)
            if specific_words < MIN_ACCEPTABLE_EXTRACT_WORDS and raw_words > MIN_ACCEPTABLE_EXTRACT_WORDS:
                generic = _generic_result(f"specific_too_short:{specific_words}w")
                if generic and _html_word_count(generic.get("content", "")) > specific_words:
                    logger.warning(
                        "INFO (%s): Fallback generico escolhido (%sw > %sw especifico).",
                        domain,
                        _html_word_count(generic.get("content", "")),
                        specific_words,
                    )
                    return generic

            # Combine videos, prioritizing ones from the container
            video_ids = set()
            all_videos = []
            for v in videos_in_container + videos_full_page:
                if v['id'] not in video_ids:
                    all_videos.append(v)
                    video_ids.add(v['id'])

            return {
                "title": title.strip(),
                "content": final_content_html,
                "excerpt": (excerpt or "").strip(),
                "featured_image_url": featured_image_url,
                "images": [img for img in body_images if img != featured_image_url],
                "image_captions": image_captions,
                "videos": all_videos,
                "source_url": url,
            }
        else:
            # --- Step 4: Fallback for unhandled sites ---
            return _generic_result("specific_missing_or_unhandled")
