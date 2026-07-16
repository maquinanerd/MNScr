import html
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse, unquote

from bs4 import BeautifulSoup

from .wordpress import WordPressClient

logger = logging.getLogger(__name__)

IMAGE_TIME_BUDGET_S = int(os.getenv("IMAGE_TIME_BUDGET_S", 150))
DEFAULT_ALLOWED_DOMAIN = os.getenv("PUBLISHER_DOMAIN", "")

_IMG_EXT_RE = re.compile(r"\.(?:jpe?g|png|webp|gif)(?:$|\?)", re.IGNORECASE)
_FILENAME_ALT_RE = re.compile(
    r"(?:^|[\s_-])(?:sgrl|trl|img|image|photo|wp|uploads?|\d{2,})(?:$|[\s_-])",
    re.IGNORECASE,
)
_STOPWORDS = {
    "a", "o", "os", "as", "um", "uma", "de", "da", "do", "das", "dos", "e",
    "em", "no", "na", "nos", "nas", "para", "por", "com", "sem", "sobre",
    "novo", "nova", "filme", "serie", "série", "materia", "matéria",
}
_JUNK_IMAGE_URL_RE = re.compile(
    r"(lazyload|fallback|placeholder|blank\.gif|spacer\.gif|(?:^|[^\d])1x1(?:[^\d]|$))",
    re.IGNORECASE,
)
_INTERNAL_SOURCE_RE = re.compile(r"\b(?:via\s+)?RSSPRIME(?:\s+Superfeed)?\b", re.IGNORECASE)


@dataclass
class LocalizedImage:
    source_url: str
    media_id: int
    local_url: str
    width: int
    height: int
    srcset: str
    section: str
    alt: str
    caption: str


def _plain_text(value: str) -> str:
    text = BeautifulSoup(value or "", "html.parser").get_text(" ", strip=True)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _tokens(value: str) -> Set[str]:
    words = re.findall(r"\w+", _plain_text(value).lower(), flags=re.UNICODE)
    return {word for word in words if len(word) >= 3 and word not in _STOPWORDS}


def _image_url(candidate: Any) -> str:
    if isinstance(candidate, str):
        raw = candidate.strip()
        if raw.startswith("<"):
            soup = BeautifulSoup(raw, "html.parser")
            img = soup.find("img")
            return str(img.get("src") or img.get("data-src") or "") if img else ""
        return raw
    if isinstance(candidate, dict):
        return str(candidate.get("url") or candidate.get("src") or candidate.get("href") or "").strip()
    return ""


def _is_junk_image_url(image_url: str) -> bool:
    return bool(_JUNK_IMAGE_URL_RE.search(image_url or ""))


def _image_alt(candidate: Any) -> str:
    if isinstance(candidate, str) and candidate.strip().startswith("<"):
        soup = BeautifulSoup(candidate, "html.parser")
        img = soup.find("img")
        return str(img.get("alt") or "") if img else ""
    if isinstance(candidate, dict):
        return str(candidate.get("alt") or candidate.get("alt_text") or "").strip()
    return ""


def _image_caption(candidate: Any) -> str:
    if isinstance(candidate, str) and candidate.strip().startswith("<"):
        soup = BeautifulSoup(candidate, "html.parser")
        cap = soup.find("figcaption")
        return cap.get_text(" ", strip=True) if cap else ""
    if isinstance(candidate, dict):
        return str(candidate.get("caption") or candidate.get("figcaption") or "").strip()
    return ""


def _caption_keys(image_url: str) -> List[str]:
    parsed = urlparse(html.unescape(image_url or ""))
    path = unquote(parsed.path or image_url or "").strip().lower()
    basename = path.rsplit("/", 1)[-1]
    return [key for key in (basename, path) if key]


def _caption_from_map(image_url: str, image_captions: Dict[str, str]) -> str:
    for key in _caption_keys(image_url):
        if key in image_captions:
            return str(image_captions[key]).strip()
    return ""


def _strip_internal_source(value: str) -> str:
    text = _INTERNAL_SOURCE_RE.sub("", value or "")
    return re.sub(r"\s+", " ", text).strip(" .;,-")


