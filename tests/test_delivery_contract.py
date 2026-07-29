"""Contrato de entrega, estados, categorias e politica de retry.

Nenhum teste aqui abre socket: e tudo dominio puro. A fixture `_no_network`
garante isso.
"""

import json
import socket

import pytest

from app.delivery import states as S
from app.delivery.builder import build_delivery_payload, check_deliverable
from app.delivery.categories import (
    TOPIC_TO_CATEGORY,
    canonical_categories,
    normalize_tags,
    resolve_category,
)
from app.delivery.contract import (
    CMS_STATUS_DRAFT,
    CONTENT_FORMAT_HTML,
    EDITORIAL_STATUS_READY,
    MAX_EXCERPT_CHARS,
    DeliveryMedia,
    EditorialDeliveryPayload,
    EntityMention,
    ensure_valid,
    is_valid_slug,
    slugify,
    validate_delivery_payload,
)
from app.delivery.destination import (
    DESTINATION_PAYLOAD_CMS,
    build_delivery_id,
    build_idempotency_key,
)
from app.delivery.errors import (
    DeliveryAuthenticationError,
    DeliveryBlockedError,
    DeliveryConflictError,
    DeliveryNotFoundError,
    DeliveryPermissionError,
    DeliveryRateLimitedError,
    DeliveryServerError,
    DeliveryTimeoutError,
    InvalidDeliveryPayloadError,
    error_for_status,
    is_retryable_status,
)
from app.delivery.retry import FakeClock, FakeSleeper, RetryPolicy, parse_retry_after
from app.delivery.states import InvalidDeliveryTransitionError
from app.editorial import DraftContent, DraftProvenance, EditorialDraft, SourceReference
from app.editorial.states import ForbiddenStateError

FILLER = " ".join(["palavra"] * 260)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _blocked(*args, **kwargs):
        raise AssertionError("Teste de contrato de entrega tentou acessar a rede")

    monkeypatch.setattr(socket, "socket", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)


class _Coverage:
    coverage_ratio = 0.9
    total_material_claims = 4
    supported_material_claims = 4
    unsupported_material_claims = 0
    source_diversity = 2
    review_required = False


class _Assessment:
    assessment_id = "fa-1"
    assessment_hash = "f" * 64
    version = "mnscr-factual-assessment-v1"
    mode = "deterministic"
    claims: list = []
    evidence: list = []
    conflicts: list = []
    critical_conflicts: list = []
    coverage = _Coverage()


class _Gate:
    gate_id = "gate-1"
    gate_hash = "g" * 64
    policy_version = "mnscr-editorial-gate-v1"
    outcome = "GATE_CLEAR"
    blocking_count = 0
    warning_count = 0
    triggered_rules: list = []


def make_draft(**overrides) -> EditorialDraft:
    content_kw = overrides.pop("content_kw", {})
    draft = EditorialDraft(
        draft_id=overrides.pop("draft_id", "draft-1"),
        article_id=1,
        content=DraftContent(**{
            "title": "Estudio confirma estreia em 29 de julho de 2026",
            "body_html": f"<p>A serie foi renovada.</p><p>{FILLER}</p>",
            "subtitle": "Resumo editorial distinto do titulo e da meta description.",
            "meta_description": "Meta description com o fato principal.",
            "category_suggestions": ["Filmes"],
            "tag_suggestions": ["estreia", "temporada"],
            **content_kw,
        }),
        provenance=DraftProvenance(
            input_hash="a" * 64, output_hash="b" * 64,
            input_contract_version="rss-prime-event-v1",
            input_event_key="evt-1", input_revision=1, prompt_version="p1",
        ),
        event_key="evt-1", revision=1,
        sources=[
            SourceReference(url="https://variety.example/a", domain="variety.example",
                            is_primary=True, extraction_status="OK"),
        ],
        **overrides,
    )
    draft.factual_assessment = _Assessment()
    draft.editorial_gate = _Gate()
    return draft


# ===========================================================================
# Estados
# ===========================================================================


@pytest.mark.parametrize("state", sorted(S.DELIVERY_STATES))
def test_valid_states_are_accepted(state):
    assert S.validate_delivery_state(state) == state


@pytest.mark.parametrize("state", ["ENTREGUE", "", "delivered_maybe"])
def test_unknown_states_are_rejected(state):
    with pytest.raises(ValueError):
        S.validate_delivery_state(state)


@pytest.mark.parametrize("forbidden", ["PUBLISHED", "APPROVED", "PUBLICATION_READY"])
def test_publication_vocabulary_is_rejected_as_a_delivery_state(forbidden):
    """A entrega cria drafts; publicar nao e um estado possivel."""
    with pytest.raises(ForbiddenStateError):
        S.validate_delivery_state(forbidden)


