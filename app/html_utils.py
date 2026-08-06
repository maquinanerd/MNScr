# app/html_utils.py
import html
import logging
import os
import re
import unicodedata
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


def _plain_text_fragment(value: str) -> str:
    if not value:
        return ""
    text = BeautifulSoup(value, "html.parser").get_text(separator=" ", strip=True)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _compact_to_limit(text: str, max_chars: int) -> str:
    text = _plain_text_fragment(text)
    if len(text) <= max_chars:
        return text
    clipped = text[: max_chars + 1].rsplit(" ", 1)[0].strip()
    clipped = clipped.rstrip(".,;:")
    if len(clipped) >= max_chars:
        clipped = clipped[:max_chars].rsplit(" ", 1)[0].strip().rstrip(".,;:")
    return clipped + "."


_META_BAD_START = re.compile(r"^(saiba|entenda|confira|veja)\b", re.IGNORECASE)


def _build_meta_description_fallback(title: str, first_paragraph: str, focus_keyword: str) -> str:
    keyword = _plain_text_fragment(focus_keyword)
    title_text = _plain_text_fragment(title)
    lead = _plain_text_fragment(first_paragraph)

    sentences = re.split(r"(?<=[.!?])\s+", lead)
    base = " ".join(sentence for sentence in sentences[:2] if sentence).strip() or lead or title_text

    if keyword and keyword.lower() not in base.lower():
        if title_text and keyword.lower() in title_text.lower() and not _text_is_too_similar(title_text, base):
            base = f"{title_text}. {base}"
        else:
            base = f"{keyword} ganha contexto na cobertura. {base}"

    return _compact_to_limit(base, 155)


def _normalized_compare_key(value: str) -> str:
    text = _plain_text_fragment(value)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.category(ch).startswith("M"))
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _text_is_too_similar(left: str, right: str) -> bool:
    left_key = _normalized_compare_key(left)
    right_key = _normalized_compare_key(right)
    if not left_key or not right_key:
        return False
    if left_key == right_key:
        return True
    shorter, longer = sorted((left_key, right_key), key=len)
    return len(shorter) >= 40 and shorter in longer


# Fragments that indicate the optimizer leaked body text into the title
_TITLE_BODY_FRAGMENTS = re.compile(
    r"\bem\s+<|<[a-z]|\bOs produzir\b|\bOs \w+ produzir\b",
    re.IGNORECASE,
)


def sanitize_final_title(title: str, fallback_title: str = "") -> tuple[str, str]:
    """
    Remove HTML tags/entities, line breaks, and body-text fragments from a title.
    Returns (title_final, status) where status is: OK | SANITIZED | FALLBACK_USED | INVALID.

    Rules:
    - Strip all HTML tags via BeautifulSoup.
    - Decode HTML entities.
    - Collapse whitespace / remove newlines.
    - Reject titles that still contain '<' or '>'.
    - Reject titles with body-text leakage patterns.
    - Enforce 25 ≤ len ≤ 90 chars; truncate at 90 at a word boundary.
    - If the cleaned title is invalid, try fallback_title with the same rules.
    - Return INVALID if both are unusable.
    """
    def _clean(t: str) -> str:
        if not t:
            return ""
        # Strip HTML
        cleaned = BeautifulSoup(t, "html.parser").get_text(separator=" ", strip=True)
        # Decode entities
        cleaned = html.unescape(cleaned)
        # Collapse whitespace
        cleaned = re.sub(r"[\r\n\t]+", " ", cleaned)
        cleaned = re.sub(r" {2,}", " ", cleaned).strip()
        return cleaned

    def _is_valid(t: str) -> bool:
        if not t:
            return False
        if "<" in t or ">" in t:
            return False
        if _TITLE_BODY_FRAGMENTS.search(t):
            return False
        if len(t) < 10:
            return False
        return True

    def _truncate(t: str, max_len: int = 90) -> str:
        if len(t) <= max_len:
            return t
        truncated = t[:max_len].rsplit(" ", 1)[0].strip()
        return truncated if truncated else t[:max_len]

    original = title
    cleaned = _clean(title)
    cleaned = _truncate(cleaned)

    if cleaned == original and _is_valid(cleaned):
        return cleaned, "OK"

    if _is_valid(cleaned):
        return cleaned, "SANITIZED"

    # Try fallback
    if fallback_title:
        fallback_cleaned = _truncate(_clean(fallback_title))
        if _is_valid(fallback_cleaned):
            return fallback_cleaned, "FALLBACK_USED"

    return "", "INVALID"


def normalize_subtitle(
    subtitle: str,
    title: str,
    first_paragraph_text: str,
    meta_description: str = "",
) -> tuple[str, str]:
    """
    Valida e normaliza o subtitle.
    Retorna (subtitle_final, status) onde status e OK | REGENERATED | MISSING.
    Ideal: 140-220 chars. Aceita: 100-240 chars.
    """
    clean_subtitle = _plain_text_fragment(subtitle)
    clean_title = _plain_text_fragment(title)

    if (
        100 <= len(clean_subtitle) <= 240
        and not _text_is_too_similar(clean_subtitle, clean_title)
        and not _text_is_too_similar(clean_subtitle, meta_description)
    ):
        return clean_subtitle, "OK"

    fallback = _compact_to_limit(first_paragraph_text, 220)
    if (
        fallback
        and len(fallback) >= 100
        and not _text_is_too_similar(fallback, clean_title)
        and not _text_is_too_similar(fallback, meta_description)
    ):
        return fallback, "REGENERATED"

    return "", "MISSING"


def normalize_meta_description(
    meta: str,
    title: str,
    first_paragraph: str,
    focus_keyword: str,
) -> tuple[str, str]:
    """
    Garante 120 <= len(meta) <= 155.
    Retorna (meta_final, status): OK | REGENERATED_TOO_LONG | REGENERATED_TOO_SHORT.
    """
    clean_meta = _plain_text_fragment(meta)
    if (
        120 <= len(clean_meta) <= 155
        and not _META_BAD_START.search(clean_meta)
        and not _text_is_too_similar(clean_meta, title)
    ):
        return clean_meta, "OK"

    if len(clean_meta) > 155:
        status = "REGENERATED_TOO_LONG"
    elif clean_meta and (_META_BAD_START.search(clean_meta) or _text_is_too_similar(clean_meta, title)):
        status = "REGENERATED_BAD_STYLE"
    else:
        status = "REGENERATED_TOO_SHORT"

    regenerated = _build_meta_description_fallback(title, first_paragraph, focus_keyword)
    if len(regenerated) < 120:
        title_text = _plain_text_fragment(title)
        combined = f"{regenerated} {title_text}".strip()
        regenerated = _compact_to_limit(combined, 155)

    return regenerated, status

