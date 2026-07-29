"""Editorial delivery: handing an approved draft to an external CMS.

MNScr creates **drafts for human review**. Nothing here publishes, approves or
schedules; ``cms_status`` is pinned to ``"draft"`` and the contract rejects any
other value.

The layering is the point:

* ``contract`` / ``builder`` / ``states`` / ``categories`` — pure domain, no HTTP
* ``destination`` — what a destination must do, still no HTTP
* ``payload_cms`` — the **only** module that imports an HTTP client

That boundary is what lets the whole delivery path be exercised without a
socket, and it is enforced by an architectural test.

**Scope honesty.** The Payload CMS instance lives in the Screen-App repository,
not in MNScr. Everything here is the MNScr side of the integration, exercised
against a local fake server. It has not been validated against a real Payload
deployment. ``docs/ms5-screen-app.md`` lists what Screen-App still has to
confirm, and the delivery guarantee is **at-least-once with best-effort
deduplication** — not exactly-once.
"""

from .builder import build_delivery_payload, check_deliverable
from .categories import TOPIC_TO_CATEGORY, normalize_tags, resolve_category
from .contract import (
    CMS_STATUS_DRAFT,
    CONTENT_FORMAT_HTML,
    EDITORIAL_STATUS_READY,
    SCHEMA_VERSION,
    DeliveryMedia,
    EditorialDeliveryPayload,
    EntityMention,
    ensure_valid,
    is_valid_slug,
    slugify,
    validate_delivery_payload,
)
from .destination import (
    DESTINATION_PAYLOAD_CMS,
    DeliveryResult,
    EditorialDestination,
    RemoteDraft,
    build_delivery_id,
    build_idempotency_key,
)
from .errors import (
    DeliveryAuthenticationError,
    DeliveryBlockedError,
    DeliveryConfigurationError,
    DeliveryConflictError,
    DeliveryConnectionError,
    DeliveryError,
    DeliveryNotFoundError,
    DeliveryPermissionError,
    DeliveryRateLimitedError,
    DeliveryServerError,
    DeliveryTimeoutError,
    InvalidDeliveryPayloadError,
    InvalidRemoteResponseError,
    classify,
    error_for_status,
)
from .retry import Clock, FakeClock, FakeSleeper, RetryPolicy, Sleeper, parse_retry_after
from .states import (
    DELIVERED,
    DELIVERY_BLOCKED,
    DELIVERY_FAILED_PERMANENT,
    DELIVERY_FAILED_RETRYABLE,
    DELIVERY_IN_PROGRESS,
    DELIVERY_NEEDS_RECONCILIATION,
    DELIVERY_PENDING,
    DELIVERY_REQUIRES_MANUAL_REVIEW,
    DELIVERY_STATES,
    InvalidDeliveryTransitionError,
    assert_transition,
    can_transition,
    is_terminal,
    validate_delivery_state,
)

__all__ = [
    "CMS_STATUS_DRAFT",
    "CONTENT_FORMAT_HTML",
    "Clock",
    "DELIVERED",
    "DELIVERY_BLOCKED",
    "DELIVERY_FAILED_PERMANENT",
    "DELIVERY_FAILED_RETRYABLE",
    "DELIVERY_IN_PROGRESS",
    "DELIVERY_NEEDS_RECONCILIATION",
    "DELIVERY_PENDING",
    "DELIVERY_REQUIRES_MANUAL_REVIEW",
    "DELIVERY_STATES",
    "DESTINATION_PAYLOAD_CMS",
    "DeliveryAuthenticationError",
    "DeliveryBlockedError",
    "DeliveryConfigurationError",
    "DeliveryConflictError",
    "DeliveryConnectionError",
    "DeliveryError",
    "DeliveryMedia",
    "DeliveryNotFoundError",
    "DeliveryPermissionError",
    "DeliveryRateLimitedError",
    "DeliveryResult",
    "DeliveryServerError",
    "DeliveryTimeoutError",
    "EDITORIAL_STATUS_READY",
    "EditorialDeliveryPayload",
    "EditorialDestination",
    "EntityMention",
    "FakeClock",
    "FakeSleeper",
    "InvalidDeliveryPayloadError",
    "InvalidDeliveryTransitionError",
    "InvalidRemoteResponseError",
    "RemoteDraft",
    "RetryPolicy",
    "SCHEMA_VERSION",
    "Sleeper",
    "TOPIC_TO_CATEGORY",
    "assert_transition",
    "build_delivery_id",
    "build_delivery_payload",
    "build_idempotency_key",
    "can_transition",
    "check_deliverable",
    "classify",
    "ensure_valid",
    "error_for_status",
    "is_terminal",
    "is_valid_slug",
    "normalize_tags",
    "parse_retry_after",
    "resolve_category",
    "slugify",
    "validate_delivery_payload",
    "validate_delivery_state",
]
