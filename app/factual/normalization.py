"""Deterministic normalization for factual comparison.

Two sources rarely spell a fact the same way. "29 de julho de 2026" and
"2026-07-29" are the same date; "R$ 2,5 milhões" and "R$ 2.500.000" are the same
amount. Comparison happens on the normalized form; the reader always sees the
original.

The hard rule throughout: **never invent precision that the text does not
carry**. "julho de 2026" normalizes to ``2026-07`` and stays there — it does not
acquire a day. Currency is never converted, because an exchange rate is a fact
MNScr was not given.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Final, Optional, Tuple

# --- Text ------------------------------------------------------------------

_WHITESPACE: Final[re.Pattern[str]] = re.compile(r"\s+")
_EDGE_PUNCT: Final[re.Pattern[str]] = re.compile(r"^[\s\.,;:!?\-–—\"'“”‘’()\[\]]+|[\s\.,;:!?\-–—\"'“”‘’()\[\]]+$")

#: Typographic variants collapsed before comparison so a smart quote and a
#: straight quote do not read as different facts.
_CHAR_FOLD: Final[dict[str, str]] = {
    "“": '"', "”": '"', "„": '"', "«": '"', "»": '"',
    "‘": "'", "’": "'", "‚": "'",
    "–": "-", "—": "-", "−": "-",
    " ": " ", " ": " ", " ": " ",
    "…": "...",
}


def normalize_text(value: Optional[str]) -> str:
    """Collapse whitespace and Unicode form. Case is preserved."""
    if not value:
        return ""
    text = unicodedata.normalize("NFC", str(value))
    for source, target in _CHAR_FOLD.items():
        text = text.replace(source, target)
    return _WHITESPACE.sub(" ", text).strip()


def normalize_for_comparison(value: Optional[str]) -> str:
    """Aggressive fold used only for matching: casefold, no accents, no edges.

    The original always survives in ``display_text``; this form exists purely so
    that "Estreia Confirmada" and "estreia confirmada." compare equal.
    """
    text = normalize_text(value)
    if not text:
        return ""
    text = _EDGE_PUNCT.sub("", text)
    decomposed = unicodedata.normalize("NFD", text)
    without_accents = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return _WHITESPACE.sub(" ", without_accents).strip().casefold()


def truncate_at_safe_boundary(text: str, limit: int) -> Tuple[str, bool]:
    """Cut at a word boundary. Returns ``(text, was_truncated)``.

    Cutting mid-word can change meaning ("não confirmou" → "não confirm"), so
    the cut lands on whitespace whenever one is reasonably close.
    """
    normalized = normalize_text(text)
    if limit <= 0 or len(normalized) <= limit:
        return normalized, False
    window = normalized[:limit]
    last_space = window.rfind(" ")
    # Only honour the boundary if it is not throwing away most of the excerpt.
    if last_space > limit * 0.6:
        window = window[:last_space]
    return window.rstrip() + "…", True


# --- Dates -----------------------------------------------------------------

_MONTHS_PT: Final[dict[str, int]] = {
    "janeiro": 1, "fevereiro": 2, "marco": 3, "março": 3, "abril": 4,
    "maio": 5, "junho": 6, "julho": 7, "agosto": 8, "setembro": 9,
    "outubro": 10, "novembro": 11, "dezembro": 12,
}
_MONTHS_EN: Final[dict[str, int]] = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}
_MONTH_NAMES: Final[dict[str, int]] = {**_MONTHS_PT, **_MONTHS_EN}

_ISO_FULL = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_ISO_MONTH = re.compile(r"\b(\d{4})-(\d{2})\b")
_DMY_SLASH = re.compile(r"\b(\d{1,2})[/.](\d{1,2})[/.](\d{4})\b")
_PT_FULL = re.compile(
    r"\b(\d{1,2})\s*(?:de\s+)?([a-zçã]+)\s*(?:de\s+)?(\d{4})\b", re.IGNORECASE
)
_PT_MONTH_YEAR = re.compile(r"\b([a-zçã]+)\s+de\s+(\d{4})\b", re.IGNORECASE)
_EN_FULL = re.compile(r"\b([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})\b")
_EN_MONTH_YEAR = re.compile(r"\b([A-Za-z]+)\s+(\d{4})\b")
_YEAR_ONLY = re.compile(r"\b(19|20)(\d{2})\b")


def _month_number(name: str) -> Optional[int]:
    key = normalize_for_comparison(name)
    return _MONTH_NAMES.get(key)


def normalize_date(value: Optional[str]) -> Optional[str]:
    """ISO 8601 at the precision the text actually provides.

    Returns ``YYYY-MM-DD``, ``YYYY-MM`` or ``YYYY`` — never more precise than
    the source. ``None`` when no date can be read at all.
    """
    if not value:
        return None
    text = normalize_text(value)
    if not text:
        return None

    match = _ISO_FULL.search(text)
    if match:
        year, month, day = match.groups()
        if _plausible(int(year), int(month), int(day)):
            return f"{year}-{month}-{day}"

    match = _DMY_SLASH.search(text)
    if match:
        day, month, year = (int(g) for g in match.groups())
        if _plausible(year, month, day):
            return f"{year:04d}-{month:02d}-{day:02d}"

    match = _PT_FULL.search(text)
    if match:
        day, month_name, year = match.group(1), match.group(2), match.group(3)
        month = _month_number(month_name)
        if month and _plausible(int(year), month, int(day)):
            return f"{int(year):04d}-{month:02d}-{int(day):02d}"

    match = _EN_FULL.search(text)
    if match:
        month_name, day, year = match.group(1), match.group(2), match.group(3)
        month = _month_number(month_name)
        if month and _plausible(int(year), month, int(day)):
            return f"{int(year):04d}-{month:02d}-{int(day):02d}"

    match = _ISO_MONTH.search(text)
    if match:
        year, month = match.groups()
        if 1 <= int(month) <= 12:
            return f"{year}-{month}"

    match = _PT_MONTH_YEAR.search(text)
    if match:
        month = _month_number(match.group(1))
        if month:
            return f"{int(match.group(2)):04d}-{month:02d}"

    match = _EN_MONTH_YEAR.search(text)
    if match:
        month = _month_number(match.group(1))
        if month:
            return f"{int(match.group(2)):04d}-{month:02d}"

    match = _YEAR_ONLY.search(text)
    if match:
        return f"{match.group(1)}{match.group(2)}"

    return None


def _plausible(year: int, month: int, day: int) -> bool:
    return 1900 <= year <= 2200 and 1 <= month <= 12 and 1 <= day <= 31


def dates_conflict(left: Optional[str], right: Optional[str]) -> bool:
    """True only when two dates genuinely disagree at shared precision.

    ``2026-07`` and ``2026-07-29`` do not conflict: the first is simply less
    precise. ``2026-07-29`` and ``2026-08-05`` do.
    """
    if not left or not right:
        return False
    a, b = left.split("-"), right.split("-")
    for part_a, part_b in zip(a, b):
        if part_a != part_b:
            return True
    return False


# --- Numbers ---------------------------------------------------------------

_MULTIPLIERS: Final[dict[str, int]] = {
    "mil": 1_000,
    "milhao": 1_000_000, "milhoes": 1_000_000,
    "million": 1_000_000, "millions": 1_000_000,
    "bilhao": 1_000_000_000, "bilhoes": 1_000_000_000,
    "billion": 1_000_000_000, "billions": 1_000_000_000,
    "trilhao": 1_000_000_000_000, "trilhoes": 1_000_000_000_000,
}

_CURRENCY_SYMBOLS: Final[dict[str, str]] = {
    "r$": "BRL", "us$": "USD", "u$s": "USD", "$": "USD",
    "€": "EUR", "£": "GBP", "¥": "JPY",
}

_PERCENT = re.compile(r"(-?[\d.,]+)\s*%")
_CURRENCY = re.compile(
    r"(r\$|us\$|u\$s|\$|€|£|¥|brl|usd|eur|gbp)\s*(-?[\d.,]+)\s*([a-zçãõ]+)?",
    re.IGNORECASE,
)
_PLAIN_NUMBER = re.compile(r"(-?[\d.,]+)\s*([a-zçãõ]+)?")


def _parse_decimal(raw: str) -> Optional[float]:
    """Read a number written in either pt-BR (1.234,5) or en (1,234.5) style."""
    text = raw.strip().replace(" ", "")
    if not text or not re.search(r"\d", text):
        return None
    has_comma, has_dot = "," in text, "." in text
    if has_comma and has_dot:
        # The rightmost separator is the decimal one.
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif has_comma:
        # A single comma with 1-2 trailing digits is decimal; otherwise it is a
        # thousands separator (1,234).
        tail = text.split(",")[-1]
        text = text.replace(",", "." if len(tail) <= 2 else "")
    elif has_dot:
        tail = text.split(".")[-1]
        if len(tail) == 3 and text.count(".") >= 1 and len(text.replace(".", "")) > 3:
            text = text.replace(".", "")
    try:
        return float(text)
    except ValueError:
        return None


def _format_number(value: float) -> str:
    if value == int(value) and abs(value) < 1e15:
        return str(int(value))
    return f"{value:.10g}"


def normalize_number(value: Optional[str]) -> Optional[str]:
    """Normalize a quantity, preserving its unit and never converting currency.

    ``1,5 milhão`` → ``1500000`` · ``R$ 2,5 milhões`` → ``BRL 2500000`` ·
    ``35%`` → ``35%``. An exchange rate is a fact MNScr was not given, so
    ``USD`` stays ``USD``.
    """
    if not value:
        return None
    text = normalize_text(value)
    if not text:
        return None
    lowered = text.lower()

    match = _PERCENT.search(lowered)
    if match:
        number = _parse_decimal(match.group(1))
        if number is not None:
            return f"{_format_number(number)}%"

    match = _CURRENCY.search(lowered)
    if match:
        symbol, raw_number, suffix = match.group(1), match.group(2), match.group(3)
        number = _parse_decimal(raw_number)
        if number is not None:
            code = _CURRENCY_SYMBOLS.get(symbol.lower(), symbol.upper())
            multiplier = _MULTIPLIERS.get(normalize_for_comparison(suffix or ""), 1)
            return f"{code} {_format_number(number * multiplier)}"

    match = _PLAIN_NUMBER.search(lowered)
    if match:
        number = _parse_decimal(match.group(1))
        if number is None:
            return None
        suffix = normalize_for_comparison(match.group(2) or "")
        multiplier = _MULTIPLIERS.get(suffix, 1)
        if multiplier > 1:
            return _format_number(number * multiplier)
        if suffix and suffix not in _MULTIPLIERS:
            # Preserve the unit: "90 minutos" is not the same fact as "90".
            return f"{_format_number(number)} {suffix}"
        return _format_number(number)

    return None


def numbers_conflict(left: Optional[str], right: Optional[str]) -> bool:
    """True when two normalized quantities disagree.

    Different units or currencies are *not* treated as a conflict: MNScr cannot
    convert them, so it declines to claim they disagree.
    """
    if not left or not right or left == right:
        return False
    left_unit, left_value = _split_unit(left)
    right_unit, right_value = _split_unit(right)
    if left_unit != right_unit:
        return False
    if left_value is None or right_value is None:
        return left != right
    return abs(left_value - right_value) > 1e-9


def _split_unit(normalized: str) -> Tuple[str, Optional[float]]:
    text = normalized.strip()
    if text.endswith("%"):
        return "%", _parse_decimal(text[:-1])
    parts = text.split(" ", 1)
    if len(parts) == 2:
        first, second = parts
        if re.fullmatch(r"[A-Z]{3}", first):
            return first, _parse_decimal(second)
        return second, _parse_decimal(first)
    return "", _parse_decimal(text)


# --- Similarity ------------------------------------------------------------


def token_overlap(left: Optional[str], right: Optional[str]) -> float:
    """Jaccard overlap of comparison tokens, in ``[0, 1]``.

    Deliberately crude and deterministic: it decides whether two strings are
    worth comparing further, never whether a claim is true.
    """
    a = set(normalize_for_comparison(left).split())
    b = set(normalize_for_comparison(right).split())
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


__all__ = [
    "dates_conflict",
    "normalize_date",
    "normalize_for_comparison",
    "normalize_number",
    "normalize_text",
    "numbers_conflict",
    "token_overlap",
    "truncate_at_safe_boundary",
]