# =========================
# CTA sanitization helpers
# =========================

def _normalize_text_for_cta(text: str) -> str:
    """Normaliza texto para comparação de CTA (minúsculas, sem acentos/pontuação)."""
    if not text:
        return ""
    # Desescapar entidades HTML e normalizar aspas/apóstrofos
    text = html.unescape(text)
    text = text.replace("’", "'").replace("`", "'")
    # Normalizar para remover acentos mantendo caracteres base
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.category(ch).startswith("M"))
    text = text.lower()
    # Remover qualquer caractere que não seja alfanumérico (substituir por espaço)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _normalize_fragment(fragment: str) -> str:
    """Normaliza pequenos fragmentos usados nas regras de detecção."""
    return _normalize_text_for_cta(fragment)


_CTA_SEQUENCE_RULES = [
    ("thank you for reading + subscribe", ("thank you for reading", "subscribe")),
    ("thank you for reading this post + subscribe", ("thank you for reading this post", "subscribe")),
    ("thank you for reading this article + subscribe", ("thank you for reading this article", "subscribe")),
    ("thanks for reading + subscribe", ("thanks for reading", "subscribe")),
    ("thanks for visiting + subscribe", ("thanks for visiting", "subscribe")),
    ("thank you for visiting + subscribe", ("thank you for visiting", "subscribe")),
    ("obrigado por ler + inscreva-se", ("obrigado por ler", "inscreva")),
    ("obrigada por ler + inscreva-se", ("obrigada por ler", "inscreva")),
]

_CTA_SEQUENCE_RULES_NORMALIZED = [
    (label, tuple(_normalize_fragment(part) for part in parts))
    for label, parts in _CTA_SEQUENCE_RULES
]

_CTA_PHRASE_RULES = [
    ("thank you for reading this post don't forget to subscribe", "thank you for reading this post don't forget to subscribe"),
    ("thank you for reading don't forget to subscribe", "thank you for reading don't forget to subscribe"),
    ("thanks for reading don't forget to subscribe", "thanks for reading don't forget to subscribe"),
    ("please subscribe", "please subscribe"),
    ("subscribe now", "subscribe now"),
    ("subscribe to our", "subscribe to our"),
    ("don't forget to subscribe", "don't forget to subscribe"),
    ("dont forget to subscribe", "dont forget to subscribe"),
    ("thanks for reading", "thanks for reading"),
    ("thank you for reading", "thank you for reading"),
    ("obrigado por ler", "obrigado por ler"),
    ("obrigada por ler", "obrigada por ler"),
    ("nao esqueça de se inscrever", "nao esqueça de se inscrever"),
    ("não esqueça de se inscrever", "não esqueça de se inscrever"),
    ("nao deixe de se inscrever", "nao deixe de se inscrever"),
    ("se inscreva", "se inscreva"),
    ("inscreva se", "inscreva se"),
    ("cadastre se", "cadastre se"),
    ("stay tuned", "stay tuned"),
    ("follow us", "follow us"),
]

_CTA_PHRASE_RULES_NORMALIZED = [
    (label, _normalize_fragment(phrase)) for label, phrase in _CTA_PHRASE_RULES
]

_CTA_FALLBACK_REGEXES = [
    r"(?is)thank\s+you\s+for\s+reading[^<]{0,200}?subscribe[^<]{0,200}?",
    r"(?is)thanks\s+for\s+reading[^<]{0,200}?subscribe[^<]{0,200}?",
    r"(?is)thank\s+you\s+for\s+visiting[^<]{0,200}?subscribe[^<]{0,200}?",
    r"(?is)obrigad[ao]\s+por\s+ler[^<]{0,200}?(?:inscreva|inscricao|inscreve)[^<]{0,200}?",
    r"(?is)nao\s+esquec[aã]\s+de\s+se\s+inscrever[^<]{0,200}?",
]


def detect_forbidden_cta_from_text(text: str) -> Optional[str]:
    """Retorna uma descrição da regra de CTA detectada no texto normalizado, se existir."""
    norm = _normalize_text_for_cta(text)
    if not norm:
        return None
    for label, parts in _CTA_SEQUENCE_RULES_NORMALIZED:
        if all(part in norm for part in parts):
            return label
    for label, phrase in _CTA_PHRASE_RULES_NORMALIZED:
        if phrase and phrase in norm:
            return label
    return None


def strip_forbidden_cta_sentences(html_content: str) -> Tuple[str, bool]:
    """Remove blocos que contenham CTAs proibidos. Retorna HTML limpo e flag se removeu algo."""
    if not html_content:
        return html_content, False

    soup = BeautifulSoup(html_content, "lxml")
    removed = False
    target_tags = ("p", "div", "span", "section", "article", "blockquote", "li", "strong", "em", "footer")

    for tag_name in target_tags:
        for node in list(soup.find_all(tag_name)):
            if not node.parent:
                continue
            text = node.get_text(" ", strip=True)
            if not text:
                continue
            if detect_forbidden_cta_from_text(text):
                logger.debug("Removendo nó com CTA proibido: %s", text[:100])
                node.decompose()
                removed = True

    cleaned_html = soup.body.decode_contents() if soup.body else str(soup)

    # Passo adicional: remover frases residuais diretamente no HTML bruto
    for pattern in _CTA_FALLBACK_REGEXES:
        cleaned_html, substitutions = re.subn(pattern, "", cleaned_html)
        if substitutions:
            removed = True

    return cleaned_html, removed


def detect_forbidden_cta(html_content: str) -> Optional[str]:
    """Detecta CTAs proibidos em HTML, retornando a descrição da regra se encontrada."""
    if not html_content:
        return None
    soup = BeautifulSoup(html_content, "lxml")
    text = soup.get_text(" ", strip=True)
    return detect_forbidden_cta_from_text(text)

