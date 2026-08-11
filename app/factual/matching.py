"""Linking claims to evidence, and deriving claim status.

The conservative rule that shapes everything here: **a mention is not support**.
A source that talks about the same film, or even the same announcement, does not
confirm a specific date or figure unless it states that value. Treating topical
overlap as confirmation is exactly how a pipeline manufactures false confidence,
so the default relation is ``MENTIONS`` and the burden is on the value matching.
"""

from __future__ import annotations

import logging
import re
from typing import Dict, Final, List, Optional, Sequence, Tuple

from . import states as S
from .errors import InvalidLinkError
from .models import ClaimEvidenceLink, FactualClaim, FactualEvidence
from .normalization import (
    dates_conflict,
    normalize_date,
    normalize_number,
    numbers_conflict,
    token_overlap,
)

logger = logging.getLogger(__name__)

_PT_MARKERS: Final[frozenset[str]] = frozenset(
    {"as", "de", "do", "dos", "em", "que", "para", "foi", "uma", "estreia", "renovada"}
)
_EN_MARKERS: Final[frozenset[str]] = frozenset(
    {"the", "of", "on", "that", "for", "was", "an", "premieres", "renewed"}
)


def _language(text: str) -> Optional[str]:
    tokens = set(re.findall(r"[a-zA-ZÀ-ÿ]+", (text or "").casefold()))
    pt = len(tokens & _PT_MARKERS)
    en = len(tokens & _EN_MARKERS)
    if pt >= 2 and pt > en:
        return "pt"
    if en >= 2 and en > pt:
        return "en"
    return None


def _cross_lingual_unavailable(
    claim: FactualClaim, evidence: Sequence[FactualEvidence]
) -> bool:
    if claim.semantic_evidence_query:
        return False
    claim_language = _language(claim.source_sentence or claim.display_text)
    evidence_languages = {_language(item.excerpt) for item in evidence}
    evidence_languages.discard(None)
    return bool(claim_language and evidence_languages and claim_language not in evidence_languages)

#: Topical overlap below this means the evidence is not even about the claim.
MIN_TOPICAL_OVERLAP: Final[float] = 0.12
#: Overlap at or above this, without a value match, is still only a mention.
STRONG_TEXT_OVERLAP: Final[float] = 0.55


#: Surface terms that express each canonical status. A claim normalized to
#: ``renewed`` is confirmed by a source saying "renovada" — matching only the
#: canonical English token would miss every Portuguese source.
_STATUS_SYNONYMS: Final[Dict[str, Tuple[str, ...]]] = {
    "renewed": ("renewed", "renovada", "renovado", "renovacao"),
    "cancelled": ("cancelled", "canceled", "cancelada", "cancelado", "cancelamento"),
    "confirmed": ("confirmed", "confirmada", "confirmado", "confirmou"),
    "delayed": ("delayed", "adiada", "adiado", "adiamento", "remarcada", "remarcado"),
    "released": ("released", "estreou", "lancado", "lancada"),
    "releases": ("releases", "estreia", "estreara", "lancamento", "premieres"),
    "available": ("available", "disponivel", "disponibilizada", "disponibilizado"),
    "unavailable": ("unavailable", "indisponivel", "removida", "removido"),
}


def _evidence_mentions_value(evidence: FactualEvidence, value: Optional[str]) -> bool:
    """Whether the normalized value is actually stated in the excerpt."""
    if not value:
        return False
    haystack = evidence.normalized_excerpt
    if value.casefold() in haystack:
        return True
    # Dates and numbers may be spelled differently in the excerpt; compare via
    # the same normalizer that produced the claim value.
    for candidate in (normalize_date(evidence.excerpt), normalize_number(evidence.excerpt)):
        if candidate and candidate.casefold() == value.casefold():
            return True
    # Status values are canonical tokens, not literal text.
    for synonym in _STATUS_SYNONYMS.get(value.casefold(), ()):
        if re.search(rf"\b{re.escape(synonym)}\b", haystack):
            return True
    return False


