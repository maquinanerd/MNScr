"""Contract detection and event validation.

Validation is *total*: it collects every issue it can find instead of raising on
the first one, so an operator sees the whole picture in one log line. The only
thing that raises is a payload MNScr cannot even classify.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional

from . import errors as E
from .hashing import calculate_event_payload_hash, is_sha256, normalize_datetime
from .rssprime_event_v1 import (
    CONTRACT_VERSION as V1_CONTRACT_VERSION,
)
from .rssprime_event_v1 import (
    HASH_ORIGIN_LEGACY,
    HASH_ORIGIN_VERIFIED,
    RssPrimeEventV1,
    domain_of,
    url_is_acceptable,
)
from .states import INVALID, VALIDATED

logger = logging.getLogger(__name__)

LEGACY_CONTRACT_VERSION = "legacy-feed-input-v0"

#: Contracts MNScr can read today. Anything else is rejected by name.
KNOWN_CONTRACT_VERSIONS = frozenset({V1_CONTRACT_VERSION, LEGACY_CONTRACT_VERSION})


@dataclass
class ValidationResult:
    """Outcome of validating one event payload."""

    event: Optional[RssPrimeEventV1]
    issues: List[E.ContractIssue] = field(default_factory=list)
    contract_version: str = ""

    @property
    def errors(self) -> List[E.ContractIssue]:
        return [issue for issue in self.issues if issue.is_blocking]

    @property
    def warnings(self) -> List[E.ContractIssue]:
        return [issue for issue in self.issues if not issue.is_blocking]

    @property
    def error_codes(self) -> List[str]:
        return E.codes(self.errors)

    @property
    def warning_codes(self) -> List[str]:
        return E.codes(self.warnings)

    @property
    def ok(self) -> bool:
        return self.event is not None and not self.errors

    @property
    def status(self) -> str:
        return VALIDATED if self.ok else INVALID

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "contract_version": self.contract_version,
            "errors": [issue.to_dict() for issue in self.errors],
            "warnings": [issue.to_dict() for issue in self.warnings],
        }


# --- Contract detection ----------------------------------------------------


def detect_contract_version(payload: Mapping[str, Any]) -> Optional[str]:
    """Read the declared contract version. ``None`` when nothing is declared.

    An undeclared version is not the same as an unknown one: undeclared means
    "possibly the legacy feed", unknown means "reject by name".
    """
    if not isinstance(payload, Mapping):
        return None
    declared = payload.get("contract_version") or payload.get("contractVersion")
    if declared is None:
        return None
    text = str(declared).strip()
    return text or None


def is_legacy_payload(payload: Mapping[str, Any]) -> bool:
    """True for an item that carries superfeed metadata but no contract version."""
    if detect_contract_version(payload):
        return False
    if not isinstance(payload, Mapping):
        return False
    legacy_markers = (
        "event_key",
        "has_superfeed_meta",
        "sf_event_key",
        "primary_source",
        "all_sources",
    )
    return any(payload.get(marker) for marker in legacy_markers)


# --- Field validators ------------------------------------------------------


def _validate_dates(event: RssPrimeEventV1, issues: List[E.ContractIssue]) -> None:
    parsed: Dict[str, Optional[datetime]] = {}
    for field_name in ("first_seen", "last_seen"):
        raw = getattr(event, field_name)
        if not raw:
            issues.append(
                E.error(E.INVALID_EVENT_DATES, f"{field_name} ausente", field_name=field_name)
            )
            parsed[field_name] = None
            continue
        normalized = normalize_datetime(raw)
        try:
            parsed[field_name] = datetime.fromisoformat(str(normalized).replace("Z", "+00:00"))
        except ValueError:
            issues.append(
                E.error(
                    E.INVALID_EVENT_DATES,
                    f"{field_name} nao e ISO 8601",
                    field_name=field_name,
                    value=str(raw)[:64],
                )
            )
            parsed[field_name] = None

    first, last = parsed.get("first_seen"), parsed.get("last_seen")
    if first and last and last < first:
        issues.append(
            E.error(
                E.INVALID_EVENT_DATES,
                "last_seen anterior a first_seen",
                field_name="last_seen",
            )
        )


def _validate_sources(event: RssPrimeEventV1, issues: List[E.ContractIssue]) -> None:
    if not event.sources:
        issues.append(E.error(E.INVALID_SOURCE_COUNT, "evento sem fontes", field_name="sources"))
        return

    seen_urls: Dict[str, int] = {}
    for index, source in enumerate(event.sources):
        if not url_is_acceptable(source.url):
            issues.append(
                E.error(
                    E.INVALID_SOURCE_URL,
                    "URL ausente ou fora de http/https",
                    field_name=f"sources[{index}].url",
                    value=str(source.url)[:120],
                )
            )
            continue
        if not source.domain:
            issues.append(
                E.error(
                    E.INVALID_SOURCE_DOMAIN,
                    "dominio obrigatorio",
                    field_name=f"sources[{index}].domain",
                )
            )
        elif domain_of(source.url) and source.domain.lower() != domain_of(source.url):
            issues.append(
                E.error(
                    E.INVALID_SOURCE_DOMAIN,
                    "dominio declarado nao corresponde a URL",
                    field_name=f"sources[{index}].domain",
                    declared=source.domain,
                    from_url=domain_of(source.url),
                )
            )
        if source.url in seen_urls:
            issues.append(
                E.error(
                    E.DUPLICATE_SOURCE_URL,
                    "URL repetida entre fontes",
                    field_name=f"sources[{index}].url",
                    first_index=seen_urls[source.url],
                )
            )
        else:
            seen_urls[source.url] = index

        if not source.author:
            issues.append(
                E.warning(
                    E.SOURCE_AUTHOR_UNKNOWN,
                    "autor nao informado pela fonte",
                    field_name=f"sources[{index}].author",
                )
            )
        if not source.published_at:
            issues.append(
                E.warning(
                    E.SOURCE_DATE_UNKNOWN,
                    "data nao informada pela fonte",
                    field_name=f"sources[{index}].published_at",
                )
            )

    primaries = [s for s in event.sources if s.is_primary]
    if not primaries:
        issues.append(
            E.error(E.NO_PRIMARY_SOURCE, "nenhuma fonte primaria", field_name="sources")
        )
    elif len(primaries) > 1:
        issues.append(
            E.error(
                E.MULTIPLE_PRIMARY_SOURCES,
                f"{len(primaries)} fontes marcadas como primarias",
                field_name="sources",
                count=len(primaries),
            )
        )


def _validate_counts(event: RssPrimeEventV1, issues: List[E.ContractIssue]) -> None:
    valid_sources = [s for s in event.sources if url_is_acceptable(s.url)]
    if event.source_count is None or event.source_count < 1:
        issues.append(
            E.error(
                E.INVALID_SOURCE_COUNT,
                "source_count deve ser >= 1",
                field_name="source_count",
                value=event.source_count,
            )
        )
    elif event.source_count != len(valid_sources):
        issues.append(
            E.error(
                E.INVALID_SOURCE_COUNT,
                "source_count nao corresponde ao numero de fontes validas",
                field_name="source_count",
                declared=event.source_count,
                actual=len(valid_sources),
            )
        )

    expected_multi = len(valid_sources) >= 2
    if bool(event.is_multi_source) != expected_multi:
        issues.append(
            E.error(
                E.INVALID_MULTI_SOURCE_FLAG,
                "is_multi_source nao corresponde a source_count >= 2",
                field_name="is_multi_source",
                declared=bool(event.is_multi_source),
                expected=expected_multi,
            )
        )


def _validate_confidence(event: RssPrimeEventV1, issues: List[E.ContractIssue]) -> None:
    value = event.cluster_confidence
    if value is None:
        issues.append(
            E.warning(
                E.MISSING_CLUSTER_CONFIDENCE,
                "cluster_confidence ausente",
                field_name="cluster_confidence",
            )
        )
        return
    if isinstance(value, float) and math.isnan(value):
        issues.append(
            E.error(
                E.INVALID_CLUSTER_CONFIDENCE,
                "cluster_confidence nao numerica",
                field_name="cluster_confidence",
            )
        )
        return
    if not (0.0 <= float(value) <= 1.0):
        issues.append(
            E.error(
                E.INVALID_CLUSTER_CONFIDENCE,
                "cluster_confidence fora de 0..1",
                field_name="cluster_confidence",
                value=float(value),
            )
        )


def _validate_topic(event: RssPrimeEventV1, issues: List[E.ContractIssue]) -> None:
    # Imported lazily so the contract package stays importable without config.
    from app.config import BLOCKED_TOPICS

    topic = (event.topic or "").strip()
    if not topic:
        issues.append(E.error(E.INVALID_TOPIC, "topic obrigatorio", field_name="topic"))
        return
    if topic.lower() in {t.lower() for t in BLOCKED_TOPICS}:
        issues.append(
            E.error(
                E.BLOCKED_TOPIC,
                f"topico '{topic}' bloqueado editorialmente",
                field_name="topic",
                topic=topic,
            )
        )


def _validate_hash(
    event: RssPrimeEventV1,
    issues: List[E.ContractIssue],
    *,
    expect_declared_hash: bool,
) -> None:
    recomputed = calculate_event_payload_hash(event)

    if not expect_declared_hash:
        # Legacy path: MNScr owns the hash and says so.
        event.payload_hash = recomputed
        event.payload_hash_origin = HASH_ORIGIN_LEGACY
        return

    declared = (event.payload_hash or "").strip().lower()
    if not declared:
        issues.append(
            E.error(E.INVALID_PAYLOAD_HASH, "payload_hash ausente", field_name="payload_hash")
        )
        return
    if not is_sha256(declared):
        issues.append(
            E.error(
                E.INVALID_PAYLOAD_HASH,
                "payload_hash nao e SHA-256 hexadecimal",
                field_name="payload_hash",
            )
        )
        return
    if declared != recomputed:
        issues.append(
            E.error(
                E.PAYLOAD_HASH_MISMATCH,
                "payload_hash declarado difere do recalculado",
                field_name="payload_hash",
                declared=declared,
                recomputed=recomputed,
            )
        )
        return
    event.payload_hash = declared
    event.payload_hash_origin = HASH_ORIGIN_VERIFIED


# --- Entry point -----------------------------------------------------------


def validate_event(
    event: RssPrimeEventV1,
    *,
    expect_declared_hash: bool = True,
    extra_issues: Optional[List[E.ContractIssue]] = None,
) -> ValidationResult:
    """Validate a normalized event against its contract rules."""
    issues: List[E.ContractIssue] = list(extra_issues or [])

    if event.contract_version not in KNOWN_CONTRACT_VERSIONS:
        issues.append(
            E.error(
                E.UNKNOWN_CONTRACT_VERSION,
                f"contrato '{event.contract_version}' desconhecido",
                field_name="contract_version",
                declared=event.contract_version,
            )
        )

    if not (event.event_key or "").strip():
        issues.append(E.error(E.INVALID_EVENT_KEY, "event_key vazio", field_name="event_key"))

    if event.revision is None or int(event.revision) < 1:
        issues.append(
            E.error(
                E.INVALID_REVISION,
                "revision deve ser >= 1",
                field_name="revision",
                value=event.revision,
            )
        )

    if not (event.title or "").strip():
        issues.append(E.error(E.INVALID_TITLE, "title obrigatorio", field_name="title"))

    _validate_topic(event, issues)
    _validate_dates(event, issues)
    _validate_sources(event, issues)
    _validate_counts(event, issues)
    _validate_confidence(event, issues)

    if not event.cursor:
        issues.append(
            E.warning(E.MISSING_CURSOR, "evento sem cursor", field_name="cursor")
        )

    # The hash is only meaningful over a structurally sound payload; a mismatch
    # reported on top of ten field errors is noise, not signal.
    structural_failure = any(
        issue.code
        in {
            E.INVALID_EVENT_KEY,
            E.INVALID_REVISION,
            E.INVALID_SOURCE_URL,
            E.INVALID_SOURCE_COUNT,
            E.INVALID_EVENT_DATES,
        }
        for issue in issues
        if issue.is_blocking
    )
    if not structural_failure:
        _validate_hash(event, issues, expect_declared_hash=expect_declared_hash)

    result = ValidationResult(
        event=event, issues=issues, contract_version=event.contract_version
    )
    logger.info(
        "[CONTRACT_VALIDATION] event_key=%s revision=%s contract=%s status=%s errors=%s warnings=%s",
        event.event_key or "-",
        event.revision,
        event.contract_version or "-",
        result.status,
        ",".join(result.error_codes) or "-",
        ",".join(result.warning_codes) or "-",
    )
    return result


def validate_payload(
    payload: Mapping[str, Any],
    *,
    accept_legacy: bool = True,
    required_contract: Optional[str] = None,
) -> ValidationResult:
    """Detect the contract, normalize, then validate.

    ``required_contract`` pins the accepted version; when set to the v1 name,
    legacy items are rejected with ``LEGACY_CONTRACT_DISABLED`` even if
    ``accept_legacy`` is true.
    """
    from .legacy_feed_v0 import LegacyFeedV0Adapter

    declared = detect_contract_version(payload)
    logger.info(
        "[CONTRACT_DETECTED] declared=%s legacy_markers=%s",
        declared or "-",
        is_legacy_payload(payload),
    )

    if declared == V1_CONTRACT_VERSION:
        event = RssPrimeEventV1.from_mapping(payload)
        return validate_event(event, expect_declared_hash=True)

    if declared and declared != LEGACY_CONTRACT_VERSION:
        event = RssPrimeEventV1.from_mapping(payload)
        return ValidationResult(
            event=event,
            contract_version=declared,
            issues=[
                E.error(
                    E.UNKNOWN_CONTRACT_VERSION,
                    f"contrato '{declared}' desconhecido",
                    field_name="contract_version",
                    declared=declared,
                )
            ],
        )

    # No v1 declaration: this is the legacy feed.
    if required_contract == V1_CONTRACT_VERSION or not accept_legacy:
        return ValidationResult(
            event=None,
            contract_version=LEGACY_CONTRACT_VERSION,
            issues=[
                E.error(
                    E.LEGACY_CONTRACT_DISABLED,
                    "compatibilidade com o feed legado esta desativada",
                    field_name="contract_version",
                )
            ],
        )

    return LegacyFeedV0Adapter().validate(payload)


__all__ = [
    "KNOWN_CONTRACT_VERSIONS",
    "LEGACY_CONTRACT_VERSION",
    "ValidationResult",
    "detect_contract_version",
    "is_legacy_payload",
    "validate_event",
    "validate_payload",
]
