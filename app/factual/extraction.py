"""Claim extraction: what does this draft actually assert, and where?

Two layers, in this order:

1. **Deterministic** — dates, numbers, percentages, currency, quotes, status
   verbs. Reproducible, cheap, and never hallucinated.
2. **Structured AI** — semantic claims the patterns cannot see. The model must
   return strict JSON, every field is validated, and any claim whose text is
   not actually present in the draft is discarded.

The model is never trusted to be right; it is only allowed to *point at* text
that already exists. Free-form output is rejected outright rather than parsed
leniently, because a half-understood response would fabricate facts.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, Final, List, Optional, Sequence, Tuple

from . import states as S
from .errors import InvalidModelResponseError
from .models import FactualClaim
from .normalization import (
    normalize_date,
    normalize_for_comparison,
    normalize_number,
    normalize_text,
)

logger = logging.getLogger(__name__)

#: Text that is navigation, promotion or opinion — never a material claim.
_NON_MATERIAL_PATTERNS: Final[Tuple[re.Pattern[str], ...]] = (
    re.compile(r"\b(clique|click)\s+(aqui|here)\b", re.IGNORECASE),
    re.compile(r"\b(leia|veja|confira|saiba|assista)\s+(mais|tamb[eé]m|agora)\b", re.IGNORECASE),
    re.compile(r"\b(read|see|watch|check)\s+(more|also|now)\b", re.IGNORECASE),
    re.compile(r"\b(inscreva|subscribe|siga|follow us|newsletter)\b", re.IGNORECASE),
    re.compile(r"\b(n[aã]o perca|don'?t miss|stay tuned)\b", re.IGNORECASE),
    re.compile(r"\b(vale a pena|imperd[ií]vel|melhor de todos|obra[- ]prima)\b", re.IGNORECASE),
    re.compile(r"\b(na minha opini[aã]o|acredito que|acho que|in my opinion)\b", re.IGNORECASE),
    re.compile(r"\b(recomendamos|recomendo|we recommend)\b", re.IGNORECASE),
)

#: Deterministic claim shapes. Each maps to a claim type and a predicate.
_DATE_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\b(\d{1,2}\s*de\s+[a-zçã]+\s*(?:de\s+)?\d{4}"
    r"|\d{4}-\d{2}-\d{2}"
    r"|\d{1,2}/\d{1,2}/\d{4}"
    r"|[A-Z][a-z]+\s+\d{1,2},\s*\d{4}"
    r"|[a-zçã]+\s+de\s+\d{4})\b",
    re.IGNORECASE,
)
# NB: the multiplier alternatives are ordered longest-first. With ``mil`` first,
# "R$ 2,5 milhões" would match only "R$ 2,5 mil" and the value would come out a
# thousand times too small.
_MULTIPLIER_ALT: Final[str] = r"milh[oõ]e?s?|bilh[oõ]e?s?|trilh[oõ]e?s?|millions?|billions?|mil"
_NUMBER_PATTERN: Final[re.Pattern[str]] = re.compile(
    rf"((?:R\$|US\$|U\$S|\$|€|£)\s*[\d.,]+(?:\s*(?:{_MULTIPLIER_ALT}))?"
    r"|[\d.,]+\s*%"
    rf"|[\d.,]+\s*(?:{_MULTIPLIER_ALT})"
    r"|\b\d{1,3}(?:[.,]\d{3})+\b)",
    re.IGNORECASE,
)
_QUOTE_PATTERN: Final[re.Pattern[str]] = re.compile(r"[\"“]([^\"”]{12,300})[\"”]")

_STATUS_TERMS: Final[Dict[str, Tuple[str, str]]] = {
    "renovada": (S.CLAIM_STATUS, "renewed"),
    "renovado": (S.CLAIM_STATUS, "renewed"),
    "renewed": (S.CLAIM_STATUS, "renewed"),
    "cancelada": (S.CLAIM_STATUS, "cancelled"),
    "cancelado": (S.CLAIM_STATUS, "cancelled"),
    "cancelled": (S.CLAIM_STATUS, "cancelled"),
    "canceled": (S.CLAIM_STATUS, "cancelled"),
    "confirmada": (S.CLAIM_STATUS, "confirmed"),
    "confirmado": (S.CLAIM_STATUS, "confirmed"),
    "confirmed": (S.CLAIM_STATUS, "confirmed"),
    "adiada": (S.CLAIM_STATUS, "delayed"),
    "adiado": (S.CLAIM_STATUS, "delayed"),
    "delayed": (S.CLAIM_STATUS, "delayed"),
    "estreia": (S.CLAIM_RELEASE, "releases"),
    "estreou": (S.CLAIM_RELEASE, "released"),
    "lancamento": (S.CLAIM_RELEASE, "releases"),
    "premieres": (S.CLAIM_RELEASE, "releases"),
    "disponivel": (S.CLAIM_AVAILABILITY, "available"),
    "available": (S.CLAIM_AVAILABILITY, "available"),
    "indisponivel": (S.CLAIM_AVAILABILITY, "unavailable"),
}

# The prefix is case-insensitive (a sentence may open with "Segundo"), but the
# attributed name must stay a proper noun — a global IGNORECASE would match any
# lowercase word and turn "segundo ele" into an attribution.
_ATTRIBUTION_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\b(?i:segundo|de acordo com|conforme|according to)\s+([A-ZÁÉÍÓÚÂÊÔÃÕÇ][\w'’\-]*"
    r"(?:\s+[A-ZÁÉÍÓÚÂÊÔÃÕÇ][\w'’\-]*){0,3})",
)

_SENTENCE_SPLIT: Final[re.Pattern[str]] = re.compile(r"(?<=[.!?])\s+")


@dataclass
class DraftSegment:
    """One addressable piece of the draft."""

    location: str
    text: str


def is_material_text(text: str) -> bool:
    """False for navigation, promotion and clearly-marked opinion."""
    if not text or len(normalize_for_comparison(text)) < 12:
        return False
    return not any(pattern.search(text) for pattern in _NON_MATERIAL_PATTERNS)


def segment_draft(draft: Any) -> List[DraftSegment]:
    """Break the draft into addressable segments with stable locations."""
    from app.html_utils import _plain_text_fragment

    content = draft.content
    segments: List[DraftSegment] = []

    for location, value in (
        (S.LOCATION_TITLE, content.title),
        (S.LOCATION_SUBTITLE, content.subtitle),
        (S.LOCATION_SUMMARY, content.summary),
        (S.LOCATION_META, content.meta_description),
    ):
        text = normalize_text(value)
        if text:
            segments.append(DraftSegment(location=location, text=text))

    body = content.body_html or ""
    paragraphs = re.findall(r"<p\b[^>]*>(.*?)</p>", body, re.IGNORECASE | re.DOTALL)
    if not paragraphs:
        plain = _plain_text_fragment(body)
        paragraphs = [p for p in re.split(r"\n{2,}", plain) if p.strip()]

    for index, raw in enumerate(paragraphs, start=1):
        text = normalize_text(_plain_text_fragment(raw))
        if text:
            segments.append(
                DraftSegment(location=f"{S.LOCATION_BODY_PREFIX}{index}", text=text)
            )
    return segments


def _sentences(text: str) -> List[str]:
    return [s.strip() for s in _SENTENCE_SPLIT.split(text) if s.strip()]


def _subject_of(sentence: str) -> Optional[str]:
    """A crude subject: the leading proper-noun run.

    Deliberately shallow — real entity resolution is MS-5. This only needs to be
    stable enough to tell "the same subject" from "a different subject".
    """
    match = re.match(
        r"\s*((?:[A-ZÁÉÍÓÚÂÊÔÃÕÇ][\w'’\-]*)(?:\s+(?:de|da|do|dos|das|of|the)?\s*"
        r"[A-ZÁÉÍÓÚÂÊÔÃÕÇ][\w'’\-]*){0,4})",
        sentence,
    )
    if not match:
        return None
    subject = normalize_text(match.group(1))
    return subject or None


def extract_deterministic_claims(draft: Any, *, max_claims: int = 100) -> List[FactualClaim]:
    """Pattern-based extraction. Reproducible and never hallucinated."""
    draft_id = getattr(draft, "draft_id", "") or ""
    found: Dict[str, FactualClaim] = {}

    for segment in segment_draft(draft):
        for sentence in _sentences(segment.text):
            if not is_material_text(sentence):
                continue
            subject = _subject_of(sentence)

            for match in _DATE_PATTERN.finditer(sentence):
                normalized = normalize_date(match.group(1))
                if normalized:
                    _add(found, draft_id, segment.location, sentence,
                         S.CLAIM_DATE, subject, "date", normalized, match.group(1))

            for match in _NUMBER_PATTERN.finditer(sentence):
                normalized = normalize_number(match.group(1))
                if normalized:
                    _add(found, draft_id, segment.location, sentence,
                         S.CLAIM_NUMBER, subject, "amount", normalized, match.group(1))

            for match in _QUOTE_PATTERN.finditer(sentence):
                quote = normalize_text(match.group(1))
                if quote:
                    _add(found, draft_id, segment.location, sentence,
                         S.CLAIM_QUOTE, subject, "quote",
                         normalize_for_comparison(quote), quote)

            match = _ATTRIBUTION_PATTERN.search(sentence)
            if match:
                who = normalize_text(match.group(1))
                _add(found, draft_id, segment.location, sentence,
                     S.CLAIM_ATTRIBUTION, subject, "attributed_to",
                     normalize_for_comparison(who), who)

            lowered = normalize_for_comparison(sentence)
            for term, (claim_type, predicate) in _STATUS_TERMS.items():
                if re.search(rf"\b{re.escape(term)}\b", lowered):
                    _add(found, draft_id, segment.location, sentence,
                         claim_type, subject, predicate, predicate, sentence)
                    break

            if len(found) >= max_claims:
                logger.warning(
                    "[CLAIMS_EXTRACTED] draft_id=%s limite de %s claims atingido",
                    draft_id, max_claims,
                )
                return list(found.values())

    return list(found.values())


def _add(
    found: Dict[str, FactualClaim],
    draft_id: str,
    location: str,
    sentence: str,
    claim_type: str,
    subject: Optional[str],
    predicate: str,
    normalized_value: str,
    display_text: str,
) -> None:
    """Register a claim, merging locations when the same claim recurs."""
    claim = FactualClaim(
        draft_id=draft_id,
        display_text=display_text,
        claim_type=claim_type,
        normalized_subject=normalize_for_comparison(subject) if subject else None,
        predicate=predicate,
        normalized_value=normalized_value,
        source_sentence=sentence,
        draft_locations=[location],
        status=S.UNVERIFIED,
        is_material=True,
    )
    existing = found.get(claim.claim_id)
    if existing is None:
        found[claim.claim_id] = claim
        return
    if location not in existing.draft_locations:
        existing.draft_locations.append(location)


# --- AI layer --------------------------------------------------------------

#: Fields the model must provide for each claim.
_REQUIRED_AI_FIELDS: Final[Tuple[str, ...]] = ("display_text", "claim_type", "location")


def parse_claim_response(
    raw: Any,
    *,
    draft: Any,
    max_claims: int = 100,
) -> Tuple[List[FactualClaim], List[str]]:
    """Validate a model response into claims. Returns ``(claims, warnings)``.

    Every claim is checked against the draft: if its text is not actually
    present, it is discarded. A model cannot introduce a fact this way — only
    point at one that is already written.
    """
    if isinstance(raw, (str, bytes)):
        text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        text = text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```[a-z]*\s*|\s*```$", "", text, flags=re.IGNORECASE)
        try:
            payload = json.loads(text)
        except (TypeError, ValueError) as exc:
            raise InvalidModelResponseError(f"nao e JSON valido ({exc})") from exc
    elif isinstance(raw, dict):
        payload = raw
    else:
        raise InvalidModelResponseError(f"tipo inesperado: {type(raw).__name__}")

    if not isinstance(payload, dict):
        raise InvalidModelResponseError("o topo da resposta precisa ser um objeto")

    items = payload.get("claims")
    if not isinstance(items, list):
        raise InvalidModelResponseError("campo 'claims' ausente ou nao e lista")

    draft_haystack = _draft_haystack(draft)
    draft_id = getattr(draft, "draft_id", "") or ""
    claims: List[FactualClaim] = []
    warnings: List[str] = []
    seen: set[str] = set()

    for index, item in enumerate(items[:max_claims]):
        if not isinstance(item, dict):
            warnings.append(f"CLAIM_ITEM_NOT_OBJECT:{index}")
            continue
        missing = [f for f in _REQUIRED_AI_FIELDS if not str(item.get(f) or "").strip()]
        if missing:
            warnings.append(f"CLAIM_MISSING_FIELDS:{index}:{','.join(missing)}")
            continue

        display_text = normalize_text(item.get("display_text"))
        # The anti-fabrication gate: the model may only surface text the draft
        # already contains.
        if normalize_for_comparison(display_text) not in draft_haystack:
            warnings.append("CLAIM_NOT_FOUND_IN_DRAFT")
            logger.warning(
                "[CLAIMS_EXTRACTED] draft_id=%s claim descartado: texto ausente no draft",
                draft_id,
            )
            continue

        claim_type = str(item.get("claim_type") or "").strip().upper()
        if claim_type not in S.CLAIM_TYPES:
            warnings.append(f"CLAIM_TYPE_UNKNOWN:{claim_type or '-'}")
            claim_type = S.CLAIM_OTHER

        confidence = item.get("confidence")
        try:
            confidence = float(confidence) if confidence is not None else None
            if confidence is not None and not (0.0 <= confidence <= 1.0):
                warnings.append("CLAIM_CONFIDENCE_OUT_OF_RANGE")
                confidence = None
        except (TypeError, ValueError):
            warnings.append("CLAIM_CONFIDENCE_NOT_NUMERIC")
            confidence = None

        locations = item.get("location")
        location_list = locations if isinstance(locations, list) else [locations]
        location_list = [str(loc).strip() for loc in location_list if str(loc or "").strip()]

        value = item.get("normalized_value") or item.get("value")
        normalized_value = None
        if value:
            normalized_value = (
                normalize_date(str(value))
                or normalize_number(str(value))
                or normalize_for_comparison(str(value))
            )

        claim = FactualClaim(
            draft_id=draft_id,
            display_text=display_text,
            claim_type=claim_type,
            normalized_subject=normalize_for_comparison(item.get("subject") or "") or None,
            predicate=normalize_for_comparison(item.get("predicate") or "") or None,
            normalized_value=normalized_value,
            source_sentence=normalize_text(item.get("source_sentence") or display_text),
            draft_locations=location_list,
            status=S.UNVERIFIED,
            confidence=confidence,
            is_material=bool(item.get("is_material", True)) and is_material_text(display_text),
        )
        if claim.claim_id in seen:
            continue
        seen.add(claim.claim_id)
        claims.append(claim)

    if len(items) > max_claims:
        warnings.append(f"CLAIMS_TRUNCATED:{len(items)}>{max_claims}")

    return claims, warnings


def _draft_haystack(draft: Any) -> str:
    """Normalized text of the whole draft, for the presence check."""
    from app.html_utils import _plain_text_fragment

    content = draft.content
    parts = [
        content.title or "", content.subtitle or "", content.summary or "",
        content.meta_description or "", _plain_text_fragment(content.body_html or ""),
    ]
    return normalize_for_comparison(" ".join(parts))


def merge_claims(
    deterministic: Sequence[FactualClaim], semantic: Sequence[FactualClaim]
) -> List[FactualClaim]:
    """Union by ``claim_id``, deterministic first.

    When both layers found the same assertion, the deterministic one wins: its
    normalized value came from a rule, not from a model.
    """
    merged: Dict[str, FactualClaim] = {}
    for claim in list(deterministic) + list(semantic):
        existing = merged.get(claim.claim_id)
        if existing is None:
            merged[claim.claim_id] = claim
            continue
        for location in claim.draft_locations:
            if location not in existing.draft_locations:
                existing.draft_locations.append(location)
    return sorted(merged.values(), key=lambda c: c.claim_id)


__all__ = [
    "DraftSegment",
    "extract_deterministic_claims",
    "is_material_text",
    "merge_claims",
    "parse_claim_response",
    "segment_draft",
]
