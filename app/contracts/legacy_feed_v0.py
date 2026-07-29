"""Temporary compatibility layer for the pre-contract superfeed.

TEMPORARY. This exists only until RSS Prime emits ``rss-prime-event-v1``. It is
deliberately honest about its own limits: it never fabricates an author, a
per-source date, a cluster confidence or a cursor, because the legacy feed
simply does not carry them. Absent stays absent, and every event it produces is
tagged ``LEGACY_CONTRACT`` so nothing downstream mistakes it for the real thing.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Mapping, Optional

from . import errors as E
from .rssprime_event_v1 import (
    HASH_ORIGIN_LEGACY,
    RssPrimeEventV1,
    RssPrimeSourceV1,
    domain_of,
    url_is_acceptable,
)

logger = logging.getLogger(__name__)

CONTRACT_VERSION = "legacy-feed-input-v0"

#: Superfeed keys the adapter understands. Everything else on the item is kept
#: verbatim under ``metadata['legacy_unmapped']`` — unknown stays unknown.
_MAPPED_LEGACY_KEYS = frozenset(
    {
        "event_key",
        "urls",
        "additional_urls",
        "source_count",
        "multi_source",
        "is_multi_source",
        "primary_source",
        "all_sources",
        "first_seen",
        "last_seen",
        "topic",
        "title",
        "url",
        "id",
        "_raw",
        "has_superfeed_meta",
        "is_cluster",
        "cluster_signature",
        "cluster_size",
        "fonte_nome",
        "published",
        "author",
        "summary",
    }
)


def _split_multi(value: Any) -> List[str]:
    if value in (None, ""):
        return []
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [part.strip() for part in re.split(r"[|,]", str(value)) if part.strip()]


class LegacyFeedV0Adapter:
    """Converts a legacy superfeed item into the normalized domain."""

    contract_version = CONTRACT_VERSION

    def adapt(self, item: Mapping[str, Any]) -> RssPrimeEventV1:
        """Normalize without validating.

        ``revision`` is always 1: the legacy feed has no notion of revisions, so
        claiming anything else would be an invention.
        """
        item = item or {}

        primary_url = str(item.get("url") or "").strip()
        urls: List[str] = []
        for candidate in [primary_url, *_split_multi(item.get("urls")), *_split_multi(item.get("additional_urls"))]:
            if candidate and candidate not in urls:
                urls.append(candidate)

        source_names = _split_multi(item.get("all_sources"))
        primary_name = str(item.get("primary_source") or item.get("fonte_nome") or "").strip() or None

        sources: List[RssPrimeSourceV1] = []
        for index, url in enumerate(urls):
            domain = domain_of(url) or ""
            is_primary = bool(primary_url) and url == primary_url
            # Names arrive as a parallel list at best; only trust the primary one.
            name: Optional[str] = None
            if is_primary and primary_name:
                name = primary_name
            elif index < len(source_names):
                name = source_names[index] or None
            sources.append(
                RssPrimeSourceV1(
                    source_id=domain or url,
                    domain=domain,
                    url=url,
                    name=name,
                    title=None,
                    author=None,          # o feed legado nao envia autor por fonte
                    published_at=None,    # nem data por fonte
                    source_type=None,
                    is_primary=is_primary,
                )
            )

        # No source flagged primary (item had no `url`): promote the first one so
        # the event is still coherent, and let validation see a single primary.
        if sources and not any(s.is_primary for s in sources):
            sources[0].is_primary = True

        unmapped = {
            key: value
            for key, value in item.items()
            if key not in _MAPPED_LEGACY_KEYS and not str(key).startswith("_")
        }
        metadata: Dict[str, Any] = {"legacy_source": True}
        if unmapped:
            metadata["legacy_unmapped"] = unmapped

        valid_sources = [s for s in sources if url_is_acceptable(s.url)]
        first_seen = str(item.get("first_seen") or item.get("published") or "").strip()
        last_seen = str(item.get("last_seen") or first_seen).strip()

        event = RssPrimeEventV1(
            event_key=str(item.get("event_key") or "").strip(),
            topic=str(item.get("topic") or "").strip(),
            title=str(item.get("title") or "").strip(),
            first_seen=first_seen,
            last_seen=last_seen,
            sources=sources,
            revision=1,
            payload_hash="",
            payload_hash_origin=HASH_ORIGIN_LEGACY,
            contract_version=CONTRACT_VERSION,
            is_multi_source=len(valid_sources) >= 2,
            source_count=len(valid_sources),
            cluster_confidence=None,  # o feed legado nao envia confianca de cluster
            cursor=None,              # nem cursor
            metadata=metadata,
        )
        event.payload_hash = event.calculate_payload_hash()
        return event

    def validate(self, item: Mapping[str, Any]):
        """Adapt then validate, always carrying the LEGACY_CONTRACT warning."""
        from .validation import validate_event

        event = self.adapt(item)
        legacy_warning = E.warning(
            E.LEGACY_CONTRACT,
            "item recebido no contrato legado; hash calculado localmente",
            field_name="contract_version",
            payload_hash_origin=HASH_ORIGIN_LEGACY,
        )
        result = validate_event(
            event, expect_declared_hash=False, extra_issues=[legacy_warning]
        )
        logger.info(
            "[CONTRACT_DETECTED] contract=%s event_key=%s cursor_mode=unavailable",
            CONTRACT_VERSION,
            event.event_key or "-",
        )
        return result


__all__ = ["CONTRACT_VERSION", "LegacyFeedV0Adapter"]
