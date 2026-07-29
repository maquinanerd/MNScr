"""Build a delivery contract from an approved editorial draft.

Every blocking condition is decided here, with no network involved. By the time
a payload exists, the draft has already been validated (MS-1), factually
assessed (MS-4) and cleared by the Editorial Gate (MS-3) — and this module
refuses to build anything if any of those is missing.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from .categories import normalize_tags, resolve_category
from .contract import (
    CONTENT_FORMAT_HTML,
    DeliveryMedia,
    EditorialDeliveryPayload,
    is_valid_slug,
    slugify,
)
from .destination import DESTINATION_PAYLOAD_CMS, build_delivery_id, build_idempotency_key
from .errors import DeliveryBlockedError

logger = logging.getLogger(__name__)

#: Only this gate outcome may ever be delivered. Imported lazily inside the
#: check so this module keeps no import-time dependency on the gate package.
GATE_CLEAR_OUTCOME = "GATE_CLEAR"


def _gate_outcome(draft: Any) -> Optional[str]:
    gate = getattr(draft, "editorial_gate", None)
    return getattr(gate, "outcome", None) if gate is not None else None


def check_deliverable(draft: Any) -> List[str]:
    """Reasons this draft must not be delivered. Empty list means it may be.

    Runs entirely offline: a blocked draft never costs a socket.
    """
    reasons: List[str] = []

    if draft is None:
        return ["DRAFT_AUSENTE"]

    if getattr(draft, "blocking_errors", None):
        reasons.append(
            "DRAFT_COM_ERROS_BLOQUEANTES:" + ",".join(list(draft.blocking_errors)[:5])
        )

    assessment = getattr(draft, "factual_assessment", None)
    if assessment is None:
        reasons.append("AVALIACAO_FACTUAL_AUSENTE")
    else:
        critical = getattr(assessment, "critical_conflicts", None) or []
        if critical:
            # The gate already blocks on this, but the delivery layer refuses
            # independently: a guard that only works because another guard works
            # is not a guard.
            reasons.append(f"CONFLITO_FACTUAL_CRITICO:{len(critical)}")

    gate = getattr(draft, "editorial_gate", None)
    if gate is None:
        reasons.append("EDITORIAL_GATE_AUSENTE")
    else:
        outcome = _gate_outcome(draft)
        if outcome != GATE_CLEAR_OUTCOME:
            reasons.append(f"GATE_NAO_LIBERADO:{outcome}")

    content = getattr(draft, "content", None)
    if content is None or not str(getattr(content, "title", "") or "").strip():
        reasons.append("TITULO_AUSENTE")
    if content is None or not str(getattr(content, "body_html", "") or "").strip():
        reasons.append("CORPO_AUSENTE")

    provenance = getattr(draft, "provenance", None)
    if provenance is None or not str(getattr(provenance, "output_hash", "") or "").strip():
        reasons.append("OUTPUT_HASH_AUSENTE")

    return reasons


def _featured_media(draft: Any) -> Optional[DeliveryMedia]:
    """The first media candidate, as metadata only.

    Nothing is downloaded, no licence is invented and no credit is dropped. A
    draft with no usable media is not a failure — media problems and editorial
    problems are separate concerns.
    """
    for candidate in getattr(draft, "media_candidates", None) or []:
        url = str(getattr(candidate, "source_url", "") or "").strip()
        if not url:
            continue
        declared_license = getattr(candidate, "license", None)
        return DeliveryMedia(
            source_url=url,
            source_name=getattr(candidate, "source_domain", None),
            credit=getattr(candidate, "credit", None),
            license_status=str(declared_license) if declared_license else "UNKNOWN",
            alt_text=getattr(candidate, "original_alt", None)
            or getattr(candidate, "original_caption", None),
            remote_media_id=None,
        )
    return None


def _sources(draft: Any) -> List[Dict[str, Any]]:
    """Source attributions, never stripped."""
    result: List[Dict[str, Any]] = []
    for source in getattr(draft, "sources", None) or []:
        url = str(getattr(source, "url", "") or "").strip()
        if not url:
            continue
        result.append(
            {
                "url": url,
                "name": getattr(source, "name", None),
                "domain": getattr(source, "domain", None),
                "title": getattr(source, "title", None),
                "author": getattr(source, "author", None),
                "published_at": getattr(source, "published_at", None),
                "is_primary": bool(getattr(source, "is_primary", False)),
            }
        )
    return result


def _assessment_summary(draft: Any) -> Tuple[Dict[str, Any], Optional[str]]:
    """A compact factual summary — counts and coverage, never full claims.

    The destination gets enough to show a reviewer why the draft is trustworthy
    without receiving the entire evidence corpus.
    """
    assessment = getattr(draft, "factual_assessment", None)
    if assessment is None:
        return {}, None
    coverage = getattr(assessment, "coverage", None)
    summary: Dict[str, Any] = {
        "assessment_id": getattr(assessment, "assessment_id", None),
        "version": getattr(assessment, "version", None),
        "mode": getattr(assessment, "mode", None),
        "claim_count": len(getattr(assessment, "claims", []) or []),
        "evidence_count": len(getattr(assessment, "evidence", []) or []),
        "conflict_count": len(getattr(assessment, "conflicts", []) or []),
    }
    if coverage is not None:
        summary.update(
            {
                "coverage_ratio": getattr(coverage, "coverage_ratio", None),
                "total_material_claims": getattr(coverage, "total_material_claims", None),
                "supported_material_claims": getattr(coverage, "supported_material_claims", None),
                "unsupported_material_claims": getattr(
                    coverage, "unsupported_material_claims", None
                ),
                "source_diversity": getattr(coverage, "source_diversity", None),
                "review_required": getattr(coverage, "review_required", None),
            }
        )
    return summary, getattr(assessment, "assessment_hash", None)


def _gate_summary(draft: Any) -> Tuple[Dict[str, Any], Optional[str]]:
    gate = getattr(draft, "editorial_gate", None)
    if gate is None:
        return {}, None
    return (
        {
            "gate_id": getattr(gate, "gate_id", None),
            "policy_version": getattr(gate, "policy_version", None),
            "outcome": getattr(gate, "outcome", None),
            "blocking_count": getattr(gate, "blocking_count", None),
            "warning_count": getattr(gate, "warning_count", None),
            "triggered_rules": [
                r.rule_code for r in (getattr(gate, "triggered_rules", None) or [])
            ][:20],
        },
        getattr(gate, "gate_hash", None),
    )


def build_delivery_payload(
    draft: Any,
    *,
    destination: str = DESTINATION_PAYLOAD_CMS,
    topic: Optional[str] = None,
) -> EditorialDeliveryPayload:
    """Build the contract for an approved draft.

    Raises :class:`DeliveryBlockedError` when the draft must not leave — including
    when its topic has no canonical category, which is a deliberate block rather
    than a fallback: shipping an invented classification would be worse than
    stopping and asking a human to extend the mapping.
    """
    reasons = check_deliverable(draft)
    if reasons:
        raise DeliveryBlockedError("Entrega bloqueada: " + "; ".join(reasons))

    content = draft.content
    provenance = draft.provenance

    resolved_topic = topic or (draft.metadata_topic if hasattr(draft, "metadata_topic") else None)
    category, category_reason = resolve_category(
        resolved_topic, list(content.category_suggestions or [])
    )
    if category is None:
        raise DeliveryBlockedError(f"Entrega bloqueada: CATEGORIA_NAO_RESOLVIDA:{category_reason}")

    content_hash = str(provenance.output_hash)
    idempotency_key = build_idempotency_key(
        draft_id=draft.draft_id, content_hash=content_hash, destination=destination
    )
    delivery_id = build_delivery_id(idempotency_key)

    slug = content.slug_suggestion if is_valid_slug(content.slug_suggestion) else None
    if slug is None and content.slug_suggestion:
        # A malformed suggestion is normalized rather than dropped: the CMS
        # still needs a slug, and the original stays visible in metadata.
        slug = slugify(content.slug_suggestion) or None
    if slug is None:
        slug = slugify(content.title) or None

    assessment_summary, assessment_hash = _assessment_summary(draft)
    gate_summary, gate_hash = _gate_summary(draft)

    return EditorialDeliveryPayload(
        delivery_id=delivery_id,
        draft_id=draft.draft_id,
        title=content.title,
        content=content.body_html,
        content_format=CONTENT_FORMAT_HTML,
        category=category,
        content_hash=content_hash,
        source_event_id=getattr(provenance, "input_event_key", None) or draft.event_key,
        source_revision=getattr(provenance, "input_revision", None) or draft.revision,
        source_item_ids=[s["url"] for s in _sources(draft)],
        slug=slug,
        excerpt=content.subtitle or content.summary or content.meta_description,
        tags=normalize_tags(list(content.tag_suggestions or [])),
        authors=[],  # MNScr nao atribui autoria humana ao que a IA redigiu
        sources=_sources(draft),
        featured_media=_featured_media(draft),
        factual_assessment=assessment_summary,
        editorial_gate=gate_summary,
        entity_mentions=[],  # MS-5 nao extrai mencoes; ver contract.EntityMention
        assessment_hash=assessment_hash,
        gate_hash=gate_hash,
        metadata={
            "idempotency_key": idempotency_key,
            "pipeline_version": getattr(provenance, "pipeline_version", None),
            "input_contract_version": getattr(provenance, "input_contract_version", None),
            "output_contract_version": getattr(provenance, "output_contract_version", None),
            "original_slug_suggestion": content.slug_suggestion,
            "draft_warnings": list(draft.warnings or [])[:20],
        },
    )


__all__ = [
    "GATE_CLEAR_OUTCOME",
    "build_delivery_payload",
    "check_deliverable",
]
