"""Assemble a :class:`FactualAssessment` from a draft and its source material.

This is the orchestration seam: extraction → evidence → linking → status →
conflicts → coverage. Each step lives in ``app.factual``; this module only
sequences them and records what happened.

It never reaches the network. In ``deterministic`` mode it never calls the AI
either — the assessment is still produced, with its limitations recorded rather
than hidden.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Sequence

from .factual import states as S
from .factual.conflicts import detect_conflicts
from .factual.coverage import compute_coverage, compute_editorial_origin_key
from .factual.errors import InvalidEvidenceError, InvalidModelResponseError
from .factual.extraction import (
    extract_deterministic_claims,
    merge_claims,
    parse_claim_response,
)
from .factual.matching import apply_statuses, link_claims_to_evidence, validate_links
from .factual.models import FactualAssessment, FactualEvidence
from .factual.normalization import normalize_text

logger = logging.getLogger(__name__)

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")

#: A source passage shorter than this carries no checkable content.
MIN_EXCERPT_CHARS = 40

#: Signals that a passage is the originating document rather than a report
#: about one. Reputation alone never makes a source PRIMARY.
_PRIMARY_SIGNALS = (
    "comunicado oficial", "official statement", "press release", "nota oficial",
    "em comunicado", "disse em comunicado", "announced in a statement",
)
_WEAK_SIGNALS = (
    "segundo o site", "de acordo com o portal", "republicado", "reproduzido de",
    "via ", "aggregator", "rumor", "boato",
)


def _sentences(text: str) -> List[str]:
    return [s.strip() for s in _SENTENCE_SPLIT.split(text or "") if s.strip()]


def infer_strength(excerpt: str, *, is_primary_source: bool = False) -> str:
    """Classify how directly a passage evidences a fact.

    ``PRIMARY`` requires a signal that this *is* the originating document — a
    statement, an official note, a direct quote from the party involved. A
    respected outlet reporting someone else's announcement is ``SECONDARY``:
    reputation is not originality.
    """
    lowered = (excerpt or "").casefold()
    if any(signal in lowered for signal in _PRIMARY_SIGNALS):
        return S.PRIMARY
    if any(signal in lowered for signal in _WEAK_SIGNALS):
        return S.WEAK
    if is_primary_source and len(lowered) >= MIN_EXCERPT_CHARS:
        return S.SECONDARY
    if len(lowered) < MIN_EXCERPT_CHARS:
        return S.WEAK
    return S.SECONDARY


def build_evidence_from_sources(
    source_material: Sequence[Dict[str, Any]],
    *,
    max_excerpt_chars: int = 500,
    max_per_source: int = 12,
) -> List[FactualEvidence]:
    """Turn received source documents into short, attributable excerpts.

    Only sentences we actually received become evidence. Nothing is fetched,
    nothing is summarised by a model, and no licence or credit is invented.
    """
    evidence: List[FactualEvidence] = []
    seen: set[str] = set()

    for document in source_material or []:
        url = normalize_text(document.get("url") or document.get("source_url"))
        if not url:
            continue
        text = normalize_text(
            document.get("content")
            or document.get("text")
            or document.get("excerpt")
            or ""
        )
        if not text:
            continue

        origin = compute_editorial_origin_key(
            source_url=url,
            source_domain=document.get("domain") or document.get("source_domain"),
            source_name=document.get("name") or document.get("source_name"),
            canonical_url=document.get("canonical_url"),
            republished_from=document.get("republished_from"),
        )
        is_primary_source = bool(document.get("is_primary"))

        for sentence in _sentences(text)[:max_per_source]:
            if len(sentence) < MIN_EXCERPT_CHARS:
                continue
            try:
                item = FactualEvidence(
                    source_url=url,
                    excerpt=sentence,
                    source_id=document.get("source_id"),
                    source_name=document.get("name") or document.get("source_name"),
                    source_domain=document.get("domain") or document.get("source_domain"),
                    source_title=document.get("title"),
                    published_at=document.get("published_at"),
                    strength=infer_strength(sentence, is_primary_source=is_primary_source),
                    editorial_origin_key=origin,
                    max_excerpt_chars=max_excerpt_chars,
                )
            except InvalidEvidenceError:
                continue
            if item.evidence_id in seen:
                continue
            seen.add(item.evidence_id)
            evidence.append(item)

    logger.info("[EVIDENCE_EXTRACTED] evidence_count=%s", len(evidence))
    return evidence


def build_factual_assessment(
    draft: Any,
    source_material: Optional[Sequence[Dict[str, Any]]] = None,
    *,
    mode: Optional[str] = None,
    claim_response: Any = None,
    max_claims: Optional[int] = None,
    max_evidence_per_claim: Optional[int] = None,
    max_excerpt_chars: Optional[int] = None,
    minimum_coverage_ratio: Optional[float] = None,
) -> FactualAssessment:
    """Build the full factual picture for a draft.

    ``claim_response`` is the already-obtained structured model output, injected
    rather than fetched, so this function performs no I/O and stays testable.
    In ``deterministic`` mode it is ignored entirely.
    """
    from . import config

    resolved_mode = S.validate_mode(mode or config.FACTUAL_ASSESSMENT_MODE)
    max_claims = int(max_claims or config.MAX_CLAIMS_PER_DRAFT)
    max_links = int(max_evidence_per_claim or config.MAX_EVIDENCE_PER_CLAIM)
    max_chars = int(max_excerpt_chars or config.MAX_EVIDENCE_EXCERPT_CHARS)
    min_ratio = (
        config.MIN_FACTUAL_COVERAGE_RATIO
        if minimum_coverage_ratio is None
        else float(minimum_coverage_ratio)
    )

    draft_id = getattr(draft, "draft_id", "") or ""
    logger.info(
        "[FACTUAL_ASSESSMENT_STARTED] draft_id=%s event_key=%s revision=%s mode=%s",
        draft_id, getattr(draft, "event_key", None) or "-",
        getattr(draft, "revision", 1), resolved_mode,
    )

    warnings: List[str] = []
    blocking_errors: List[str] = []

    # 1. Claims — deterministic always, semantic only in hybrid mode.
    claims = extract_deterministic_claims(draft, max_claims=max_claims)
    if resolved_mode == S.MODE_DETERMINISTIC:
        warnings.append("DETERMINISTIC_MODE_ONLY_PATTERN_CLAIMS")
        if claim_response is not None:
            warnings.append("CLAIM_RESPONSE_IGNORED_IN_DETERMINISTIC_MODE")
    elif claim_response is not None:
        try:
            semantic, parse_warnings = parse_claim_response(
                claim_response, draft=draft, max_claims=max_claims
            )
            claims = merge_claims(claims, semantic)
            warnings.extend(parse_warnings)
        except InvalidModelResponseError as exc:
            # A response we cannot parse is discarded, never guessed at: the
            # deterministic claims still stand on their own.
            warnings.append(f"CLAIM_RESPONSE_REJECTED:{exc.reason}")
            logger.warning(
                "[CLAIMS_EXTRACTED] draft_id=%s resposta de IA rejeitada: %s",
                draft_id, exc.reason,
            )
    else:
        warnings.append("NO_SEMANTIC_CLAIM_RESPONSE")

    if len(claims) > max_claims:
        warnings.append(f"CLAIMS_TRUNCATED:{len(claims)}>{max_claims}")
        claims = claims[:max_claims]
    logger.info("[CLAIMS_EXTRACTED] draft_id=%s claim_count=%s", draft_id, len(claims))

    # 2. Evidence from the material we already received.
    evidence = build_evidence_from_sources(
        source_material or [], max_excerpt_chars=max_chars
    )

    # 3. Link, validate, then derive status.
    links = link_claims_to_evidence(claims, evidence, max_evidence_per_claim=max_links)
    links = validate_links(links, claims, evidence)
    claims = apply_statuses(claims, links, evidence)

    # 4. Conflicts, recorded and never resolved.
    conflicts = detect_conflicts(claims, links, evidence)

    # 5. Coverage.
    coverage = compute_coverage(claims, evidence, minimum_ratio=min_ratio)

    if not evidence and claims:
        warnings.append("NO_EVIDENCE_AVAILABLE")
    if not claims:
        warnings.append("NO_CLAIMS_EXTRACTED")

    assessment = FactualAssessment(
        draft_id=draft_id,
        claims=claims,
        evidence=evidence,
        links=links,
        conflicts=conflicts,
        coverage=coverage,
        event_key=getattr(draft, "event_key", None),
        revision=int(getattr(draft, "revision", 1) or 1),
        contract_version=str(
            getattr(getattr(draft, "provenance", None), "input_contract_version", "") or ""
        ),
        version=config.FACTUAL_ASSESSMENT_VERSION,
        mode=resolved_mode,
        warnings=warnings,
        blocking_errors=blocking_errors,
    )

    logger.info(
        "[FACTUAL_ASSESSMENT_COMPLETED] draft_id=%s assessment_id=%s claim_count=%s "
        "evidence_count=%s conflict_count=%s coverage_ratio=%s assessment_hash=%s",
        draft_id, assessment.assessment_id, len(claims), len(evidence),
        len(conflicts), coverage.coverage_ratio, assessment.assessment_hash[:16],
    )
    return assessment


def source_material_from_draft(draft: Any) -> List[Dict[str, Any]]:
    """Best-effort source material reconstructed from the draft itself.

    Used when the original extraction output is no longer around — a
    re-evaluation, for instance. The existing ``EvidenceRecord`` excerpts are
    all that survives, so the assessment it produces is necessarily thinner; the
    alternative would be re-fetching the sources, which replay must not do.
    """
    material: Dict[str, Dict[str, Any]] = {}

    for record in getattr(draft, "evidence", None) or []:
        url = getattr(record, "source_url", "") or ""
        excerpt = getattr(record, "source_excerpt", "") or ""
        if not url or not excerpt:
            continue
        entry = material.setdefault(
            url,
            {
                "url": url,
                "source_name": getattr(record, "source_name", None),
                "content": "",
            },
        )
        entry["content"] = f"{entry['content']} {excerpt}".strip()

    for source in getattr(draft, "sources", None) or []:
        url = getattr(source, "url", "") or ""
        if not url or url in material:
            continue
        title = getattr(source, "title", None)
        if not title:
            continue
        material[url] = {
            "url": url,
            "source_name": getattr(source, "name", None),
            "source_domain": getattr(source, "domain", None),
            "source_id": getattr(source, "source_id", None),
            "published_at": getattr(source, "published_at", None),
            "is_primary": bool(getattr(source, "is_primary", False)),
            "title": title,
            "content": title,
        }

    return list(material.values())


__all__ = [
    "MIN_EXCERPT_CHARS",
    "build_evidence_from_sources",
    "build_factual_assessment",
    "infer_strength",
    "source_material_from_draft",
]
