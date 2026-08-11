"""HTML content limits based on visible text instead of raw markup size."""

from __future__ import annotations

import re

from bs4 import BeautifulSoup, NavigableString, Tag

_VOID_TAGS = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"})
_SKIP_TAGS = frozenset({"script", "style", "noscript"})


def _truncate_text(value: str, remaining: int) -> tuple[str, int, bool]:
    normalized = re.sub(r"\s+", " ", value)
    if not normalized or remaining <= 0:
        return "", 0, remaining <= 0
    if len(normalized) <= remaining:
        return normalized, len(normalized), False

    candidate = normalized[:remaining]
    # Corta SEMPRE em fronteira de palavra: o corte no meio ("confli") entra no
    # prompt da IA e pode voltar publicado. A regra antiga (recuar so quando o
    # espaco estava nos ultimos 20%) deixava a palavra picada passar. Quando o
    # no nao tem espaco nenhum antes do corte, a palavra continua alem dele —
    # descartar o pedaco perde uma palavra do prompt; mante-lo publicaria meia.
    if normalized[remaining : remaining + 1] not in ("", " "):
        split_at = candidate.rfind(" ")
        candidate = candidate[:split_at] if split_at > 0 else ""
    candidate = candidate.rstrip()
    return candidate, len(candidate), True


def truncate_html_by_visible_chars(content_html: str, max_chars: int) -> str:
    """Return valid HTML capped by normalized visible-text characters."""
    if not content_html or max_chars <= 0:
        return ""

    source = BeautifulSoup(content_html, "html.parser")
    output = BeautifulSoup("", "html.parser")
    remaining = max_chars
    stopped = False

    def clone(node, parent) -> None:
        nonlocal remaining, stopped
        if stopped:
            return
        if isinstance(node, NavigableString):
            text, used, reached_limit = _truncate_text(str(node), remaining)
            if text:
                parent.append(NavigableString(text))
                remaining -= used
            stopped = reached_limit or remaining <= 0
            return
        if not isinstance(node, Tag) or node.name in _SKIP_TAGS:
            return

        cloned = output.new_tag(node.name, attrs=dict(node.attrs))
        parent.append(cloned)
        for child in node.children:
            clone(child, cloned)
            if stopped:
                break
        if not cloned.contents and node.name not in _VOID_TAGS:
            cloned.extract()

    roots = source.body.contents if source.body else source.contents
    for root in list(roots):
        clone(root, output)
        if stopped:
            break
    return "".join(str(node) for node in output.contents)