def _extract_credit(value: str) -> str:
    text = _plain_text(value)
    match = re.search(r"(?:credit|photo|image|courtesy)\s*(?:by|:)?\s*([^.;()]+)", text, re.IGNORECASE)
    return _strip_internal_source(match.group(1)) if match else ""


def _is_bad_alt(value: str) -> bool:
    text = _plain_text(value)
    if len(text) < 20:
        return True
    if _IMG_EXT_RE.search(text):
        return True
    if "_" in text:
        return True
    if _FILENAME_ALT_RE.search(text):
        return True
    if len(re.findall(r"\d+", text)) >= 2:
        return True
    return False


def _looks_english(value: str) -> bool:
    lowered = f" {_plain_text(value).lower()} "
    strong_markers = (
        " riding ", " set for ", " on set ", " mid-fight ", " stares down ",
        " faces off ", " behind the scenes ", " yellow hills ",
    )
    if any(marker in lowered for marker in strong_markers):
        return True

    weak_markers = (" as ", " in ", " and ", " with ")
    return sum(1 for marker in weak_markers if marker in lowered) >= 2


def _caption_to_pt(value: str) -> str:
    text = _plain_text(value)
    replacements = [
        (r"\bRiding Spacehog\b", "em sua moto espacial"),
        (r"\bDCU\b", "DC Universe"),
        (r"\bmid-fight\b", "em cena de luta"),
        (r"\bon Set\b", "no set"),
        (r"\bbehind the scenes\b", "nos bastidores"),
        (r"\bfaces off\b", "enfrenta"),
        (r"\bstares down\b", "encara"),
    ]
    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return _plain_text(text)


