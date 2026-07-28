"""Editorial domain for MNScr.

MNScr produces structured editorial drafts. It does not publish, does not
approve and does not own any public surface.
"""

from .models import (
    INPUT_CONTRACT_VERSION,
    OUTPUT_CONTRACT_VERSION,
    PIPELINE_VERSION,
    DraftContent,
    DraftProvenance,
    EditorialDraft,
    EvidenceRecord,
    MediaCandidate,
    SourceReference,
    build_draft_id,
    validate_draft,
)
from .serialization import canonical_json, dumps_pretty, stable_hash, to_plain
from .states import (
    ALLOWED_DRAFT_STATES,
    DRAFT_FAILED,
    DRAFT_GENERATED,
    FORBIDDEN_DRAFT_STATES,
    ForbiddenStateError,
    validate_draft_status,
)

__all__ = [
    "ALLOWED_DRAFT_STATES",
    "DRAFT_FAILED",
    "DRAFT_GENERATED",
    "FORBIDDEN_DRAFT_STATES",
    "ForbiddenStateError",
    "INPUT_CONTRACT_VERSION",
    "OUTPUT_CONTRACT_VERSION",
    "PIPELINE_VERSION",
    "DraftContent",
    "DraftProvenance",
    "EditorialDraft",
    "EvidenceRecord",
    "MediaCandidate",
    "SourceReference",
    "build_draft_id",
    "canonical_json",
    "dumps_pretty",
    "stable_hash",
    "to_plain",
    "validate_draft",
    "validate_draft_status",
]
