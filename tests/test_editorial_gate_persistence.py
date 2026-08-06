"""Persistencia, historico e reavaliacao do Editorial Gate.

SQLite real em tmp_path. Sem rede, sem feed, sem IA, sem Payload.
"""

import json
import socket
from pathlib import Path

import pytest

from app.editorial import DraftContent, DraftProvenance, EditorialDraft, SourceReference
from app.editorial_gate import (
    GATE_BLOCKED,
    GATE_CLEAR,
    GATE_REVIEW_REQUIRED,
    SEVERITY_BLOCKING,
    SEVERITY_WARNING,
    EditorialGateResult,
    EditorialRuleResult,
)
from app.editorial_gate.errors import GateResultNotFoundError, PolicyNotFoundError
from app.factual.models import FactualAssessment, FactualCoverage
from app.gate_service import GateService, draft_from_artifact
from app.gate_store import GateStore

POLICY_V1 = "mnscr-editorial-gate-v1"
LONG_BODY = "<p>" + " ".join(["palavra"] * 420) + "</p>"


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _blocked(*args, **kwargs):
        raise AssertionError("Teste de persistencia do gate tentou acessar a rede")

    monkeypatch.setattr(socket, "socket", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)


@pytest.fixture
def store(tmp_path) -> GateStore:
    gate_store = GateStore(db_path=str(tmp_path / "app.db"))
    yield gate_store
    gate_store.close()


def make_draft(draft_id="draft-1", **overrides) -> EditorialDraft:
    base = dict(
        draft_id=draft_id, article_id=1,
        content=DraftContent(
            title="Estudio confirma a data de estreia da nova fase",
            body_html=LONG_BODY,
            subtitle="Resumo editorial proprio, distinto do titulo e da meta description.",
            meta_description="Meta description com o fato principal do acontecimento.",
        ),
        provenance=DraftProvenance(
            input_hash="a" * 64, output_hash="b" * 64,
            input_contract_version="rss-prime-event-v1",
            input_event_key="evt-1", input_revision=1,
            prompt_version="universal_prompt-ms1",
        ),
        event_key="evt-1", revision=1,
        sources=[
            SourceReference(url="https://variety.example/a", domain="variety.example",
                            is_primary=True, extraction_status="OK"),
            SourceReference(url="https://deadline.example/b", domain="deadline.example",
                            extraction_status="OK"),
        ],
    )
    base.update(overrides)
    return EditorialDraft(**base)


def make_result(draft_id="draft-1", outcome=GATE_REVIEW_REQUIRED, policy=POLICY_V1, **kw):
    rules = kw.pop("rules", [
        EditorialRuleResult(rule_code="GATE_EVIDENCE_MISSING", rule_version="1",
                            severity=SEVERITY_WARNING, triggered=True, message="sem evidencia"),
    ])
    return EditorialGateResult(
        draft_id=draft_id, policy_version=policy, outcome=outcome,
        rules=rules, event_key="evt-1", revision=1, **kw
    )


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def test_schema_is_created(store):
    cursor = store.conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    assert "editorial_gate_results" in {row["name"] for row in cursor.fetchall()}


def test_migration_is_idempotent(tmp_path):
    path = str(tmp_path / "app.db")
    for _ in range(3):
        GateStore(db_path=path).close()
    store = GateStore(db_path=path)
    cursor = store.conn.cursor()
    cursor.execute("PRAGMA table_info(editorial_gate_results)")
    names = [row["name"] for row in cursor.fetchall()]
    assert len(names) == len(set(names))
    store.close()


def test_required_indexes_exist(store):
    cursor = store.conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='index'")
    indexes = " ".join(row["name"] or "" for row in cursor.fetchall())
    for expected in ("draft_id", "event_revision", "outcome", "policy", "evaluated_at"):
        assert expected in indexes


def test_gate_id_is_unique(store):
    result = make_result()
    assert store.save_result(result) is not None
    assert store.save_result(result) is None


# ---------------------------------------------------------------------------
# Leitura
# ---------------------------------------------------------------------------