def _normalize_original_caption(value: str) -> str:
    text = _strip_internal_source(_plain_text(value))
    text = re.sub(r"\bfrom left(?: to right)?:\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\([^)]*(?:credit|photo|image|courtesy|via)[^)]*\)", "", text, flags=re.IGNORECASE)
    text = re.sub(r"(?:credit|photo|image|courtesy)\s*(?:by|:).*$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip(" .;,-")
    if text.isupper():
        text = text.capitalize()
    return text


def _translate_caption_to_pt(value: str, api_client: Any = None) -> str:
    text = _normalize_original_caption(value)
    if not text:
        return ""
    if api_client is not None:
        prompt = (
            "Traduza e normalize esta legenda de imagem para PT-BR. "
            "Use frase curta jornalistica, sem credito, sem 'from left', sem explicacao. "
            f"Legenda: {text}"
        )
        try:
            raw, _tokens = api_client.generate_text(
                prompt,
                generation_config={
                    "temperature": 0.1,
                    "max_output_tokens": 120,
                    "response_mime_type": "text/plain",
                },
                response_schema=None,
            )
            translated = _strip_internal_source(_plain_text(str(raw)).strip('"'))
            if translated:
                return translated
        except Exception as exc:
            logger.warning("IMG_CAPTION_TRANSLATE_FAILED error=%s", exc)
    if _looks_english(text):
        return _caption_to_pt(text)
    return text


def _trim_words(value: str, min_words: int = 5, max_words: int = 12) -> str:
    words = _plain_text(value).split()
    if len(words) > max_words:
        words = words[:max_words]
    if len(words) < min_words:
        return _plain_text(value)
    return " ".join(words).rstrip(".,;:")


def _main_entity(title: str) -> str:
    match = re.search(r"\b[A-ZÁÀÂÃÉÊÍÓÔÕÚÇ][\wÁÀÂÃÉÊÍÓÔÕÚÇáàâãéêíóôõúç.-]+(?:\s+[A-ZÁÀÂÃÉÊÍÓÔÕÚÇ][\wÁÀÂÃÉÊÍÓÔÕÚÇáàâãéêíóôõúç.-]+)?", title or "")
    return match.group(0) if match else "Imagem da matéria"


def _build_alt(
    *,
    original_alt: str,
    article_title: str,
    section_title: str,
    used_alts: Set[str],
    caption: str = "",
) -> str:
    if caption:
        base = _trim_words(caption, max_words=18)
    elif original_alt and not _is_bad_alt(original_alt):
        base = _trim_words(_caption_to_pt(original_alt) if _looks_english(original_alt) else original_alt)
    else:
        base = _main_entity(article_title)
        base = _trim_words(base)

    alt = base
    suffix = 2
    while alt.lower() in used_alts:
        alt = _trim_words(f"{base} {suffix}", max_words=12)
        suffix += 1
    used_alts.add(alt.lower())
    logger.info('ALT_FIX alt="%s"', alt)
    return alt


def _build_caption(original_caption: str, alt: str = "", holder: str = "", api_client: Any = None) -> str:
    caption = _translate_caption_to_pt(original_caption, api_client=api_client)
    caption = _trim_words(caption, min_words=4, max_words=18)
    if not caption:
        logger.info('FIGCAPTION caption="" credito=""')
        return ""
    credit_holder = _strip_internal_source(holder)
    if not credit_holder:
        logger.info('FIGCAPTION caption="%s" credito=""', caption)
        return f"{caption}." if not caption.endswith(".") else caption
    credit = f"Crédito: {credit_holder}."
    logger.info('FIGCAPTION caption="%s" credito="%s"', caption, credit)
    return f"{caption}. <em>{credit}</em>" if not caption.endswith(".") else f"{caption} <em>{credit}</em>"


def _rights_holder(source_name: str, article_title: str, content_html: str) -> str:
    combined = f"{source_name} {article_title} {_plain_text(content_html)}".lower()
    if "dc studios" in combined or "supergirl" in combined or "warner" in combined:
        return "DC Studios/Warner Bros. Pictures"
    if "house of the dragon" in combined or "hbo" in combined:
        return "HBO"
    if "marvel" in combined:
        return "Marvel Studios"
    return ""


def _extract_sections(soup: BeautifulSoup) -> List[Tuple[Any, str]]:
    sections: List[Tuple[Any, str]] = []
    for heading in soup.find_all("h2"):
        title = heading.get_text(" ", strip=True)
        if title:
            sections.append((heading, title))
    return sections


def _score_section(image_text: str, section_title: str) -> int:
    return len(_tokens(image_text) & _tokens(section_title))


def _choose_section(
    sections: List[Tuple[Any, str]],
    image_text: str,
    used_sections: Set[str],
) -> Tuple[Optional[Any], str]:
    if not sections:
        return None, ""
    scored = []
    for index, (heading, title) in enumerate(sections):
        penalty = 100 if title in used_sections else 0
        scored.append((_score_section(image_text, title) - penalty, -index, heading, title))
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    _, _, heading, title = scored[0]
    used_sections.add(title)
    return heading, title


def _make_figure(soup: BeautifulSoup, image: LocalizedImage):
    figure = soup.new_tag("figure")
    figure["class"] = ["wp-block-image", "size-large"]
    figure["data-wp-media-id"] = str(image.media_id)

    img = soup.new_tag("img")
    img["src"] = image.local_url
    img["alt"] = image.alt
    img["class"] = [f"wp-image-{image.media_id}"]
    if image.width:
        img["width"] = str(image.width)
    if image.height:
        img["height"] = str(image.height)
    if image.srcset:
        img["srcset"] = image.srcset
        img["sizes"] = f"(max-width: {image.width}px) 100vw, {image.width}px"
    figure.append(img)

    if image.caption:
        figcaption = soup.new_tag("figcaption")
        figcaption.append(BeautifulSoup(image.caption, "html.parser"))
        figure.append(figcaption)
    return figure


def _media_image_data(media: Dict[str, Any]) -> Dict[str, Any]:
    """Select a registered WordPress body-image size and its intrinsic dimensions."""
    details = media.get("media_details") or {}
    sizes = details.get("sizes") or {}
    selected: Dict[str, Any] = {}
    for size_name in ("large", "medium_large", "medium"):
        candidate = sizes.get(size_name) or {}
        if candidate.get("source_url"):
            selected = candidate
            break

    local_url = str(selected.get("source_url") or media.get("source_url") or "")
    width = int(selected.get("width") or details.get("width") or 0)
    height = int(selected.get("height") or details.get("height") or 0)

    srcset_items = []
    for candidate in sizes.values():
        candidate_url = str(candidate.get("source_url") or "")
        candidate_width = int(candidate.get("width") or 0)
        if candidate_url and candidate_width and (not width or candidate_width <= width):
            srcset_items.append((candidate_width, candidate_url))
    srcset = ", ".join(
        f"{candidate_url} {candidate_width}w"
        for candidate_width, candidate_url in sorted(set(srcset_items))
    )
    return {
        "url_local": local_url,
        "width": width,
        "height": height,
        "srcset": srcset,
    }


def _is_allowed_image_url(image_url: str, allowed_domain: str) -> bool:
    hostname = (urlparse(image_url or "").hostname or "").lower().rstrip(".")
    allowed = (allowed_domain or "").lower().lstrip(".").rstrip(".")
    return bool(hostname and allowed and (hostname == allowed or hostname.endswith("." + allowed)))


def finalize_content_images(
    *,
    content_html: str,
    wp_client: WordPressClient,
    article_title: str,
    post_ref: Any,
    localized_images: Optional[List[Dict[str, Any]]] = None,
    allowed_domain: str = DEFAULT_ALLOWED_DOMAIN,
) -> Tuple[str, List[Dict[str, Any]]]:
    """
    Final image pass after every content enricher.

    Reuses already localized attachments, uploads any newly introduced external
    image, applies a registered WP size, and writes intrinsic dimensions.
    """
    soup = BeautifulSoup(content_html or "", "html.parser")
    records: Dict[str, Dict[str, Any]] = {}
    output_records = list(localized_images or [])
    for item in output_records:
        for key in (item.get("origem"), item.get("url_local")):
            if key:
                records[_norm_image_url(str(key))] = item

    for img in soup.find_all("img"):
        source_url = str(img.get("src") or "").strip()
        if not source_url:
            continue
        record = records.get(_norm_image_url(source_url))

        if record is None and not _is_allowed_image_url(source_url, allowed_domain):
            media = wp_client.upload_media_from_url(source_url, article_title)
            if media and media.get("id") and media.get("source_url"):
                image_data = _media_image_data(media)
                record = {
                    "media_id": int(media["id"]),
                    "url_local": image_data["url_local"],
                    "width": image_data["width"],
                    "height": image_data["height"],
                    "srcset": image_data["srcset"],
                    "origem": source_url,
                    "alt": str(img.get("alt") or article_title),
                    "caption": "",
                    "secao": "",
                }
                output_records.append(record)
                records[_norm_image_url(source_url)] = record
                records[_norm_image_url(record["url_local"])] = record
                logger.info(
                    "IMG_FINAL_LOCALIZED post=%s origem=%s media_id=%s url_local=%s",
                    post_ref,
                    source_url,
                    record["media_id"],
                    record["url_local"],
                )

        if record is None:
            continue

        img["src"] = str(record.get("url_local") or source_url)
        media_id = int(record.get("media_id") or 0)
        if media_id:
            classes = [c for c in (img.get("class") or []) if not str(c).startswith("wp-image-")]
            img["class"] = [*classes, f"wp-image-{media_id}"]
            figure = img.find_parent("figure")
            if figure is not None:
                figure["data-wp-media-id"] = str(media_id)
                figure["class"] = ["wp-block-image", "size-large"]
        width = int(record.get("width") or 0)
        height = int(record.get("height") or 0)
        if width and height:
            img["width"] = str(width)
            img["height"] = str(height)
        srcset = str(record.get("srcset") or "")
        if srcset:
            img["srcset"] = srcset
            img["sizes"] = f"(max-width: {width}px) 100vw, {width}px"
        else:
            img.attrs.pop("srcset", None)
            img.attrs.pop("sizes", None)
        for attr in ("data-src", "data-original", "data-lazy-src", "data-image", "data-img-url"):
            img.attrs.pop(attr, None)

    return str(soup), output_records


def _norm_image_url(image_url: str) -> str:
    return html.unescape(image_url or "").strip().rstrip("/").lower()


def _remove_existing_body_images(soup: BeautifulSoup) -> None:
    for figure in list(soup.find_all("figure")):
        if figure.find("img"):
            figure.decompose()
    for img in list(soup.find_all("img")):
        img.decompose()


def localize_and_place_body_images(
    *,
    content_html: str,
    images: List[Any],
    image_captions: Optional[Dict[str, str]] = None,
    wp_client: WordPressClient,
    article_title: str,
    source_name: str,
    max_images: int = 3,
    api_client: Any = None,
) -> Tuple[str, List[Dict[str, Any]]]:
    """
    Upload body images to WordPress media and place one contextual image per H2 section.
    Never falls back to hotlinking when upload fails.
    """
    if not content_html or not images:
        return content_html, []

    soup = BeautifulSoup(content_html, "html.parser")
    _remove_existing_body_images(soup)

    sections = _extract_sections(soup)
    image_captions = image_captions or {}
    used_urls: Set[str] = set()
    used_alts: Set[str] = set()
    used_sections: Set[str] = set()
    localized: List[LocalizedImage] = []
    budget_start = time.monotonic()
    total_candidates = len(images)

    for candidate in images:
        if time.monotonic() - budget_start > IMAGE_TIME_BUDGET_S:
            logger.warning(
                "[IMAGE_BUDGET] tempo esgotado, publicando com %s imagens (de %s candidatas)",
                len(localized),
                total_candidates,
            )
            break

        source_url = _image_url(candidate)
        if not source_url:
            continue
        normalized_url = source_url.strip()
        if normalized_url in used_urls:
            continue
        used_urls.add(normalized_url)
        if _is_junk_image_url(normalized_url):
            logger.info("IMG_DISCARDED origem=%s motivo=junk_placeholder_url", normalized_url)
            continue

        media = wp_client.upload_media_from_url(normalized_url, article_title)
        if not media or not media.get("id") or not media.get("source_url"):
            logger.info("IMG_DISCARDED origem=%s motivo=download_fail", normalized_url)
            continue

        original_caption = _caption_from_map(normalized_url, image_captions)
        image_text = " ".join([_image_alt(candidate), original_caption, article_title])
        heading, section_title = _choose_section(sections, image_text, used_sections)
        caption = _build_caption(
            original_caption,
            holder=_extract_credit(original_caption),
            api_client=api_client,
        )
        alt = _build_alt(
            original_alt=_image_alt(candidate),
            article_title=article_title,
            section_title=section_title,
            used_alts=used_alts,
            caption=_plain_text(caption),
        )

        media_id = int(media["id"])
        image_data = _media_image_data(media)
        wp_client.set_media_alt_text(media_id, alt)
        try:
            wp_client.session.post(
                f"{wp_client.api_url}/media/{media_id}",
                json={"caption": {"raw": caption}},
                timeout=20,
            )
        except Exception as exc:
            logger.warning("IMG_CAPTION_META_FAILED media_id=%s error=%s", media_id, exc)

        item = LocalizedImage(
            source_url=normalized_url,
            media_id=media_id,
            local_url=image_data["url_local"],
            width=image_data["width"],
            height=image_data["height"],
            srcset=image_data["srcset"],
            section=section_title,
            alt=alt,
            caption=caption,
        )
        localized.append(item)
        logger.info("IMG_LOCALIZED origem=%s media_id=%s url_local=%s", item.source_url, item.media_id, item.local_url)

        if heading is not None:
            heading.insert_after(_make_figure(soup, item))
            logger.info('IMG_PLACED secao="%s" media_id=%s', section_title, media_id)
        else:
            soup.append(_make_figure(soup, item))
            logger.info('IMG_PLACED secao="%s" media_id=%s', "", media_id)

        if len(localized) >= max_images:
            break

    snapshot = [
        {
            "media_id": image.media_id,
            "url_local": image.local_url,
            "width": image.width,
            "height": image.height,
            "srcset": image.srcset,
            "secao": image.section,
            "alt": image.alt,
            "caption": _plain_text(image.caption),
            "origem": image.source_url,
        }
        for image in localized
    ]
    return str(soup), snapshot
