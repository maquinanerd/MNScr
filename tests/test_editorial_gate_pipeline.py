"""Integracao do Editorial Gate: draft, submitters, feed legado e CLI.

O ponto central: um draft bloqueado continua sendo salvo localmente. O gate
impede uma submissao externa futura, nunca a preservacao local que o operador
precisa para entender o que aconteceu.
"""

import json
import socket
from pathlib import Path

import pytest

from app.editorial import (
    DRAFT_GENERATED,
    DraftContent,
    DraftProvenance,
    EditorialDraft,
    EvidenceRecord,
    SourceReference,
)
from app.editorial_gate import (
    GATE_BLOCKED,
    GATE_CLEAR,
    GATE_DISABLED,
    GATE_REVIEW_REQUIRED,
    OUTCOMES_ELIGIBLE_FOR_SUBMISSION,
    EditorialGate,
    disabled_result,
)
from app.editorial_gate.policy import load_policy
from app.submitters import LocalDraftSubmitter, PayloadConfig, PayloadDraftSubmitter
from app.submitters.payload import GATE_REJECTION_MESSAGE

POLICY_DIR = "config/editorial_gate"
POLICY_V1 = "mnscr-editorial-gate-v1"
FIXTURES = Path(__file__).parent / "fixtures" / "editorial_gate"
LONG_BODY = "<p>" + " ".join(["palavra"] * 420) + "</p>"


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _blocked(*args, **kwargs):
        raise AssertionError("Teste de integracao do gate tentou acessar a rede")

    monkeypatch.setattr(socket, "socket", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)


@pytest.fixture
def policy():
    return load_policy(POLICY_V1, POLICY_DIR)


def make_draft(**overrides) -> EditorialDraft:
    content_kw = overrides.pop("content_kw", {})
    provenance_kw = overrides.pop("provenance_kw", {})
    base = dict(
        draft_id="draft-1", article_id=1,
        content=DraftContent(**{
            "title": "Estudio confirma a data de estreia da nova fase",
            "body_html": LONG_BODY,
            "subtitle": "Resumo editorial proprio, distinto do titulo e da meta.",
            "meta_description": "Meta description com o fato principal do acontecimento.",
            **content_kw,
        }),
        provenance=DraftProvenance(**{
            "input_hash": "a" * 64, "output_hash": "b" * 64,
            "input_contract_version": "rss-prime-event-v1",
            "input_event_key": "evt-1", "input_revision": 1,
            "prompt_version": "universal_prompt-ms1",
            **provenance_kw,
        }),
        event_key="evt-1", revision=1,
        sources=[
            SourceReference(url="https://variety.example/a", domain="variety.example",
                            is_primary=True, extraction_status="OK"),
            SourceReference(url="https://deadline.example/b", domain="deadline.example",
                            extraction_status="OK"),
        ],
        evidence=[
            EvidenceRecord(evidence_id="ev-1", source_url="https://variety.example/a",
                           claim="Estreia confirmada", status="observed"),
        ],
    )
    base.update(overrides)
    return EditorialDraft(**base)


# ---------------------------------------------------------------------------
# Anexacao ao draft
# ---------------------------------------------------------------------------


def test_gate_result_attaches_to_the_draft(policy):
    draft = make_draft()
    draft.editorial_gate = EditorialGate(policy=policy).evaluate(draft)
    assert draft.editorial_gate.draft_id == draft.draft_id


def test_gate_result_reaches_the_local_artifact(policy, tmp_path):
    draft = make_draft()
    draft.editorial_gate = EditorialGate(policy=policy).evaluate(draft)

    LocalDraftSubmitter(output_dir=tmp_path).submit(draft)
    payload = json.loads((tmp_path / "draft-1.json").read_text(encoding="utf-8"))

    assert payload["editorial_gate"]["outcome"] in (GATE_CLEAR, GATE_REVIEW_REQUIRED)
    assert payload["editorial_gate"]["gate_hash"]


def test_gate_does_not_change_the_content_hash(policy):
    """Mudar politica nao pode parecer mudanca de conteudo editorial."""
    draft = make_draft()
    before = draft.content_hash()
    draft.editorial_gate = EditorialGate(policy=policy).evaluate(draft)
    assert draft.content_hash() == before


def test_gate_does_not_change_the_output_hash(policy):
    draft = make_draft()
    before = draft.provenance.output_hash
    draft.editorial_gate = EditorialGate(policy=policy).evaluate(draft)
    assert draft.provenance.output_hash == before


def test_gate_hash_is_separate_from_the_output_hash(policy):
    draft = make_draft()
    result = EditorialGate(policy=policy).evaluate(draft)
    assert result.gate_hash != draft.provenance.output_hash


def test_draft_content_does_not_carry_gate_rules(policy):
    draft = make_draft()
    draft.editorial_gate = EditorialGate(policy=policy).evaluate(draft)
    content = draft.to_dict()["content"]
    assert "rules" not in content
    assert "outcome" not in content