# =========================
# HTML unescape utility
# =========================

def unescape_html_content(content: str) -> str:
    """
    Desescapa HTML escapado (ex: &lt; para <, &gt; para >, etc).
    Útil para conteúdo que retorna da IA com HTML escapado no JSON.
    """
    if not content:
        return content
    # A IA às vezes coloca uma figura HTML inteira, escapada, dentro de `src`.
    # Desescapar tudo primeiro transforma `&quot;` em aspas aninhadas e destrói
    # a fronteira do atributo antes que o parser possa recuperar a URL. Extraia
    # a URL enquanto a estrutura ainda é inequívoca; a validação de esquema e a
    # normalização da figura continuam em `validate_and_fix_figures`.
    nested_src = re.compile(
        r'src="&lt;(?:figure|img)\b.*?src=&quot;(https?://[^&"<>\s]+)&quot;.*?"',
        re.IGNORECASE | re.DOTALL,
    )
    content = nested_src.sub(lambda match: f'src="{match.group(1)}"', content)
    return html.unescape(content)

# =========================
# YouTube helpers/normalizer
# =========================

YOUTUBE_HOSTS = {
    "youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "www.youtu.be"
}

def _yt_id_from_url(url: str) -> Optional[str]:
    if not url:
        return None
    try:
        u = urlparse(url)
        host = (u.hostname or "").lower()
        if host not in YOUTUBE_HOSTS:
            return None
        # /embed/ID
        if u.path.startswith("/embed/"):
            return u.path.split("/")[2].split("?")[0]
        # /shorts/ID
        if u.path.startswith("/shorts/"):
            return u.path.split("/")[2].split("?")[0]
        # youtu.be/ID
        if host.endswith("youtu.be"):
            return u.path.lstrip("/").split("?")[0]
        # /watch?v=ID
        if u.path == "/watch":
            q = parse_qs(u.query)
            return (q.get("v") or [None])[0]
    except Exception:
        pass
    return None


def strip_credits_and_normalize_youtube(html: str) -> str:
    """
    - Remove linhas de crédito (figcaption/p/span iniciando com Crédito/Credito/Fonte)
    - Converte iframes do YouTube em <p> com URL watch (oEmbed do CMS)
    - Remove iframes não-YouTube, vazios ou com placeholders (ex.: URL_DO_EMBED_AQUI)
    - Remove <p> vazios após a limpeza e desfaz <figure> que só envolvem embed
    """
    if not html:
        return html

    soup = BeautifulSoup(html, "lxml")

    # 1) Remover “Crédito:”, “Credito:”, “Fonte:”
    for node in soup.find_all(["figcaption", "p", "span"]):
        t = (node.get_text() or "").strip().lower()
        if t.startswith(("crédito:", "credito:", "fonte:")):
            node.decompose()

    # 2) Tratar iframes
    for iframe in list(soup.find_all("iframe")):
        src = (iframe.get("src") or "").strip()
        # placeholder ou vazio? remover
        if (not src) or ("URL_DO_EMBED_AQUI" in src):
            iframe.decompose()
            continue
        # YouTube -> URL watch
        vid = _yt_id_from_url(src)
        if vid:
            p = soup.new_tag("p")
            p.string = f"https://www.youtube.com/watch?v={vid}"
            iframe.replace_with(p)
        else:
            # não-YouTube -> remove
            iframe.decompose()

    # 3) Limpar <figure> que só envolvem o embed ou ficaram vazias
    for fig in list(soup.find_all("figure")):
        if fig.find("img"):
            continue
        children_tags = [c for c in fig.contents if getattr(c, "name", None)]
        only_p = (len(children_tags) == 1 and getattr(children_tags[0], "name", None) == "p")
        p = children_tags[0] if only_p else None
        p_text = (p.get_text().strip() if p else "")
        if only_p and ("youtube.com/watch" in p_text or "youtu.be/" in p_text):
            fig.replace_with(p)
        elif not fig.get_text(strip=True):
            fig.unwrap()

    # 4) Remover <p> vazios (sem texto e sem elementos)
    for p in list(soup.find_all("p")):
        if not p.get_text(strip=True) and not p.find(True):
            p.decompose()

    return soup.body.decode_contents() if soup.body else str(soup)


def hard_filter_forbidden_html(html: str) -> str:
    """
    Sanitiza HTML:
      - remove: script, style, noscript, form, input, button, select, option,
                textarea, object, embed, svg, canvas, link, meta
      - iframes: permite só YouTube (vira oEmbed); remove vazios/placeholder
      - remove atributos on* e href/src com javascript:
      - remove <p> vazios após limpeza
    """
    if not html:
        return html

    soup = BeautifulSoup(html, "lxml")

    REMOVE_TAGS = {
        "script","style","noscript","form","input","button","select","option",
        "textarea","object","embed","svg","canvas","link","meta"
    }
    for tag_name in REMOVE_TAGS:
        for t in soup.find_all(tag_name):
            t.decompose()

    # iframes
    for iframe in list(soup.find_all("iframe")):
        src = (iframe.get("src") or "").strip()
        if (not src) or ("URL_DO_EMBED_AQUI" in src):
            iframe.decompose()
            continue
        vid = _yt_id_from_url(src)
        if vid:
            p = soup.new_tag("p")
            p.string = f"https://www.youtube.com/watch?v={vid}"
            iframe.replace_with(p)
        else:
            iframe.decompose()

    # atributos perigosos
    for el in soup.find_all(True):
        for attr in list(el.attrs.keys()):
            if attr.lower().startswith("on"):
                del el.attrs[attr]
        for attr in ("href", "src"):
            if el.has_attr(attr):
                val = (el.get(attr) or "").strip()
                if val.lower().startswith("javascript:"):
                    del el.attrs[attr]

    # <p> vazios
    for p in list(soup.find_all("p")):
        if not p.get_text(strip=True) and not p.find(True):
            p.decompose()

    return soup.body.decode_contents() if soup.body else str(soup)


# =========================
# Imagens: merge e rewrite
# =========================

