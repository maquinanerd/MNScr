"""Typed delivery errors and their retry classification.

The classification is the safety-critical part. Getting it wrong in one
direction wastes attempts on a request that will never succeed; getting it wrong
in the other direction gives up on a destination that was merely restarting.

This does **not** reuse ``app.ai_client_gemini._extract_status_code``: that
helper deliberately excludes 429 (there it means "this API key is out of quota",
not "the transport failed") and falls back to scanning ``str(exc)`` for a status
number, which misfires whenever an unrelated number appears in the message. Here
the status code comes from the HTTP response itself.
"""

from __future__ import annotations

from typing import Final, FrozenSet, Optional

# --- Error classes ---------------------------------------------------------

ERROR_BLOCKED: Final[str] = "BLOCKED"
ERROR_INVALID_PAYLOAD: Final[str] = "INVALID_PAYLOAD"
ERROR_CONFIGURATION: Final[str] = "CONFIGURATION"
ERROR_AUTHENTICATION: Final[str] = "AUTHENTICATION"
ERROR_PERMISSION: Final[str] = "PERMISSION"
ERROR_NOT_FOUND: Final[str] = "NOT_FOUND"
ERROR_CONFLICT: Final[str] = "CONFLICT"
ERROR_RATE_LIMITED: Final[str] = "RATE_LIMITED"
ERROR_TIMEOUT: Final[str] = "TIMEOUT"
ERROR_CONNECTION: Final[str] = "CONNECTION"
ERROR_SERVER: Final[str] = "SERVER"
ERROR_INVALID_RESPONSE: Final[str] = "INVALID_RESPONSE"
ERROR_UNKNOWN: Final[str] = "UNKNOWN"

ERROR_CLASSES: Final[FrozenSet[str]] = frozenset(
    {
        ERROR_BLOCKED, ERROR_INVALID_PAYLOAD, ERROR_CONFIGURATION,
        ERROR_AUTHENTICATION, ERROR_PERMISSION, ERROR_NOT_FOUND, ERROR_CONFLICT,
        ERROR_RATE_LIMITED, ERROR_TIMEOUT, ERROR_CONNECTION, ERROR_SERVER,
        ERROR_INVALID_RESPONSE, ERROR_UNKNOWN,
    }
)

#: Worth trying again: the destination was momentarily unable, not unwilling.
RETRYABLE_ERROR_CLASSES: Final[FrozenSet[str]] = frozenset(
    {ERROR_TIMEOUT, ERROR_CONNECTION, ERROR_SERVER, ERROR_RATE_LIMITED}
)

#: 408 Request Timeout and 429 Too Many Requests are retryable here — unlike in
#: the Gemini client, where 429 means an exhausted key rather than a busy server.
RETRYABLE_STATUS_CODES: Final[FrozenSet[int]] = frozenset({408, 429, 500, 502, 503, 504})

#: A different answer will not come from repeating the same request.
PERMANENT_STATUS_CODES: Final[FrozenSet[int]] = frozenset({400, 401, 403, 404, 405, 409, 410, 422})


class DeliveryError(RuntimeError):
    """Base class for every delivery failure.

    Carries a classification so the caller never has to re-derive whether a
    retry makes sense.
    """

    error_class: str = ERROR_UNKNOWN
    retryable: bool = False

    def __init__(
        self,
        message: str,
        *,
        http_status: Optional[int] = None,
        retry_after_seconds: Optional[float] = None,
    ):
        super().__init__(message)
        self.http_status = http_status
        self.retry_after_seconds = retry_after_seconds

    def to_dict(self) -> dict:
        return {
            "error_class": self.error_class,
            "retryable": self.retryable,
            "http_status": self.http_status,
            "message": str(self),
        }


class DeliveryBlockedError(DeliveryError):
    """The draft is not allowed to leave. Never a network problem."""

    error_class = ERROR_BLOCKED
    retryable = False


class InvalidDeliveryPayloadError(DeliveryError):
    """The contract failed validation before any request was made."""

    error_class = ERROR_INVALID_PAYLOAD
    retryable = False


class DeliveryConfigurationError(DeliveryError):
    """Missing or malformed destination configuration."""

    error_class = ERROR_CONFIGURATION
    retryable = False


class DeliveryAuthenticationError(DeliveryError):
    """401. Retrying with the same credentials is pointless."""

    error_class = ERROR_AUTHENTICATION
    retryable = False