def test_gate_never_marks_the_draft_as_approved(policy):
    draft = make_draft()
    draft.editorial_gate = EditorialGate(policy=policy).evaluate(draft)
    rendered = json.dumps(draft.to_dict(), default=str)
    for forbidden in ("APPROVED", "PUBLISHED", "PUBLICATION_READY"):
        assert forbidden not in rendered


# ---------------------------------------------------------------------------
# Draft bloqueado continua sendo salvo
# ---------------------------------------------------------------------------


def blocked_draft(policy) -> EditorialDraft:
    draft = make_draft()
    draft.content.title = ""
    draft.editorial_gate = EditorialGate(policy=policy).evaluate(draft)
    assert draft.editorial_gate.outcome == GATE_BLOCKED
    return draft


def test_blocked_draft_is_still_written_locally(policy, tmp_path):
    draft = blocked_draft(policy)
    result = LocalDraftSubmitter(output_dir=tmp_path).submit(draft)
    assert result.success is True
    assert (tmp_path / "draft-1.json").exists()


def test_blocked_draft_keeps_draft_generated_status(policy):
    """O draft foi gerado corretamente; o bloqueio e editorial, nao tecnico."""
    draft = blocked_draft(policy)
    assert draft.status == DRAFT_GENERATED


def test_blocked_draft_records_its_blocking_rules(policy, tmp_path):
    draft = blocked_draft(policy)
    LocalDraftSubmitter(output_dir=tmp_path).submit(draft)
    payload = json.loads((tmp_path / "draft-1.json").read_text(encoding="utf-8"))
    codes = [r["rule_code"] for r in payload["editorial_gate"]["rules"] if r["triggered"]]
    assert "GATE_MISSING_TITLE" in codes


def test_review_required_draft_is_written_with_its_warnings(policy, tmp_path):
    draft = make_draft(provenance_kw={"input_contract_version": "legacy-feed-input-v0"})
    draft.editorial_gate = EditorialGate(policy=policy).evaluate(draft)
    assert draft.editorial_gate.outcome == GATE_REVIEW_REQUIRED

    LocalDraftSubmitter(output_dir=tmp_path).submit(draft)
    payload = json.loads((tmp_path / "draft-1.json").read_text(encoding="utf-8"))
    codes = [r["rule_code"] for r in payload["editorial_gate"]["rules"] if r["triggered"]]
    assert "GATE_LEGACY_INPUT_CONTRACT" in codes
    assert draft.status == DRAFT_GENERATED


# ---------------------------------------------------------------------------
# Payload continua bloqueado, agora tambem pelo gate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("outcome_factory", ["blocked", "review", "none"])
def test_payload_rejects_drafts_that_did_not_clear_the_gate(policy, outcome_factory):
    """Mesmo com PAYLOAD_ENABLED=true, o gate barra antes de qualquer coisa."""
    draft = make_draft()
    if outcome_factory == "blocked":
        draft.content.title = ""
        draft.editorial_gate = EditorialGate(policy=policy).evaluate(draft)
    elif outcome_factory == "review":
        draft.provenance.input_contract_version = "legacy-feed-input-v0"
        draft.editorial_gate = EditorialGate(policy=policy).evaluate(draft)
    else:
        draft.editorial_gate = None

    submitter = PayloadDraftSubmitter(
        PayloadConfig(enabled=True, base_url="https://cms.example", api_token="tok")
    )
    result = submitter.submit(draft)

    assert result.success is False
    assert GATE_REJECTION_MESSAGE in result.error


def test_payload_rejects_a_draft_with_no_gate_verdict(policy):
    """A ausencia de opiniao nao e aprovacao."""
    draft = make_draft()
    result = PayloadDraftSubmitter(PayloadConfig(enabled=True)).submit(draft)
    assert result.success is False
    assert "GATE_NOT_EVALUATED" in result.error


def test_payload_still_blocked_even_for_a_clear_draft(policy):
    """GATE_CLEAR nao libera nada nesta fase: o contrato do Cinerie e que manda."""
    from app.submitters.base import SubmitterNotEnabledError
    from app.submitters.payload import NOT_ENABLED_MESSAGE

    draft = make_draft()
    draft.editorial_gate = EditorialGate(policy=policy).evaluate(draft)
    assert draft.editorial_gate.outcome == GATE_CLEAR, [
        r.rule_code for r in draft.editorial_gate.triggered_rules if not r.is_info
    ]

    submitter = PayloadDraftSubmitter(
        PayloadConfig(enabled=True, base_url="https://cms.example", api_token="tok")
    )
    with pytest.raises(SubmitterNotEnabledError) as exc:
        submitter.submit(draft)
    assert str(exc.value) == NOT_ENABLED_MESSAGE


def test_payload_never_makes_network_calls():
    source = Path("app/submitters/payload.py").read_text(encoding="utf-8")
    for forbidden in ("import requests", "import httpx", "urlopen", "socket"):
        assert forbidden not in source