def test_result_round_trips_through_sqlite(store):
    original = make_result()
    store.save_result(original)
    loaded = store.get_latest_gate_result("draft-1")
    assert loaded.gate_id == original.gate_id
    assert loaded.gate_hash == original.gate_hash
    assert loaded.outcome == original.outcome
    assert [r.rule_code for r in loaded.rules] == [r.rule_code for r in original.rules]


def test_history_is_preserved_across_policies(store):
    store.save_result(make_result(policy="mnscr-editorial-gate-v1"))
    store.save_result(make_result(policy="mnscr-editorial-gate-v2"))
    history = store.list_gate_results("draft-1")
    assert [r.policy_version for r in history] == [
        "mnscr-editorial-gate-v1", "mnscr-editorial-gate-v2",
    ]


def test_latest_result_is_the_most_recent(store):
    store.save_result(make_result(policy="v-antiga"))
    store.save_result(make_result(policy="v-nova"))
    assert store.get_latest_gate_result("draft-1").policy_version == "v-nova"


def test_a_new_policy_never_overwrites_the_previous_verdict(store):
    first = make_result(policy="v-antiga", outcome=GATE_CLEAR, rules=[])
    store.save_result(first)
    store.save_result(make_result(policy="v-nova", outcome=GATE_BLOCKED, rules=[
        EditorialRuleResult(rule_code="GATE_MISSING_TITLE", rule_version="1",
                            severity=SEVERITY_BLOCKING, triggered=True),
    ]))
    history = store.list_gate_results("draft-1")
    assert len(history) == 2
    assert history[0].outcome == GATE_CLEAR


def test_listing_by_outcome_returns_only_the_latest_per_draft(store):
    store.save_result(make_result("draft-a", outcome=GATE_BLOCKED, policy="v1", rules=[
        EditorialRuleResult(rule_code="GATE_MISSING_TITLE", rule_version="1",
                            severity=SEVERITY_BLOCKING, triggered=True),
    ]))
    store.save_result(make_result("draft-a", outcome=GATE_CLEAR, policy="v2", rules=[]))
    store.save_result(make_result("draft-b", outcome=GATE_BLOCKED, policy="v1", rules=[
        EditorialRuleResult(rule_code="GATE_MISSING_BODY", rule_version="1",
                            severity=SEVERITY_BLOCKING, triggered=True),
    ]))

    blocked = [row["draft_id"] for row in store.list_blocked()]
    assert blocked == ["draft-b"], "draft-a foi liberado pela politica nova"


def test_listing_review_required(store):
    store.save_result(make_result("draft-r", outcome=GATE_REVIEW_REQUIRED))
    assert [row["draft_id"] for row in store.list_review_required()] == ["draft-r"]


def test_no_result_returns_none(store):
    assert store.get_latest_gate_result("draft-inexistente") is None


# ---------------------------------------------------------------------------
# Reavaliacao
# ---------------------------------------------------------------------------


@pytest.fixture
def service(tmp_path, store):
    drafts = tmp_path / "drafts"
    drafts.mkdir()
    from app.editorial.serialization import dumps_pretty

    draft = make_draft()
    (drafts / f"{draft.draft_id}.json").write_text(dumps_pretty(draft), encoding="utf-8")
    return GateService(gate_store=store, draft_dir=str(drafts))


def test_reevaluation_reads_only_the_local_artifact(service):
    result = service.reevaluate_draft("draft-1", POLICY_V1)
    assert result.result.draft_id == "draft-1"
    assert result.result.outcome in (GATE_CLEAR, GATE_REVIEW_REQUIRED)


def test_reevaluation_is_idempotent(service):
    first = service.reevaluate_draft("draft-1", POLICY_V1)
    second = service.reevaluate_draft("draft-1", POLICY_V1)
    assert first.result.gate_hash == second.result.gate_hash
    assert second.created_new_record is False
    assert len(service.list_gate_results("draft-1")) == 1


def test_reevaluation_never_changes_the_output_hash(service):
    before = service.load_draft("draft-1").provenance.output_hash
    service.reevaluate_draft("draft-1", POLICY_V1)
    assert service.load_draft("draft-1").provenance.output_hash == before


def test_reevaluation_does_not_modify_the_stored_artifact(service, tmp_path):
    path = tmp_path / "drafts" / "draft-1.json"
    before = path.read_text(encoding="utf-8")
    service.reevaluate_draft("draft-1", POLICY_V1)
    assert path.read_text(encoding="utf-8") == before


