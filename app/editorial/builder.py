"""Assemble an :class:`EditorialDraft` from pipeline runtime data.

This is the boundary between the legacy pipeline dictionaries and the MNScr
output contract. Everything CMS-specific stops here.
"""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Any, Iterable, Optional
from urllib.parse import urlparse

from .models import (
    INPUT_CONTRACT_VERSION,
    DraftContent,
    DraftProvenance,
    EditorialDraft,
    EvidenceRecord,
    MediaCandidate,
    SourceReference,
    build_draft_id,
)
from .serialization import stable_hash
from .states import (
    DRAFT_GENERATED,
    EVIDENCE_OBSERVED,
    EVIDENCE_UNVERIFIED,
    MEDIA_UNVERIFIED,
)

logger = logging.getLogger(__name__)

_IMG_SRC_RE = re.compile(r"""<img[^>]+src=["']([^"']+)["']""", re.IGNORECASE)
_IMG_ALT_RE = re.compile(r"""alt=["']([^"']*)["']""", re.IGNORECASE)
_FIGCAPTION_RE = re.compile(r"<figcaption[^>]*>(.*?)</figcaption>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")


def _domain_of(url: str) -> Optional[str]:
    try:
        host = (urlparse(url or "").hostname or "").lower()
    except Exception:
        return None
    if host.startswith("www."):
        host = host[4:]
    return host or None


def _text_of(html: str) -> str:
    return re.sub(r"\s+", " ", _TAG_RE.sub(" ", html or "")).strip()