def test_pending_can_start():
    assert S.can_transition(S.DELIVERY_PENDING, S.DELIVERY_IN_PROGRESS)


def test_in_progress_can_succeed_or_fail():
    for target in (S.DELIVERED, S.DELIVERY_FAILED_RETRYABLE, S.DELIVERY_FAILED_PERMANENT):
        assert S.can_transition(S.DELIVERY_IN_PROGRESS, target)


def test_retryable_can_be_retried():
    assert S.can_transition(S.DELIVERY_FAILED_RETRYABLE, S.DELIVERY_IN_PROGRESS)


@pytest.mark.parametrize(
    "terminal",
    [S.DELIVERED, S.DELIVERY_FAILED_PERMANENT, S.DELIVERY_BLOCKED,
     S.DELIVERY_REQUIRES_MANUAL_REVIEW],
)
def test_terminal_states_have_no_exit(terminal):
    assert S.is_terminal(terminal)
    assert S.ALLOWED_TRANSITIONS[terminal] == frozenset()


def test_delivered_cannot_go_back_to_in_progress():
    with pytest.raises(InvalidDeliveryTransitionError):
        S.assert_transition(S.DELIVERED, S.DELIVERY_IN_PROGRESS)


def test_blocked_never_becomes_delivered():
    with pytest.raises(InvalidDeliveryTransitionError):
        S.assert_transition(S.DELIVERY_BLOCKED, S.DELIVERED)


def test_pending_cannot_jump_straight_to_delivered():
    """Uma entrega so vira DELIVERED depois de ter sido tentada."""
    with pytest.raises(InvalidDeliveryTransitionError):
        S.assert_transition(S.DELIVERY_PENDING, S.DELIVERED)


def test_transition_error_lists_what_is_allowed():
    with pytest.raises(InvalidDeliveryTransitionError) as exc:
        S.assert_transition(S.DELIVERED, S.DELIVERY_IN_PROGRESS)
    assert "terminal" in str(exc.value)


# ===========================================================================
# Contrato
# ===========================================================================


def payload(**overrides) -> EditorialDeliveryPayload:
    base = dict(
        delivery_id="dlv-1", draft_id="draft-1",
        title="Estudio confirma estreia", content="<p>corpo editorial</p>",
        category="Filmes", content_hash="b" * 64,
    )
    base.update(overrides)
    return EditorialDeliveryPayload(**base)


def test_valid_payload_passes_validation():
    assert validate_delivery_payload(payload()) == []


@pytest.mark.parametrize(
    "field", ["delivery_id", "draft_id", "title", "content", "category", "content_hash"]
)
def test_missing_required_field_is_rejected(field):
    result = validate_delivery_payload(payload(**{field: ""}))
    assert any(f"CAMPO_OBRIGATORIO_AUSENTE:{field}" == e for e in result)


def test_cms_status_is_always_draft():
    assert payload().cms_status == CMS_STATUS_DRAFT


def test_cms_status_cannot_be_set_to_published():
    """Nao existe caminho de codigo que peca publicacao."""
    item = payload(cms_status="published")
    assert item.cms_status == CMS_STATUS_DRAFT
    assert validate_delivery_payload(item) == []


def test_a_payload_forced_to_published_is_rejected_by_validation():
    item = payload()
    object.__setattr__(item, "cms_status", "published")
    assert any(e.startswith("CMS_STATUS_INVALIDO") for e in validate_delivery_payload(item))


def test_editorial_status_is_ready_for_draft():
    assert payload().editorial_status == EDITORIAL_STATUS_READY


def test_unknown_editorial_status_is_rejected():
    assert any(
        e.startswith("EDITORIAL_STATUS_INVALIDO")
        for e in validate_delivery_payload(payload(editorial_status="APPROVED"))
    )


def test_unknown_schema_version_is_rejected():
    assert any(
        e.startswith("SCHEMA_VERSION_DESCONHECIDA")
        for e in validate_delivery_payload(payload(schema_version="99"))
    )


def test_content_format_is_declared_explicitly():
    assert payload().content_format == CONTENT_FORMAT_HTML


def test_unknown_content_format_is_rejected():
    assert any(
        e.startswith("CONTENT_FORMAT_INVALIDO")
        for e in validate_delivery_payload(payload(content_format="markdown"))
    )


def test_non_canonical_category_is_rejected():
    assert any(
        e.startswith("CATEGORIA_NAO_CANONICA")
        for e in validate_delivery_payload(payload(category="Culinaria"))
    )


