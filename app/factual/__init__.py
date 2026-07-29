"""Factual layer: claims, evidence, conflicts and coverage.

MNScr used to keep *references* to sources. This package keeps the **structure**
that lets a reviewer ask, and get an answer to:

* what does this draft assert, and where does each assertion appear?
* which received source sustains it, and with what excerpt?
* which source contradicts it?
* what is asserted with no support at all?
* how much of the draft is actually covered?

Three meanings are load-bearing and easy to get wrong:

* ``UNSUPPORTED`` means *no source we received sustains this*. It is **not** a
  claim of falsehood — absence of evidence is not evidence of absence.
* ``MENTIONS`` means a source discusses the same subject without confirming the
  assertion. It never counts as support.
* ``UNRESOLVED`` is the only resolution status a conflict can have. MNScr never
  picks a winner, never counts votes, and never uses source reputation as a
  tiebreaker. Contradictions are preserved for a human.
"""

from .conflicts import (
    conflicts_for_claim,
    describe_conflict,
    detect_conflicts,
    has_critical_conflict,
)
from .coverage import (
    compute_coverage,
    compute_editorial_origin_key,
    count_editorial_origins,
    count_independent_origins_supporting,
    unsupported_claims_in_critical_locations,
)
from .errors import (
    AssessmentNotFoundError,
    ClaimExtractionError,
    FactualError,
    InvalidClaimError,
    InvalidEvidenceError,
    InvalidFactualConfigError,
    InvalidLinkError,
    InvalidModelResponseError,
)
from .extraction import (
    extract_deterministic_claims,
    is_material_text,
    merge_claims,
    parse_claim_response,
    segment_draft,
)
from .hashing import stable_hash
from .matching import (
    apply_statuses,
    classify_relation,
    compute_claim_status,
    link_claims_to_evidence,
    validate_links,
)
from .models import (
    ClaimEvidenceLink,
    FactualAssessment,
    FactualClaim,
    FactualConflict,
    FactualCoverage,
    FactualEvidence,
)
from .normalization import (
    dates_conflict,
    normalize_date,
    normalize_for_comparison,
    normalize_number,
    normalize_text,
    numbers_conflict,
    token_overlap,
)
from .states import (
    ASSESSMENT_VERSION_V1,
    CONFLICTING,
    CONTRADICTS,
    MENTIONS,
    MODE_DETERMINISTIC,
    MODE_HYBRID,
    PARTIALLY_SUPPORTED,
    PARTIALLY_SUPPORTS,
    PRIMARY,
    SECONDARY,
    STRENGTH_UNKNOWN,
    SUPPORTED,
    SUPPORTS,
    UNRESOLVED,
    UNSUPPORTED,
    UNVERIFIED,
    WEAK,
)

__all__ = [
    "ASSESSMENT_VERSION_V1",
    "AssessmentNotFoundError",
    "CONFLICTING",
    "CONTRADICTS",
    "ClaimEvidenceLink",
    "ClaimExtractionError",
    "FactualAssessment",
    "FactualClaim",
    "FactualConflict",
    "FactualCoverage",
    "FactualError",
    "FactualEvidence",
    "InvalidClaimError",
    "InvalidEvidenceError",
    "InvalidFactualConfigError",
    "InvalidLinkError",
    "InvalidModelResponseError",
    "MENTIONS",
    "MODE_DETERMINISTIC",
    "MODE_HYBRID",
    "PARTIALLY_SUPPORTED",
    "PARTIALLY_SUPPORTS",
    "PRIMARY",
    "SECONDARY",
    "STRENGTH_UNKNOWN",
    "SUPPORTED",
    "SUPPORTS",
    "UNRESOLVED",
    "UNSUPPORTED",
    "UNVERIFIED",
    "WEAK",
    "apply_statuses",
    "classify_relation",
    "compute_claim_status",
    "compute_coverage",
    "compute_editorial_origin_key",
    "conflicts_for_claim",
    "count_editorial_origins",
    "count_independent_origins_supporting",
    "dates_conflict",
    "describe_conflict",
    "detect_conflicts",
    "extract_deterministic_claims",
    "has_critical_conflict",
    "is_material_text",
    "link_claims_to_evidence",
    "merge_claims",
    "normalize_date",
    "normalize_for_comparison",
    "normalize_number",
    "normalize_text",
    "numbers_conflict",
    "parse_claim_response",
    "segment_draft",
    "stable_hash",
    "token_overlap",
    "unsupported_claims_in_critical_locations",
    "validate_links",
]
