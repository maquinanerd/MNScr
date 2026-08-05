"""Typed domain for facts, evidence, conflicts and coverage.

The four objects answer four different questions:

* ``FactualClaim`` — what the draft asserts, and where.
* ``FactualEvidence`` — what a received source actually says, verbatim but short.
* ``ClaimEvidenceLink`` — how a source relates to an assertion, with a reason.
* ``FactualConflict`` — where sources disagree. Recorded, never resolved.

``FactualCoverage`` then summarises how much of the draft is actually sustained.
None of it is a public score: it is an instrument for a human reviewer.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dc_field
from datetime import datetime, timezone
from typing import Any, Dict, Final, List, Optional

from . import states as S
from .errors import InvalidClaimError, InvalidEvidenceError
from .hashing import (
    build_assessment_id,
    build_claim_id,
    build_conflict_id,
    build_evidence_id,
    build_link_id,
    stable_hash,
)
from .normalization import (
    normalize_for_comparison,
    normalize_text,
    truncate_at_safe_boundary,
)

#: Hard ceiling on a stored source sentence. Evidence is a pointer into a
#: source, never a copy of it.
MAX_SOURCE_SENTENCE_CHARS: Final[int] = 400
DEFAULT_MAX_EVIDENCE_EXCERPT_CHARS: Final[int] = 500
MAX_RATIONALE_CHARS: Final[int] = 240


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean(value: Any) -> Optional[str]:
    text = normalize_text(value)
    return text or None


@dataclass
class FactualClaim:
    """One assertion the draft makes."""

    display_text: str
    claim_type: str = S.CLAIM_OTHER
    normalized_subject: Optional[str] = None
    predicate: Optional[str] = None
    normalized_value: Optional[str] = None
    source_sentence: Optional[str] = None
    draft_locations: List[str] = dc_field(default_factory=list)
    status: str = S.UNVERIFIED
    confidence: Optional[float] = None
    is_material: bool = True
    requires_review: bool = False
    warnings: List[str] = dc_field(default_factory=list)
    claim_id: str = ""
    claim_hash: str = ""
    draft_id: str = ""

    def __post_init__(self) -> None:
        self.display_text = normalize_text(self.display_text)
        if not self.display_text:
            raise InvalidClaimError("FactualClaim.display_text nao pode ser vazio")
        self.claim_type = S.validate_claim_type(self.claim_type)
        self.status = S.validate_claim_status(self.status)
        self.normalized_subject = _clean(self.normalized_subject)
        self.predicate = _clean(self.predicate)
        self.normalized_value = _clean(self.normalized_value)

        if self.source_sentence:
            sentence, truncated = truncate_at_safe_boundary(
                self.source_sentence, MAX_SOURCE_SENTENCE_CHARS
            )
            self.source_sentence = sentence
            if truncated and "SOURCE_SENTENCE_TRUNCATED" not in self.warnings:
                self.warnings.append("SOURCE_SENTENCE_TRUNCATED")

        if self.confidence is not None:
            value = float(self.confidence)
            if not (0.0 <= value <= 1.0):
                raise InvalidClaimError(
                    f"FactualClaim.confidence fora de 0..1: {self.confidence}"
                )
            self.confidence = value

        self.draft_locations = [
            loc for loc in (str(item).strip() for item in self.draft_locations) if loc
        ]
        self.warnings = [w for w in (str(x).strip() for x in self.warnings) if w]

        if self.claim_type == S.CLAIM_OTHER and "CLAIM_TYPE_UNCERTAIN" not in self.warnings:
            # Forcing a type we are not sure of would be a fabricated fact about
            # the fact; recording the uncertainty is the honest option.
            self.warnings.append("CLAIM_TYPE_UNCERTAIN")

        if self.status in S.STATUSES_REQUIRING_REVIEW:
            self.requires_review = True

        if not self.claim_id:
            self.claim_id = build_claim_id(
                draft_id=self.draft_id,
                claim_type=self.claim_type,
                normalized_subject=self.normalized_subject or "",
                predicate=self.predicate or "",
                normalized_value=self.normalized_value or "",
                display_text=normalize_for_comparison(self.display_text),
            )
        if not self.claim_hash:
            self.claim_hash = stable_hash(self.to_hashable_dict())

    @property
    def is_in_critical_location(self) -> bool:
        return any(S.is_critical_location(loc) for loc in self.draft_locations)

    def to_hashable_dict(self) -> Dict[str, Any]:
        """Identity of the assertion. Status is excluded — it is a *verdict*
        about the claim, computed later, not part of what the claim says."""
        return {
            "claim_type": self.claim_type,
            "normalized_subject": self.normalized_subject,
            "predicate": self.predicate,
            "normalized_value": self.normalized_value,
            "display_text": normalize_for_comparison(self.display_text),
            "draft_locations": sorted(self.draft_locations),
            "is_material": bool(self.is_material),
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "claim_type": self.claim_type,
            "normalized_subject": self.normalized_subject,
            "predicate": self.predicate,
            "normalized_value": self.normalized_value,
            "display_text": self.display_text,
            "source_sentence": self.source_sentence,
            "draft_locations": list(self.draft_locations),
            "status": self.status,
            "confidence": self.confidence,
            "is_material": bool(self.is_material),
            "requires_review": bool(self.requires_review),
            "warnings": list(self.warnings),
            "claim_hash": self.claim_hash,
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "FactualClaim":
        return cls(
            display_text=payload.get("display_text", ""),
            claim_type=payload.get("claim_type", S.CLAIM_OTHER),
            normalized_subject=payload.get("normalized_subject"),
            predicate=payload.get("predicate"),
            normalized_value=payload.get("normalized_value"),
            source_sentence=payload.get("source_sentence"),
            draft_locations=list(payload.get("draft_locations") or []),
            status=payload.get("status", S.UNVERIFIED),
            confidence=payload.get("confidence"),
            is_material=bool(payload.get("is_material", True)),
            requires_review=bool(payload.get("requires_review", False)),
            warnings=list(payload.get("warnings") or []),
            claim_id=payload.get("claim_id", ""),
            claim_hash=payload.get("claim_hash", ""),
        )


@dataclass
class FactualEvidence:
    """A short passage from a source we actually received."""

    source_url: str
    excerpt: str
    source_id: Optional[str] = None
    source_name: Optional[str] = None
    source_domain: Optional[str] = None
    source_title: Optional[str] = None
    normalized_excerpt: str = ""
    published_at: Optional[str] = None
    relation_target: Optional[str] = None
    strength: str = S.STRENGTH_UNKNOWN
    editorial_origin_key: Optional[str] = None
    evidence_id: str = ""
    evidence_hash: str = ""
    warnings: List[str] = dc_field(default_factory=list)
    max_excerpt_chars: int = DEFAULT_MAX_EVIDENCE_EXCERPT_CHARS

    def __post_init__(self) -> None:
        self.source_url = normalize_text(self.source_url)
        if not self.source_url:
            raise InvalidEvidenceError("FactualEvidence.source_url e obrigatoria")

        excerpt, truncated = truncate_at_safe_boundary(self.excerpt, self.max_excerpt_chars)
        self.excerpt = excerpt
        if not self.excerpt:
            raise InvalidEvidenceError("FactualEvidence.excerpt nao pode ser vazio")
        if truncated and "EVIDENCE_EXCERPT_TRUNCATED" not in self.warnings:
            self.warnings.append("EVIDENCE_EXCERPT_TRUNCATED")

        self.strength = S.validate_strength(self.strength)
        self.source_id = _clean(self.source_id)
        self.source_name = _clean(self.source_name)
        self.source_title = _clean(self.source_title)
        self.published_at = _clean(self.published_at)
        self.relation_target = _clean(self.relation_target)

        if not self.source_domain:
            self.source_domain = _domain_of(self.source_url)
        else:
            self.source_domain = normalize_text(self.source_domain).lower() or None

        if not self.normalized_excerpt:
            self.normalized_excerpt = normalize_for_comparison(self.excerpt)

        if not self.editorial_origin_key:
            self.editorial_origin_key = self.source_domain or self.source_url

        if not self.evidence_id:
            self.evidence_id = build_evidence_id(
                source_url=self.source_url, normalized_excerpt=self.normalized_excerpt
            )
        if not self.evidence_hash:
            self.evidence_hash = stable_hash(self.to_hashable_dict())

    def to_hashable_dict(self) -> Dict[str, Any]:
        return {
            "source_url": self.source_url,
            "source_domain": self.source_domain,
            "normalized_excerpt": self.normalized_excerpt,
            "published_at": self.published_at,
            "strength": self.strength,
            "editorial_origin_key": self.editorial_origin_key,
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "source_id": self.source_id,
            "source_url": self.source_url,
            "source_name": self.source_name,
            "source_domain": self.source_domain,
            "source_title": self.source_title,
            "excerpt": self.excerpt,
            "normalized_excerpt": self.normalized_excerpt,
            "published_at": self.published_at,
            "relation_target": self.relation_target,
            "strength": self.strength,
            "editorial_origin_key": self.editorial_origin_key,
            "evidence_hash": self.evidence_hash,
            "warnings": list(self.warnings),
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "FactualEvidence":
        return cls(
            source_url=payload.get("source_url", ""),
            excerpt=payload.get("excerpt", ""),
            source_id=payload.get("source_id"),
            source_name=payload.get("source_name"),
            source_domain=payload.get("source_domain"),
            source_title=payload.get("source_title"),
            normalized_excerpt=payload.get("normalized_excerpt", ""),
            published_at=payload.get("published_at"),
            relation_target=payload.get("relation_target"),
            strength=payload.get("strength", S.STRENGTH_UNKNOWN),
            editorial_origin_key=payload.get("editorial_origin_key"),
            evidence_id=payload.get("evidence_id", ""),
            evidence_hash=payload.get("evidence_hash", ""),
            warnings=list(payload.get("warnings") or []),
        )

    def to_evidence_record(self):
        """Back-compat view as the MS-1 ``EvidenceRecord``.

        The older model is still what ``EditorialDraft.evidence`` carries, so
        the factual layer can feed it without breaking anything downstream.
        """
        from app.editorial.models import EvidenceRecord
        from app.editorial.states import EVIDENCE_OBSERVED

        return EvidenceRecord(
            evidence_id=self.evidence_id,
            source_url=self.source_url,
            source_name=self.source_name,
            source_excerpt=self.excerpt,
            claim=self.relation_target,
            status=EVIDENCE_OBSERVED,
        )


def _domain_of(url: str) -> Optional[str]:
    from urllib.parse import urlparse

    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return None
    if not host:
        return None
    return host[4:] if host.startswith("www.") else host


@dataclass
class ClaimEvidenceLink:
    """How one source relates to one assertion."""

    claim_id: str
    evidence_id: str
    relation: str
    strength: str = S.STRENGTH_UNKNOWN
    similarity: Optional[float] = None
    rationale: Optional[str] = None
    link_id: str = ""

    def __post_init__(self) -> None:
        self.relation = S.validate_relation(self.relation)
        self.strength = S.validate_strength(self.strength)
        if self.similarity is not None:
            self.similarity = round(float(self.similarity), 6)
        if self.rationale:
            # Objective justification only — never the model's chain of thought.
            self.rationale, _ = truncate_at_safe_boundary(self.rationale, MAX_RATIONALE_CHARS)
        if not self.link_id:
            self.link_id = build_link_id(
                claim_id=self.claim_id, evidence_id=self.evidence_id, relation=self.relation
            )

    @property
    def is_supporting(self) -> bool:
        return self.relation in S.SUPPORTING_RELATIONS

    def to_hashable_dict(self) -> Dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "evidence_id": self.evidence_id,
            "relation": self.relation,
            "strength": self.strength,
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "link_id": self.link_id,
            "claim_id": self.claim_id,
            "evidence_id": self.evidence_id,
            "relation": self.relation,
            "strength": self.strength,
            "similarity": self.similarity,
            "rationale": self.rationale,
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "ClaimEvidenceLink":
        return cls(
            claim_id=payload.get("claim_id", ""),
            evidence_id=payload.get("evidence_id", ""),
            relation=payload.get("relation", S.MENTIONS),
            strength=payload.get("strength", S.STRENGTH_UNKNOWN),
            similarity=payload.get("similarity"),
            rationale=payload.get("rationale"),
            link_id=payload.get("link_id", ""),
        )


@dataclass
class FactualConflict:
    """A disagreement between sources. Recorded, never resolved."""

    conflict_type: str
    claim_ids: List[str] = dc_field(default_factory=list)
    evidence_ids: List[str] = dc_field(default_factory=list)
    subject: Optional[str] = None
    values: List[str] = dc_field(default_factory=list)
    description: str = ""
    requires_review: bool = True
    resolution_status: str = S.UNRESOLVED
    conflict_id: str = ""
    conflict_hash: str = ""

    def __post_init__(self) -> None:
        self.conflict_type = S.validate_conflict_type(self.conflict_type)
        self.resolution_status = S.validate_resolution_status(self.resolution_status)
        self.subject = _clean(self.subject)
        self.values = [v for v in (normalize_text(x) for x in self.values) if v]
        self.claim_ids = sorted({c for c in self.claim_ids if c})
        self.evidence_ids = sorted({e for e in self.evidence_ids if e})
        self.description = normalize_text(self.description)
        # A conflict is by definition a human decision; nothing turns this off.
        self.requires_review = True
        if not self.conflict_id:
            self.conflict_id = build_conflict_id(
                conflict_type=self.conflict_type,
                subject=self.subject or "",
                values=self.values,
                claim_ids=self.claim_ids,
            )
        if not self.conflict_hash:
            self.conflict_hash = stable_hash(self.to_hashable_dict())

    @property
    def is_critical(self) -> bool:
        return self.conflict_type in S.CRITICAL_CONFLICT_TYPES

    def to_hashable_dict(self) -> Dict[str, Any]:
        return {
            "conflict_type": self.conflict_type,
            "subject": self.subject,
            "values": sorted(self.values),
            "claim_ids": sorted(self.claim_ids),
            "evidence_ids": sorted(self.evidence_ids),
            "resolution_status": self.resolution_status,
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "conflict_id": self.conflict_id,
            "conflict_type": self.conflict_type,
            "claim_ids": list(self.claim_ids),
            "evidence_ids": list(self.evidence_ids),
            "subject": self.subject,
            "values": list(self.values),
            "description": self.description,
            "requires_review": True,
            "resolution_status": self.resolution_status,
            "conflict_hash": self.conflict_hash,
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "FactualConflict":
        return cls(
            conflict_type=payload.get("conflict_type", S.VALUE_MISMATCH),
            claim_ids=list(payload.get("claim_ids") or []),
            evidence_ids=list(payload.get("evidence_ids") or []),
            subject=payload.get("subject"),
            values=list(payload.get("values") or []),
            description=payload.get("description", ""),
            resolution_status=payload.get("resolution_status", S.UNRESOLVED),
            conflict_id=payload.get("conflict_id", ""),
            conflict_hash=payload.get("conflict_hash", ""),
        )


@dataclass
class FactualCoverage:
    """How much of the draft's material assertions is actually sustained.

    Not a public score and not an editorial grade: an instrument that tells a
    reviewer where to look first.
    """

    total_material_claims: int = 0
    supported_material_claims: int = 0
    partially_supported_material_claims: int = 0
    unsupported_material_claims: int = 0
    conflicting_material_claims: int = 0
    unverified_material_claims: int = 0
    coverage_ratio: float = 0.0
    source_diversity: int = 0
    primary_evidence_count: int = 0
    review_required: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_material_claims": self.total_material_claims,
            "supported_material_claims": self.supported_material_claims,
            "partially_supported_material_claims": self.partially_supported_material_claims,
            "unsupported_material_claims": self.unsupported_material_claims,
            "conflicting_material_claims": self.conflicting_material_claims,
            "unverified_material_claims": self.unverified_material_claims,
            "coverage_ratio": self.coverage_ratio,
            "source_diversity": self.source_diversity,
            "primary_evidence_count": self.primary_evidence_count,
            "review_required": bool(self.review_required),
        }

    def to_hashable_dict(self) -> Dict[str, Any]:
        return self.to_dict()

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "FactualCoverage":
        return cls(**{k: v for k, v in payload.items() if k in cls.__dataclass_fields__})


@dataclass
class FactualAssessment:
    """The full factual picture for one draft."""

    draft_id: str
    claims: List[FactualClaim] = dc_field(default_factory=list)
    evidence: List[FactualEvidence] = dc_field(default_factory=list)
    links: List[ClaimEvidenceLink] = dc_field(default_factory=list)
    conflicts: List[FactualConflict] = dc_field(default_factory=list)
    coverage: FactualCoverage = dc_field(default_factory=FactualCoverage)
    event_key: Optional[str] = None
    revision: int = 1
    contract_version: str = ""
    version: str = S.ASSESSMENT_VERSION_V1
    mode: str = S.MODE_HYBRID
    #: Modo PEDIDO e modo que de fato rodou. Eles divergem quando `hybrid` foi
    #: solicitado e a camada semantica nao entregou claim nenhum — por falta de
    #: resposta, por resposta irrecuperavel ou por adapter ausente. Sem essa
    #: distincao o laudo persistido dizia `mode: hybrid` e quem o lesse depois
    #: (a reavaliacao do gate, um humano, um relatorio) concluiria que a camada
    #: semantica rodou e nao achou nada — quando ela simplesmente nao rodou.
    #:
    #: Ficam FORA de `to_hashable_dict` de proposito, seguindo `warnings`: sao
    #: metadado sobre COMO a avaliacao foi produzida, nao sobre o que ela apurou,
    #: e inclui-los mudaria o hash de todo laudo ja persistido.
    requested_mode: str = ""
    effective_mode: str = ""
    warnings: List[str] = dc_field(default_factory=list)
    blocking_errors: List[str] = dc_field(default_factory=list)
    assessment_id: str = ""
    assessment_hash: str = ""
    created_at: str = dc_field(default_factory=_utc_now_iso)

    def __post_init__(self) -> None:
        self.draft_id = normalize_text(self.draft_id)
        if not self.draft_id:
            raise InvalidClaimError("FactualAssessment.draft_id e obrigatorio")
        self.revision = int(self.revision or 1)
        self.event_key = _clean(self.event_key)
        self.mode = S.validate_mode(self.mode)
        # Um laudo antigo, ou construido a mao, nao declara os dois modos: nesse
        # caso `mode` responde pelos dois, que e o que ele sempre significou.
        self.requested_mode = S.validate_mode(self.requested_mode or self.mode)
        self.effective_mode = S.validate_mode(self.effective_mode or self.mode)
        self.warnings = [w for w in (str(x).strip() for x in self.warnings) if w]
        self.blocking_errors = [e for e in (str(x).strip() for x in self.blocking_errors) if e]
        if not self.assessment_hash:
            self.assessment_hash = stable_hash(self.to_hashable_dict())
        if not self.assessment_id:
            self.assessment_id = build_assessment_id(
                draft_id=self.draft_id, version=self.version,
                assessment_hash=self.assessment_hash,
            )

    # --- Views -------------------------------------------------------------

    @property
    def material_claims(self) -> List[FactualClaim]:
        return [c for c in self.claims if c.is_material]

    def claims_with_status(self, status: str) -> List[FactualClaim]:
        return [c for c in self.material_claims if c.status == status]

    @property
    def unsupported_in_critical_locations(self) -> List[FactualClaim]:
        return [
            c for c in self.claims_with_status(S.UNSUPPORTED) if c.is_in_critical_location
        ]

    @property
    def critical_conflicts(self) -> List[FactualConflict]:
        return [c for c in self.conflicts if c.is_critical]

    @property
    def requires_review(self) -> bool:
        return (
            self.coverage.review_required
            or bool(self.conflicts)
            or any(c.requires_review for c in self.material_claims)
        )

    def links_for_claim(self, claim_id: str) -> List[ClaimEvidenceLink]:
        return [link for link in self.links if link.claim_id == claim_id]

    def evidence_by_id(self, evidence_id: str) -> Optional[FactualEvidence]:
        for item in self.evidence:
            if item.evidence_id == evidence_id:
                return item
        return None

    # --- Identity ----------------------------------------------------------

    def to_hashable_dict(self) -> Dict[str, Any]:
        """``created_at`` is excluded: the same facts evaluated tomorrow are
        the same facts."""
        return {
            "draft_id": self.draft_id,
            "event_key": self.event_key,
            "revision": int(self.revision),
            "contract_version": self.contract_version,
            "version": self.version,
            "mode": self.mode,
            "claims": [c.to_hashable_dict() for c in sorted(self.claims, key=lambda x: x.claim_id)],
            "evidence": [
                e.to_hashable_dict() for e in sorted(self.evidence, key=lambda x: x.evidence_id)
            ],
            "links": [
                link.to_hashable_dict() for link in sorted(self.links, key=lambda x: x.link_id)
            ],
            "conflicts": [
                c.to_hashable_dict() for c in sorted(self.conflicts, key=lambda x: x.conflict_id)
            ],
            "coverage": self.coverage.to_hashable_dict(),
            "claim_statuses": {
                c.claim_id: c.status for c in sorted(self.claims, key=lambda x: x.claim_id)
            },
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "assessment_id": self.assessment_id,
            "draft_id": self.draft_id,
            "event_key": self.event_key,
            "revision": int(self.revision),
            "contract_version": self.contract_version,
            "version": self.version,
            "mode": self.mode,
            "requested_mode": self.requested_mode,
            "effective_mode": self.effective_mode,
            "claims": [c.to_dict() for c in self.claims],
            "evidence": [e.to_dict() for e in self.evidence],
            "links": [link.to_dict() for link in self.links],
            "conflicts": [c.to_dict() for c in self.conflicts],
            "coverage": self.coverage.to_dict(),
            "warnings": list(self.warnings),
            "blocking_errors": list(self.blocking_errors),
            "assessment_hash": self.assessment_hash,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "FactualAssessment":
        return cls(
            draft_id=payload.get("draft_id", ""),
            claims=[FactualClaim.from_dict(c) for c in (payload.get("claims") or [])],
            evidence=[FactualEvidence.from_dict(e) for e in (payload.get("evidence") or [])],
            links=[ClaimEvidenceLink.from_dict(x) for x in (payload.get("links") or [])],
            conflicts=[FactualConflict.from_dict(c) for c in (payload.get("conflicts") or [])],
            coverage=FactualCoverage.from_dict(payload.get("coverage") or {}),
            event_key=payload.get("event_key"),
            revision=payload.get("revision", 1),
            contract_version=payload.get("contract_version", ""),
            version=payload.get("version", S.ASSESSMENT_VERSION_V1),
            mode=payload.get("mode", S.MODE_HYBRID),
            requested_mode=payload.get("requested_mode", ""),
            effective_mode=payload.get("effective_mode", ""),
            warnings=list(payload.get("warnings") or []),
            blocking_errors=list(payload.get("blocking_errors") or []),
            assessment_id=payload.get("assessment_id", ""),
            assessment_hash=payload.get("assessment_hash", ""),
            created_at=payload.get("created_at") or _utc_now_iso(),
        )


__all__ = [
    "DEFAULT_MAX_EVIDENCE_EXCERPT_CHARS",
    "MAX_RATIONALE_CHARS",
    "MAX_SOURCE_SENTENCE_CHARS",
    "ClaimEvidenceLink",
    "FactualAssessment",
    "FactualClaim",
    "FactualConflict",
    "FactualCoverage",
    "FactualEvidence",
]