@pytest.mark.parametrize("bad", ["Slug Invalido", "slug_invalido", "SLUG", "-inicio", "fim-"])
def test_invalid_slug_is_rejected(bad):
    assert any(e.startswith("SLUG_INVALIDO") for e in validate_delivery_payload(payload(slug=bad)))


def test_valid_slug_is_accepted():
    assert validate_delivery_payload(payload(slug="estudio-confirma-estreia")) == []


def test_overlong_excerpt_is_rejected():
    assert any(
        e.startswith("EXCERPT_LONGO_DEMAIS")
        for e in validate_delivery_payload(payload(excerpt="x" * (MAX_EXCERPT_CHARS + 1)))
    )


def test_ensure_valid_raises_on_invalid_payload():
    with pytest.raises(InvalidDeliveryPayloadError):
        ensure_valid(payload(title=""))


def test_entity_mentions_default_to_empty():
    """A MS-5 nao extrai mencoes; o campo existe para evoluir sem quebrar."""
    assert payload().entity_mentions == []


def test_a_resolved_entity_mention_is_rejected():
    """Resolver uma mencao agora seria inventar um fato."""
    item = payload(entity_mentions=[EntityMention(label="Alguem", resolution_status="RESOLVED")])
    assert any(
        e.startswith("ENTITY_MENTION_RESOLVIDA_INDEVIDAMENTE")
        for e in validate_delivery_payload(item)
    )


def test_unresolved_entity_mention_is_accepted():
    item = payload(entity_mentions=[EntityMention(label="Alguem", type="PERSON")])
    assert validate_delivery_payload(item) == []


def test_payload_serializes_to_json():
    json.dumps(payload().to_dict())


def test_serialization_is_deterministic():
    assert payload().canonical_json() == payload().canonical_json()


def test_generated_at_is_not_part_of_the_identity():
    a = payload(generated_at="2020-01-01T00:00:00Z")
    b = payload(generated_at="2030-12-31T23:59:59Z")
    assert a.payload_fingerprint() == b.payload_fingerprint()


def test_fingerprint_changes_with_the_content():
    assert payload().payload_fingerprint() != payload(content="<p>outro</p>").payload_fingerprint()


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Estúdio confirma estreia", "estudio-confirma-estreia"),
        ("Ação e Coração", "acao-e-coracao"),
        ("Título   com   espaços", "titulo-com-espacos"),
    ],
)
def test_slugify_handles_portuguese(raw, expected):
    assert slugify(raw) == expected


def test_unicode_content_survives_serialization():
    item = payload(title="Estreia de Ação: coração & emoção — 2026")
    assert "Ação" in json.loads(item.canonical_json())["title"]


def test_media_never_invents_a_licence():
    media = DeliveryMedia(source_url="https://cdn.example/a.jpg")
    assert media.license_status == "UNKNOWN"
    assert media.remote_media_id is None


# ===========================================================================
# Categorias
# ===========================================================================


@pytest.mark.parametrize("topic, expected", [("movies", "Filmes"), ("tv", "Séries")])
def test_known_topic_resolves(topic, expected):
    category, reason = resolve_category(topic, [])
    assert category == expected and reason is None


def test_unknown_topic_does_not_resolve():
    category, reason = resolve_category("culinaria", [])
    assert category is None
    assert "culinaria" in reason


def test_no_topic_and_no_suggestion_does_not_resolve():
    category, reason = resolve_category(None, [])
    assert category is None and reason


def test_canonical_suggestion_wins():
    category, _ = resolve_category("culinaria", ["Séries"])
    assert category == "Séries"


def test_non_canonical_suggestion_is_ignored():
    """Sugestao editorial nao autoriza criar taxonomia."""
    category, reason = resolve_category("culinaria", ["Culinaria Gourmet"])
    assert category is None and reason


def test_every_mapped_category_is_canonical():
    allowed = set(canonical_categories())
    assert set(TOPIC_TO_CATEGORY.values()) <= allowed


def test_tags_are_normalized_and_deduplicated():
    assert normalize_tags(["  Estreia ", "estreia", "", "Temporada"]) == ["Estreia", "Temporada"]


def test_tags_are_capped():
    assert len(normalize_tags([f"tag{i}" for i in range(50)])) <= 12


# ===========================================================================
# Idempotencia
# ===========================================================================


def test_idempotency_key_is_deterministic():
    args = dict(draft_id="draft-1", content_hash="b" * 64, destination=DESTINATION_PAYLOAD_CMS)
    assert build_idempotency_key(**args) == build_idempotency_key(**args)