def _evidence_contradicts_value(claim: FactualClaim, evidence: FactualEvidence) -> bool:
    """Whether the excerpt states a *different* value for the same predicate."""
    if not claim.normalized_value:
        return False
    if claim.claim_type == S.CLAIM_DATE:
        other = normalize_date(evidence.excerpt)
        return bool(other) and dates_conflict(claim.normalized_value, other)
    if claim.claim_type == S.CLAIM_NUMBER:
        other = normalize_number(evidence.excerpt)
        return bool(other) and numbers_conflict(claim.normalized_value, other)
    if claim.claim_type in (S.CLAIM_STATUS, S.CLAIM_RELEASE, S.CLAIM_AVAILABILITY):
        return _status_contradiction(claim.normalized_value, evidence.normalized_excerpt)
    return False


_OPPOSITE_STATUS: Final[Dict[str, Tuple[str, ...]]] = {
    "renewed": ("cancelled", "canceled", "cancelada", "cancelado"),
    "cancelled": ("renewed", "renovada", "renovado"),
    "available": ("unavailable", "indisponivel", "removido", "retirado"),
    "unavailable": ("available", "disponivel"),
    "confirmed": ("desmentiu", "negou", "denied", "rumor"),
    "delayed": ("mantida", "mantido", "on schedule"),
}


def _status_contradiction(value: str, excerpt: str) -> bool:
    for opposite in _OPPOSITE_STATUS.get(value, ()):  # noqa: SIM110 - explicit is clearer
        if opposite in excerpt:
            return True
    return False


def classify_relation(
    claim: FactualClaim, evidence: FactualEvidence
) -> Tuple[Optional[str], float, str]:
    """Decide how one excerpt relates to one claim.

    Returns ``(relation, similarity, rationale)``; ``relation`` is ``None`` when
    the excerpt is not about the claim at all.
    """
    comparison_text = claim.semantic_evidence_query or claim.display_text
    similarity = token_overlap(comparison_text, evidence.excerpt)
    subject_match = (
        bool(claim.normalized_subject)
        and claim.normalized_subject in evidence.normalized_excerpt
    )

    if similarity < MIN_TOPICAL_OVERLAP and not subject_match:
        return None, similarity, ""

    if _evidence_contradicts_value(claim, evidence):
        return (
            S.CONTRADICTS,
            similarity,
            "A fonte informa um valor diferente para a mesma afirmacao.",
        )

    if claim.normalized_value and _evidence_mentions_value(evidence, claim.normalized_value):
        if subject_match or similarity >= MIN_TOPICAL_OVERLAP:
            return (
                S.SUPPORTS,
                similarity,
                "A fonte informa o mesmo valor para a mesma afirmacao.",
            )
        return (
            S.PARTIALLY_SUPPORTS,
            similarity,
            "A fonte informa o mesmo valor, mas o sujeito nao pode ser confirmado.",
        )

    if not claim.normalized_value and similarity >= STRONG_TEXT_OVERLAP:
        return (
            S.PARTIALLY_SUPPORTS,
            similarity,
            "A fonte descreve a mesma afirmacao, sem valor verificavel.",
        )

    # Same subject, no matching value: the source is talking about the thing,
    # not confirming the claim.
    return (
        S.MENTIONS,
        similarity,
        "A fonte cita o mesmo assunto, mas nao confirma a afirmacao.",
    )


def link_claims_to_evidence(
    claims: Sequence[FactualClaim],
    evidence: Sequence[FactualEvidence],
    *,
    max_evidence_per_claim: int = 10,
) -> List[ClaimEvidenceLink]:
    """Build the deterministic claim–evidence graph."""
    links: List[ClaimEvidenceLink] = []
    for claim in claims:
        matches: List[Tuple[float, ClaimEvidenceLink]] = []
        for item in evidence:
            relation, similarity, rationale = classify_relation(claim, item)
            if relation is None:
                continue
            matches.append(
                (
                    similarity,
                    ClaimEvidenceLink(
                        claim_id=claim.claim_id,
                        evidence_id=item.evidence_id,
                        relation=relation,
                        strength=item.strength,
                        similarity=similarity,
                        rationale=rationale,
                    ),
                )
            )
        # Contradictions are never dropped by the per-claim cap: losing one
        # would silently turn a conflict into agreement.
        contradictions = [m for m in matches if m[1].relation == S.CONTRADICTS]
        others = [m for m in matches if m[1].relation != S.CONTRADICTS]
        others.sort(key=lambda pair: pair[0], reverse=True)
        selected = contradictions + others[: max(0, max_evidence_per_claim - len(contradictions))]
        links.extend(link for _, link in selected)

    logger.info("[CLAIM_EVIDENCE_LINKED] links=%s claims=%s", len(links), len(claims))
    return links