def test_reevaluation_with_a_new_policy_creates_a_new_record(service, tmp_path):
    service.reevaluate_draft("draft-1", POLICY_V1)

    # Uma politica derivada, com menos regras: outro veredito legitimo.
    reduced = json.loads(Path("config/editorial_gate/mnscr-editorial-gate-v1.json").read_text(encoding="utf-8"))
    reduced["policyVersion"] = "mnscr-editorial-gate-vtest"
    reduced["enabledRules"] = ["GATE_MISSING_TITLE"]
    policy_dir = tmp_path / "policies"
    policy_dir.mkdir()
    (policy_dir / "mnscr-editorial-gate-vtest.json").write_text(
        json.dumps(reduced), encoding="utf-8"
    )

    from app import config

    original_dir = config.EDITORIAL_GATE_POLICY_DIR
    config.EDITORIAL_GATE_POLICY_DIR = str(policy_dir)
    try:
        second = service.reevaluate_draft("draft-1", "mnscr-editorial-gate-vtest")
    finally:
        config.EDITORIAL_GATE_POLICY_DIR = original_dir

    assert second.created_new_record is True
    assert second.changed is True
    assert len(service.list_gate_results("draft-1")) == 2


def test_reevaluation_of_an_unknown_draft_raises(service):
    with pytest.raises(GateResultNotFoundError):
        service.reevaluate_draft("draft-que-nao-existe", POLICY_V1)


def test_reevaluation_with_an_unknown_policy_raises(service):
    with pytest.raises(PolicyNotFoundError):
        service.reevaluate_draft("draft-1", "politica-inexistente-v9")


def test_reevaluation_reports_the_previous_outcome(service):
    first = service.reevaluate_draft("draft-1", POLICY_V1)
    second = service.reevaluate_draft("draft-1", POLICY_V1)
    assert second.previous_outcome == first.result.outcome


def test_reevaluation_does_not_advance_any_cursor(service, tmp_path):
    """O gate nao toca na ingestao: nenhuma tabela de cursor e sequer criada."""
    service.reevaluate_draft("draft-1", POLICY_V1)
    cursor = service.store.conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='ingestion_cursors'")
    assert cursor.fetchone() is None


# ---------------------------------------------------------------------------
# Reconstrucao do draft
# ---------------------------------------------------------------------------


def test_draft_is_faithfully_rebuilt_from_the_artifact(service):
    rebuilt = service.load_draft("draft-1")
    original = make_draft()
    assert rebuilt.draft_id == original.draft_id
    assert rebuilt.content.title == original.content.title
    assert rebuilt.provenance.output_hash == original.provenance.output_hash
    assert [s.url for s in rebuilt.sources] == [s.url for s in original.sources]
    assert rebuilt.provenance.input_contract_version == "rss-prime-event-v1"


def test_rebuild_ignores_unknown_provenance_fields():
    """Um artefato de uma versao futura nao pode quebrar a reconstrucao."""
    payload = {
        "draft_id": "draft-x",
        "article_id": 1,
        "content": {"title": "T", "body_html": "<p>x</p>"},
        "provenance": {
            "input_hash": "a" * 64, "output_hash": "b" * 64,
            "campo_do_futuro": "valor",
        },
        "sources": [{"url": "https://variety.example/a"}],
    }
    draft = draft_from_artifact(payload)
    assert draft.draft_id == "draft-x"
    assert draft.provenance.output_hash == "b" * 64


def test_rebuild_preserva_laudos_que_compõem_o_payload_cinerie():
    gate = make_result(draft_id="draft-replay", outcome=GATE_CLEAR)
    factual = FactualAssessment(
        draft_id="draft-replay",
        coverage=FactualCoverage(coverage_measured=False, unmeasured_material_claims=1),
    )
    payload = make_draft(draft_id="draft-replay").to_dict()
    payload["editorial_gate"] = gate.to_dict()
    payload["factual_assessment"] = factual.to_dict()

    rebuilt = draft_from_artifact(payload)

    assert rebuilt.editorial_gate.to_dict() == gate.to_dict()
    assert rebuilt.factual_assessment.to_dict() == factual.to_dict()
