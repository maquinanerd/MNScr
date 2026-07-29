"""Factual coverage and editorial source independence.

Coverage answers one question: *how much of what this draft asserts is actually
sustained by the sources we received?* It is an instrument for a reviewer, not a
public score and not an editorial grade.

Source diversity counts **editorial origins**, not URLs. Two links from the same
outlet, or a republication of the same wire piece, are one origin — counting
them twice would manufacture the appearance of independent confirmation.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Sequence
from urllib.parse import urlparse

from . import states as S
from .models import ClaimEvidenceLink, FactualClaim, FactualCoverage, FactualEvidence
from .normalization import normalize_for_comparison

logger = logging.getLogger(__name__)


def _registrable_domain(url: str) -> str:
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""
    if host.startswith("www."):
        host = host[4:]
    parts = host.split(".")
    # Fold sub-brands (br.variety.example → variety.example) so a regional
    # edition does not read as an independent outlet.
    if len(parts) > 2:
        host = ".".join(parts[-2:])
    return host


def compute_editorial_origin_key(
    *,
    source_url: str,
    source_domain: Optional[str] = None,
    source_name: Optional[str] = None,
    canonical_url: Optional[str] = None,
    republished_from: Optional[str] = None,
) -> str:
    """A stable key for "who editorially originated this".

    Deliberately shallow: it groups by declared republication, canonical URL and
    registrable domain. A full media-ownership graph is out of scope — and
    guessing at ownership would be worse than admitting the limit.
    """
    if republished_from:
        return _registrable_domain(republished_from) or normalize_for_comparison(republished_from)
    if canonical_url:
        domain = _registrable_domain(canonical_url)
        if domain:
            return domain
    if source_domain:
        parts = str(source_domain).lower().split(".")
        return ".".join(parts[-2:]) if len(parts) > 2 else str(source_domain).lower()
    domain = _registrable_domain(source_url)
    if domain:
        return domain
    return normalize_for_comparison(source_name or source_url)


def count_editorial_origins(evidence: Sequence[FactualEvidence]) -> int:
    """Distinct editorial origins across evidence."""
    return len({e.editorial_origin_key for e in evidence if e.editorial_origin_key})


def count_independent_origins_supporting(
    claim: FactualClaim,
    links: Sequence[ClaimEvidenceLink],
    evidence: Sequence[FactualEvidence],
) -> int:
    """How many distinct outlets actually sustain this claim.

    Two sources copying the same wire report are one confirmation, so this
    counts origins rather than links.
    """
    by_id = {e.evidence_id: e for e in evidence}
    origins = set()
    for link in links:
        if link.claim_id != claim.claim_id:
            continue
        if link.relation not in S.SUPPORTING_RELATIONS:
            continue
        item = by_id.get(link.evidence_id)
        if item and item.editorial_origin_key:
            origins.add(item.editorial_origin_key)
    return len(origins)


def compute_coverage(
    claims: Sequence[FactualClaim],
    evidence: Sequence[FactualEvidence],
    *,
    minimum_ratio: float = 0.75,
) -> FactualCoverage:
    """Summarise how well the material claims are sustained.

    ``coverage_ratio = (supported + 0.5 × partially_supported) / total``.
    With no material claims the ratio is 0 and review is required: a draft that
    asserts nothing checkable is not a well-covered draft, it is an unexamined
    one.
    """
    material = [c for c in claims if c.is_material]
    total = len(material)

    counts: Dict[str, int] = {status: 0 for status in S.CLAIM_STATUSES}
    for claim in material:
        counts[claim.status] = counts.get(claim.status, 0) + 1

    supported = counts.get(S.SUPPORTED, 0)
    partial = counts.get(S.PARTIALLY_SUPPORTED, 0)
    unsupported = counts.get(S.UNSUPPORTED, 0)
    conflicting = counts.get(S.CONFLICTING, 0)
    unverified = counts.get(S.UNVERIFIED, 0)

    if total == 0:
        ratio = 0.0
        review_required = True
    else:
        ratio = round((supported + 0.5 * partial) / total, 6)
        review_required = (
            ratio < minimum_ratio
            or unsupported > 0
            or conflicting > 0
            or unverified > 0
        )

    coverage = FactualCoverage(
        total_material_claims=total,
        supported_material_claims=supported,
        partially_supported_material_claims=partial,
        unsupported_material_claims=unsupported,
        conflicting_material_claims=conflicting,
        unverified_material_claims=unverified,
        coverage_ratio=ratio,
        source_diversity=count_editorial_origins(evidence),
        primary_evidence_count=sum(1 for e in evidence if e.strength == S.PRIMARY),
        review_required=review_required,
    )
    logger.info(
        "[FACTUAL_COVERAGE_CALCULATED] total=%s supported=%s partial=%s unsupported=%s "
        "conflicting=%s unverified=%s coverage_ratio=%s source_diversity=%s",
        total, supported, partial, unsupported, conflicting, unverified,
        ratio, coverage.source_diversity,
    )
    return coverage


def unsupported_claims_in_critical_locations(
    claims: Sequence[FactualClaim],
) -> List[FactualClaim]:
    """Material, unsupported, and loud — headline, subtitle, meta, lead."""
    return [
        claim for claim in claims
        if claim.is_material
        and claim.status == S.UNSUPPORTED
        and claim.is_in_critical_location
    ]


__all__ = [
    "compute_coverage",
    "compute_editorial_origin_key",
    "count_editorial_origins",
    "count_independent_origins_supporting",
    "unsupported_claims_in_critical_locations",
]
