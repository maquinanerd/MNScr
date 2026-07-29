"""Typed domain for the Editorial Gate.

The gate is a *technical classifier*, not an approver. Its three outcomes say
whether a draft is worth a human's time and what a human should look at first —
never that the content may be published. The publication vocabulary
(APPROVED / PUBLISHED / PUBLICATION_READY) does not exist here and is actively
rejected by :func:`validate_gate_outcome`.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from dataclasses import field as dc_field
from datetime import datetime, timezone
from typing import Any, Dict, Final, FrozenSet, List, Optional

from app.editorial.states import FORBIDDEN_DRAFT_STATES, ForbiddenStateError

# --- Severity --------------------------------------------------------------

SEVERITY_INFO: Final[str] = "INFO"
SEVERITY_WARNING: Final[str] = "WARNING"
SEVERITY_BLOCKING: Final[str] = "BLOCKING"

GATE_SEVERITIES: Final[FrozenSet[str]] = frozenset(
    {SEVERITY_INFO, SEVERITY_WARNING, SEVERITY_BLOCKING}
)

# --- Outcome ---------------------------------------------------------------

GATE_CLEAR: Final[str] = "GATE_CLEAR"
GATE_REVIEW_REQUIRED: Final[str] = "GATE_REVIEW_REQUIRED"
GATE_BLOCKED: Final[str] = "GATE_BLOCKED"

#: Recorded when the gate is deliberately switched off (development only).
GATE_DISABLED: Final[str] = "GATE_DISABLED"

GATE_OUTCOMES: Final[FrozenSet[str]] = frozenset(
    {GATE_CLEAR, GATE_REVIEW_REQUIRED, GATE_BLOCKED, GATE_DISABLED}
)

#: The only outcome that would ever let a draft move on to an external CMS —
#: and even then, MS-3 keeps that submission blocked for other reasons.
OUTCOMES_ELIGIBLE_FOR_SUBMISSION: Final[FrozenSet[str]] = frozenset({GATE_CLEAR})

POLICY_VERSION_V1: Final[str] = "mnscr-editorial-gate-v1"

#: Evidence excerpts are capped so a gate result can never become a copy of the
#: source article, and so an injection attempt cannot smuggle a long payload
#: into logs or artifacts.
MAX_EVIDENCE_LENGTH: Final[int] = 200
MAX_EVIDENCE_ITEMS: Final[int] = 10


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def validate_gate_severity(severity: str) -> str:
    normalized = (severity or "").strip().upper()
    if normalized not in GATE_SEVERITIES:
        raise ValueError(f"Severidade de gate desconhecida: '{severity}'")
    return normalized


def validate_gate_outcome(outcome: str) -> str:
    """Reject both unknown outcomes and the publication vocabulary."""
    normalized = (outcome or "").strip().upper()
    if normalized in FORBIDDEN_DRAFT_STATES:
        raise ForbiddenStateError(
            f"'{normalized}' e um estado de publicacao; o Editorial Gate nao aprova conteudo."
        )
    if normalized not in GATE_OUTCOMES:
        raise ValueError(f"Outcome de gate desconhecido: '{outcome}'")
    return normalized


def sanitize_evidence(items: Any) -> List[str]:
    """Normalize evidence into short, single-line, deterministic strings.

    Evidence must be concrete enough to act on and small enough that it never
    reproduces source material. Newlines are collapsed so a multi-line excerpt
    cannot forge extra log lines.
    """
    if items is None:
        return []
    if isinstance(items, (str, bytes)):
        items = [items]
    cleaned: List[str] = []
    for item in list(items)[:MAX_EVIDENCE_ITEMS]:
        text = " ".join(str(item).split())
        if not text:
            continue
        if len(text) > MAX_EVIDENCE_LENGTH:
            text = text[:MAX_EVIDENCE_LENGTH].rstrip() + "…"
        cleaned.append(text)
    return cleaned


@dataclass
class EditorialRuleResult:
    """The outcome of one rule against one draft."""

    rule_code: str
    rule_version: str
    severity: str
    triggered: bool
    message: str = ""
    evidence: List[str] = dc_field(default_factory=list)
    affected_fields: List[str] = dc_field(default_factory=list)
    remediation: Optional[str] = None
    metrics: Dict[str, Any] = dc_field(default_factory=dict)

    def __post_init__(self) -> None:
        self.rule_code = str(self.rule_code or "").strip()
        if not self.rule_code:
            raise ValueError("EditorialRuleResult.rule_code e obrigatorio")
        self.severity = validate_gate_severity(self.severity)
        self.triggered = bool(self.triggered)
        self.message = " ".join(str(self.message or "").split())
        self.evidence = sanitize_evidence(self.evidence)
        self.affected_fields = [str(f).strip() for f in (self.affected_fields or []) if str(f).strip()]
        self.remediation = (self.remediation or "").strip() or None

    @property
    def is_blocking(self) -> bool:
        return self.triggered and self.severity == SEVERITY_BLOCKING

    @property
    def is_warning(self) -> bool:
        return self.triggered and self.severity == SEVERITY_WARNING

    @property
    def is_info(self) -> bool:
        return self.triggered and self.severity == SEVERITY_INFO

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rule_code": self.rule_code,
            "rule_version": self.rule_version,
            "severity": self.severity,
            "triggered": self.triggered,
            "message": self.message,
            "evidence": list(self.evidence),
            "affected_fields": list(self.affected_fields),
            "remediation": self.remediation,
            "metrics": dict(self.metrics),
        }

    def to_hashable_dict(self) -> Dict[str, Any]:
        """Only what identifies the finding — never the message wording.

        Rewording a message must not look like a different evaluation.
        """
        return {
            "rule_code": self.rule_code,
            "rule_version": self.rule_version,
            "severity": self.severity,
            "triggered": self.triggered,
            "evidence": list(self.evidence),
            "affected_fields": sorted(self.affected_fields),
            "metrics": dict(self.metrics),
        }


@dataclass
class EditorialGateResult:
    """The full, explainable verdict for one draft under one policy."""

    draft_id: str
    policy_version: str
    outcome: str
    rules: List[EditorialRuleResult] = dc_field(default_factory=list)
    event_key: Optional[str] = None
    revision: int = 1
    gate_id: str = ""
    gate_hash: str = ""
    evaluated_at: str = dc_field(default_factory=_utc_now_iso)
    summary: str = ""
    metrics: Dict[str, Any] = dc_field(default_factory=dict)

    def __post_init__(self) -> None:
        self.draft_id = str(self.draft_id or "").strip()
        if not self.draft_id:
            raise ValueError("EditorialGateResult.draft_id e obrigatorio")
        self.outcome = validate_gate_outcome(self.outcome)
        self.revision = int(self.revision or 1)
        self.event_key = (self.event_key or "").strip() or None
        if not self.gate_hash:
            self.gate_hash = self.compute_gate_hash()
        if not self.gate_id:
            self.gate_id = build_gate_id(
                draft_id=self.draft_id,
                policy_version=self.policy_version,
                gate_hash=self.gate_hash,
            )
        if not self.summary:
            self.summary = self.build_summary()

    # --- Counters ----------------------------------------------------------

    @property
    def triggered_rules(self) -> List[EditorialRuleResult]:
        return [rule for rule in self.rules if rule.triggered]

    @property
    def blocking_rules(self) -> List[EditorialRuleResult]:
        return [rule for rule in self.rules if rule.is_blocking]

    @property
    def warning_rules(self) -> List[EditorialRuleResult]:
        return [rule for rule in self.rules if rule.is_warning]

    @property
    def info_rules(self) -> List[EditorialRuleResult]:
        return [rule for rule in self.rules if rule.is_info]

    @property
    def blocking_count(self) -> int:
        return len(self.blocking_rules)

    @property
    def warning_count(self) -> int:
        return len(self.warning_rules)

    @property
    def info_count(self) -> int:
        return len(self.info_rules)

    @property
    def is_blocked(self) -> bool:
        return self.outcome == GATE_BLOCKED

    @property
    def needs_human_review(self) -> bool:
        return self.outcome == GATE_REVIEW_REQUIRED

    @property
    def eligible_for_submission(self) -> bool:
        """Whether the *gate* objects. Other guards may still refuse."""
        return self.outcome in OUTCOMES_ELIGIBLE_FOR_SUBMISSION

    # --- Identity ----------------------------------------------------------

    def to_hashable_dict(self) -> Dict[str, Any]:
        """Exactly what identifies this evaluation.

        ``evaluated_at``, the summary and the human messages are excluded: the
        same draft under the same policy must hash the same tomorrow.
        """
        return {
            "draft_id": self.draft_id,
            "event_key": self.event_key,
            "revision": int(self.revision),
            "policy_version": self.policy_version,
            "outcome": self.outcome,
            "rules": [
                rule.to_hashable_dict()
                for rule in sorted(self.rules, key=lambda r: r.rule_code)
            ],
            "metrics": dict(self.metrics),
        }

    def compute_gate_hash(self) -> str:
        from .serialization import gate_canonical_json

        payload = gate_canonical_json(self.to_hashable_dict())
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def build_summary(self) -> str:
        codes = ", ".join(rule.rule_code for rule in self.triggered_rules) or "nenhuma regra acionada"
        return (
            f"{self.outcome} | bloqueios={self.blocking_count} "
            f"warnings={self.warning_count} info={self.info_count} | {codes}"
        )

    # --- Serialization -----------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "gate_id": self.gate_id,
            "draft_id": self.draft_id,
            "event_key": self.event_key,
            "revision": int(self.revision),
            "policy_version": self.policy_version,
            "outcome": self.outcome,
            "rules": [rule.to_dict() for rule in self.rules],
            "blocking_count": self.blocking_count,
            "warning_count": self.warning_count,
            "info_count": self.info_count,
            "gate_hash": self.gate_hash,
            "evaluated_at": self.evaluated_at,
            "summary": self.summary,
            "metrics": dict(self.metrics),
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "EditorialGateResult":
        rules = [
            EditorialRuleResult(
                rule_code=item.get("rule_code", ""),
                rule_version=item.get("rule_version", ""),
                severity=item.get("severity", SEVERITY_INFO),
                triggered=item.get("triggered", False),
                message=item.get("message", ""),
                evidence=item.get("evidence") or [],
                affected_fields=item.get("affected_fields") or [],
                remediation=item.get("remediation"),
                metrics=item.get("metrics") or {},
            )
            for item in (payload.get("rules") or [])
        ]
        return cls(
            draft_id=payload.get("draft_id", ""),
            policy_version=payload.get("policy_version", ""),
            outcome=payload.get("outcome", GATE_CLEAR),
            rules=rules,
            event_key=payload.get("event_key"),
            revision=payload.get("revision", 1),
            gate_id=payload.get("gate_id", ""),
            gate_hash=payload.get("gate_hash", ""),
            evaluated_at=payload.get("evaluated_at") or _utc_now_iso(),
            summary=payload.get("summary", ""),
            metrics=payload.get("metrics") or {},
        )


def build_gate_id(*, draft_id: str, policy_version: str, gate_hash: str) -> str:
    """Deterministic id from draft + policy + evaluation content."""
    seed = f"draft:{draft_id}|policy:{policy_version}|hash:{gate_hash}"
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]
    return f"gate-{digest}"


__all__ = [
    "EditorialGateResult",
    "EditorialRuleResult",
    "GATE_BLOCKED",
    "GATE_CLEAR",
    "GATE_DISABLED",
    "GATE_OUTCOMES",
    "GATE_REVIEW_REQUIRED",
    "GATE_SEVERITIES",
    "MAX_EVIDENCE_LENGTH",
    "OUTCOMES_ELIGIBLE_FOR_SUBMISSION",
    "POLICY_VERSION_V1",
    "SEVERITY_BLOCKING",
    "SEVERITY_INFO",
    "SEVERITY_WARNING",
    "build_gate_id",
    "sanitize_evidence",
    "validate_gate_outcome",
    "validate_gate_severity",
]