def test_idempotency_key_changes_with_the_content():
    a = build_idempotency_key(draft_id="d", content_hash="a" * 64, destination="x")
    b = build_idempotency_key(draft_id="d", content_hash="b" * 64, destination="x")
    assert a != b


def test_idempotency_key_changes_with_the_destination():
    a = build_idempotency_key(draft_id="d", content_hash="a" * 64, destination="x")
    b = build_idempotency_key(draft_id="d", content_hash="a" * 64, destination="y")
    assert a != b


@pytest.mark.parametrize("missing", ["draft_id", "content_hash", "destination"])
def test_idempotency_key_requires_every_part(missing):
    args = {"draft_id": "d", "content_hash": "h", "destination": "x", missing: ""}
    with pytest.raises(ValueError):
        build_idempotency_key(**args)


def test_delivery_id_derives_from_the_key():
    key = build_idempotency_key(draft_id="d", content_hash="h", destination="x")
    assert build_delivery_id(key).startswith("dlv-")


# ===========================================================================
# Classificacao de erro
# ===========================================================================


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504])
def test_retryable_statuses_are_retryable(status):
    assert error_for_status(status, "x").retryable is True
    assert is_retryable_status(status)


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 422])
def test_permanent_statuses_are_not_retryable(status):
    assert error_for_status(status, "x").retryable is False
    assert not is_retryable_status(status)


@pytest.mark.parametrize(
    "status, expected",
    [
        (400, InvalidDeliveryPayloadError),
        (401, DeliveryAuthenticationError),
        (403, DeliveryPermissionError),
        (404, DeliveryNotFoundError),
        (408, DeliveryTimeoutError),
        (409, DeliveryConflictError),
        (429, DeliveryRateLimitedError),
        (500, DeliveryServerError),
        (503, DeliveryServerError),
    ],
)
def test_status_maps_to_the_right_error(status, expected):
    assert isinstance(error_for_status(status, "x"), expected)


def test_authentication_error_is_never_retried():
    """Repetir com a mesma credencial invalida nao muda nada."""
    assert error_for_status(401, "x").retryable is False


def test_error_serializes_without_leaking():
    payload_dict = error_for_status(503, "destino indisponivel").to_dict()
    assert payload_dict["error_class"] == "SERVER"
    assert payload_dict["retryable"] is True


# ===========================================================================
# Retry
# ===========================================================================


def test_first_attempt_has_no_delay():
    assert RetryPolicy().delay_for(1) == 0.0


def test_backoff_grows():
    policy = RetryPolicy(max_attempts=5, base_seconds=2, jitter_ratio=0.0)
    delays = [policy.delay_for(a) for a in range(2, 6)]
    assert delays == sorted(delays)
    assert delays[0] < delays[-1]


def test_backoff_is_capped():
    policy = RetryPolicy(max_attempts=20, base_seconds=2, max_backoff_seconds=10)
    assert all(d <= 10 for d in policy.schedule())


def test_jitter_is_deterministic():
    policy = RetryPolicy(max_attempts=4, base_seconds=2)
    assert policy.schedule(seed="dlv-1") == policy.schedule(seed="dlv-1")


def test_jitter_differs_between_deliveries():
    policy = RetryPolicy(max_attempts=4, base_seconds=2)
    assert policy.schedule(seed="dlv-1") != policy.schedule(seed="dlv-2")


def test_retry_after_overrides_the_backoff():
    """O destino sabe melhor do que a nossa estimativa exponencial."""
    policy = RetryPolicy(base_seconds=100, max_backoff_seconds=600)
    assert policy.delay_for(3, retry_after_seconds=7) == 7


def test_retry_after_is_still_capped():
    policy = RetryPolicy(max_backoff_seconds=30)
    assert policy.delay_for(2, retry_after_seconds=9999) == 30


@pytest.mark.parametrize("value, expected", [("12", 12.0), ("0", 0.0), ("2.5", 2.5)])
def test_retry_after_header_is_parsed(value, expected):
    assert parse_retry_after(value) == expected


@pytest.mark.parametrize("value", [None, "", "Wed, 21 Oct 2026 07:28:00 GMT", "abc", "-5"])
def test_unparseable_retry_after_is_ignored(value):
    assert parse_retry_after(value) is None


@pytest.mark.parametrize("bad", [{"max_attempts": 0}, {"base_seconds": -1}, {"jitter_ratio": 2}])
def test_invalid_policy_is_rejected(bad):
    with pytest.raises(ValueError):
        RetryPolicy(**bad)


def test_fake_sleeper_records_without_waiting():
    sleeper = FakeSleeper()
    sleeper.sleep(3.5)
    sleeper.sleep(1.0)
    assert sleeper.slept == [3.5, 1.0]
    assert sleeper.total_slept == 4.5


