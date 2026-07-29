"""Regras de revisao do mesmo ``event_key``.

A decisao e pura: recebe o que chegou e o que esta armazenado, devolve o que
fazer. Nenhum banco, nenhum relogio, nenhuma rede.
"""

import pytest

from app.contracts import errors as E
from app.contracts import states as S
from app.contracts.revisions import (
    ACCEPT_NEW_EVENT,
    ACCEPT_NEW_REVISION,
    CONFLICT,
    DUPLICATE,
    STALE,
    StoredRevision,
    decide_revision,
)

HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64


def stored(revision: int, payload_hash: str = HASH_A) -> StoredRevision:
    return StoredRevision(event_key="evt-1", revision=revision, payload_hash=payload_hash)


def decide(revision: int, payload_hash: str, previous=None, same_revision_hash=None):
    return decide_revision(
        event_key="evt-1",
        incoming_revision=revision,
        incoming_payload_hash=payload_hash,
        stored=previous,
        stored_same_revision_hash=same_revision_hash,
    )


# ---------------------------------------------------------------------------
# Primeira entrega
# ---------------------------------------------------------------------------


def test_first_delivery_of_a_new_event_is_accepted():
    decision = decide(1, HASH_A, previous=None)
    assert decision.decision == ACCEPT_NEW_EVENT
    assert decision.accepted
    assert decision.should_generate_draft
    assert decision.validation_status == S.VALIDATED


def test_first_delivery_reports_no_issues():
    assert decide(1, HASH_A).issues == []


# ---------------------------------------------------------------------------
# Reentrega identica
# ---------------------------------------------------------------------------


def test_identical_redelivery_is_idempotent():
    decision = decide(1, HASH_A, previous=stored(1, HASH_A), same_revision_hash=HASH_A)
    assert decision.decision == DUPLICATE
    assert decision.validation_status == S.DUPLICATE_DELIVERY
    assert not decision.should_generate_draft


def test_identical_redelivery_is_not_an_error():
    """Reentrega e normal em entrega at-least-once; nao pode virar erro."""
    decision = decide(1, HASH_A, previous=stored(1, HASH_A), same_revision_hash=HASH_A)
    assert decision.error_codes == []


def test_hash_comparison_ignores_case_and_padding():
    decision = decide(
        1, "  " + HASH_A.upper() + "  ", previous=stored(1, HASH_A), same_revision_hash=HASH_A
    )
    assert decision.decision == DUPLICATE


# ---------------------------------------------------------------------------
# Conflito na mesma revisao
# ---------------------------------------------------------------------------


def test_same_revision_with_different_hash_is_a_conflict():
    decision = decide(1, HASH_B, previous=stored(1, HASH_A), same_revision_hash=HASH_A)
    assert decision.decision == CONFLICT
    assert decision.validation_status == S.REVISION_HASH_CONFLICT
    assert not decision.should_generate_draft


def test_conflict_reports_the_structured_code():
    decision = decide(1, HASH_B, previous=stored(1, HASH_A), same_revision_hash=HASH_A)
    assert E.REVISION_HASH_CONFLICT in decision.error_codes


def test_conflict_records_both_hashes_for_the_operator():
    decision = decide(1, HASH_B, previous=stored(1, HASH_A), same_revision_hash=HASH_A)
    issue = next(i for i in decision.issues if i.code == E.REVISION_HASH_CONFLICT)
    assert issue.context["stored_hash"] == HASH_A
    assert issue.context["incoming_hash"] == HASH_B


# ---------------------------------------------------------------------------
# Revisao nova
# ---------------------------------------------------------------------------


def test_next_revision_is_accepted():
    decision = decide(2, HASH_B, previous=stored(1, HASH_A))
    assert decision.decision == ACCEPT_NEW_REVISION
    assert decision.should_generate_draft
    assert decision.validation_status == S.VALIDATED


def test_next_revision_has_no_gap_warning():
    decision = decide(2, HASH_B, previous=stored(1, HASH_A))
    assert E.REVISION_GAP not in decision.warning_codes
    assert decision.missing_revisions == []


def test_new_revision_is_accepted_even_when_content_looks_similar():
    """A decisao de revisao olha o numero e o hash, nao o conteudo."""
    decision = decide(3, HASH_C, previous=stored(2, HASH_B))
    assert decision.decision == ACCEPT_NEW_REVISION


# ---------------------------------------------------------------------------
# Revisao antiga
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("old_revision", [1, 2, 3])
def test_older_revision_is_rejected_as_stale(old_revision):
    decision = decide(old_revision, HASH_C, previous=stored(4, HASH_A))
    assert decision.decision == STALE
    assert decision.validation_status == S.STALE_REVISION
    assert E.STALE_REVISION in decision.error_codes
    assert not decision.should_generate_draft


def test_stale_decision_reports_the_stored_revision():
    decision = decide(2, HASH_C, previous=stored(5, HASH_A))
    issue = next(i for i in decision.issues if i.code == E.STALE_REVISION)
    assert issue.context["stored_revision"] == 5
    assert issue.context["revision"] == 2


# ---------------------------------------------------------------------------
# Salto de revisao
# ---------------------------------------------------------------------------


def test_revision_gap_is_accepted_with_a_warning():
    """Salto nao rejeita: o RSS Prime pode ter perdido entregas intermediarias."""
    decision = decide(5, HASH_C, previous=stored(2, HASH_A))
    assert decision.decision == ACCEPT_NEW_REVISION
    assert decision.accepted
    assert E.REVISION_GAP in decision.warning_codes


def test_revision_gap_records_which_revisions_are_missing():
    decision = decide(5, HASH_C, previous=stored(2, HASH_A))
    assert decision.missing_revisions == [3, 4]


def test_revision_gap_does_not_invent_intermediate_revisions():
    """As revisoes ausentes sao registradas, nunca fabricadas."""
    decision = decide(5, HASH_C, previous=stored(2, HASH_A))
    issue = next(i for i in decision.issues if i.code == E.REVISION_GAP)
    assert issue.context["missing_revisions"] == [3, 4]
    assert not issue.is_blocking


def test_large_gap_is_still_accepted():
    decision = decide(100, HASH_C, previous=stored(1, HASH_A))
    assert decision.accepted
    assert len(decision.missing_revisions) == 98


# ---------------------------------------------------------------------------
# Serializacao
# ---------------------------------------------------------------------------


def test_decision_serializes_for_persistence():
    payload = decide(5, HASH_C, previous=stored(2, HASH_A)).to_dict()
    assert payload["decision"] == ACCEPT_NEW_REVISION
    assert payload["missing_revisions"] == [3, 4]
    assert payload["warnings"][0]["code"] == E.REVISION_GAP


def test_decision_never_uses_publication_vocabulary():
    for decision in (
        decide(1, HASH_A),
        decide(1, HASH_A, previous=stored(1, HASH_A), same_revision_hash=HASH_A),
        decide(1, HASH_B, previous=stored(2, HASH_A)),
    ):
        assert decision.validation_status not in {"PUBLISHED", "APPROVED", "PUBLICATION_READY"}