def test_local_submitter_ignores_the_gate_outcome(policy, tmp_path):
    """Preservacao local nunca depende do veredito."""
    for outcome, mutate in (("blocked", lambda d: setattr(d.content, "title", "")),
                            ("clear", lambda d: None)):
        draft = make_draft(draft_id=f"draft-{outcome}")
        mutate(draft)
        draft.editorial_gate = EditorialGate(policy=policy).evaluate(draft)
        assert LocalDraftSubmitter(output_dir=tmp_path).submit(draft).success is True


# ---------------------------------------------------------------------------
# Gate desabilitado
# ---------------------------------------------------------------------------


def test_disabled_gate_records_gate_disabled():
    result = disabled_result(make_draft())
    assert result.outcome == GATE_DISABLED
    assert result.rules == []


def test_disabled_gate_never_enables_submission():
    """Desabilitar o gate nao pode virar atalho para submissao."""
    assert GATE_DISABLED not in OUTCOMES_ELIGIBLE_FOR_SUBMISSION
    assert disabled_result(make_draft()).eligible_for_submission is False


def test_disabled_gate_is_rejected_by_payload():
    draft = make_draft()
    draft.editorial_gate = disabled_result(draft)
    result = PayloadDraftSubmitter(PayloadConfig(enabled=True)).submit(draft)
    assert result.success is False
    assert GATE_DISABLED in result.error


def test_only_gate_clear_is_eligible_for_submission():
    assert OUTCOMES_ELIGIBLE_FOR_SUBMISSION == {GATE_CLEAR}


# ---------------------------------------------------------------------------
# Feed legado
# ---------------------------------------------------------------------------


def test_legacy_feed_draft_reaches_review_required_not_blocked(policy):
    """O feed atual precisa continuar produzindo drafts inspecionaveis."""
    draft = make_draft(provenance_kw={
        "input_contract_version": "legacy-feed-input-v0",
        "input_cursor": None,
        "input_revision": 1,
    })
    result = EditorialGate(policy=policy).evaluate(draft)
    assert result.outcome == GATE_REVIEW_REQUIRED
    assert result.blocking_count == 0


def test_legacy_absent_cursor_does_not_block(policy):
    draft = make_draft(provenance_kw={
        "input_contract_version": "legacy-feed-input-v0", "input_cursor": None,
    })
    assert EditorialGate(policy=policy).evaluate(draft).blocking_count == 0


def test_legacy_revision_one_does_not_block(policy):
    draft = make_draft(revision=1, provenance_kw={
        "input_contract_version": "legacy-feed-input-v0", "input_revision": 1,
    })
    assert EditorialGate(policy=policy).evaluate(draft).blocking_count == 0


def test_legacy_draft_is_saved_locally(policy, tmp_path):
    draft = make_draft(provenance_kw={"input_contract_version": "legacy-feed-input-v0"})
    draft.editorial_gate = EditorialGate(policy=policy).evaluate(draft)
    assert LocalDraftSubmitter(output_dir=tmp_path).submit(draft).success is True


# ---------------------------------------------------------------------------
# Contrato v1
# ---------------------------------------------------------------------------


def test_v1_draft_can_reach_gate_clear(policy):
    draft = make_draft()
    result = EditorialGate(policy=policy).evaluate(draft)
    triggered = [r.rule_code for r in result.triggered_rules if not r.is_info]
    assert result.outcome == GATE_CLEAR, triggered


def test_v1_provenance_appears_in_the_gate_metrics(policy):
    result = EditorialGate(policy=policy).evaluate(make_draft())
    assert result.metrics["GATE_V1_INPUT_CONTRACT.input_contract_version"] == "rss-prime-event-v1"


def test_revision_gap_from_ingestion_is_only_a_warning(policy):
    draft = make_draft(warnings=["REVISION_GAP: revisoes 3,4 ausentes"])
    result = EditorialGate(policy=policy).evaluate(draft)
    assert result.outcome == GATE_REVIEW_REQUIRED
    assert result.blocking_count == 0


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fixture_name, expected",
    [
        ("draft_gate_clear.json", GATE_CLEAR),
        ("draft_gate_review_required.json", GATE_REVIEW_REQUIRED),
        ("draft_gate_blocked.json", GATE_BLOCKED),
    ],
)
def test_fixtures_produce_their_declared_outcome(policy, fixture_name, expected):
    from app.gate_service import draft_from_artifact

    payload = json.loads((FIXTURES / fixture_name).read_text(encoding="utf-8"))
    draft = draft_from_artifact(payload)
    result = EditorialGate(policy=policy).evaluate(draft)
    assert result.outcome == expected, [r.rule_code for r in result.triggered_rules if not r.is_info]


def test_policy_fixture_matches_the_shipped_policy():
    shipped = json.loads(Path(POLICY_DIR, f"{POLICY_V1}.json").read_text(encoding="utf-8"))
    fixture = json.loads((FIXTURES / "policy_gate_v1.json").read_text(encoding="utf-8"))
    assert fixture == shipped


def test_fixtures_use_example_domains():
    for name in ("draft_gate_clear.json", "draft_gate_review_required.json", "draft_gate_blocked.json"):
        text = (FIXTURES / name).read_text(encoding="utf-8")
        assert ".example" in text
