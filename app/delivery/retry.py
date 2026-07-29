"""Retry policy for delivery attempts.

Exponential backoff with deterministic jitter, and — unlike the rest of the
codebase — an **injectable** sleeper and clock. Elsewhere tests monkeypatch
``time.sleep`` on the module; that works, but a retry loop is exactly the place
where a test wants to *assert on the delays* rather than merely suppress them.
The defaults still call ``time.sleep``, so the existing monkeypatch convention
keeps working for anyone who prefers it.

Jitter is derived from the attempt number and a seed, never from
``random.random()``: a flaky test is worse than a slightly less uniform spread.
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass
from typing import Callable, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_MAX_ATTEMPTS: int = 3
DEFAULT_BASE_SECONDS: float = 2.0
DEFAULT_MAX_BACKOFF_SECONDS: float = 60.0
#: Fraction of the computed delay that jitter may add. Small on purpose: this
#: spreads a thundering herd without making the wait unpredictable to an operator.
DEFAULT_JITTER_RATIO: float = 0.25


def _deterministic_jitter(seed: str, attempt: int) -> float:
    """A stable value in ``[0, 1)`` for this seed and attempt."""
    digest = hashlib.sha256(f"{seed}|{attempt}".encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") / 0xFFFFFFFF


@dataclass(frozen=True)
class RetryPolicy:
    """How many times to try, and how long to wait between attempts."""

    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    base_seconds: float = DEFAULT_BASE_SECONDS
    max_backoff_seconds: float = DEFAULT_MAX_BACKOFF_SECONDS
    jitter_ratio: float = DEFAULT_JITTER_RATIO

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts precisa ser >= 1")
        if self.base_seconds < 0:
            raise ValueError("base_seconds nao pode ser negativo")
        if self.max_backoff_seconds < 0:
            raise ValueError("max_backoff_seconds nao pode ser negativo")
        if not (0.0 <= self.jitter_ratio <= 1.0):
            raise ValueError("jitter_ratio precisa estar entre 0 e 1")

    def delay_for(
        self,
        attempt: int,
        *,
        seed: str = "",
        retry_after_seconds: Optional[float] = None,
    ) -> float:
        """Seconds to wait before ``attempt`` (1-based).

        A ``Retry-After`` from the destination always wins: it knows better than
        our exponential guess, and ignoring it is how a client gets banned.
        """
        if retry_after_seconds is not None and retry_after_seconds >= 0:
            return min(float(retry_after_seconds), self.max_backoff_seconds)
        if attempt <= 1:
            return 0.0
        raw = self.base_seconds * (2 ** (attempt - 2))
        capped = min(raw, self.max_backoff_seconds)
        jitter = capped * self.jitter_ratio * _deterministic_jitter(seed, attempt)
        return round(min(capped + jitter, self.max_backoff_seconds), 6)

    def schedule(self, *, seed: str = "") -> List[float]:
        """Every delay this policy would use, for documentation and tests."""
        return [self.delay_for(attempt, seed=seed) for attempt in range(1, self.max_attempts + 1)]

    @classmethod
    def from_config(cls) -> "RetryPolicy":
        from app import config

        return cls(
            max_attempts=int(getattr(config, "PAYLOAD_CMS_MAX_ATTEMPTS", DEFAULT_MAX_ATTEMPTS)),
            base_seconds=float(
                getattr(config, "PAYLOAD_CMS_RETRY_BASE_SECONDS", DEFAULT_BASE_SECONDS)
            ),
        )


class Sleeper:
    """Indirection over ``time.sleep`` so tests never actually wait.

    Records what it was asked to wait for, which is how the retry tests assert
    on backoff growth without spending the time.
    """

    def __init__(self, sleep_fn: Optional[Callable[[float], None]] = None):
        self._sleep = sleep_fn if sleep_fn is not None else time.sleep
        self.slept: List[float] = []

    def sleep(self, seconds: float) -> None:
        seconds = max(0.0, float(seconds))
        self.slept.append(seconds)
        if seconds > 0:
            self._sleep(seconds)

    @property
    def total_slept(self) -> float:
        return sum(self.slept)


class FakeSleeper(Sleeper):
    """A sleeper that records and never waits. For tests only."""

    def __init__(self) -> None:
        super().__init__(sleep_fn=lambda _seconds: None)


class Clock:
    """Monotonic clock indirection, for measuring attempt duration."""

    def __init__(self, monotonic_fn: Optional[Callable[[], float]] = None):
        self._monotonic = monotonic_fn if monotonic_fn is not None else time.monotonic

    def now(self) -> float:
        return self._monotonic()

    def elapsed_ms_since(self, started: float) -> int:
        return int(max(0.0, self.now() - started) * 1000)


class FakeClock(Clock):
    """A clock that advances only when told to. For tests only."""

    def __init__(self, start: float = 0.0) -> None:
        self._value = float(start)
        super().__init__(monotonic_fn=lambda: self._value)

    def advance(self, seconds: float) -> None:
        self._value += float(seconds)


def parse_retry_after(value: Optional[str]) -> Optional[float]:
    """Read a ``Retry-After`` header expressed in seconds.

    Only the delta-seconds form is honoured. The HTTP-date form is ignored
    rather than guessed at, since parsing it wrong would produce a wait of hours
    or a wait of none.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        seconds = float(text)
    except ValueError:
        logger.info("[delivery] Retry-After nao numerico ignorado: %r", text[:32])
        return None
    return seconds if seconds >= 0 else None


__all__ = [
    "Clock",
    "DEFAULT_BASE_SECONDS",
    "DEFAULT_JITTER_RATIO",
    "DEFAULT_MAX_ATTEMPTS",
    "DEFAULT_MAX_BACKOFF_SECONDS",
    "FakeClock",
    "FakeSleeper",
    "RetryPolicy",
    "Sleeper",
    "parse_retry_after",
]