def _norm_key(u: str) -> str:
    """Normaliza URL para comparação/chave de dicionário."""
    if not u:
        return ""
    return (u.strip().rstrip("/")).lower()


def _replace_in_srcset(srcset: str, mapping: Dict[str, str]) -> str:
    """
    Substitui URLs dentro de um atributo srcset usando o mapping (url_original -> nova_url).
    Mantém os sufixos (ex.: '320w').
    """
    if not srcset:
        return srcset
    parts = []
    for chunk in srcset.split(","):
        item = chunk.strip()
        if not item:
            continue
        tokens = item.split()
        url = tokens[0]
        rest = " ".join(tokens[1:]) if len(tokens) > 1 else ""
        new_url = mapping.get(_norm_key(url), url)
        parts.append((new_url + (" " + rest if rest else "")).strip())
    return ", ".join(parts)


def _coerce_image_candidate(candidate: Any) -> Optional[Dict[str, str]]:
    """
    Normaliza um candidato de imagem para um payload simples.

    Aceita URL direta, HTML com <figure>/<img> ou dicts com chaves comuns.
    """
    if not candidate:
        return None

    def _build(url: str, alt: str = "", caption: str = "") -> Optional[Dict[str, str]]:
        clean_url = (url or "").strip()
        if not clean_url:
            return None
        return {
            "url": clean_url,
            "alt": (alt or "").strip(),
            "caption": (caption or "").strip(),
        }

    if isinstance(candidate, str):
        raw = candidate.strip()
        if not raw:
            return None
        if raw.startswith("<"):
            soup = BeautifulSoup(raw, "html.parser")
            img = soup.find("img")
            if not img:
                return None
            figcaption = soup.find("figcaption")
            return _build(
                img.get("src", ""),
                img.get("alt", ""),
                figcaption.get_text(" ", strip=True) if figcaption else "",
            )
        return _build(raw)

    if isinstance(candidate, dict):
        return _build(
            str(candidate.get("url") or candidate.get("src") or candidate.get("href") or ""),
            str(candidate.get("alt") or candidate.get("alt_text") or ""),
            str(candidate.get("caption") or candidate.get("figcaption") or ""),
        )

    return None


def merge_images_into_content(content_html: str, image_urls: List[str], max_images: int = 6) -> str:
    """
    Garante imagens no corpo:
      - mantém as que já existem
      - injeta até `max_images` novas (que não estejam no HTML)
      - não adiciona créditos/legendas
      - insere após o primeiro parágrafo; se não houver, ao final

    IMPORTANTE: Garante que cada imagem tem pelo menos um alt text e está dentro
    de uma figura com figcaption corretamente estruturada.
    """
    if not content_html:
        content_html = ""
    soup = BeautifulSoup(content_html, "lxml")

    # conjunto de URLs já presentes
    present: set[str] = set()
    for img in soup.find_all("img"):
        src = (img.get("src") or "").strip()
        if src:
            present.add(_norm_key(src))
        # considerar srcset como presença também
        if img.get("srcset"):
            for chunk in img["srcset"].split(","):
                u = chunk.strip().split()[0]
                if u:
                    present.add(_norm_key(u))

    to_add: List[Dict[str, str]] = []
    for raw_candidate in (image_urls or []):
        image = _coerce_image_candidate(raw_candidate)
        if not image:
            continue
        key = _norm_key(image["url"])
        if not key or key in present:
            logger.warning("[IMAGES] Skipped duplicate: %s", image["url"])
            continue
        to_add.append(image)
        present.add(key)
        if len(to_add) >= max_images:
            break

    if to_add:
        # ponto de inserção: após o primeiro <p>; senão, ao final do body/raiz
        insertion_point = soup.find("p")
        parent = insertion_point.parent if insertion_point and insertion_point.parent else (soup.body or soup)

        for image in to_add:
            image_url = image["url"]
            fig = soup.new_tag("figure")
            img = soup.new_tag("img", src=image_url, alt="")

            # Extrair um nome descritivo da URL para usar como alt text básico
            try:
                filename = image_url.split('/')[-1].split('?')[0]
                # Limpar extensão e caracteres especiais
                clean_name = re.sub(r'\.(jpg|jpeg|png|gif|webp)$', '', filename, flags=re.IGNORECASE)
                clean_name = re.sub(r'[-_]', ' ', clean_name)
                if clean_name:
                    img['alt'] = clean_name.strip()
            except Exception:
                img['alt'] = 'Imagem do artigo'

            if image.get("alt"):
                img['alt'] = image["alt"]

            fig.append(img)

            # Adicionar figcaption vazio (para ser preenchido posteriormente se necessário)
            figcaption = soup.new_tag("figcaption")
            figcaption.string = image.get("caption") or img.get('alt', 'Imagem')
            fig.append(figcaption)

            if insertion_point:
                insertion_point.insert_after(fig)
                insertion_point = fig  # próximo entra depois do que inserimos
            else:
                parent.append(fig)

    logger.info("[IMAGES] Merged %d images into content", len(to_add))
    return soup.body.decode_contents() if soup.body else str(soup)


def merge_videos_into_content(content_html: str, videos: List[Dict[str, str]], max_videos: int = 2) -> str:
    """
    Garante que videos do YouTube entrem no corpo via URL watch para oEmbed do CMS.
    Nao duplica videos ja presentes no HTML.
    """
    if not videos:
        return content_html

    soup = BeautifulSoup(content_html or "", "lxml")
    existing_ids = set()

    for iframe in soup.find_all("iframe"):
        video_id = _yt_id_from_url((iframe.get("src") or "").strip())
        if video_id:
            existing_ids.add(video_id)

    for tag in soup.find_all(["p", "a"]):
        text = tag.get_text(" ", strip=True)
        video_id = _yt_id_from_url(text)
        if video_id:
            existing_ids.add(video_id)

    insertion_point = soup.find("p")
    parent = insertion_point.parent if insertion_point and insertion_point.parent else (soup.body or soup)
    added = 0

    for video in videos:
        if not isinstance(video, dict):
            continue
        raw_url = (video.get("watch_url") or video.get("embed_url") or "").strip()
        video_id = _yt_id_from_url(raw_url)
        if not video_id or video_id in existing_ids:
            continue

        p = soup.new_tag("p")
        p.string = f"https://www.youtube.com/watch?v={video_id}"

        if insertion_point:
            insertion_point.insert_after(p)
            insertion_point = p
        else:
            parent.append(p)

        existing_ids.add(video_id)
        added += 1
        if added >= max_videos:
            break

    return soup.body.decode_contents() if soup.body else str(soup)


