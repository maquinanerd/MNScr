"""Dominio do Editorial Gate: severidades, outcomes, hash e politica.

Nenhum teste aqui acessa rede, RSS Prime, Gemini ou Payload.
"""

import json
import socket
from pathlib import Path

import pytest

from app.editorial.states import ForbiddenStateError
from app.editorial_gate import (
    GATE_BLOCKED,
    GATE_CLEAR,
    GATE_DISABLED,
    GATE_REVIEW_REQUIRED,
    SEVERITY_BLOCKING,
    SEVERITY_INFO,
    SEVERITY_WARNING,
    EditorialGateResult,
    EditorialRuleResult,
    build_gate_id,
    compute_outcome,
    validate_gate_outcome,
    validate_gate_severity,
)
from app.editorial_gate.engine import EditorialGate, _assert_consistent
from app.editorial_gate.errors import (
    InconsistentGateResultError,
    InvalidPolicyError,
    PolicyNotFoundError,
    UnknownRuleError,
)
from app.editorial_gate.models import MAX_EVIDENCE_LENGTH, sanitize_evidence
from app.editorial_gate.policy import EditorialGatePolicy, load_policy

FIXTURES = Path(__file__).parent / "fixtures" / "editorial_gate"
POLICY_DIR = "config/editorial_gate"
POLICY_V1 = "mnscr-editorial-gate-v1"


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _blocked(*args, **kwargs):
        raise AssertionError("Teste do Editorial Gate tentou acessar a rede")

    monkeypatch.setattr(socket, "socket", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)


def rule(code="GATE_X", severity=SEVERITY_INFO, triggered=False, **kw):
    return EditorialRuleResult(
        rule_code=code, rule_version="1", severity=severity, triggered=triggered, **kw
    )


# ---------------------------------------------------------------------------
# Severidades e outcomes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("severity", ["INFO", "WARNING", "BLOCKING"])
def test_valid_severities_are_accepted(severity):
    assert validate_gate_severity(severity) == severity


@pytest.mark.parametrize("severity", ["CRITICAL", "", "fatal", None])
def test_invalid_severities_are_rejected(severity):
    with pytest.raises(ValueError):
        validate_gate_severity(severity)


@pytest.mark.parametrize(
    "outcome", [GATE_CLEAR, GATE_REVIEW_REQUIRED, GATE_BLOCKED, GATE_DISABLED]
)
def test_valid_outcomes_are_accepted(outcome):
    assert validate_gate_outcome(outcome) == outcome


@pytest.mark.parametrize("forbidden", ["APPROVED", "PUBLISHED", "PUBLICATION_READY"])
def test_publication_vocabulary_is_rejected_as_an_outcome(forbidden):
    """O gate classifica; nunca aprova."""
    with pytest.raises(ForbiddenStateError):
        validate_gate_outcome(forbidden)


def test_unknown_outcome_is_rejected():
    with pytest.raises(ValueError):
        validate_gate_outcome("GATE_MAYBE")


# ---------------------------------------------------------------------------
# Calculo do outcome
# ---------------------------------------------------------------------------


def test_no_rules_triggered_is_clear():
    assert compute_outcome([rule(triggered=False)]) == GATE_CLEAR


def test_warning_without_blocking_requires_review():
    rules = [rule("A", SEVERITY_WARNING, True), rule("B", SEVERITY_INFO, True)]
    assert compute_outcome(rules) == GATE_REVIEW_REQUIRED


def test_one_blocking_rule_blocks():
    assert compute_outcome([rule("A", SEVERITY_BLOCKING, True)]) == GATE_BLOCKED


def test_blocking_always_wins_over_warning():
    rules = [rule("A", SEVERITY_WARNING, True), rule("B", SEVERITY_BLOCKING, True)]
    assert compute_outcome(rules) == GATE_BLOCKED


def test_info_never_changes_the_outcome():
    rules = [rule("A", SEVERITY_INFO, True), rule("B", SEVERITY_INFO, True)]
    assert compute_outcome(rules) == GATE_CLEAR


def test_untriggered_blocking_rule_does_not_block():
    assert compute_outcome([rule("A", SEVERITY_BLOCKING, False)]) == GATE_CLEAR


# ---------------------------------------------------------------------------
# Invariante interna
# ---------------------------------------------------------------------------


def test_clear_with_a_blocking_rule_is_rejected():
    with pytest.raises(InconsistentGateResultError):
        _assert_consistent(GATE_CLEAR, [rule("A", SEVERITY_BLOCKING, True)])


