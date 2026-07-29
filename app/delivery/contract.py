"""The versioned editorial delivery contract.

This is what actually leaves MNScr. It is built from an ``EditorialDraft`` that
has already been validated, factually assessed and cleared by the Editorial
Gate, and it is validated again here — **before** any socket is opened.

Two invariants are load-bearing:

* ``cms_status`` is always ``"draft"``. There is no code path that sets anything
  else, and :func:`validate_delivery_payload` rejects the payload if it somehow
  is. MNScr creates drafts for a human to review; it does not publish.
* the hashes are *carried*, not recomputed. ``content_hash`` is the draft's own
  ``output_hash`` (MS-1), ``assessment_hash`` comes from MS-4 and ``gate_hash``
  from MS-3. Recomputing them here would let the delivery layer disagree with
  the layer that produced them.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from dataclasses import field as dc_field
from datetime import datetime, timezone
from typing import Any, Dict, Final, List, Optional

from .categories import normalize_tags
from .errors import InvalidDeliveryPayloadError

SCHEMA_VERSION: Final[str] = "1"

#: The only value ``cms_status`` may hold. Payload's own draft flag.
CMS_STATUS_DRAFT: Final[str] = "draft"

#: What MNScr asserts about its own output: ready to become a draft for review.
#: Deliberately not "approved" — approval is a human act inside the CMS.
EDITORIAL_STATUS_READY: Final[str] = "READY_FOR_DRAFT"

#: MNScr produces HTML (``DraftContent.body_html``). Declared explicitly so the
#: destination never has to guess, and so a future format change is visible.
CONTENT_FORMAT_HTML: Final[str] = "html"

MAX_EXCERPT_CHARS: Final[int] = 400
MAX_SLUG_LENGTH: Final[int] = 200
_SLUG_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean(value: Any) -> Optional[str]:
    text = " ".join(str(value or "").split()).strip()
    return text or None


def slugify(value: str) -> str:
    """A conservative, deterministic slug.

    Uses ``python-slugify`` (already a dependency) so accented Portuguese
    becomes a sane ASCII slug rather than being stripped to nothing.
    """
    from slugify import slugify as _slugify

    return _slugify(str(value or ""), max_length=MAX_SLUG_LENGTH)[:MAX_SLUG_LENGTH]


def is_valid_slug(value: Optional[str]) -> bool:
    return bool(value) and bool(_SLUG_PATTERN.match(value)) and len(value) <= MAX_SLUG_LENGTH


@dataclass(frozen=True)
class DeliveryMedia:
    """Featured media as *metadata only*.

    MNScr never downloads, re-hosts or uploads an image, and never invents a
    licence or a credit. ``license_status`` stays ``UNKNOWN`` unless the source
    itself said otherwise, and ``remote_media_id`` stays ``None`` because media
    upload is not part of this phase.
    """

    source_url: str
    source_name: Optional[str] = None
    credit: Optional[str] = None
    license_status: str = "UNKNOWN"
    alt_text: Optional[str] = None
    remote_media_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_url": self.source_url,
            "source_name": self.source_name,
            "credit": self.credit,
            "license_status": self.license_status,
            "alt_text": self.alt_text,
            "remote_media_id": self.remote_media_id,
        }


@dataclass(frozen=True)
class EntityMention:
    """A named entity the draft mentions.

    Present so the contract does not need a breaking change when entity linking
    arrives. MS-5 always emits an **empty list**: MNScr has no mention
    extraction, and a second AI pipeline just to populate this field is out of
    scope. ``resolution_status`` is therefore always ``UNRESOLVED`` and no
    external id is ever invented.
    """

    label: str
    type: str = "UNKNOWN"
    resolution_status: str = "UNRESOLVED"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "label": self.label,
            "type": self.type,
            "resolution_status": self.resolution_status,
        }


@dataclass
class EditorialDeliveryPayload:
    """The payload handed to a destination."""

    delivery_id: str
    draft_id: str
    title: str
    content: str
    category: str
    content_hash: str
    source_event_id: Optional[str] = None
    source_revision: Optional[int] = None
    source_item_ids: List[str] = dc_field(default_factory=list)
    slug: Optional[str] = None
    excerpt: Optional[str] = None
    content_format: str = CONTENT_FORMAT_HTML
    tags: List[str] = dc_field(default_factory=list)
    authors: List[str] = dc_field(default_factory=list)
    sources: List[Dict[str, Any]] = dc_field(default_factory=list)
    featured_media: Optional[DeliveryMedia] = None
    factual_assessment: Dict[str, Any] = dc_field(default_factory=dict)
    editorial_gate: Dict[str, Any] = dc_field(default_factory=dict)
    entity_mentions: List[EntityMention] = dc_field(default_factory=list)
    assessment_hash: Optional[str] = None
    gate_hash: Optional[str] = None
    editorial_status: str = EDITORIAL_STATUS_READY
    cms_status: str = CMS_STATUS_DRAFT
    schema_version: str = SCHEMA_VERSION
    generated_at: str = dc_field(default_factory=_utc_now_iso)
    metadata: Dict[str, Any] = dc_field(default_factory=dict)

    def __post_init__(self) -> None:
        self.title = " ".join(str(self.title or "").split()).strip()
        self.content = str(self.content or "").strip()
        self.slug = _clean(self.slug)
        self.excerpt = _clean(self.excerpt)
        self.tags = normalize_tags(self.tags)
        self.authors = [a for a in (_clean(x) for x in self.authors) if a]
        # Not a settable field: no code path may request publication.
        self.cms_status = CMS_STATUS_DRAFT

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "delivery_id": self.delivery_id,
            "draft_id": self.draft_id,
            "source_event_id": self.source_event_id,
            "source_revision": self.source_revision,
            "source_item_ids": list(self.source_item_ids),
            "title": self.title,
            "slug": self.slug,
            "excerpt": self.excerpt,
            "content": self.content,
            "content_format": self.content_format,
            "category": self.category,
            "tags": list(self.tags),
            "authors": list(self.authors),
            "sources": list(self.sources),
            "featured_media": self.featured_media.to_dict() if self.featured_media else None,
            "factual_assessment": dict(self.factual_assessment),
            "editorial_gate": dict(self.editorial_gate),
            "entity_mentions": [m.to_dict() for m in self.entity_mentions],
            "editorial_status": self.editorial_status,
            "cms_status": self.cms_status,
            "generated_at": self.generated_at,
            "content_hash": self.content_hash,
            "assessment_hash": self.assessment_hash,
            "gate_hash": self.gate_hash,
            "metadata": dict(self.metadata),
        }

    def canonical_json(self) -> str:
        """Deterministic serialization: sorted keys, no incidental whitespace."""
        payload = self.to_dict()
        payload.pop("generated_at", None)  # volatile, never part of identity
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def payload_fingerprint(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


# --- Validation ------------------------------------------------------------

REQUIRED_FIELDS: Final[tuple[str, ...]] = (
    "delivery_id", "draft_id", "title", "content", "category", "content_hash",
)


def validate_delivery_payload(payload: EditorialDeliveryPayload) -> List[str]:
    """Return blocking problems. Empty list means the payload may be sent.

    Runs before any network call, so a malformed payload costs nothing.
    """
    errors: List[str] = []

    for name in REQUIRED_FIELDS:
        if not str(getattr(payload, name, "") or "").strip():
            errors.append(f"CAMPO_OBRIGATORIO_AUSENTE:{name}")

    if payload.cms_status != CMS_STATUS_DRAFT:
        errors.append(f"CMS_STATUS_INVALIDO:{payload.cms_status}")

    if payload.editorial_status != EDITORIAL_STATUS_READY:
        errors.append(f"EDITORIAL_STATUS_INVALIDO:{payload.editorial_status}")

    if payload.schema_version != SCHEMA_VERSION:
        errors.append(f"SCHEMA_VERSION_DESCONHECIDA:{payload.schema_version}")

    if payload.content_format != CONTENT_FORMAT_HTML:
        errors.append(f"CONTENT_FORMAT_INVALIDO:{payload.content_format}")

    if payload.slug is not None and not is_valid_slug(payload.slug):
        errors.append(f"SLUG_INVALIDO:{payload.slug[:40]}")

    if payload.excerpt and len(payload.excerpt) > MAX_EXCERPT_CHARS:
        errors.append(f"EXCERPT_LONGO_DEMAIS:{len(payload.excerpt)}")

    if payload.category and payload.category not in _allowed_categories():
        errors.append(f"CATEGORIA_NAO_CANONICA:{payload.category}")

    for mention in payload.entity_mentions:
        if mention.resolution_status != "UNRESOLVED":
            # MS-5 resolves nothing; a resolved mention would be an invented fact.
            errors.append(f"ENTITY_MENTION_RESOLVIDA_INDEVIDAMENTE:{mention.label[:40]}")

    return errors


def _allowed_categories() -> List[str]:
    from .categories import canonical_categories

    return canonical_categories()


def ensure_valid(payload: EditorialDeliveryPayload) -> EditorialDeliveryPayload:
    """Validate or raise. The last gate before the transport layer."""
    errors = validate_delivery_payload(payload)
    if errors:
        raise InvalidDeliveryPayloadError(
            "Payload de entrega invalido: " + "; ".join(errors)
        )
    return payload


__all__ = [
    "CMS_STATUS_DRAFT",
    "CONTENT_FORMAT_HTML",
    "DeliveryMedia",
    "EDITORIAL_STATUS_READY",
    "EditorialDeliveryPayload",
    "EntityMention",
    "MAX_EXCERPT_CHARS",
    "MAX_SLUG_LENGTH",
    "REQUIRED_FIELDS",
    "SCHEMA_VERSION",
    "ensure_valid",
    "is_valid_slug",
    "slugify",
    "validate_delivery_payload",
]
