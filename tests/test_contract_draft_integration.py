"""O evento normalizado chega ao EditorialDraft.

Revisoes distintas do mesmo acontecimento precisam gerar drafts distintos e
deterministicos, sem que a revisao 2 sobrescreva silenciosamente a revisao 1.
"""

import json
import socket
from pathlib import Path

import pytest

from app.contracts.rssprime_event_v1 import RssPrimeEventV1
from app.editorial import DRAFT_GENERATED, validate_draft
from app.editorial.builder import build_editorial_draft
from app.editorial.models import OUTPUT_CONTRACT_VERSION, build_draft_id

FIXTURES = Path(__file__).parent / "fixtures" / "rssprime"


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _blocked(*args, **kwargs):
        raise AssertionError("Teste de integracao tentou acessar a rede")

    monkeypatch.setattr(socket, "socket", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)


def event(name: str) -> RssPrimeEventV1:
    return RssPrimeEventV1.from_mapping(
        json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    )


@pytest.fixture
def rev1() -> RssPrimeEventV1:
    return event("event_v1_valid.json")


@pytest.fixture
def rev2() -> RssPrimeEventV1:
    return event("event_v1_revision_2.json")


def art_data_for(evt: RssPrimeEventV1) -> dict:
    return {
        "db_id": 42,
        "event_key": evt.event_key,
        "url": evt.primary_url,
        "canonical_url": evt.primary_url,
        "source_id": "rssprime_movies",
        "cluster_item": {"event_key": evt.event_key},
        "sources_used": [s.url for s in evt.sources],
    }


def build(evt: RssPrimeEventV1, title="Titulo editorial do draft", body=None):
    return build_editorial_draft(
        art_data=art_data_for(evt),
        rewritten={"slug": "titulo-editorial", "categorias": [], "tags_sugeridas": []},
        body_html=body or "<p>Corpo editorial suficientemente longo para o draft.</p>",
        title=title,
        subtitle="Resumo editorial distinto do titulo e da meta description.",
        meta_description="Meta description propria, com o fato principal do acontecimento.",
        input_event=evt,
    )


# ---------------------------------------------------------------------------
# Proveniencia
# ---------------------------------------------------------------------------


def test_draft_records_the_input_contract_version(rev1):
    assert build(rev1).provenance.input_contract_version == "rss-prime-event-v1"


def test_draft_records_the_input_event_key(rev1):
    assert build(rev1).provenance.input_event_key == rev1.event_key


def test_draft_records_the_input_revision(rev2):
    assert build(rev2).provenance.input_revision == 2


def test_draft_records_the_input_payload_hash(rev1):
    assert build(rev1).provenance.input_payload_hash == rev1.payload_hash


def test_draft_records_the_input_cursor(rev1):
    assert build(rev1).provenance.input_cursor == rev1.cursor


def test_draft_carries_the_event_window(rev1):
    draft = build(rev1)
    assert draft.first_seen == rev1.first_seen
    assert draft.last_seen == rev1.last_seen


def test_draft_carries_cluster_confidence(rev1):
    assert build(rev1).cluster_confidence == pytest.approx(0.91)


def test_output_contract_version_is_unchanged_by_ms2(rev1):
    """A MS-2 nao renegocia o contrato de saida."""
    assert build(rev1).provenance.output_contract_version == "mnscr-editorial-draft-v0"
    assert OUTPUT_CONTRACT_VERSION == "mnscr-editorial-draft-v0"


# ---------------------------------------------------------------------------
# Identidade por revisao
# ---------------------------------------------------------------------------


def test_draft_revision_follows_the_event_revision(rev2):
    assert build(rev2).revision == 2


def test_draft_id_is_deterministic_for_the_same_revision(rev1):
    assert build(rev1).draft_id == build(rev1).draft_id


def test_different_revisions_produce_different_draft_ids(rev1, rev2):
    assert build(rev1).draft_id != build(rev2).draft_id


def test_draft_id_matches_event_key_plus_revision(rev2):
    assert build(rev2).draft_id == build_draft_id(event_key=rev2.event_key, revision=2)


def test_different_revisions_produce_different_output_hashes(rev1, rev2):
    assert build(rev1).provenance.output_hash != build(rev2).provenance.output_hash


def test_different_revisions_produce_different_input_hashes(rev1, rev2):
    assert build(rev1).provenance.input_hash != build(rev2).provenance.input_hash


def test_same_body_on_different_revisions_still_differs(rev1, rev2):
    """Mesmo com conteudo identico, a revisao separa os drafts.

    E o que impede a revisao 2 de sobrescrever silenciosamente a revisao 1 no
    LocalDraftSubmitter, que e idempotente por ``draft_id`` + ``output_hash``.
    """
    same_body = "<p>Exatamente o mesmo corpo editorial nas duas revisoes.</p>"
    a = build(rev1, body=same_body)
    b = build(rev2, body=same_body)
    assert a.draft_id != b.draft_id
    assert a.provenance.output_hash != b.provenance.output_hash


# ---------------------------------------------------------------------------
# Compatibilidade e validade
# ---------------------------------------------------------------------------


def test_draft_from_a_versioned_event_is_valid(rev1):
    draft = build(rev1)
    assert draft.status == DRAFT_GENERATED
    assert validate_draft(draft) == []


def test_building_without_an_input_event_still_works(rev1):
    """Caminho pre-MS-2: sem evento, o comportamento antigo permanece."""
    draft = build_editorial_draft(
        art_data=art_data_for(rev1),
        rewritten={"categorias": [], "tags_sugeridas": []},
        body_html="<p>Corpo editorial do caminho legado.</p>",
        title="Titulo do caminho legado",
        subtitle="Resumo distinto do titulo.",
        meta_description="Meta description do caminho legado.",
        input_event=None,
    )
    assert draft.provenance.input_event_key is None
    assert draft.provenance.input_revision is None
    assert draft.revision == 1


def test_legacy_event_marks_its_contract_in_provenance():
    from app.contracts.legacy_feed_v0 import LegacyFeedV0Adapter

    legacy = LegacyFeedV0Adapter().adapt(
        json.loads((FIXTURES / "legacy_superfeed_item.json").read_text(encoding="utf-8"))
    )
    draft = build(legacy)
    assert draft.provenance.input_contract_version == "legacy-feed-input-v0"
    assert draft.provenance.input_cursor is None


def test_draft_never_gains_publication_state_from_the_contract(rev1):
    draft = build(rev1)
    rendered = json.dumps(draft.to_dict(), default=str)
    for forbidden in ("PUBLISHED", "APPROVED", "PUBLICATION_READY", "wp_post_id"):
        assert forbidden not in rendered
