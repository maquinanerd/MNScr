"""Errors raised by the factual layer."""

from __future__ import annotations

from typing import List, Optional


class FactualError(RuntimeError):
    """Base class for every factual failure."""


class InvalidClaimError(FactualError):
    """A claim cannot be built as described."""


class InvalidEvidenceError(FactualError):
    """An evidence excerpt is unusable — no URL, empty text, whole article."""


class InvalidLinkError(FactualError):
    """A claim–evidence link references something that does not exist."""

    def __init__(self, message: str, *, claim_id: Optional[str] = None,
                 evidence_id: Optional[str] = None):
        super().__init__(message)
        self.claim_id = claim_id
        self.evidence_id = evidence_id


class ClaimExtractionError(FactualError):
    """Claim extraction produced nothing usable."""


class InvalidModelResponseError(ClaimExtractionError):
    """The model returned something that is not a valid claim payload.

    Never fall back to interpreting free text: an unparseable response means we
    do not know what the model claimed, and guessing would fabricate facts.
    """

    def __init__(self, reason: str, *, issues: Optional[List[str]] = None):
        super().__init__(f"Resposta de extracao de claims invalida: {reason}")
        self.reason = reason
        self.issues: List[str] = list(issues or [])


class AssessmentNotFoundError(FactualError):
    """No stored factual assessment for the requested draft."""

    def __init__(self, draft_id: str):
        super().__init__(f"Nenhuma avaliacao factual registrada para o draft '{draft_id}'.")
        self.draft_id = draft_id


class InvalidFactualConfigError(FactualError):
    """A factual setting is out of range or unknown. Fails at startup."""


__all__ = [
    "AssessmentNotFoundError",
    "ClaimExtractionError",
    "FactualError",
    "InvalidClaimError",
    "InvalidEvidenceError",
    "InvalidFactualConfigError",
    "InvalidLinkError",
    "InvalidModelResponseError",
]