def validate_and_fix_figures(html: str, ensure_figcaption: bool = True) -> str:
    """
    Valida e corrige estruturas de <figure> no HTML.

    Problemas corrigidos:
    - Figuras com src contendo HTML (ex: src="<figure><img src=...>")
    - Figuras sem <figcaption>
    - <img> fora de <figure>
    - URLs inválidas em src

    NOTA: BeautifulSoup desescapa automaticamente ao fazer parsing,
    então procuramos por '<' no src, não '&lt;'.
    """
    if not html:
        return html

    # PASSO 0: Detectar e corrigir src malformados ANTES do parsing
    # Se temos src="<figure>... ou src="<img... extrair a URL real
    def fix_malformed_src(text):
        """Fixa srcs que contêm HTML."""
        # Procurar por src="...conteúdo com < e >"
        # E extrair a URL https://... dentro
        pattern = r'src="[^"]*<[^"]*https?://[^\s"\'<>]*[^"]*"'

        def extract_and_fix(match):
            src_with_html = match.group(0)  # ex: src="<figure><img src="https://..."...>"
            # Extrair apenas a URL
            url_match = re.search(r'https?://[^\s"\'<>]+', src_with_html)
            if url_match:
                url = url_match.group(0)
                return f'src="{url}"'
            return ''

        return re.sub(pattern, extract_and_fix, text)

    html = fix_malformed_src(html)

    soup = BeautifulSoup(html, "lxml")

    # 1. Corrigir figuras com img que tem src contendo HTML estrutural
    # (BeautifulSoup desescapa automaticamente, então procuramos por '<' literal)
    for img in soup.find_all("img"):
        src = (img.get("src") or "").strip()

        # Detectar se o src contém estrutura HTML (começa com < ou contém <img)
        if src and (src.startswith("<") or "<img" in src or "<figure" in src):
            logger.warning(f"Encontrada imagem com src contendo HTML estrutural: {src[:100]}...")

            # Tentar encontrar uma URL https://... ou http://... dentro do src
            url_match = re.search(r'https?://[^\s"\'<>]+', src)
            if url_match:
                real_url = url_match.group(0)
                logger.info(f"Extraída URL real do HTML no src: {real_url}")
                img["src"] = real_url
            else:
                logger.info("Removendo <img> com src contendo HTML estrutural (URL não encontrada)")
                fig = img.find_parent("figure")
                if fig:
                    fig.decompose()
                else:
                    img.decompose()

    # 2. Garantir que toda <img> está dentro de <figure> e tem <figcaption>
    for img in soup.find_all("img"):
        # Se a imagem não está dentro de figure, envolver
        fig_parent = img.find_parent("figure")
        if not fig_parent:
            fig = soup.new_tag("figure")
            img.wrap(fig)
            fig_parent = fig

        # Garantir que tem figcaption quando o chamador ainda usa fallback legado.
        if ensure_figcaption and not fig_parent.find("figcaption"):
            figcaption = soup.new_tag("figcaption")
            alt_text = img.get("alt", "Imagem")
            figcaption.string = alt_text if alt_text else "Imagem"
            fig_parent.append(figcaption)

        # Garantir que img tem alt text
        if not img.get("alt"):
            img["alt"] = "Imagem"

    # 3. Remover figuras vazias ou com conteúdo inválido
    for fig in soup.find_all("figure"):
        img = fig.find("img")
        if not img:
            # Figure sem img, remover
            fig.decompose()
            continue

        src = (img.get("src") or "").strip()
        if not src or not src.startswith(("http://", "https://")):
            logger.warning(f"Removendo figura com URL inválida: {src}")
            fig.decompose()

    return soup.body.decode_contents() if soup.body else str(soup)

# --- Stub para compatibilidade com pipeline: não adiciona crédito nenhum ---

def add_credit_to_figures(html: str, source_url: Optional[str] = None) -> str:
    """
    Compat: função mantida apenas para evitar ImportError.
    Não faz nada e retorna o HTML intacto (sem créditos).
    """
    logger.info("add_credit_to_figures desabilitada: retornando HTML sem alterações.")
    return html

# =========================
# Source Credits
# =========================

_SOURCE_LABEL_MAP = {
    "screenrant.com": "ScreenRant",
    "collider.com": "Collider",
    "hollywoodreporter.com": "THR",
    "thewrap.com": "TheWrap",
    "variety.com": "Variety",
    "deadline.com": "Deadline",
    "comicbook.com": "ComicBook",
    "gamerant.com": "GameRant",
    "cbr.com": "CBR",
    "empireonline.com": "Empire",
    "ign.com": "IGN",
    "ew.com": "Entertainment Weekly",
    "people.com": "People",
    "tvline.com": "TVLine",
    "indiewire.com": "IndieWire",
    "rollingstone.com": "Rolling Stone",
    "bbc.com": "BBC",
    "reuters.com": "Reuters",
    "nytimes.com": "The New York Times",
    "g1.globo.com": "G1",
    "globo.com": "Globo",
    "valor.globo.com": "Valor",
    "estadao.com.br": "Estadão",
    "uol.com.br": "UOL",
    "folha.uol.com.br": "Folha",
}

def source_label_from_url(url: str) -> str:
    """Converte URL em um rÃ³tulo curto e legÃ­vel para crÃ©dito editorial."""
    try:
        hostname = (urlparse(url).hostname or "").lower()
    except Exception:
        hostname = ""

    if not hostname:
        return "fonte original"

    if hostname.startswith("www."):
        hostname = hostname[4:]

    for domain, label in _SOURCE_LABEL_MAP.items():
        if hostname == domain or hostname.endswith("." + domain):
            return label

    base = hostname.split(".")[0]
    pretty = base.replace("-", " ").replace("_", " ").strip()
    if not pretty:
        return "Fonte original"
    return pretty.title()


