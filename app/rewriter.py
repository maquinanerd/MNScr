"""Compatibility facade for the legacy rewriter interface.

The production pipeline now uses the 3-phase AI modules directly. This module
is kept only for tests and older integrations that still expect
`ContentRewriter.process_content()`.
"""

import logging
import re
from typing import Dict, List

from bs4 import BeautifulSoup
from slugify import slugify

logger = logging.getLogger(__name__)

_TITLE_MARKERS = ("Novo Título:", "Novo TÃ­tulo:")
_EXCERPT_MARKERS = ("Novo Resumo:",)
_CONTENT_MARKERS = ("Novo Conteúdo:", "Novo ConteÃºdo:")
_ALLOWED_TAGS = {"p", "b", "strong", "i", "em", "a"}
_ALLOWED_ATTRS = {"a": {"href", "title", "target", "rel"}}
_STRIP_TAGS = {"script", "style", "iframe", "img"}


class ContentRewriter:
    """Legacy compatibility layer for parsing and sanitizing AI output."""

    def _parse_ai_response(self, raw_text: str) -> Dict[str, str]:
        response = {"title": "", "excerpt": "", "content": ""}
        if not raw_text:
            return response

        text = raw_text.strip()
        title_end = self._find_first_marker(text, _EXCERPT_MARKERS)
        excerpt_end = self._find_first_marker(text, _CONTENT_MARKERS)

        title_start = self._find_first_marker(text, _TITLE_MARKERS, return_end=True)
        excerpt_start = self._find_first_marker(text, _EXCERPT_MARKERS, return_end=True)
        content_start = self._find_first_marker(text, _CONTENT_MARKERS, return_end=True)

        if title_start is not None and title_end is not None:
            response["title"] = text[title_start:title_end].strip()
        if excerpt_start is not None and excerpt_end is not None:
            response["excerpt"] = text[excerpt_start:excerpt_end].strip()
        if content_start is not None:
            response["content"] = text[content_start:].strip()

        if not all(response.values()):
            logger.warning("AI response parsing was incomplete. Check AI output format.")

        return response

    def _find_first_marker(self, text: str, markers: tuple[str, ...], return_end: bool = False):
        positions = []
        for marker in markers:
            idx = text.find(marker)
            if idx >= 0:
                positions.append((idx, idx + len(marker)))
        if not positions:
            return None
        positions.sort(key=lambda item: item[0])
        return positions[0][1] if return_end else positions[0][0]

    def _sanitize_html(self, html_content: str, domain: str, tags: List[str]) -> str:
        if not html_content:
            return ""

        soup = BeautifulSoup(html_content, "html.parser")

        for tag in soup.find_all(True):
            if tag.name in _STRIP_TAGS:
                tag.decompose()
                continue
            if tag.name not in _ALLOWED_TAGS:
                tag.unwrap()
                continue

            for attr in list(tag.attrs):
                if attr not in _ALLOWED_ATTRS.get(tag.name, set()):
                    del tag[attr]

        self._insert_internal_links(soup, domain, tags)
        cleaned = str(soup).strip()
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        return cleaned

    def _insert_internal_links(self, soup: BeautifulSoup, domain: str, tags: List[str]) -> None:
        if not domain or not tags:
            return

        normalized_tags = [tag.strip() for tag in tags if tag and tag.strip()]
        if not normalized_tags:
            return

        pattern = re.compile(
            r"\b(" + "|".join(re.escape(tag) for tag in sorted(normalized_tags, key=len, reverse=True)) + r")\b",
            re.IGNORECASE,
        )
        linked_tags: set[str] = set()

        for paragraph in soup.find_all("p"):
            for text_node in list(paragraph.find_all(string=True)):
                if text_node.parent and text_node.parent.name == "a":
                    continue

                original_text = str(text_node)
                if not pattern.search(original_text):
                    continue

                def replace_match(match: re.Match[str]) -> str:
                    key = slugify(match.group(1)).lower()
                    if key in linked_tags:
                        return match.group(1)
                    linked_tags.add(key)
                    return f'<a href="{domain.rstrip("/")}/tag/{slugify(match.group(1))}">{match.group(1)}</a>'

                updated_html = pattern.sub(replace_match, original_text)
                text_node.replace_with(BeautifulSoup(updated_html, "html.parser"))

    def process_content(self, raw_ai_text: str, tags: List[str], domain: str) -> Dict[str, str]:
        logger.info("Processing and validating rewritten content")
        parsed = self._parse_ai_response(raw_ai_text)
        parsed["content"] = self._sanitize_html(parsed["content"], domain, tags)
        logger.info("Content processing completed successfully")
        return parsed


def process_content(raw_ai_text: str, tags: List[str], domain: str) -> Dict[str, str]:
    """Module-level helper kept for older call sites."""
    return ContentRewriter().process_content(raw_ai_text, tags, domain)