def test_clear_with_a_warning_is_rejected():
    with pytest.raises(InconsistentGateResultError):
        _assert_consistent(GATE_CLEAR, [rule("A", SEVERITY_WARNING, True)])


def test_review_required_with_a_blocking_rule_is_rejected():
    with pytest.raises(InconsistentGateResultError):
        _assert_consistent(GATE_REVIEW_REQUIRED, [rule("A", SEVERITY_BLOCKING, True)])


def test_blocked_without_a_blocking_rule_is_rejected():
    with pytest.raises(InconsistentGateResultError):
        _assert_consistent(GATE_BLOCKED, [rule("A", SEVERITY_WARNING, True)])


# ---------------------------------------------------------------------------
# Serializacao
# ---------------------------------------------------------------------------


def test_rule_result_serializes():
    payload = rule("GATE_X", SEVERITY_WARNING, True, message="m", evidence=["e"]).to_dict()
    assert payload["rule_code"] == "GATE_X"
    assert payload["severity"] == SEVERITY_WARNING
    assert payload["evidence"] == ["e"]


def test_rule_result_requires_a_code():
    with pytest.raises(ValueError):
        rule(code="")


def test_gate_result_serializes_and_round_trips():
    original = EditorialGateResult(
        draft_id="draft-1", policy_version=POLICY_V1, outcome=GATE_REVIEW_REQUIRED,
        rules=[rule("GATE_X", SEVERITY_WARNING, True)], event_key="evt-1", revision=2,
    )
    restored = EditorialGateResult.from_dict(original.to_dict())
    assert restored.gate_hash == original.gate_hash
    assert restored.gate_id == original.gate_id
    assert restored.outcome == original.outcome
    assert restored.revision == 2


def test_gate_result_counts_by_severity():
    result = EditorialGateResult(
        draft_id="d", policy_version=POLICY_V1, outcome=GATE_BLOCKED,
        rules=[
            rule("A", SEVERITY_BLOCKING, True),
            rule("B", SEVERITY_WARNING, True),
            rule("C", SEVERITY_INFO, True),
            rule("D", SEVERITY_BLOCKING, False),
        ],
    )
    assert (result.blocking_count, result.warning_count, result.info_count) == (1, 1, 1)


def test_gate_result_requires_a_draft_id():
    with pytest.raises(ValueError):
        EditorialGateResult(draft_id="", policy_version=POLICY_V1, outcome=GATE_CLEAR)


# ---------------------------------------------------------------------------
# Determinismo
# ---------------------------------------------------------------------------


def _result(**kw):
    base = dict(
        draft_id="draft-1", policy_version=POLICY_V1, outcome=GATE_REVIEW_REQUIRED,
        rules=[rule("GATE_X", SEVERITY_WARNING, True, message="original", evidence=["ev"])],
        event_key="evt-1", revision=1,
    )
    base.update(kw)
    return EditorialGateResult(**base)


def test_gate_hash_is_deterministic():
    assert _result().gate_hash == _result().gate_hash


def test_gate_hash_is_sha256():
    value = _result().gate_hash
    assert len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def test_gate_hash_excludes_the_timestamp():
    a = _result(evaluated_at="2020-01-01T00:00:00Z")
    b = _result(evaluated_at="2030-12-31T23:59:59Z")
    assert a.gate_hash == b.gate_hash


def test_gate_hash_excludes_the_human_message():
    """Reescrever uma mensagem nao pode parecer uma avaliacao diferente."""
    a = _result(rules=[rule("GATE_X", SEVERITY_WARNING, True, message="texto A", evidence=["ev"])])
    b = _result(rules=[rule("GATE_X", SEVERITY_WARNING, True, message="texto B", evidence=["ev"])])
    assert a.gate_hash == b.gate_hash


def test_gate_hash_changes_with_the_policy_version():
    assert _result().gate_hash != _result(policy_version="outra-politica").gate_hash


def test_gate_hash_changes_when_a_rule_triggers_differently():
    a = _result(rules=[rule("GATE_X", SEVERITY_WARNING, True)])
    b = _result(rules=[rule("GATE_X", SEVERITY_WARNING, False)], outcome=GATE_CLEAR)
    assert a.gate_hash != b.gate_hash


def test_gate_hash_changes_with_the_evidence():
    a = _result(rules=[rule("GATE_X", SEVERITY_WARNING, True, evidence=["ev-a"])])
    b = _result(rules=[rule("GATE_X", SEVERITY_WARNING, True, evidence=["ev-b"])])
    assert a.gate_hash != b.gate_hash


