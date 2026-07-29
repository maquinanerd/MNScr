"""Errors raised by the Editorial Gate.

The gate never fails silently and never falls back to a default policy: an
unknown or malformed policy is a startup error, because evaluating drafts under
a policy nobody asked for is worse than not evaluating them at all.
"""

from __future__ import annotations

from typing import Optional


class EditorialGateError(RuntimeError):
    """Base class for every gate failure."""


class PolicyNotFoundError(EditorialGateError):
    """The configured policy version has no file on disk."""

    def __init__(self, policy_version: str, search_dir: Optional[str] = None):
        self.policy_version = policy_version
        self.search_dir = search_dir
        location = f" em '{search_dir}'" if search_dir else ""
        super().__init__(
            f"Politica do Editorial Gate '{policy_version}' nao encontrada{location}. "
            "Nenhum fallback silencioso e aplicado."
        )


class InvalidPolicyError(EditorialGateError):
    """The policy file exists but cannot be used as written."""

    def __init__(self, policy_version: str, reason: str):
        self.policy_version = policy_version
        self.reason = reason
        super().__init__(
            f"Politica '{policy_version}' invalida: {reason}"
        )


class UnknownRuleError(EditorialGateError):
    """The policy enables a rule the engine does not implement."""

    def __init__(self, rule_code: str, policy_version: str):
        self.rule_code = rule_code
        self.policy_version = policy_version
        super().__init__(
            f"Regra '{rule_code}' habilitada em '{policy_version}' nao existe no engine."
        )


class InconsistentGateResultError(EditorialGateError):
    """The computed outcome contradicts the rules that produced it.

    This is an internal invariant check, not a content problem. It exists so a
    future refactor cannot quietly make the gate return GATE_CLEAR for a draft
    that actually tripped a blocking rule.
    """


class GateResultNotFoundError(EditorialGateError):
    """No stored gate result for the requested draft."""

    def __init__(self, draft_id: str):
        self.draft_id = draft_id
        super().__init__(f"Nenhum resultado de gate registrado para o draft '{draft_id}'.")


__all__ = [
    "EditorialGateError",
    "GateResultNotFoundError",
    "InconsistentGateResultError",
    "InvalidPolicyError",
    "PolicyNotFoundError",
    "UnknownRuleError",
]
