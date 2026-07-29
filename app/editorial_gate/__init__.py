"""Editorial Gate: a deterministic, versioned, explainable classifier.

The gate answers one question about an ``EditorialDraft``: *is this worth a
human's attention, and what should they look at first?* It answers with three
outcomes — ``GATE_CLEAR``, ``GATE_REVIEW_REQUIRED``, ``GATE_BLOCKED``.

It is emphatically **not** an approver. ``GATE_CLEAR`` means "no deterministic
objection was found", not "may be published". Editorial authority stays human
and lives in the CMS. The publication vocabulary (``APPROVED``, ``PUBLISHED``,
``PUBLICATION_READY``) does not exist here and is rejected on assignment.

A blocked draft is still written to disk: blocking stops a *future external
submission*, never the local preservation an operator needs in order to see
what went wrong.
"""

from .engine import EditorialGate, compute_outcome, disabled_result, evaluate_draft
from .errors import (
    EditorialGateError,
    GateResultNotFoundError,
    InconsistentGateResultError,
    InvalidPolicyError,
    PolicyNotFoundError,
    UnknownRuleError,
)
from .models import (
    GATE_BLOCKED,
    GATE_CLEAR,
    GATE_DISABLED,
    GATE_OUTCOMES,
    GATE_REVIEW_REQUIRED,
    GATE_SEVERITIES,
    OUTCOMES_ELIGIBLE_FOR_SUBMISSION,
    POLICY_VERSION_V1,
    SEVERITY_BLOCKING,
    SEVERITY_INFO,
    SEVERITY_WARNING,
    EditorialGateResult,
    EditorialRuleResult,
    build_gate_id,
    validate_gate_outcome,
    validate_gate_severity,
)
from .policy import EditorialGatePolicy, available_policies, load_policy
from .rules import ALL_RULES, BLOCKING_RULES, INFO_RULES, WARNING_RULES
from .serialization import gate_canonical_json

__all__ = [
    "ALL_RULES",
    "BLOCKING_RULES",
    "EditorialGate",
    "EditorialGateError",
    "EditorialGatePolicy",
    "EditorialGateResult",
    "EditorialRuleResult",
    "GATE_BLOCKED",
    "GATE_CLEAR",
    "GATE_DISABLED",
    "GATE_OUTCOMES",
    "GATE_REVIEW_REQUIRED",
    "GATE_SEVERITIES",
    "GateResultNotFoundError",
    "INFO_RULES",
    "InconsistentGateResultError",
    "InvalidPolicyError",
    "OUTCOMES_ELIGIBLE_FOR_SUBMISSION",
    "POLICY_VERSION_V1",
    "PolicyNotFoundError",
    "SEVERITY_BLOCKING",
    "SEVERITY_INFO",
    "SEVERITY_WARNING",
    "UnknownRuleError",
    "WARNING_RULES",
    "available_policies",
    "build_gate_id",
    "compute_outcome",
    "disabled_result",
    "evaluate_draft",
    "gate_canonical_json",
    "load_policy",
    "validate_gate_outcome",
    "validate_gate_severity",
]