def test_rule_order_does_not_change_the_hash():
    r1 = rule("GATE_A", SEVERITY_WARNING, True)
    r2 = rule("GATE_B", SEVERITY_INFO, True)
    assert _result(rules=[r1, r2]).gate_hash == _result(rules=[r2, r1]).gate_hash


def test_gate_id_is_deterministic():
    assert _result().gate_id == _result().gate_id


def test_gate_id_derives_from_draft_policy_and_hash():
    result = _result()
    assert result.gate_id == build_gate_id(
        draft_id=result.draft_id,
        policy_version=result.policy_version,
        gate_hash=result.gate_hash,
    )


def test_gate_id_changes_with_the_policy():
    assert _result().gate_id != _result(policy_version="outra").gate_id


# ---------------------------------------------------------------------------
# Evidencia
# ---------------------------------------------------------------------------


def test_evidence_is_truncated_so_it_never_reproduces_the_source():
    long_text = "x" * (MAX_EVIDENCE_LENGTH * 3)
    assert len(sanitize_evidence([long_text])[0]) <= MAX_EVIDENCE_LENGTH + 1


def test_evidence_collapses_newlines():
    """Uma evidencia multilinha nao pode forjar linhas extras de log."""
    assert sanitize_evidence(["linha 1\nlinha 2\r\nlinha 3"]) == ["linha 1 linha 2 linha 3"]


def test_evidence_drops_empty_entries():
    assert sanitize_evidence(["", "   ", "ok"]) == ["ok"]


def test_evidence_accepts_a_bare_string():
    assert sanitize_evidence("apenas uma") == ["apenas uma"]


# ---------------------------------------------------------------------------
# Politica versionada
# ---------------------------------------------------------------------------


def test_policy_v1_loads():
    policy = load_policy(POLICY_V1, POLICY_DIR)
    assert policy.policy_version == POLICY_V1
    assert policy.enabled_rules


def test_unknown_policy_is_rejected_without_fallback():
    with pytest.raises(PolicyNotFoundError) as exc:
        load_policy("mnscr-editorial-gate-v999", POLICY_DIR)
    assert "v999" in str(exc.value)


def test_policy_version_must_match_the_filename(tmp_path):
    path = tmp_path / "minha-politica.json"
    path.write_text(json.dumps({"policyVersion": "outra", "enabledRules": ["GATE_MISSING_TITLE"]}),
                    encoding="utf-8")
    with pytest.raises(InvalidPolicyError):
        load_policy("minha-politica", str(tmp_path))


def test_policy_without_rules_is_rejected(tmp_path):
    path = tmp_path / "vazia.json"
    path.write_text(json.dumps({"policyVersion": "vazia", "enabledRules": []}), encoding="utf-8")
    with pytest.raises(InvalidPolicyError):
        load_policy("vazia", str(tmp_path))


def test_malformed_policy_json_is_rejected(tmp_path):
    path = tmp_path / "quebrada.json"
    path.write_text("{isto nao e json", encoding="utf-8")
    with pytest.raises(InvalidPolicyError):
        load_policy("quebrada", str(tmp_path))


def test_policy_enabling_an_unknown_rule_is_rejected():
    policy = EditorialGatePolicy(policy_version="teste", enabled_rules=["GATE_NAO_EXISTE"])
    with pytest.raises(UnknownRuleError):
        EditorialGate(policy=policy)


def test_trusted_domains_come_from_the_existing_allowlist():
    """A politica migra a allowlist atual; nao inventa veiculos novos."""
    from app.superfeed_policy import TRUSTED_SINGLE_SOURCES

    policy = load_policy(POLICY_V1, POLICY_DIR)
    assert set(policy.trusted_single_source_domains) == set(TRUSTED_SINGLE_SOURCES)


def test_policy_matches_trusted_domain_by_substring():
    policy = load_policy(POLICY_V1, POLICY_DIR)
    assert policy.is_trusted_domain("variety.com")
    assert policy.is_trusted_domain("www.variety.com")
    assert not policy.is_trusted_domain("blog-desconhecido.example")
    assert not policy.is_trusted_domain(None)


def test_policy_file_has_no_secrets():
    text = (Path(POLICY_DIR) / f"{POLICY_V1}.json").read_text(encoding="utf-8")
    for marker in ("token", "password", "secret", "AIza", "Bearer"):
        assert marker.lower() not in text.lower()


def test_every_implemented_rule_is_enabled_in_v1():
    from app.editorial_gate.rules import ALL_RULES

    policy = load_policy(POLICY_V1, POLICY_DIR)
    assert set(policy.enabled_rules) == set(ALL_RULES)
