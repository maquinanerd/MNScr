"""Detecting disagreement between sources.

A conflict is created only when the *same subject* has *incompatible values* for
the *same predicate*, each backed by concrete evidence. Two texts that merely
read differently are not a conflict — most rewrites of the same fact do.

Nothing here resolves anything. There is no majority vote, no source-reputation
tiebreaker and no silent preference. The disagreement is recorded, both values
are preserved, and a human decides.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Sequence, Tuple

from . import states as S
from .models import ClaimEvidenceLink, FactualClaim, FactualConflict, FactualEvidence
from .normalization import dates_conflict, numbers_conflict

logger = logging.getLogger(__name__)

#: Which conflict type a given claim type produces.
_CONFLICT_BY_CLAIM_TYPE: Dict[str, str] = {
    S.CLAIM_DATE: S.DATE_MISMATCH,
    S.CLAIM_RELEASE: S.DATE_MISMATCH,
    S.CLAIM_NUMBER: S.NUMBER_MISMATCH,
    S.CLAIM_IDENTITY: S.IDENTITY_MISMATCH,
    S.CLAIM_ATTRIBUTION: S.ATTRIBUTION_MISMATCH,
    S.CLAIM_STATUS: S.STATUS_MISMATCH,
    S.CLAIM_AVAILABILITY: S.STATUS_MISMATCH,
    S.CLAIM_QUOTE: S.QUOTE_MISMATCH,
}


def _values_incompatible(claim_type: str, left: str, right: str) -> bool:
    """Whether two normalized values genuinely disagree."""
    if left == right:
        return False
    if claim_type in (S.CLAIM_DATE, S.CLAIM_RELEASE):
        return dates_conflict(left, right)
    if claim_type == S.CLAIM_NUMBER:
        return numbers_conflict(left, right)
    # For identity, attribution, status and quotes, different normalized values
    # for the same predicate are already a disagreement.
    return True


def detect_conflicts(
    claims: Sequence[FactualClaim],
    links: Sequence[ClaimEvidenceLink],
    evidence: Sequence[FactualEvidence],
) -> List[FactualConflict]:
    """Find every disagreement the evidence actually supports."""
    evidence_by_id = {e.evidence_id: e for e in evidence}
    conflicts: List[FactualConflict] = []

    conflicts.extend(_conflicts_between_claims(claims))
    conflicts.extend(_conflicts_from_contradicting_evidence(claims, links, evidence_by_id))

    # Deduplicate by identity: the same disagreement found twice is one.
    unique: Dict[str, FactualConflict] = {}
    for conflict in conflicts:
        unique.setdefault(conflict.conflict_id, conflict)

    for conflict in unique.values():
        logger.info(
            "[FACTUAL_CONFLICT_DETECTED] type=%s subject=%s values=%s critical=%s",
            conflict.conflict_type, conflict.subject or "-",
            len(conflict.values), conflict.is_critical,
        )
    return sorted(unique.values(), key=lambda c: c.conflict_id)


def _conflicts_between_claims(claims: Sequence[FactualClaim]) -> List[FactualConflict]:
    """Two claims about the same subject+predicate with incompatible values."""
    grouped: Dict[Tuple[str, str, str], List[FactualClaim]] = {}
    for claim in claims:
        if not claim.is_material or not claim.normalized_value:
            continue
        if not claim.normalized_subject or not claim.predicate:
            # Without a subject we cannot tell "two facts" from "the same fact
            # about different things"; declaring a conflict would be a guess.
            continue
        key = (claim.claim_type, claim.normalized_subject, claim.predicate)
        grouped.setdefault(key, []).append(claim)

    conflicts: List[FactualConflict] = []
    for (claim_type, subject, predicate), group in grouped.items():
        if len(group) < 2:
            continue
        for index, left in enumerate(group):
            for right in group[index + 1:]:
                if not _values_incompatible(
                    claim_type, left.normalized_value or "", right.normalized_value or ""
                ):
                    continue
                conflicts.append(
                    FactualConflict(
                        conflict_type=_CONFLICT_BY_CLAIM_TYPE.get(claim_type, S.VALUE_MISMATCH),
                        claim_ids=[left.claim_id, right.claim_id],
                        subject=subject,
                        values=[left.normalized_value or "", right.normalized_value or ""],
                        description=(
                            f"Valores incompativeis para '{predicate}' de '{subject}': "
                            f"{left.normalized_value} vs {right.normalized_value}."
                        ),
                    )
                )
    return conflicts


def _conflicts_from_contradicting_evidence(
    claims: Sequence[FactualClaim],
    links: Sequence[ClaimEvidenceLink],
    evidence_by_id: Dict[str, FactualEvidence],
) -> List[FactualConflict]:
    """A claim that at least one received source directly contradicts."""
    claims_by_id = {c.claim_id: c for c in claims}
    contradicted: Dict[str, List[str]] = {}
    for link in links:
        if link.relation == S.CONTRADICTS:
            contradicted.setdefault(link.claim_id, []).append(link.evidence_id)

    conflicts: List[FactualConflict] = []
    for claim_id, evidence_ids in contradicted.items():
        claim = claims_by_id.get(claim_id)
        if claim is None or not claim.is_material:
            continue
        if not claim.normalized_subject:
            # Same guard as `_conflicts_between_claims`: without a subject a
            # conflict cannot be told apart from a mislabeled fact, and the log
            # would show a bare "-" instead of something a human can act on.
            continue
        values = [claim.normalized_value or claim.display_text]
        for evidence_id in evidence_ids:
            item = evidence_by_id.get(evidence_id)
            if item:
                values.append(_evidence_value(claim, item))
        conflicts.append(
            FactualConflict(
                conflict_type=_CONFLICT_BY_CLAIM_TYPE.get(
                    claim.claim_type, S.SOURCE_DISAGREEMENT
                ),
                claim_ids=[claim_id],
                evidence_ids=evidence_ids,
                subject=claim.normalized_subject,
                values=[v for v in values if v],
                description=(
                    f"{len(evidence_ids)} fonte(s) contradizem a afirmacao: "
                    f"{claim.display_text}"
                ),
            )
        )
    return conflicts


def _evidence_value(claim: FactualClaim, evidence: FactualEvidence) -> str:
    """The competing value a source states, when we can normalize one."""
    from .normalization import normalize_date, normalize_number

    if claim.claim_type in (S.CLAIM_DATE, S.CLAIM_RELEASE):
        return normalize_date(evidence.excerpt) or ""
    if claim.claim_type == S.CLAIM_NUMBER:
        return normalize_number(evidence.excerpt) or ""
    return evidence.normalized_excerpt[:80]


def has_critical_conflict(conflicts: Sequence[FactualConflict]) -> bool:
    return any(conflict.is_critical for conflict in conflicts)


def conflicts_for_claim(
    conflicts: Sequence[FactualConflict], claim_id: str
) -> List[FactualConflict]:
    return [c for c in conflicts if claim_id in c.claim_ids]


def describe_conflict(conflict: FactualConflict) -> str:
    """One-line rendering for the CLI. Values are shown side by side, never
    reconciled."""
    values = " | ".join(conflict.values) or "-"
    return (
        f"{conflict.conflict_type} subject={conflict.subject or '-'} "
        f"values=[{values}] status={conflict.resolution_status}"
    )


__all__ = [
    "conflicts_for_claim",
    "describe_conflict",
    "detect_conflicts",
    "has_critical_conflict",
]