def append_source_credit_block(
    html_content: str,
    primary_url: Optional[str] = None,
    additional_urls: Optional[List[str]] = None,
) -> str:
    """
    Adiciona um bloco discreto de crÃ©ditos no final do artigo.

    O objetivo Ã© manter a atribuiÃ§Ã£o editorial sem poluir o corpo do texto.
    """
    if not html_content:
        return html_content

    ordered_urls: List[str] = []
    seen_urls = set()
    for url in [primary_url, *(additional_urls or [])]:
        if not url:
            continue
        clean_url = url.strip()
        if not clean_url or clean_url in seen_urls:
            continue
        seen_urls.add(clean_url)
        ordered_urls.append(clean_url)

    if not ordered_urls:
        return html_content

    source_count = len(ordered_urls)
    source_label = "Fonte" if source_count == 1 else "Fontes"

    links_html = " ".join(
        (
            f'<a href="{html.escape(url, quote=True)}" '
            'target="_blank" rel="noopener noreferrer nofollow" '
            'style="color:inherit;opacity:0.82;text-decoration:underline;'
            'text-decoration-color:rgba(127,127,127,0.45);text-underline-offset:2px;">'
            f'{html.escape(source_label_from_url(url))}</a>'
        )
        for url in ordered_urls
    )

    credit_block = (
        '<p class="mn-source-credit-line" '
        'style="margin:28px 0 0 0;padding-top:10px;border-top:1px solid rgba(127,127,127,0.18);'
        'font-size:12px;line-height:1.5;color:inherit;">'
        f'<span style="font-weight:600;opacity:0.74;">{source_label}:</span> '
        f'{links_html}</p>'
    )

    return html_content.rstrip() + "\n" + credit_block


# =========================
# Post-AI Defensive Cleanup
# =========================

def remove_broken_image_placeholders(html: str) -> str:
    """
    Removes text-based image placeholders that the AI might mistakenly add,
    like '[Imagem Destacada]' on its own line, without affecting real content.
    """
    if not html or "Imagem" not in html:
        return html
    # This regex targets lines that ONLY contain the placeholder.
    # `^` and `$` anchor to the start and end of a line due to MULTILINE flag.
    # It avoids touching legitimate text that happens to contain the word "Imagem".
    return re.sub(
        r'^\s*(\[?Imagem[^\n<]*\]?)\s*$',
        '',
        html,
        flags=re.IGNORECASE | re.MULTILINE
    )


def downgrade_h1_to_h2(html: str) -> str:
    """
    Replaces any <h1>...</h1> in the article body with <h2>...</h2>.
    The CMS renders the title as H1; a second H1 in the body hurts SEO.
    A second H1 in the content is a duplicate and hurts SEO.
    """
    if not html or '<h1' not in html.lower():
        return html
    html = re.sub(r'<h1(\s[^>]*)?>', lambda m: '<h2' + (m.group(1) or '') + '>', html, flags=re.IGNORECASE)
    html = re.sub(r'</h1>', '</h2>', html, flags=re.IGNORECASE)
    return html


def strip_ai_tag_links(html: str, domain: Optional[str] = None) -> str:
    """
    Removes <a href> links invented by the AI that point to /tag/ pages.
    Keeps the anchor text, removes the link wrapper.
    This prevents publishing links to tag archives that may not exist.
    Example: <a href="https://portal.example/tag/the-boys">The Boys</a>
             → The Boys
    """
    if not html or "/tag/" not in html:
        return html
    domain = domain or os.getenv("PUBLISHER_DOMAIN", "")
    if not domain:
        return html
    escaped_domain = re.escape(domain)
    return re.sub(
        r'<a\s+[^>]*href=["\']https?://(?:www\.)?' + escaped_domain + r'/tag/[^"\'>]+["\'][^>]*>(.*?)</a>',
        r'\1',
        html,
        flags=re.IGNORECASE | re.DOTALL
    )


def strip_naked_internal_links(html: str) -> str:
    """
    Removes paragraphs that contain nothing but a bare URL to an internal
    tag or category page, a common AI formatting error.
    """
    if not html or ("/tag/" not in html and "/categoria/" not in html):
        return html
    # This regex looks for a <p> tag containing only a URL to /tag/ or /categoria/.
    return re.sub(
        r'<p>\s*https?://[^<>\s]+/(?:tag|categoria)/[a-z0-9\-_/]+/?\s*</p>',
        '',
        html,
        flags=re.IGNORECASE
    )


def remove_source_domain_schemas(html: str) -> str:
    """
    Remove todos os blocos JSON-LD que vieram do conteúdo original da fonte.

    O schema estruturado é responsabilidade do sistema que publica. Remover os
    schemas da fonte evita conflitos de Schema.org no destino editorial.

    Remove:
    - <script type="application/ld+json">...</script> com qualquer conteúdo

    Preserva outros scripts (analytics, publicidade, etc.)
    """
    if not html:
        return html

    # Remove qualquer bloco de script JSON-LD
    # Usa re.DOTALL para capturar conteúdo multi-linha
    cleaned = re.sub(
        r'<script[^>]*type="application/ld\+json"[^>]*>.*?</script>',
        '',
        html,
        flags=re.DOTALL | re.IGNORECASE
    )

    # Remove também a variação inversa do atributo type
    cleaned = re.sub(
        r'<script[^>]*type\s*=\s*["\']application/ld\+json["\'][^>]*>.*?</script>',
        '',
        cleaned,
        flags=re.DOTALL | re.IGNORECASE
    )

    logger.debug("Removed source domain JSON-LD schemas from content")
    return cleaned


# ===========================
# Final pre-publish cleanup
# ===========================

