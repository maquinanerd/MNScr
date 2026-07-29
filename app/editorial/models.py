"""Domain model for the MNScr editorial draft.

This is the output contract of MNScr. It is CMS-neutral on purpose: it carries
no WordPress post id, no media attachment id, no taxonomy id and no published
URL. Categories and tags are *suggestions* (plain strings) for a human editor
to accept or reject inside the CMS.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from .serialization import stable_hash, to_plain
from .states import (
    ALLOWED_EVIDENCE_STATUSES,
    ALLOWED_MEDIA_STATUSES,
    DRAFT_GENERATED,
    EVIDENCE_OBSERVED,
    MEDIA_UNVERIFIED,
    validate_draft_status,
)

# --- Contract versions -----------------------------------------------------
# TEMPORARY (MS-1). `legacy-feed-input-v0` describes the current, unversioned
# `sf:*` RSS namespace that MNScr consumes today. It must be replaced by the
# real versioned contract once RSS Prime publishes one.
# `mnscr-editorial-draft-v0` is likewise provisional and will be renegotiated
# with Cinerie Editorial when the Payload CMS contract is finalized.
INPUT_CONTRACT_VERSION = "legacy-feed-input-v0"  # TEMPORARY — awaiting RSS Prime
OUTPUT_CONTRACT_VERSION = "mnscr-editorial-draft-v0"  # TEMPORARY — awaiting Cinerie

PIPELINE_VERSION = "mnscr-ms1"

_WP_UPLOAD_PATTERN = re.compile(r"/wp-content/uploads/", re.IGNORECASE)
_WP_IMAGE_CLASS_PATTERN = re.compile(r"wp-image-\d+", re.IGNORECASE)
_WP_BLOCK_PATTERN = re.compile(r"<!--\s*/?wp:", re.IGNORECASE)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


@dataclass
class SourceReference:
    """One source document that fed the draft."""

    url: str
    source_id: Optional[str] = None
    name: Optional[str] = None
    domain: Optional[str] = None
    title: Optional[str] = None
    author: Optional[str] = None
    published_at: Optional[str] = None
    extraction_status: Optional[str] = None
    is_primary: bool = False

    def __post_init__(self) -> None:
        if not _clean(self.url):
            raise ValueError("SourceReference.url is required")
        self.url = str(self.url).strip()
        self.source_id = _clean(self.source_id)
        self.name = _clean(self.name)
        self.domain = _clean(self.domain)
        self.title = _clean(self.title)
        self.author = _clean(self.author)
        self.published_at = _clean(self.published_at)
        self.extraction_status = _clean(self.extraction_status)
        self.is_primary = bool(self.is_primary)


@dataclass
class EvidenceRecord:
    """A claim traced back to the source excerpt that supports (or not) it."""

    evidence_id: str
    source_url: str
    source_name: Optional[str] = None
    source_excerpt: Optional[str] = None
    claim: Optional[str] = None
    status: str = EVIDENCE_OBSERVED

    def __post_init__(self) -> None:
        if not _clean(self.evidence_id):
            raise ValueError("EvidenceRecord.evidence_id is required")
        if not _clean(self.source_url):
            raise ValueError("EvidenceRecord.source_url is required")
        if self.status not in ALLOWED_EVIDENCE_STATUSES:
            raise ValueError(
                f"Unknown evidence status {self.status!r}. "
                f"Allowed: {sorted(ALLOWED_EVIDENCE_STATUSES)}"
            )
        self.evidence_id = str(self.evidence_id).strip()
        self.source_url = str(self.source_url).strip()
        self.source_name = _clean(self.source_name)
        self.source_excerpt = _clean(self.source_excerpt)
        self.claim = _clean(self.claim)


@dataclass
class MediaCandidate:
    """An image the source used. MNScr never downloads or re-hosts it.

    Unknown licence and unknown credit stay unknown. MS-0 found the previous
    pipeline guessing rights holders from keywords and inventing captions; that
    is deliberately not reproduced here.
    """

    source_url: str
    source_domain: Optional[str] = None
    original_alt: Optional[str] = None
    original_caption: Optional[str] = None
    credit: Optional[str] = None
    license: Optional[str] = None
    media_type: str = "image"
    status: str = MEDIA_UNVERIFIED

    def __post_init__(self) -> None:
        if not _clean(self.source_url):
            raise ValueError("MediaCandidate.source_url is required")
        if self.status not in ALLOWED_MEDIA_STATUSES:
            raise ValueError(
                f"Unknown media status {self.status!r}. "
                f"Allowed: {sorted(ALLOWED_MEDIA_STATUSES)}"
            )
        self.source_url = str(self.source_url).strip()
        self.source_domain = _clean(self.source_domain)
        self.original_alt = _clean(self.original_alt)
        self.original_caption = _clean(self.original_caption)
        self.credit = _clean(self.credit)
        self.license = _clean(self.license)
        self.media_type = _clean(self.media_type) or "image"


@dataclass
class DraftContent:
    """The editorial body and its SEO suggestions. No CMS identifiers."""

    title: str
    body_html: str
    subtitle: Optional[str] = None
    summary: Optional[str] = None
    meta_description: Optional[str] = None
    slug_suggestion: Optional[str] = None
    focus_keyphrase: Optional[str] = None
    related_keyphrases: list[str] = field(default_factory=list)
    category_suggestions: list[str] = field(default_factory=list)
    tag_suggestions: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.title = str(self.title or "").strip()
        self.body_html = str(self.body_html or "").strip()
        self.subtitle = _clean(self.subtitle)
        self.summary = _clean(self.summary)
        self.meta_description = _clean(self.meta_description)
        self.slug_suggestion = _clean(self.slug_suggestion)
        self.focus_keyphrase = _clean(self.focus_keyphrase)
        self.related_keyphrases = [
            text for text in (_clean(item) for item in self.related_keyphrases or []) if text
        ]
        self.category_suggestions = [
            text for text in (_clean(item) for item in self.category_suggestions or []) if text
        ]
        self.tag_suggestions = [
            text for text in (_clean(item) for item in self.tag_suggestions or []) if text
        ]


@dataclass
class DraftProvenance:
    """How this draft was produced, for auditability."""

    input_hash: str
    output_hash: str
    pipeline_version: str = PIPELINE_VERSION
    commit_sha: Optional[str] = None
    input_contract_version: str = INPUT_CONTRACT_VERSION
    output_contract_version: str = OUTPUT_CONTRACT_VERSION
    # MS-2: identity of the input delivery this draft was produced from. Lets a
    # reviewer trace a draft back to the exact revision and payload that caused
    # it, and lets replay rebuild it without going back to the feed.
    input_event_key: Optional[str] = None
    input_revision: Optional[int] = None
    input_payload_hash: Optional[str] = None
    input_cursor: Optional[str] = None
    model_provider: Optional[str] = None
    model_name: Optional[str] = None
    prompt_version: Optional[str] = None
    created_at: str = field(default_factory=_utc_now_iso)
    duration_ms: Optional[int] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    total_tokens: Optional[int] = None


@dataclass
class EditorialDraft:
    """The MNScr output artifact. Terminal state of the pipeline."""

    draft_id: str
    article_id: int | str
    content: DraftContent
    provenance: DraftProvenance
    event_key: Optional[str] = None
    cluster_signature: Optional[str] = None
    revision: int = 1
    # MS-2: when the acontecimento was first and last observed by RSS Prime, and
    # how confident it is that these sources are one story. Editorial context for
    # the reviewer; never a publication decision.
    first_seen: Optional[str] = None
    last_seen: Optional[str] = None
    cluster_confidence: Optional[float] = None
    status: str = DRAFT_GENERATED
    sources: list[SourceReference] = field(default_factory=list)
    evidence: list[EvidenceRecord] = field(default_factory=list)
    media_candidates: list[MediaCandidate] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    blocking_errors: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.status = validate_draft_status(self.status)
        self.revision = int(self.revision or 1)
        self.event_key = _clean(self.event_key)
        self.cluster_signature = _clean(self.cluster_signature)
        self.warnings = [text for text in (_clean(item) for item in self.warnings or []) if text]
        self.blocking_errors = [
            text for text in (_clean(item) for item in self.blocking_errors or []) if text
        ]

    # -- helpers ------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return to_plain(self)

    def content_hash(self) -> str:
        """Deterministic hash of the editorial payload only."""
        return stable_hash(
            {
                "content": to_plain(self.content),
                "sources": to_plain(self.sources),
                "evidence": to_plain(self.evidence),
                "media_candidates": to_plain(self.media_candidates),
                "event_key": self.event_key,
                "cluster_signature": self.cluster_signature,
                "revision": self.revision,
            }
        )


# --- Deterministic identifiers --------------------------------------------


def build_draft_id(
    *,
    event_key: Optional[str] = None,
    article_id: int | str | None = None,
    canonical_url: Optional[str] = None,
    revision: int = 1,
) -> str:
    """Deterministic draft id.

    Preferred key: ``event_key + revision``.
    Fallback when there is no event_key: ``article_id + canonical_url + revision``.
    """
    revision = int(revision or 1)
    normalized_event_key = _clean(event_key)
    if normalized_event_key:
        seed = f"event:{normalized_event_key}|rev:{revision}"
    else:
        normalized_article = _clean(article_id) or ""
        normalized_url = _clean(canonical_url) or ""
        if not normalized_article and not normalized_url:
            raise ValueError(
                "build_draft_id requires event_key, or article_id/canonical_url"
            )
        seed = f"article:{normalized_article}|url:{normalized_url}|rev:{revision}"
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]
    return f"draft-{digest}"


# --- Validation ------------------------------------------------------------


def validate_draft(draft: EditorialDraft) -> list[str]:
    """Return blocking errors. Empty list means the draft may be submitted.

    Warnings never become approval: they are preserved on the draft and do not
    appear here.
    """
    errors: list[str] = []

    if not draft.content.title:
        errors.append("EMPTY_TITLE")
    if not draft.content.body_html:
        errors.append("EMPTY_BODY")
    if not draft.sources:
        errors.append("NO_SOURCES")

    if not draft.provenance.input_hash:
        errors.append("MISSING_INPUT_HASH")
    if not draft.provenance.output_hash:
        errors.append("MISSING_OUTPUT_HASH")
    if not draft.provenance.output_contract_version:
        errors.append("MISSING_OUTPUT_CONTRACT")

    try:
        validate_draft_status(draft.status)
    except ValueError as exc:
        errors.append(f"INVALID_STATE: {exc}")

    # CMS leakage guards: the draft must be CMS-neutral.
    payload = draft.to_dict()
    flat = str(payload)
    if "wp_post_id" in flat or "featured_media" in flat:
        errors.append("WORDPRESS_IDENTIFIER_PRESENT")
    if _WP_UPLOAD_PATTERN.search(draft.content.body_html):
        errors.append("WORDPRESS_UPLOAD_URL_PRESENT")
    if _WP_IMAGE_CLASS_PATTERN.search(draft.content.body_html):
        errors.append("WORDPRESS_MEDIA_CLASS_PRESENT")
    if _WP_BLOCK_PATTERN.search(draft.content.body_html):
        errors.append("WORDPRESS_BLOCK_MARKUP_PRESENT")

    return errors