class DeliveryPermissionError(DeliveryError):
    """403."""

    error_class = ERROR_PERMISSION
    retryable = False


class DeliveryNotFoundError(DeliveryError):
    """404 — wrong endpoint or collection. A contract problem, not a blip."""

    error_class = ERROR_NOT_FOUND
    retryable = False


class DeliveryConflictError(DeliveryError):
    """409 — the destination says this already exists.

    Not retryable, but not a plain failure either: it is a strong hint that a
    previous attempt landed, so the caller should reconcile rather than give up.
    """

    error_class = ERROR_CONFLICT
    retryable = False


class DeliveryRateLimitedError(DeliveryError):
    """429. Retryable, and the destination may have told us how long to wait."""

    error_class = ERROR_RATE_LIMITED
    retryable = True


class DeliveryTimeoutError(DeliveryError):
    """The request did not complete in time.

    Ambiguous by nature: the destination may still have created the draft.
    """

    error_class = ERROR_TIMEOUT
    retryable = True


class DeliveryConnectionError(DeliveryError):
    """The connection failed or dropped."""

    error_class = ERROR_CONNECTION
    retryable = True


class DeliveryServerError(DeliveryError):
    """5xx from the destination."""

    error_class = ERROR_SERVER
    retryable = True


class InvalidRemoteResponseError(DeliveryError):
    """The destination answered, but not with something we can trust.

    Unparseable body, or a success status with no usable remote id. Not
    retryable: repeating the request would not make the answer better, and the
    draft may already exist remotely.
    """

    error_class = ERROR_INVALID_RESPONSE
    retryable = False


_STATUS_TO_ERROR = {
    400: InvalidDeliveryPayloadError,
    401: DeliveryAuthenticationError,
    403: DeliveryPermissionError,
    404: DeliveryNotFoundError,
    405: DeliveryNotFoundError,
    408: DeliveryTimeoutError,
    409: DeliveryConflictError,
    410: DeliveryNotFoundError,
    422: InvalidDeliveryPayloadError,
    429: DeliveryRateLimitedError,
}


def error_for_status(
    status: int,
    message: str,
    *,
    retry_after_seconds: Optional[float] = None,
) -> DeliveryError:
    """Map an HTTP status onto a typed, classified error."""
    status = int(status)
    factory = _STATUS_TO_ERROR.get(status)
    if factory is not None:
        return factory(message, http_status=status, retry_after_seconds=retry_after_seconds)
    if 500 <= status <= 599:
        return DeliveryServerError(
            message, http_status=status, retry_after_seconds=retry_after_seconds
        )
    if 400 <= status <= 499:
        return InvalidDeliveryPayloadError(message, http_status=status)
    return InvalidRemoteResponseError(message, http_status=status)


def is_retryable_status(status: Optional[int]) -> bool:
    return status is not None and int(status) in RETRYABLE_STATUS_CODES


def classify(error: BaseException) -> str:
    """The error class of any exception, for persistence and logs."""
    if isinstance(error, DeliveryError):
        return error.error_class
    return ERROR_UNKNOWN


__all__ = [
    "DeliveryAuthenticationError",
    "DeliveryBlockedError",
    "DeliveryConfigurationError",
    "DeliveryConflictError",
    "DeliveryConnectionError",
    "DeliveryError",
    "DeliveryNotFoundError",
    "DeliveryPermissionError",
    "DeliveryRateLimitedError",
    "DeliveryServerError",
    "DeliveryTimeoutError",
    "ERROR_AUTHENTICATION",
    "ERROR_BLOCKED",
    "ERROR_CLASSES",
    "ERROR_CONFIGURATION",
    "ERROR_CONFLICT",
    "ERROR_CONNECTION",
    "ERROR_INVALID_PAYLOAD",
    "ERROR_INVALID_RESPONSE",
    "ERROR_NOT_FOUND",
    "ERROR_PERMISSION",
    "ERROR_RATE_LIMITED",
    "ERROR_SERVER",
    "ERROR_TIMEOUT",
    "ERROR_UNKNOWN",
    "InvalidDeliveryPayloadError",
    "InvalidRemoteResponseError",
    "PERMANENT_STATUS_CODES",
    "RETRYABLE_ERROR_CLASSES",
    "RETRYABLE_STATUS_CODES",
    "classify",
    "error_for_status",
    "is_retryable_status",
]
