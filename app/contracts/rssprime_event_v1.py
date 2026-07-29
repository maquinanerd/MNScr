"""The ``rss-prime-event-v1`` input contract.

This is the *normalized domain*, not a wire format. Nothing here knows about
XML, namespaces or feedparser: the transport layer hands over a plain mapping
and gets back typed objects. Fields MNScr does not model are preserved verbatim
in ``metadata`` rather than dropped or guessed at.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Final, List, Mapping, Optional
from urllib.parse import urlsplit

from .hashing import calculate_event_payload_hash, normalize_datetime

CONTRACT_VERSION: Final[str] = "rss-prime-event-v1"

#: Where the hash came from. Never conflate the two: a locally computed hash
#: proves internal consistency, not that RSS Prime agrees with us.
HASH_ORIGIN_VERIFIED: Final[str] = "rss-prime-verified"
HASH_ORIGIN_LEGACY: Final[str] = "mnscr-calculated-legacy"

ALLOWED_URL_SCHEMES: Final[frozenset[str]] = frozenset({"http", "https"})

#: Keys the v1 model owns. Anything else the feed sends survives in ``metadata``.
_KNOWN_EVENT_KEYS: Final[frozenset[str]] = frozenset(
    {
        "contract_version",
        "event_key",
        "revision",
        "payload_hash",
        "topic",
        "title",
        "first_seen",
        "last_seen",
        "is_multi_source",
        "source_count",
        "sources",
        "cluster_confidence",
        "cursor",
        "metadata",
    }
)

_KNOWN_SOURCE_KEYS: Final[frozenset[str]] = frozenset(
    {
        "source_id",
        "name",
        "domain",
        "url",
        "title",
        "author",
        "published_at",
        "source_type",
        "is_primary",
    }
)

#: camelCase spellings accepted on the wire, mapped to the internal snake_case.
_EVENT_ALIASES: Final[Dict[str, str]] = {
    "contractVersion": "contract_version",
    "eventKey": "event_key",
    "payloadHash": "payload_hash",
    "firstSeen": "first_seen",
    "lastSeen": "last_seen",
    "isMultiSource": "is_multi_source",
    "multiSource": "is_multi_source",
    "sourceCount": "source_count",
    "clusterConfidence": "cluster_confidence",
}

_SOURCE_ALIASES: Final[Dict[str, str]] = {
    "sourceId": "source_id",
    "publishedAt": "published_at",
    "sourceType": "source_type",
    "isPrimary": "is_primary",
}


def _clean(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _coerce_bool(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "sim", "y"}:
        return True
    if text in {"0", "false", "no", "nao", "não", "n", ""}:
        return False
    return None


def _apply_aliases(payload: Mapping[str, Any], aliases: Mapping[str, str]) -> Dict[str, Any]:
    """Rename known camelCase keys without losing anything."""
    result: Dict[str, Any] = {}
    for key, value in payload.items():
        result[aliases.get(str(key), str(key))] = value
    return result


@dataclass
class RssPrimeSourceV1:
    """One source inside an event.

    ``author`` and ``published_at`` stay ``None`` when the feed does not send
    them. MNScr never infers either — a wrong byline is worse than no byline.
    """

    source_id: str
    domain: str
    url: str
    name: Optional[str] = None
    title: Optional[str] = None
    author: Optional[str] = None
    published_at: Optional[str] = None
    source_type: Optional[str] = None
    is_primary: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "RssPrimeSourceV1":
        data = _apply_aliases(payload or {}, _SOURCE_ALIASES)
        url = _clean(data.get("url")) or ""
        domain = _clean(data.get("domain")) or domain_of(url) or ""
        source_id = _clean(data.get("source_id")) or domain or url
        extra = {k: v for k, v in data.items() if k not in _KNOWN_SOURCE_KEYS}
        return cls(
            source_id=source_id,
            domain=domain,
            url=url,
            name=_clean(data.get("name")),
            title=_clean(data.get("title")),
            author=_clean(data.get("author")),
            published_at=_clean(data.get("published_at")),
            source_type=_clean(data.get("source_type")),
            is_primary=bool(_coerce_bool(data.get("is_primary")) or False),
            metadata=extra,
        )

    def to_hashable_dict(self) -> Dict[str, Any]:
        return {
            "source_id": self.source_id,
            "domain": self.domain,
            "url": self.url,
            "name": self.name,
            "title": self.title,
            "author": self.author,
            "published_at": normalize_datetime(self.published_at) if self.published_at else None,
            "source_type": self.source_type,
            "is_primary": self.is_primary,
        }

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RssPrimeEventV1:
    """A normalized RSS Prime event.

    An *acontecimento* is identified by ``event_key``; a *revision* is a
    numbered take on it; the *payload* is what arrived for that revision and is
    identified by ``payload_hash``. Keeping the three apart is what makes
    idempotency and replay possible.
    """

    event_key: str
    topic: str
    title: str
    first_seen: str
    last_seen: str
    sources: List[RssPrimeSourceV1] = field(default_factory=list)
    revision: int = 1
    payload_hash: str = ""
    payload_hash_origin: str = HASH_ORIGIN_VERIFIED
    contract_version: str = CONTRACT_VERSION
    is_multi_source: bool = False
    source_count: int = 0
    cluster_confidence: Optional[float] = None
    cursor: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    # --- Construction ------------------------------------------------------

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "RssPrimeEventV1":
        """Build without validating. Use ``validation.validate_event`` after."""
        data = _apply_aliases(payload or {}, _EVENT_ALIASES)

        raw_sources = data.get("sources") or []
        if not isinstance(raw_sources, (list, tuple)):
            raw_sources = []
        sources = [
            RssPrimeSourceV1.from_mapping(item)
            for item in raw_sources
            if isinstance(item, Mapping)
        ]

        declared_metadata = data.get("metadata")
        metadata: Dict[str, Any] = dict(declared_metadata) if isinstance(declared_metadata, Mapping) else {}
        # Unknown top-level fields are preserved rather than silently dropped;
        # a future contract may promote them, and losing them here is lossy.
        for key, value in data.items():
            if key not in _KNOWN_EVENT_KEYS:
                metadata.setdefault(key, value)

        multi_source = _coerce_bool(data.get("is_multi_source"))
        source_count = _coerce_int(data.get("source_count"))

        return cls(
            event_key=_clean(data.get("event_key")) or "",
            topic=_clean(data.get("topic")) or "",
            title=_clean(data.get("title")) or "",
            first_seen=_clean(data.get("first_seen")) or "",
            last_seen=_clean(data.get("last_seen")) or "",
            sources=sources,
            revision=_coerce_int(data.get("revision"), default=1),
            payload_hash=(_clean(data.get("payload_hash")) or "").lower(),
            contract_version=_clean(data.get("contract_version")) or "",
            is_multi_source=bool(multi_source) if multi_source is not None else len(sources) >= 2,
            source_count=source_count if source_count is not None else len(sources),
            cluster_confidence=_coerce_float(data.get("cluster_confidence")),
            cursor=_clean(data.get("cursor")),
            metadata=metadata,
        )

    # --- Derived views -----------------------------------------------------

    @property
    def primary_source(self) -> Optional[RssPrimeSourceV1]:
        for source in self.sources:
            if source.is_primary:
                return source
        return self.sources[0] if self.sources else None

    @property
    def primary_url(self) -> Optional[str]:
        primary = self.primary_source
        return primary.url if primary else None

    @property
    def urls(self) -> List[str]:
        return [source.url for source in self.sources if source.url]

    @property
    def additional_urls(self) -> List[str]:
        primary = self.primary_url
        return [url for url in self.urls if url != primary]

    def to_hashable_dict(self) -> Dict[str, Any]:
        """Exactly the content that identifies this payload."""
        return {
            "contract_version": self.contract_version,
            "event_key": self.event_key,
            "revision": int(self.revision),
            "topic": self.topic,
            "title": self.title,
            "first_seen": normalize_datetime(self.first_seen),
            "last_seen": normalize_datetime(self.last_seen),
            "is_multi_source": bool(self.is_multi_source),
            "source_count": int(self.source_count),
            "cluster_confidence": self.cluster_confidence,
            "sources": [source.to_hashable_dict() for source in self.sources],
            "metadata": self.metadata,
        }

    def calculate_payload_hash(self) -> str:
        return calculate_event_payload_hash(self)

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["sources"] = [source.to_dict() for source in self.sources]
        return payload


def _coerce_int(value: Any, default: Optional[int] = None) -> Optional[int]:
    if value is None or value == "":
        return default
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        # Preserved as an out-of-range sentinel so validation can report
        # INVALID_REVISION / INVALID_SOURCE_COUNT instead of silently defaulting.
        return -1


def _coerce_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return float("nan")


def domain_of(url: str) -> Optional[str]:
    """Registrable host of a URL, lowercased, without ``www.``."""
    if not url:
        return None
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return None
    if not host:
        return None
    return host[4:] if host.startswith("www.") else host


def url_is_acceptable(url: str) -> bool:
    """http/https, with a host. Anything else is not an article we can fetch."""
    if not url or not isinstance(url, str):
        return False
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return False
    if parts.scheme.lower() not in ALLOWED_URL_SCHEMES:
        return False
    return bool(parts.hostname)


__all__ = [
    "ALLOWED_URL_SCHEMES",
    "CONTRACT_VERSION",
    "HASH_ORIGIN_LEGACY",
    "HASH_ORIGIN_VERIFIED",
    "RssPrimeEventV1",
    "RssPrimeSourceV1",
    "domain_of",
    "url_is_acceptable",
]
