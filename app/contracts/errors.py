"""Structured error and warning vocabulary for input contracts.

Codes are the stable interface. Messages are for humans and may change; nothing
in the pipeline, the database or the tests may branch on message text.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import Any, Dict, Final, FrozenSet, List, Optional

# --- Blocking errors -------------------------------------------------------

UNKNOWN_CONTRACT_VERSION: Final[str] = "UNKNOWN_CONTRACT_VERSION"
INVALID_EVENT_KEY: Final[str] = "INVALID_EVENT_KEY"
INVALID_REVISION: Final[str] = "INVALID_REVISION"
INVALID_PAYLOAD_HASH: Final[str] = "INVALID_PAYLOAD_HASH"
PAYLOAD_HASH_MISMATCH: Final[str] = "PAYLOAD_HASH_MISMATCH"
REVISION_HASH_CONFLICT: Final[str] = "REVISION_HASH_CONFLICT"
INVALID_SOURCE_COUNT: Final[str] = "INVALID_SOURCE_COUNT"
INVALID_SOURCE_URL: Final[str] = "INVALID_SOURCE_URL"
INVALID_EVENT_DATES: Final[str] = "INVALID_EVENT_DATES"
STALE_REVISION: Final[str] = "STALE_REVISION"
LEGACY_CONTRACT_DISABLED: Final[str] = "LEGACY_CONTRACT_DISABLED"

# Additional blocking codes required by the domain rules of the v1 contract.
INVALID_TITLE: Final[str] = "INVALID_TITLE"
INVALID_TOPIC: Final[str] = "INVALID_TOPIC"
BLOCKED_TOPIC: Final[str] = "BLOCKED_TOPIC"
INVALID_MULTI_SOURCE_FLAG: Final[str] = "INVALID_MULTI_SOURCE_FLAG"
INVALID_CLUSTER_CONFIDENCE: Final[str] = "INVALID_CLUSTER_CONFIDENCE"
NO_PRIMARY_SOURCE: Final[str] = "NO_PRIMARY_SOURCE"
MULTIPLE_PRIMARY_SOURCES: Final[str] = "MULTIPLE_PRIMARY_SOURCES"
DUPLICATE_SOURCE_URL: Final[str] = "DUPLICATE_SOURCE_URL"
INVALID_SOURCE_DOMAIN: Final[str] = "INVALID_SOURCE_DOMAIN"
PAYLOAD_TOO_LARGE: Final[str] = "PAYLOAD_TOO_LARGE"
MALFORMED_PAYLOAD: Final[str] = "MALFORMED_PAYLOAD"

BLOCKING_ERROR_CODES: Final[FrozenSet[str]] = frozenset(
    {
        UNKNOWN_CONTRACT_VERSION,
        INVALID_EVENT_KEY,
        INVALID_REVISION,
        INVALID_PAYLOAD_HASH,
        PAYLOAD_HASH_MISMATCH,
        REVISION_HASH_CONFLICT,
        INVALID_SOURCE_COUNT,
        INVALID_SOURCE_URL,
        INVALID_EVENT_DATES,
        STALE_REVISION,
        LEGACY_CONTRACT_DISABLED,
        INVALID_TITLE,
        INVALID_TOPIC,
        BLOCKED_TOPIC,
        INVALID_MULTI_SOURCE_FLAG,
        INVALID_CLUSTER_CONFIDENCE,
        NO_PRIMARY_SOURCE,
        MULTIPLE_PRIMARY_SOURCES,
        DUPLICATE_SOURCE_URL,
        INVALID_SOURCE_DOMAIN,
        PAYLOAD_TOO_LARGE,
        MALFORMED_PAYLOAD,
    }
)

# --- Warnings --------------------------------------------------------------

LEGACY_CONTRACT: Final[str] = "LEGACY_CONTRACT"
REVISION_GAP: Final[str] = "REVISION_GAP"
MISSING_CURSOR: Final[str] = "MISSING_CURSOR"
MISSING_CLUSTER_CONFIDENCE: Final[str] = "MISSING_CLUSTER_CONFIDENCE"
SOURCE_AUTHOR_UNKNOWN: Final[str] = "SOURCE_AUTHOR_UNKNOWN"
SOURCE_DATE_UNKNOWN: Final[str] = "SOURCE_DATE_UNKNOWN"
UNKNOWN_FIELDS_PRESERVED: Final[str] = "UNKNOWN_FIELDS_PRESERVED"
DEDUPLICATED_SOURCE_URL: Final[str] = "DEDUPLICATED_SOURCE_URL"

WARNING_CODES: Final[FrozenSet[str]] = frozenset(
    {
        LEGACY_CONTRACT,
        REVISION_GAP,
        MISSING_CURSOR,
        MISSING_CLUSTER_CONFIDENCE,
        SOURCE_AUTHOR_UNKNOWN,
        SOURCE_DATE_UNKNOWN,
        UNKNOWN_FIELDS_PRESERVED,
        DEDUPLICATED_SOURCE_URL,
    }
)

SEVERITY_ERROR: Final[str] = "error"
SEVERITY_WARNING: Final[str] = "warning"


@dataclass(frozen=True)
class ContractIssue:
    """One structured validation finding.

    Never collapse these into free text: the pipeline, the store and the tests
    all match on ``code``.
    """

    code: str
    severity: str = SEVERITY_ERROR
    # NB: this attribute shadows ``dataclasses.field`` inside the class body,
    # hence the ``dc_field`` alias on the line below.
    field: Optional[str] = None
    message: str = ""
    context: Dict[str, Any] = dc_field(default_factory=dict)

    @property
    def is_blocking(self) -> bool:
        return self.severity == SEVERITY_ERROR

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"code": self.code, "severity": self.severity}
        if self.field:
            payload["field"] = self.field
        if self.message:
            payload["message"] = self.message
        if self.context:
            payload["context"] = dict(self.context)
        return payload

    def __str__(self) -> str:  # pragma: no cover - convenience only
        where = f" ({self.field})" if self.field else ""
        return f"{self.code}{where}: {self.message}" if self.message else f"{self.code}{where}"


def error(code: str, message: str = "", *, field_name: Optional[str] = None, **context: Any) -> ContractIssue:
    return ContractIssue(
        code=code,
        severity=SEVERITY_ERROR,
        field=field_name,
        message=message,
        context=context,
    )


def warning(code: str, message: str = "", *, field_name: Optional[str] = None, **context: Any) -> ContractIssue:
    return ContractIssue(
        code=code,
        severity=SEVERITY_WARNING,
        field=field_name,
        message=message,
        context=context,
    )


def codes(issues: List[ContractIssue]) -> List[str]:
    """Codes in order, for assertions and structured logs."""
    return [issue.code for issue in issues]


class ContractError(ValueError):
    """Raised when a payload cannot be interpreted under any known contract."""

    def __init__(self, message: str, issues: Optional[List[ContractIssue]] = None):
        super().__init__(message)
        self.issues: List[ContractIssue] = list(issues or [])


class UnknownContractVersionError(ContractError):
    """The declared ``contract_version`` is not one MNScr knows how to read."""


class LegacyContractDisabledError(ContractError):
    """A legacy item arrived while legacy compatibility is turned off."""


__all__ = [
    "BLOCKED_TOPIC",
    "BLOCKING_ERROR_CODES",
    "ContractError",
    "ContractIssue",
    "DEDUPLICATED_SOURCE_URL",
    "DUPLICATE_SOURCE_URL",
    "INVALID_CLUSTER_CONFIDENCE",
    "INVALID_EVENT_DATES",
    "INVALID_EVENT_KEY",
    "INVALID_MULTI_SOURCE_FLAG",
    "INVALID_PAYLOAD_HASH",
    "INVALID_REVISION",
    "INVALID_SOURCE_COUNT",
    "INVALID_SOURCE_DOMAIN",
    "INVALID_SOURCE_URL",
    "INVALID_TITLE",
    "INVALID_TOPIC",
    "LEGACY_CONTRACT",
    "LEGACY_CONTRACT_DISABLED",
    "LegacyContractDisabledError",
    "MALFORMED_PAYLOAD",
    "MISSING_CLUSTER_CONFIDENCE",
    "MISSING_CURSOR",
    "MULTIPLE_PRIMARY_SOURCES",
    "NO_PRIMARY_SOURCE",
    "PAYLOAD_HASH_MISMATCH",
    "PAYLOAD_TOO_LARGE",
    "REVISION_GAP",
    "REVISION_HASH_CONFLICT",
    "SEVERITY_ERROR",
    "SEVERITY_WARNING",
    "SOURCE_AUTHOR_UNKNOWN",
    "SOURCE_DATE_UNKNOWN",
    "STALE_REVISION",
    "UNKNOWN_CONTRACT_VERSION",
    "UNKNOWN_FIELDS_PRESERVED",
    "UnknownContractVersionError",
    "WARNING_CODES",
    "codes",
    "error",
    "warning",
]
