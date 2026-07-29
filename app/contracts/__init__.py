"""Versioned input contracts for MNScr.

MNScr reads acontecimentos from RSS Prime. Until MS-2 that reading was implicit
and tolerant: whatever the feed sent became a dict and flowed straight into the
pipeline. This package makes the input explicit, validated and versioned.

Three things are kept apart on purpose:

* **acontecimento** — identified by ``event_key``, stable across revisions;
* **revisão** — a numbered take on the acontecimento (``revision``);
* **payload** — what actually arrived for that revision, identified by
  ``payload_hash``.

The current contract is ``rss-prime-event-v1``. The pre-contract superfeed is
still accepted as ``legacy-feed-input-v0`` through a temporary adapter that
never invents the fields the old feed does not carry.
"""

from .errors import (
    BLOCKING_ERROR_CODES,
    WARNING_CODES,
    ContractError,
    ContractIssue,
    LegacyContractDisabledError,
    UnknownContractVersionError,
)
from .hashing import (
    calculate_event_payload_hash,
    canonical_payload,
    canonical_payload_json,
    is_sha256,
    normalize_datetime,
    verify_event_payload_hash,
)
from .legacy_feed_v0 import CONTRACT_VERSION as LEGACY_CONTRACT_VERSION
from .legacy_feed_v0 import LegacyFeedV0Adapter
from .revisions import (
    ACCEPT_NEW_EVENT,
    ACCEPT_NEW_REVISION,
    CONFLICT,
    DUPLICATE,
    STALE,
    RevisionDecision,
    StoredRevision,
    decide_revision,
)
from .rssprime_event_v1 import (
    CONTRACT_VERSION as RSSPRIME_EVENT_V1,
)
from .rssprime_event_v1 import (
    HASH_ORIGIN_LEGACY,
    HASH_ORIGIN_VERIFIED,
    RssPrimeEventV1,
    RssPrimeSourceV1,
    domain_of,
    url_is_acceptable,
)
from .states import (
    DUPLICATE_DELIVERY,
    INVALID,
    QUEUED,
    RECEIVED,
    REPLAY_COMPLETED,
    REPLAY_FAILED,
    REPLAY_PROCESSING,
    REPLAY_REQUESTED,
    VALIDATED,
)
from .validation import (
    KNOWN_CONTRACT_VERSIONS,
    ValidationResult,
    detect_contract_version,
    is_legacy_payload,
    validate_event,
    validate_payload,
)

__all__ = [
    "ACCEPT_NEW_EVENT",
    "ACCEPT_NEW_REVISION",
    "BLOCKING_ERROR_CODES",
    "CONFLICT",
    "ContractError",
    "ContractIssue",
    "DUPLICATE",
    "DUPLICATE_DELIVERY",
    "HASH_ORIGIN_LEGACY",
    "HASH_ORIGIN_VERIFIED",
    "INVALID",
    "KNOWN_CONTRACT_VERSIONS",
    "LEGACY_CONTRACT_VERSION",
    "LegacyContractDisabledError",
    "LegacyFeedV0Adapter",
    "QUEUED",
    "RECEIVED",
    "REPLAY_COMPLETED",
    "REPLAY_FAILED",
    "REPLAY_PROCESSING",
    "REPLAY_REQUESTED",
    "RSSPRIME_EVENT_V1",
    "RevisionDecision",
    "RssPrimeEventV1",
    "RssPrimeSourceV1",
    "STALE",
    "StoredRevision",
    "UnknownContractVersionError",
    "VALIDATED",
    "ValidationResult",
    "WARNING_CODES",
    "calculate_event_payload_hash",
    "canonical_payload",
    "canonical_payload_json",
    "decide_revision",
    "detect_contract_version",
    "domain_of",
    "is_legacy_payload",
    "is_sha256",
    "normalize_datetime",
    "url_is_acceptable",
    "validate_event",
    "validate_payload",
    "verify_event_payload_hash",
]