# Canonical entity map — deterministic casing applied to visible text
_ENTITY_CANONICAL: List[tuple] = [
    # Streaming / Platforms
    (re.compile(r'\bnetflix\b', re.IGNORECASE), "Netflix"),
    (re.compile(r'\bhbo\s+max\b', re.IGNORECASE), "HBO Max"),
    (re.compile(r'\bhbo\b(?!\s+max)', re.IGNORECASE), "HBO"),
    (re.compile(r'\bdisney\s*\+', re.IGNORECASE), "Disney+"),
    (re.compile(r'\bprime\s+video\b', re.IGNORECASE), "Prime Video"),
    (re.compile(r'\bamazon\s+prime\b', re.IGNORECASE), "Amazon Prime"),
    (re.compile(r'\bapple\s+tv\s*\+', re.IGNORECASE), "Apple TV+"),
    (re.compile(r'\bparamount\s*\+', re.IGNORECASE), "Paramount+"),
    (re.compile(r'\bcrunchyroll\b', re.IGNORECASE), "Crunchyroll"),
    (re.compile(r'\bgloboplay\b', re.IGNORECASE), "Globoplay"),
    # Series / Films
    (re.compile(r'\bthe\s+boys\b', re.IGNORECASE), "The Boys"),
    (re.compile(r'\bstranger\s+things\b', re.IGNORECASE), "Stranger Things"),
    (re.compile(r'\bgame\s+of\s+thrones\b', re.IGNORECASE), "Game of Thrones"),
    (re.compile(r'\bhouse\s+of\s+the\s+dragon\b', re.IGNORECASE), "House of the Dragon"),
    (re.compile(r'\bstar\s+wars\b', re.IGNORECASE), "Star Wars"),
    (re.compile(r'\bthe\s+mandalorian\b', re.IGNORECASE), "The Mandalorian"),
    (re.compile(r'\bthe\s+witcher\b', re.IGNORECASE), "The Witcher"),
    (re.compile(r'\bbreaking\s+bad\b', re.IGNORECASE), "Breaking Bad"),
    (re.compile(r'\bbetter\s+call\s+saul\b', re.IGNORECASE), "Better Call Saul"),
    (re.compile(r'\bthe\s+last\s+of\s+us\b', re.IGNORECASE), "The Last of Us"),
    (re.compile(r'\bsquid\s+game\b', re.IGNORECASE), "Squid Game"),
    (re.compile(r'\bblack\s+mirror\b', re.IGNORECASE), "Black Mirror"),
    (re.compile(r'\bseverance\b', re.IGNORECASE), "Severance"),
    (re.compile(r'\bfallout\b', re.IGNORECASE), "Fallout"),
    (re.compile(r'\barcane\b', re.IGNORECASE), "Arcane"),
    (re.compile(r'\bone\s+piece\b', re.IGNORECASE), "One Piece"),
    (re.compile(r'\bdragon\s+ball\b', re.IGNORECASE), "Dragon Ball"),
    (re.compile(r'\bnaruto\b', re.IGNORECASE), "Naruto"),
    (re.compile(r'\battack\s+on\s+titan\b', re.IGNORECASE), "Attack on Titan"),
    (re.compile(r'\bvought\s+rising\b', re.IGNORECASE), "Vought Rising"),
    (re.compile(r'\bvought\b(?!\s+rising)', re.IGNORECASE), "Vought"),
    (re.compile(r'\bsoldier\s+boy\b', re.IGNORECASE), "Soldier Boy"),
    (re.compile(r'\bbilly\s+butcher\b', re.IGNORECASE), "Billy Butcher"),
    (re.compile(r'\bhomelander\b', re.IGNORECASE), "Homelander"),
    (re.compile(r'\bstarlight\b', re.IGNORECASE), "Starlight"),
    # Studios
    (re.compile(r'\bwarner\s+bros?\.?(?=\s|/|$|[,;)])', re.IGNORECASE), "Warner Bros."),
    (re.compile(r'\buniversal\s+pictures\b', re.IGNORECASE), "Universal Pictures"),
    (re.compile(r'\bparamount\s+pictures\b', re.IGNORECASE), "Paramount Pictures"),
    (re.compile(r'\bsony\s+pictures\b', re.IGNORECASE), "Sony Pictures"),
    (re.compile(r'\bnaughty\s+dog\b', re.IGNORECASE), "Naughty Dog"),
    (re.compile(r'\bbethesda\b', re.IGNORECASE), "Bethesda"),
    (re.compile(r'\bnintendo\b', re.IGNORECASE), "Nintendo"),
    (re.compile(r'\bplaystation\b', re.IGNORECASE), "PlayStation"),
    (re.compile(r'\bxbox\b', re.IGNORECASE), "Xbox"),
    (re.compile(r'\bmarvel\b', re.IGNORECASE), "Marvel"),
    (re.compile(r'\ba24\b', re.IGNORECASE), "A24"),
]

# Patterns that indicate a raw alt-text/caption from the source (English, no editorial punctuation)
_RAW_CAPTION_PATTERNS = [
    re.compile(r'\b(in front of|have tense meeting|looking at|sitting at|standing in|'
               r'from season|in the \w+ season|as \w+ in|stares down|faces off|'
               r'appears in|is seen in|talking to|walks through|arrives at|confronts)\b', re.IGNORECASE),
    re.compile(r'\b(homelander|butcher|hughie|starlight)\b.{0,40}'
               r'\b(in|at|from|during|while|as)\b', re.IGNORECASE),
    re.compile(r'^[a-z][a-z0-9 \'-]{0,120}$'),  # all-lowercase no punctuation (raw alt)
]

# Fallback captions keyed loosely by entity mentioned in the caption
_FALLBACK_CAPTIONS: List[tuple] = [
    (re.compile(r'\bhomelander\b.*\bbutcher\b|\bbutcher\b.*\bhomelander\b', re.IGNORECASE),
     "Butcher e Homelander em cena de tensão na série The Boys."),
    (re.compile(r'\bhomelander\b', re.IGNORECASE),
     "Homelander em momento decisivo da trama de The Boys."),
    (re.compile(r'\bbutcher\b', re.IGNORECASE),
     "Billy Butcher retorna ao centro do conflito em The Boys."),
    (re.compile(r'\bstarlight\b', re.IGNORECASE),
     "Starlight em cena da série The Boys."),
    (re.compile(r'\bthe\s+boys\b', re.IGNORECASE),
     "Cena da série The Boys."),
    (re.compile(r'\bstranger\s+things\b', re.IGNORECASE),
     "Cena da série Stranger Things."),
    (re.compile(r'\bgame\s+of\s+thrones\b', re.IGNORECASE),
     "Cena da série Game of Thrones."),
    (re.compile(r'\bhouse\s+of\s+the\s+dragon\b', re.IGNORECASE),
     "Cena da série House of the Dragon."),
    (re.compile(r'\bstar\s+wars\b', re.IGNORECASE),
     "Cena do universo Star Wars."),
]


