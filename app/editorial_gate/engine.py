"""The Editorial Gate engine.

Runs the enabled rules against a draft and folds their severities into one
outcome. The fold is trivial on purpose — blocking beats warning beats clear —
and is then re-checked by :func:`_assert_consistent`, so no future change can
make the gate report GATE_CLEAR for a draft that tripped a blocking rule.

The engine performs no I/O: persistence and CLI live elsewhere.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from .errors import InconsistentGateResultError, UnknownRuleError
from .models import (
    GATE_BLOCKED,
    GATE_CLEAR,
    GATE_DISABLED,
    GATE_REVIEW_REQUIRED,
    EditorialGateResult,
    EditorialRuleResult,
)
from .policy import EditorialGatePolicy, load_policy
from .rules import ALL_RULES

logger = logging.getLogger(__name__)


def compute_outcome(rules: List[EditorialRuleResult]) -> str:
    """blocking → GATE_BLOCKED, else warning → GATE_REVIEW_REQUIRED, else clear.

    INFO results never affect the outcome.
    """
    if any(rule.is_blocking for rule in rules):
        return GATE_BLOCKED
    if any(rule.is_warning for rule in rules):
        return GATE_REVIEW_REQUIRED
    return GATE_CLEAR


def _assert_consistent(outcome: str, rules: List[EditorialRuleResult]) -> None:
    """Invariant guard: the outcome must match the rules that produced it."""
    has_blocking = any(rule.is_blocking for rule in rules)
    has_warning = any(rule.is_warning for rule in rules)

    if outcome == GATE_CLEAR and (has_blocking or has_warning):
        raise InconsistentGateResultError(
            "GATE_CLEAR com regra bloqueante ou de warning acionada: resultado invalido."
        )
    if outcome == GATE_REVIEW_REQUIRED and has_blocking:
        raise InconsistentGateResultError(
            "GATE_REVIEW_REQUIRED com regra bloqueante acionada: deveria ser GATE_BLOCKED."
        )
    if outcome == GATE_BLOCKED and not has_blocking:
        raise InconsistentGateResultError(
            "GATE_BLOCKED sem nenhuma regra bloqueante acionada: resultado invalido."
        )


class EditorialGate:
    """Evaluates drafts against a versioned policy."""

    def __init__(self, policy: Optional[EditorialGatePolicy] = None, *, policy_version: Optional[str] = None):
        self.policy = policy or load_policy(policy_version)
        unknown = [code for code in self.policy.enabled_rules if code not in ALL_RULES]
        if unknown:
            raise UnknownRuleError(unknown[0], self.policy.policy_version)

    @property
    def policy_version(self) -> str:
        return self.policy.policy_version

    def evaluate(
        self,
        draft: Any,
        *,
        context: Optional[Dict[str, Any]] = None,
    ) -> EditorialGateResult:
        """Run every enabled rule and fold the result.

        A rule that raises does not take the whole gate down: it is recorded as
        a blocking finding, because an unevaluable draft is exactly the case a
        human needs to look at.
        """
        ctx: Dict[str, Any] = dict(context or {})
        draft_id = getattr(draft, "draft_id", "") or ""
        event_key = getattr(draft, "event_key", None)
        revision = int(getattr(draft, "revision", 1) or 1)

        logger.info(
            "[GATE_STARTED] draft_id=%s event_key=%s revision=%s policy_version=%s",
            draft_id, event_key or "-", revision, self.policy_version,
        )

        results: List[EditorialRuleResult] = []
        for rule_code in self.policy.enabled_rules:
            rule_fn = ALL_RULES[rule_code]
            try:
                result = rule_fn(draft, self.policy, ctx)
            except Exception as exc:  # noqa: BLE001 - a broken rule must not hide the draft
                logger.exception(
                    "[GATE_RULE_TRIGGERED] draft_id=%s rule_code=%s severity=BLOCKING erro_interno",
                    draft_id, rule_code,
                )
                result = EditorialRuleResult(
                    rule_code=rule_code,
                    rule_version="error",
                    severity="BLOCKING",
                    triggered=True,
                    message="A regra falhou ao ser avaliada; o draft precisa de inspecao humana.",
                    evidence=[f"{type(exc).__name__}"],
                )
            results.append(result)
            if result.triggered:
                logger.info(
                    "[GATE_RULE_TRIGGERED] draft_id=%s event_key=%s revision=%s "
                    "policy_version=%s rule_code=%s severity=%s",
                    draft_id, event_key or "-", revision, self.policy_version,
                    result.rule_code, result.severity,
                )

        outcome = compute_outcome(results)
        _assert_consistent(outcome, results)

        gate_result = EditorialGateResult(
            draft_id=draft_id,
            policy_version=self.policy_version,
            outcome=outcome,
            rules=results,
            event_key=event_key,
            revision=revision,
            metrics=self._aggregate_metrics(results),
        )

        logger.info(
            "[GATE_COMPLETED] draft_id=%s event_key=%s revision=%s policy_version=%s "
            "outcome=%s blocking_count=%s warning_count=%s gate_hash=%s",
            draft_id, event_key or "-", revision, self.policy_version, outcome,
            gate_result.blocking_count, gate_result.warning_count, gate_result.gate_hash[:16],
        )
        logger.info(
            "[%s] draft_id=%s regras=%s",
            outcome,
            draft_id,
            ",".join(rule.rule_code for rule in gate_result.triggered_rules) or "-",
        )
        return gate_result

    @staticmethod
    def _aggregate_metrics(rules: List[EditorialRuleResult]) -> Dict[str, Any]:
        """Deterministic roll-up: only scalar metrics, sorted by rule code."""
        metrics: Dict[str, Any] = {}
        for rule in sorted(rules, key=lambda r: r.rule_code):
            for key, value in rule.metrics.items():
                if isinstance(value, (int, float, bool, str)) or value is None:
                    metrics[f"{rule.rule_code}.{key}"] = value
        return metrics


def disabled_result(draft: Any, policy_version: str = "disabled") -> EditorialGateResult:
    """The record written when the gate is deliberately switched off.

    Switching the gate off never grants anything: the outcome is GATE_DISABLED,
    which is not in ``OUTCOMES_ELIGIBLE_FOR_SUBMISSION``, so a disabled gate can
    never be used as a shortcut to submission.
    """
    logger.warning(
        "[GATE_COMPLETED] draft_id=%s outcome=%s motivo=gate_desabilitado",
        getattr(draft, "draft_id", "-"), GATE_DISABLED,
    )
    return EditorialGateResult(
        draft_id=getattr(draft, "draft_id", "") or "",
        policy_version=policy_version,
        outcome=GATE_DISABLED,
        rules=[],
        event_key=getattr(draft, "event_key", None),
        revision=int(getattr(draft, "revision", 1) or 1),
        summary="Editorial Gate desabilitado por configuracao; nenhuma regra foi avaliada.",
    )


def evaluate_draft(
    draft: Any,
    policy_version: Optional[str] = None,
    *,
    context: Optional[Dict[str, Any]] = None,
) -> EditorialGateResult:
    """Convenience entry point: load the policy and evaluate."""
    return EditorialGate(policy_version=policy_version).evaluate(draft, context=context)


__all__ = [
    "EditorialGate",
    "compute_outcome",
    "disabled_result",
    "evaluate_draft",
]