def test_fake_clock_only_advances_when_told():
    clock = FakeClock()
    started = clock.now()
    clock.advance(2.5)
    assert clock.elapsed_ms_since(started) == 2500


# ===========================================================================
# Builder e bloqueio
# ===========================================================================


def test_approved_draft_builds_a_payload():
    result = build_delivery_payload(make_draft(), topic="movies")
    assert result.cms_status == CMS_STATUS_DRAFT
    assert result.category == "Filmes"
    assert result.content_hash == "b" * 64
    assert validate_delivery_payload(result) == []


def test_payload_carries_the_source_event_identity():
    result = build_delivery_payload(make_draft(), topic="movies")
    assert result.source_event_id == "evt-1"
    assert result.source_revision == 1


def test_payload_carries_the_hashes_from_earlier_phases():
    """Hashes sao transportados, nunca recalculados."""
    result = build_delivery_payload(make_draft(), topic="movies")
    assert result.content_hash == "b" * 64
    assert result.assessment_hash == "f" * 64
    assert result.gate_hash == "g" * 64


def test_sources_are_never_stripped():
    result = build_delivery_payload(make_draft(), topic="movies")
    assert result.sources and result.sources[0]["url"] == "https://variety.example/a"


def test_draft_without_assessment_is_blocked():
    draft = make_draft()
    draft.factual_assessment = None
    assert "AVALIACAO_FACTUAL_AUSENTE" in check_deliverable(draft)


def test_draft_without_gate_is_blocked():
    draft = make_draft()
    draft.editorial_gate = None
    assert "EDITORIAL_GATE_AUSENTE" in check_deliverable(draft)


@pytest.mark.parametrize("outcome", ["GATE_BLOCKED", "GATE_REVIEW_REQUIRED", "GATE_DISABLED"])
def test_draft_without_a_clear_gate_is_blocked(outcome):
    draft = make_draft()
    draft.editorial_gate.outcome = outcome
    assert any(r.startswith("GATE_NAO_LIBERADO") for r in check_deliverable(draft))
    draft.editorial_gate.outcome = "GATE_CLEAR"


def test_critical_conflict_blocks_independently_of_the_gate():
    """A guarda da entrega nao depende de outra guarda ter funcionado."""
    draft = make_draft()

    class _Conflict:
        pass

    draft.factual_assessment.critical_conflicts = [_Conflict()]
    assert any(r.startswith("CONFLITO_FACTUAL_CRITICO") for r in check_deliverable(draft))
    draft.factual_assessment.critical_conflicts = []


def test_draft_with_blocking_errors_is_blocked():
    draft = make_draft()
    draft.blocking_errors = ["EMPTY_TITLE"]
    assert any(r.startswith("DRAFT_COM_ERROS_BLOQUEANTES") for r in check_deliverable(draft))


def test_unknown_topic_blocks_the_build():
    draft = make_draft(content_kw={"category_suggestions": []})
    with pytest.raises(DeliveryBlockedError) as exc:
        build_delivery_payload(draft, topic="culinaria")
    assert "CATEGORIA_NAO_RESOLVIDA" in str(exc.value)


def test_approved_draft_has_no_blocking_reasons():
    assert check_deliverable(make_draft()) == []


def test_media_metadata_only_no_download():
    draft = make_draft()
    from app.editorial.models import MediaCandidate

    draft.media_candidates = [
        MediaCandidate(source_url="https://cdn.example/a.jpg", source_domain="cdn.example")
    ]
    result = build_delivery_payload(draft, topic="movies")
    assert result.featured_media.source_url == "https://cdn.example/a.jpg"
    assert result.featured_media.license_status == "UNKNOWN"
    assert result.featured_media.remote_media_id is None


def test_missing_media_does_not_block():
    """Falha de midia e falha editorial sao problemas separados."""
    result = build_delivery_payload(make_draft(), topic="movies")
    assert result.featured_media is None
    assert validate_delivery_payload(result) == []


def test_authors_are_empty_because_the_ai_wrote_it():
    assert build_delivery_payload(make_draft(), topic="movies").authors == []


def test_slug_is_derived_when_the_suggestion_is_invalid():
    draft = make_draft(content_kw={"slug_suggestion": "Slug Totalmente Invalido"})
    result = build_delivery_payload(draft, topic="movies")
    assert is_valid_slug(result.slug)


def test_payload_never_contains_publication_vocabulary():
    rendered = json.dumps(build_delivery_payload(make_draft(), topic="movies").to_dict())
    for forbidden in ("PUBLISHED", "APPROVED", "PUBLICATION_READY"):
        assert forbidden not in rendered