def _strip_internal_credit_suffix(text: str) -> str:
    text = re.sub(r"\s+via\s+RSSPRIME\s+Superfeed(?=\s*\.|\b)", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*Cr[eé]dito:\s*RSSPRIME\s+Superfeed\.?\s*$", "", text, flags=re.IGNORECASE)
    return re.sub(r"\s{2,}", " ", text).strip()


def _apply_entity_casing_to_text(text: str) -> tuple[str, List[str]]:
    """Apply canonical entity casing to a plain-text string. Returns (fixed_text, list_of_fixes)."""
    fixes: List[str] = []
    for pattern, replacement in _ENTITY_CANONICAL:
        new = pattern.sub(replacement, text)
        if new != text:
            for m in pattern.finditer(text):
                orig = m.group(0)
                if orig != replacement:
                    fixes.append(f"{orig!r} -> {replacement!r}")
            text = new
    return text, fixes


def clean_image_caption(caption: str, context_title: str = "") -> Optional[str]:
    """
    Evaluate a figcaption string and return a cleaned version or None (remove it).

    Returns:
        str  — cleaned/rewritten caption to use
        None — caption should be removed
    """
    if not caption:
        return None

    stripped = _strip_internal_credit_suffix(caption.strip())
    if not stripped:
        return None

    # Captions produced by the local image pipeline already carry attribution.
    # Keep them intact so the final cleanup does not remove credits.
    if re.search(r"\bcr[eé]dito:\b", stripped, flags=re.IGNORECASE):
        fixed, _ = _apply_entity_casing_to_text(stripped)
        return fixed

    # Too short or too long with no punctuation -> likely raw alt text
    if len(stripped) < 12:
        return None
    if len(stripped) > 140 and not re.search(r'[.,;!?]', stripped):
        return None

    # Detect raw English alt patterns
    is_raw = any(p.search(stripped) for p in _RAW_CAPTION_PATTERNS)

    if is_raw:
        # Try to build a contextual fallback
        for pattern, fallback in _FALLBACK_CAPTIONS:
            if pattern.search(stripped) or (context_title and pattern.search(context_title)):
                return fallback
        # No match: remove the caption
        return None

    # Apply entity casing fixes to the caption
    fixed, _ = _apply_entity_casing_to_text(stripped)
    return fixed


def final_pre_publish_cleanup(
    html_content: str,
    title: str = "",
    entities: Optional[List[str]] = None,
    db_id: Any = "?",
) -> str:
    """
    Last-pass sanitization of HTML content immediately before WordPress publish.

    Applies:
    - Canonical entity casing to visible text nodes in <p>, <h2>, <h3>, <li>,
      <figcaption>, <strong>, <em>, <a> (text only, not href/src)
    - Cleans or rewrites raw English figcaptions
    - Removes duplicate consecutive captions

    Does NOT alter href, src, class, id, data-* attributes or URLs.
    """
    if not html_content:
        return html_content

    logger.info("[FINAL_CLEANUP] start db_id=%s", db_id)

    soup = BeautifulSoup(html_content, "html.parser")
    entity_fixes: List[str] = []
    captions_removed = 0
    captions_rewritten = 0

    # --- 1. Fix entity casing in text nodes of visible tags ---
    TEXT_TAGS = {"p", "h2", "h3", "h4", "li", "strong", "em", "blockquote", "span"}
    for tag in soup.find_all(TEXT_TAGS):
        for node in tag.children:
            # Only NavigableString (plain text), not nested tags
            if hasattr(node, 'string') and node.string is None:
                continue
            from bs4 import NavigableString
            if not isinstance(node, NavigableString):
                continue
            original = str(node)
            fixed, fixes = _apply_entity_casing_to_text(original)
            if fixes:
                node.replace_with(fixed)
                entity_fixes.extend(fixes)

    # Fix entity casing inside <a> text (but not href attribute)
    for a_tag in soup.find_all("a"):
        for node in list(a_tag.children):
            from bs4 import NavigableString
            if not isinstance(node, NavigableString):
                continue
            original = str(node)
            fixed, fixes = _apply_entity_casing_to_text(original)
            if fixes:
                node.replace_with(fixed)
                entity_fixes.extend(fixes)

    # --- 2. Clean figcaptions ---
    seen_captions: set = set()
    for figure in soup.find_all("figure"):
        cap = figure.find("figcaption")
        if cap is None:
            continue
        raw_text = cap.get_text(" ", strip=True)

        # Deduplicate
        norm_key = re.sub(r'\s+', ' ', raw_text.lower().strip())
        if norm_key in seen_captions:
            cap.decompose()
            captions_removed += 1
            logger.info("[FINAL_CLEANUP] removed_duplicate_caption db_id=%s", db_id)
            continue
        seen_captions.add(norm_key)

        cleaned = clean_image_caption(raw_text, context_title=title)
        if cleaned is None:
            logger.info(
                "[FINAL_CLEANUP] removed_raw_caption=%r db_id=%s",
                raw_text[:80],
                db_id,
            )
            cap.decompose()
            captions_removed += 1
        elif cleaned != raw_text:
            cap.clear()
            cap.append(cleaned)
            captions_rewritten += 1
            logger.info(
                "[FINAL_CLEANUP] rewritten_caption %r -> %r db_id=%s",
                raw_text[:60],
                cleaned[:60],
                db_id,
            )

    for fix in entity_fixes:
        logger.info("[FINAL_CLEANUP] entity_case_fixed %s db_id=%s", fix, db_id)

    logger.info(
        "[FINAL_CLEANUP] done captions_removed=%s captions_rewritten=%s entity_fixes=%s db_id=%s",
        captions_removed, captions_rewritten, len(entity_fixes), db_id,
    )

    return str(soup)


def apply_final_field_casing(value: str) -> str:
    """Apply canonical entity casing to a plain scalar field (title, subtitle, meta, tag)."""
    if not value:
        return value
    fixed, _ = _apply_entity_casing_to_text(value)
    return fixed


# ===========================
# Gutenberg Blocks Converter
# ===========================