def validate_links(
    links: Sequence[ClaimEvidenceLink],
    claims: Sequence[FactualClaim],
    evidence: Sequence[FactualEvidence],
) -> List[ClaimEvidenceLink]:
    """Drop links that point at nothing, or that carry no justification.

    A link the model suggested but that references an unknown claim or evidence
    is not repaired — it is discarded.
    """
    claim_ids = {c.claim_id for c in claims}
    evidence_ids = {e.evidence_id for e in evidence}
    valid: List[ClaimEvidenceLink] = []
    for link in links:
        if link.claim_id not in claim_ids:
            raise InvalidLinkError(
                f"link aponta para claim inexistente: {link.claim_id}",
                claim_id=link.claim_id,
            )
        if link.evidence_id not in evidence_ids:
            raise InvalidLinkError(
                f"link aponta para evidencia inexistente: {link.evidence_id}",
                evidence_id=link.evidence_id,
            )
        if not link.rationale:
            logger.warning(
                "[CLAIM_EVIDENCE_LINKED] link sem justificativa descartado claim_id=%s",
                link.claim_id,
            )
            continue
        valid.append(link)
    return valid


def independent_origins(
    links: Sequence[ClaimEvidenceLink],
    evidence_by_id: Dict[str, FactualEvidence],
    relations: frozenset[str],
) -> set[str]:
    """Distinct editorial origins among links with the given relations.

    Two URLs from the same outlet — or a republication of the same piece — are
    one origin, not two confirmations.
    """
    origins: set[str] = set()
    for link in links:
        if link.relation not in relations:
            continue
        item = evidence_by_id.get(link.evidence_id)
        if item and item.editorial_origin_key:
            origins.add(item.editorial_origin_key)
    return origins


def compute_claim_status(
    claim: FactualClaim,
    links: Sequence[ClaimEvidenceLink],
    evidence_by_id: Dict[str, FactualEvidence],
) -> str:
    """Derive a claim's status from its links.

    ``CONFLICTING`` always wins: a contradicted claim is not "supported with a
    caveat", it is a claim a human must settle.
    """
    own = [link for link in links if link.claim_id == claim.claim_id]
    if not own:
        return S.UNSUPPORTED

    if any(link.relation == S.CONTRADICTS for link in own):
        return S.CONFLICTING

    full = [link for link in own if link.relation == S.SUPPORTS]
    partial = [link for link in own if link.relation == S.PARTIALLY_SUPPORTS]

    if full:
        return S.SUPPORTED
    if partial:
        return S.PARTIALLY_SUPPORTED

    # Only mentions: the sources are about the subject but confirm nothing, so
    # we can neither support nor deny the claim.
    return S.UNVERIFIED


def apply_statuses(
    claims: Sequence[FactualClaim],
    links: Sequence[ClaimEvidenceLink],
    evidence: Sequence[FactualEvidence],
) -> List[FactualClaim]:
    """Recompute every claim's status in place and return the list."""
    evidence_by_id = {e.evidence_id: e for e in evidence}
    for claim in claims:
        own = [link for link in links if link.claim_id == claim.claim_id]
        if not own and _cross_lingual_unavailable(claim, evidence):
            claim.status = S.UNVERIFIED
            if "CROSS_LINGUAL_MATCH_UNAVAILABLE" not in claim.warnings:
                claim.warnings.append("CROSS_LINGUAL_MATCH_UNAVAILABLE")
        else:
            claim.status = compute_claim_status(claim, links, evidence_by_id)
        claim.requires_review = claim.status in S.STATUSES_REQUIRING_REVIEW
        # The hash covers the assertion, not the verdict, so it stays valid.
    return list(claims)


__all__ = [
    "MIN_TOPICAL_OVERLAP",
    "STRONG_TEXT_OVERLAP",
    "apply_statuses",
    "classify_relation",
    "compute_claim_status",
    "independent_origins",
    "link_claims_to_evidence",
    "validate_links",
]
