"""HTTP adapter for the Payload CMS destination.

**This is the only module in the delivery path allowed to import an HTTP
client.** Everything above it — contract, builder, state machine, persistence —
is transport-free, which is what makes the rest of the delivery path testable
without a socket.

Scope honesty: the Payload CMS instance lives in the Screen-App repository, not
here. The request shape below is MNScr's *expectation* of the contract, exercised
against a local fake server. Nothing in this file has been validated against a
real Payload deployment, and the README says so. See ``docs/ms5-screen-app.md``
for the list of things Screen-App must confirm.

The class performs no I/O at import time and none at all while
``PAYLOAD_CMS_ENABLED`` is false.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional
from urllib.parse import urlsplit

import requests

from .contract import CMS_STATUS_DRAFT, EditorialDeliveryPayload
from .destination import DESTINATION_PAYLOAD_CMS, RemoteDraft
from .errors import (
    DeliveryConfigurationError,
    DeliveryConnectionError,
    DeliveryTimeoutError,
    InvalidRemoteResponseError,
    error_for_status,
)
from .retry import parse_retry_after

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS: float = 15.0
DEFAULT_COLLECTION: str = "editorial-drafts"

#: Keys a destination might use for the created document's id. Checked in order.
_REMOTE_ID_KEYS = ("id", "_id", "docId", "documentId")
#: Payload wraps the created document under "doc" in some versions.
_DOCUMENT_ENVELOPE_KEYS = ("doc", "document", "data", "result")


@dataclass(frozen=True)
class PayloadCmsConfig:
    """Runtime configuration for the Payload CMS destination."""

    enabled: bool = False
    base_url: Optional[str] = None
    draft_endpoint: Optional[str] = None
    token: Optional[str] = None
    collection: str = DEFAULT_COLLECTION
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS

    @classmethod
    def from_config(cls) -> "PayloadCmsConfig":
        from app import config

        return cls(
            enabled=bool(getattr(config, "PAYLOAD_CMS_ENABLED", False)),
            base_url=getattr(config, "PAYLOAD_CMS_BASE_URL", None) or None,
            draft_endpoint=getattr(config, "PAYLOAD_CMS_DRAFT_ENDPOINT", None) or None,
            token=getattr(config, "PAYLOAD_CMS_TOKEN", None) or None,
            collection=getattr(config, "PAYLOAD_CMS_COLLECTION", DEFAULT_COLLECTION)
            or DEFAULT_COLLECTION,
            timeout_seconds=float(
                getattr(config, "PAYLOAD_CMS_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS)
            ),
        )

    def issues(self) -> list[str]:
        """Configuration problems, checked before any request."""
        problems: list[str] = []
        if not self.enabled:
            return problems
        if not self.base_url:
            problems.append("PAYLOAD_CMS_BASE_URL ausente")
        elif urlsplit(self.base_url).scheme not in ("http", "https"):
            problems.append(f"PAYLOAD_CMS_BASE_URL invalida: {self.base_url!r}")
        if not self.token:
            problems.append("PAYLOAD_CMS_TOKEN ausente")
        if self.timeout_seconds <= 0:
            problems.append("PAYLOAD_CMS_TIMEOUT_SECONDS precisa ser positivo")
        return problems

    def __repr__(self) -> str:
        """Never render the token, not even in a traceback."""
        return (
            f"PayloadCmsConfig(enabled={self.enabled}, base_url={self.base_url!r}, "
            f"collection={self.collection!r}, timeout_seconds={self.timeout_seconds}, "
            f"token={'***' if self.token else None})"
        )


class PayloadCmsDestination:
    """Creates drafts in Payload CMS over HTTP.

    Only ever creates drafts: ``_status`` is pinned to ``draft`` in the request
    body and the payload's own ``cms_status`` is validated upstream. There is no
    publish path, and adding one would require changing the contract first.
    """

    name = DESTINATION_PAYLOAD_CMS

    def __init__(
        self,
        config: Optional[PayloadCmsConfig] = None,
        *,
        session: Optional[Any] = None,
    ):
        self.config = config or PayloadCmsConfig.from_config()
        problems = self.config.issues()
        if problems:
            raise DeliveryConfigurationError(
                "Configuracao do Payload CMS incompleta: " + "; ".join(problems)
            )
        # A session is injected in tests; created lazily otherwise so that
        # importing this module never opens anything.
        self._session = session
        self._owns_session = session is None

    # --- transport ---------------------------------------------------------

    @property
    def session(self):
        if self._session is None:
            self._session = requests.Session()
        return self._session

    def collection_url(self) -> str:
        if self.config.draft_endpoint:
            return self.config.draft_endpoint
        base = (self.config.base_url or "").rstrip("/")
        return f"{base}/api/{self.config.collection}"

    def _headers(self, idempotency_key: Optional[str] = None) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self.config.token:
            # Header shape is an assumption until Screen-App confirms it; kept in
            # one place so changing it is a one-line edit.
            headers["Authorization"] = f"Bearer {self.config.token}"
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        return headers

    def _request_body(self, payload: EditorialDeliveryPayload) -> Dict[str, Any]:
        body = payload.to_dict()
        # Payload's own draft flag. Pinned, never derived from input.
        body["_status"] = CMS_STATUS_DRAFT
        return body

    # --- destination protocol ---------------------------------------------

    def submit_draft(self, payload: EditorialDeliveryPayload) -> RemoteDraft:
        """Create the remote draft. Raises a typed ``DeliveryError`` on failure."""
        url = self.collection_url()
        idempotency_key = str(payload.metadata.get("idempotency_key") or payload.delivery_id)

        try:
            response = self.session.post(
                url,
                data=json.dumps(self._request_body(payload), ensure_ascii=False).encode("utf-8"),
                headers=self._headers(idempotency_key),
                timeout=self.config.timeout_seconds,
            )
        except requests.ConnectTimeout as exc:
            # Checked before Timeout: ConnectTimeout subclasses both. A timeout
            # while *connecting* means the request never arrived, so nothing was
            # created — unambiguous, and safe to retry cleanly. Only a read
            # timeout leaves the outcome unknown.
            raise DeliveryConnectionError(
                f"falha ao conectar em {_safe_url(url)}"
            ) from exc
        except requests.Timeout as exc:
            raise DeliveryTimeoutError(
                f"timeout aguardando resposta de {_safe_url(url)}"
            ) from exc
        except requests.ConnectionError as exc:
            raise DeliveryConnectionError(
                f"falha de conexao com {_safe_url(url)}"
            ) from exc
        except requests.RequestException as exc:
            raise DeliveryConnectionError(
                f"erro de transporte com {_safe_url(url)}: {type(exc).__name__}"
            ) from exc

        return self._interpret(response, url)

    def _interpret(self, response: Any, url: str) -> RemoteDraft:
        status = int(getattr(response, "status_code", 0) or 0)

        if status >= 400:
            retry_after = parse_retry_after(_header(response, "Retry-After"))
            raise error_for_status(
                status,
                f"destino respondeu {status} em {_safe_url(url)}",
                retry_after_seconds=retry_after,
            )

        if not (200 <= status < 300):
            raise InvalidRemoteResponseError(
                f"status inesperado {status} de {_safe_url(url)}", http_status=status
            )

        try:
            body = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise InvalidRemoteResponseError(
                f"resposta nao e JSON valido (status {status})", http_status=status
            ) from exc

        remote_id = _extract_remote_id(body)
        if not remote_id:
            # A 2xx with no usable id is worse than an error: the draft may exist
            # remotely but we cannot reference it. Never retried blindly.
            raise InvalidRemoteResponseError(
                f"resposta {status} sem identificador de draft remoto", http_status=status
            )

        return RemoteDraft(
            remote_id=str(remote_id),
            url=_extract_remote_url(body),
            raw={"status": status},
        )

    def find_existing_draft(self, delivery_id: str) -> Optional[RemoteDraft]:
        """Reconciliation lookup.

        Payload's query contract has not been confirmed by Screen-App, so this
        deliberately raises instead of guessing a query syntax. The caller then
        records ``DELIVERY_NEEDS_RECONCILIATION`` rather than assuming nothing
        was created — which is the honest outcome, not a silent success.
        """
        raise NotImplementedError(
            "Lookup por delivery_id ainda nao foi confirmado pelo Screen-App; "
            "a entrega fica em reconciliacao pendente em vez de recriar as cegas."
        )

    def close(self) -> None:
        if self._owns_session and self._session is not None:
            try:
                self._session.close()
            except Exception:  # noqa: BLE001 - fechar sessao nunca pode derrubar a entrega
                logger.debug("[delivery] falha ao fechar sessao HTTP", exc_info=True)
            self._session = None


def _header(response: Any, name: str) -> Optional[str]:
    headers = getattr(response, "headers", None) or {}
    try:
        return headers.get(name)
    except AttributeError:
        return None


def _extract_remote_id(body: Any) -> Optional[str]:
    """Find the created document id, tolerating common envelope shapes."""
    if not isinstance(body, dict):
        return None
    for envelope in _DOCUMENT_ENVELOPE_KEYS:
        nested = body.get(envelope)
        if isinstance(nested, dict):
            found = _extract_remote_id(nested)
            if found:
                return found
    for key in _REMOTE_ID_KEYS:
        value = body.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def _extract_remote_url(body: Any) -> Optional[str]:
    if not isinstance(body, dict):
        return None
    for envelope in _DOCUMENT_ENVELOPE_KEYS:
        nested = body.get(envelope)
        if isinstance(nested, dict):
            found = _extract_remote_url(nested)
            if found:
                return found
    for key in ("url", "adminUrl", "permalink"):
        value = body.get(key)
        if value:
            return str(value)
    return None


def _safe_url(url: str) -> str:
    """Scheme, host and path only — never a query string, which may carry a token."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return "<url invalida>"
    return f"{parts.scheme}://{parts.netloc}{parts.path}"


__all__ = [
    "DEFAULT_COLLECTION",
    "DEFAULT_TIMEOUT_SECONDS",
    "PayloadCmsConfig",
    "PayloadCmsDestination",
]
