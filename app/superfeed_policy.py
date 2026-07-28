"""
Entry policy for RSSPRIME Superfeed items.

The checks here run before extraction so bad cluster candidates do not spend
network or AI budget.
"""

from __future__ import annotations

from urllib.parse import urlparse


POLICY_STATUSES = [
    "OK",
    "LIVEBLOG_BLOCKED",
    "OPINION_BLOCKED",
    "BROKEN_ENCODING",
    "SINGLE_SOURCE_NOT_RELIABLE",
    "DUPLICATE_CLUSTER_SIGNATURE",
    "NAVIGATION_NOISE",
    "TOO_SHORT",
    "PAYWALLED",
]

TRUSTED_SINGLE_SOURCES = [
    "deadline.com",
    "variety.com",
    "hollywoodreporter.com",
    "ign.com",
    "gamespot.com",
    "polygon.com",
    "marvel.com",
    "dc.com",
    "nintendo.com",
    "playstation.com",
    "xbox.com",
    "steam",
]

LIVEBLOG_SIGNALS = (
    "/ao-vivo/",
    "aovivo.",
    "/live/",
    "/tempo-real/",
    "ao vivo",
)

OPINION_SIGNALS = (
    "/blog/",
    "/coluna/",
    "coluna-do-",
    "/opiniao/",
    "/opinião/",
    "opinião:",
)

BROKEN_ENCODING_SIGNALS = (
    "\ufffd",
    "\\ufffd",
    "S\\xefo",
    "D\\xf3lar",
    "j\\xe1",
    "ir\\xe3o",
    "Bras\\xedlia",
    "Sïo",
    "jï¿½",
    "irï¿½o",
    "Brasï¿½lia",
    "Ã£",
    "Ã¡",
    "Ã©",
    "Ã­",
    "Ã³",
    "Ãº",
    "Ã§",
)


def check_superfeed_policy(item: dict, *, allow_single_source: bool = False) -> tuple[str, str]:
    """
    Aplica política de entrada ao item do Superfeed.
    Retorna (status, reason).
    status: um dos POLICY_STATUSES
    """
    if _is_liveblog(item):
        return "LIVEBLOG_BLOCKED", "Item parece liveblog/tempo real"
    if _is_opinion(item):
        return "OPINION_BLOCKED", "Item parece opinião, blog ou coluna"
    if _has_broken_encoding(item):
        return "BROKEN_ENCODING", "Título ou descrição contém sinais de encoding quebrado"
    if _is_single_source_unreliable(item, allow_single_source):
        return "SINGLE_SOURCE_NOT_RELIABLE", "Item single-source sem domínio confiável"
    return "OK", "Item aprovado pela política de entrada"


def _item_text(item: dict) -> str:
    return " ".join(
        str(item.get(key) or "")
        for key in ("url", "title", "summary", "description")
    )


def _is_liveblog(item: dict) -> bool:
    """Detecta URLs e títulos de liveblog/tempo real."""
    text = _item_text(item).lower()
    return any(signal in text for signal in LIVEBLOG_SIGNALS)


def _is_opinion(item: dict) -> bool:
    """Detecta blog, coluna e opinião."""
    text = _item_text(item).lower()
    return any(signal in text for signal in OPINION_SIGNALS)


def _has_broken_encoding(item: dict) -> bool:
    """Detecta mojibake no título ou descrição."""
    text = " ".join(
        str(item.get(key) or "")
        for key in ("title", "summary", "description")
    )
    return any(signal in text for signal in BROKEN_ENCODING_SIGNALS)


def _source_domains(item: dict) -> list[str]:
    values: list[str] = []
    for key in ("url", "primary_source"):
        value = item.get(key)
        if value:
            values.append(str(value))
    values.extend(str(value) for value in item.get("urls") or [] if value)
    values.extend(str(value) for value in item.get("all_sources") or [] if value)

    domains: list[str] = []
    for value in values:
        parsed = urlparse(value if "://" in value else f"https://{value}")
        domain = (parsed.hostname or value).lower()
        if domain.startswith("www."):
            domain = domain[4:]
        domains.append(domain)
    return domains


def _has_trusted_single_source(item: dict) -> bool:
    domains = _source_domains(item)
    return any(
        trusted in domain
        for domain in domains
        for trusted in TRUSTED_SINGLE_SOURCES
    )


def _is_single_source_unreliable(item: dict, allow_single_source: bool) -> bool:
    """Bloqueia single-source exceto allowlist ou flag."""
    if allow_single_source:
        return False
    source_count = int(item.get("source_count") or 0)
    multi_source = bool(item.get("multi_source") or item.get("is_multi_source"))
    if source_count <= 1 and not multi_source:
        return not _has_trusted_single_source(item)
    return False