def build_source_references(art_data: dict[str, Any]) -> list[SourceReference]:
    """Build source references from cluster docs or from the single source."""
    references: list[SourceReference] = []
    seen: set[str] = set()

    audit = art_data.get("sources_used") or []
    docs = art_data.get("cluster_docs") or []
    audit_by_url = {
        str(item.get("url")): item for item in audit if isinstance(item, dict) and item.get("url")
    }

    for index, doc in enumerate(docs):
        if not isinstance(doc, dict):
            continue
        url = str(doc.get("url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        audited = audit_by_url.get(url, {})
        references.append(
            SourceReference(
                url=url,
                source_id=art_data.get("source_id"),
                name=doc.get("source") or doc.get("source_name") or audited.get("source_name"),
                domain=doc.get("domain") or audited.get("domain") or _domain_of(url),
                title=doc.get("title") or audited.get("title"),
                author=doc.get("author") or audited.get("author"),
                published_at=doc.get("published_at") or audited.get("published_at"),
                extraction_status=doc.get("status") or audited.get("status"),
                is_primary=index == 0,
            )
        )

    if not references:
        url = str(art_data.get("url") or "").strip()
        if url:
            extracted = art_data.get("extracted") or {}
            references.append(
                SourceReference(
                    url=url,
                    source_id=art_data.get("source_id"),
                    name=(art_data.get("feed_config") or {}).get("source_name"),
                    domain=_domain_of(url),
                    title=extracted.get("title") or art_data.get("title"),
                    author=extracted.get("author"),
                    published_at=extracted.get("published_at") or art_data.get("published_at"),
                    extraction_status="ARTICLE_BODY_OK",
                    is_primary=True,
                )
            )

    return references


def build_skipped_evidence(art_data: dict[str, Any]) -> list[EvidenceRecord]:
    """Record sources the pipeline discarded, so the loss is auditable."""
    records: list[EvidenceRecord] = []
    for skipped in art_data.get("sources_skipped") or []:
        if not isinstance(skipped, dict):
            continue
        url = str(skipped.get("url") or "").strip()
        if not url:
            continue
        records.append(
            EvidenceRecord(
                evidence_id=f"skipped-{hashlib.sha256(url.encode('utf-8')).hexdigest()[:16]}",
                source_url=url,
                source_name=skipped.get("source_name") or skipped.get("domain"),
                source_excerpt=None,
                claim=None,
                status=EVIDENCE_UNVERIFIED,
            )
        )
    return records


def build_source_evidence(sources: Iterable[SourceReference], art_data: dict[str, Any]) -> list[EvidenceRecord]:
    """One evidence record per used source, carrying a short excerpt."""
    records: list[EvidenceRecord] = []
    docs = {
        str(doc.get("url")): doc
        for doc in (art_data.get("cluster_docs") or [])
        if isinstance(doc, dict) and doc.get("url")
    }
    fallback_excerpt = _text_of((art_data.get("extracted") or {}).get("content", ""))[:400]

    for source in sources:
        doc = docs.get(source.url)
        excerpt = _text_of(doc.get("content", ""))[:400] if isinstance(doc, dict) else fallback_excerpt
        records.append(
            EvidenceRecord(
                evidence_id=f"src-{hashlib.sha256(source.url.encode('utf-8')).hexdigest()[:16]}",
                source_url=source.url,
                source_name=source.name or source.domain,
                source_excerpt=excerpt or None,
                claim=source.title,
                status=EVIDENCE_OBSERVED,
            )
        )
    return records


def build_media_candidates(art_data: dict[str, Any], body_html: str = "") -> list[MediaCandidate]:
    """Collect image references without downloading or re-hosting anything.

    Licence and credit stay ``None`` unless the source itself provided them.
    """
    candidates: list[MediaCandidate] = []
    seen: set[str] = set()
    extracted = art_data.get("extracted") or {}
    captions = extracted.get("image_captions") or {}

    def _add(url: str, alt: Optional[str] = None, caption: Optional[str] = None) -> None:
        clean_url = (url or "").strip()
        if not clean_url or clean_url.startswith("data:") or clean_url in seen:
            return
        seen.add(clean_url)
        basename = clean_url.rsplit("/", 1)[-1].split("?")[0].lower()
        candidates.append(
            MediaCandidate(
                source_url=clean_url,
                source_domain=_domain_of(clean_url),
                original_alt=alt,
                original_caption=caption or captions.get(basename),
                credit=None,
                license=None,
                media_type="image",
                status=MEDIA_UNVERIFIED,
            )
        )

    featured = extracted.get("featured_image_url")
    if featured:
        _add(str(featured))

    for item in extracted.get("images", []) or []:
        if isinstance(item, str) and item.strip().startswith("<"):
            src = _IMG_SRC_RE.search(item)
            alt = _IMG_ALT_RE.search(item)
            caption = _FIGCAPTION_RE.search(item)
            if src:
                _add(
                    src.group(1),
                    alt.group(1) if alt else None,
                    _text_of(caption.group(1)) if caption else None,
                )
        elif isinstance(item, str):
            _add(item)
        elif isinstance(item, dict):
            _add(
                str(item.get("url") or item.get("src") or ""),
                item.get("alt"),
                item.get("caption"),
            )

    for doc in art_data.get("cluster_docs") or []:
        if not isinstance(doc, dict):
            continue
        if doc.get("featured_image_url"):
            _add(str(doc["featured_image_url"]))

    for match in _IMG_SRC_RE.finditer(body_html or ""):
        _add(match.group(1))

    return candidates


def build_input_fingerprint(
    art_data: dict[str, Any],
    sources: Iterable[SourceReference],
    input_event: Any = None,
) -> str:
    """Deterministic hash of what went into the draft.

    When the draft came from a versioned event, the input payload hash and the
    revision take part: revision 2 of an acontecimento must never share an
    input fingerprint with revision 1.
    """
    fingerprint: dict[str, Any] = {
        "article_id": art_data.get("db_id"),
        "event_key": art_data.get("event_key")
        or (art_data.get("cluster_item") or {}).get("event_key"),
        "cluster_signature": art_data.get("cluster_signature"),
        "source_urls": sorted({source.url for source in sources}),
        "source_word_count": art_data.get("source_word_count"),
        "source_id": art_data.get("source_id"),
    }
    if input_event is not None:
        fingerprint["event_key"] = input_event.event_key or fingerprint["event_key"]
        fingerprint["input_contract_version"] = input_event.contract_version
        fingerprint["input_revision"] = int(input_event.revision or 1)
        fingerprint["input_payload_hash"] = input_event.payload_hash
    return stable_hash(fingerprint)


def build_editorial_draft(
    *,
    art_data: dict[str, Any],
    rewritten: dict[str, Any],
    body_html: str,
    title: str,
    subtitle: Optional[str],
    meta_description: Optional[str],
    warnings: Optional[list[str]] = None,
    blocking_errors: Optional[list[str]] = None,
    model_provider: Optional[str] = None,
    model_name: Optional[str] = None,
    prompt_version: Optional[str] = None,
    commit_sha: Optional[str] = None,
    duration_ms: Optional[int] = None,
    revision: int = 1,
    input_event: Any = None,
) -> EditorialDraft:
    """Assemble the final draft object with deterministic ids and hashes.

    ``input_event`` is the normalized ``RssPrimeEventV1`` this draft came from,
    when there is one. It fixes the revision and carries the input contract
    identity into provenance, so a reviewer can trace the draft back to the
    exact delivery. Passing ``None`` keeps the pre-MS-2 behaviour.
    """
    cluster_item = art_data.get("cluster_item") or {}
    event_key = art_data.get("event_key") or cluster_item.get("event_key")
    cluster_signature = art_data.get("cluster_signature") or cluster_item.get("cluster_signature")

    input_contract_version = INPUT_CONTRACT_VERSION
    input_event_key = input_revision = input_payload_hash = input_cursor = None
    first_seen = art_data.get("first_seen") or cluster_item.get("first_seen")
    last_seen = art_data.get("last_seen") or cluster_item.get("last_seen")
    cluster_confidence = None
    if input_event is not None:
        # The event is the authority on its own identity: never let a stale
        # art_data field override the revision the draft is being built for.
        event_key = input_event.event_key or event_key
        revision = int(input_event.revision or revision)
        input_contract_version = input_event.contract_version or INPUT_CONTRACT_VERSION
        input_event_key = input_event.event_key
        input_revision = int(input_event.revision or 1)
        input_payload_hash = input_event.payload_hash or None
        input_cursor = input_event.cursor
        first_seen = input_event.first_seen or first_seen
        last_seen = input_event.last_seen or last_seen
        cluster_confidence = input_event.cluster_confidence
    article_id = art_data.get("db_id") or art_data.get("id") or ""
    canonical_url = art_data.get("canonical_url") or art_data.get("url") or ""

    sources = build_source_references(art_data)
    evidence = build_source_evidence(sources, art_data) + build_skipped_evidence(art_data)
    media_candidates = build_media_candidates(art_data, body_html)

    categories = [
        str(item.get("nome"))
        for item in (rewritten.get("categorias") or [])
        if isinstance(item, dict) and item.get("nome")
    ]
    tags = [str(item) for item in (rewritten.get("tags_sugeridas") or []) if item]

    content = DraftContent(
        title=title,
        body_html=body_html,
        subtitle=subtitle,
        summary=rewritten.get("summary") or subtitle,
        meta_description=meta_description,
        slug_suggestion=rewritten.get("slug"),
        focus_keyphrase=rewritten.get("focus_keyphrase") or rewritten.get("focus_keyword"),
        related_keyphrases=list(rewritten.get("related_keyphrases") or []),
        category_suggestions=categories,
        tag_suggestions=tags,
    )

    draft_id = build_draft_id(
        event_key=event_key,
        article_id=article_id,
        canonical_url=canonical_url,
        revision=revision,
    )

    input_hash = build_input_fingerprint(art_data, sources, input_event)
    output_hash = stable_hash(
        {
            "content": content,
            "sources": sources,
            "evidence": evidence,
            "media_candidates": media_candidates,
            "event_key": event_key,
            "cluster_signature": cluster_signature,
            "revision": revision,
            "input_payload_hash": input_payload_hash,
        }
    )

    provenance = DraftProvenance(
        input_hash=input_hash,
        output_hash=output_hash,
        input_contract_version=input_contract_version,
        input_event_key=input_event_key,
        input_revision=input_revision,
        input_payload_hash=input_payload_hash,
        input_cursor=input_cursor,
        commit_sha=commit_sha,
        model_provider=model_provider,
        model_name=model_name,
        prompt_version=prompt_version,
        duration_ms=duration_ms,
        input_tokens=rewritten.get("_input_tokens"),
        output_tokens=rewritten.get("_output_tokens"),
        total_tokens=rewritten.get("_total_tokens"),
    )

    return EditorialDraft(
        draft_id=draft_id,
        article_id=article_id,
        event_key=event_key,
        cluster_signature=cluster_signature,
        revision=revision,
        first_seen=first_seen,
        last_seen=last_seen,
        cluster_confidence=cluster_confidence,
        status=DRAFT_GENERATED,
        content=content,
        sources=sources,
        evidence=evidence,
        media_candidates=media_candidates,
        warnings=list(warnings or []),
        blocking_errors=list(blocking_errors or []),
        provenance=provenance,
    )
