"""Transporte, preflight, desfechos, retry, persistencia e segredo.

Estes testes usam um **servidor HTTP local de verdade** (``127.0.0.1``, porta
efemera) em vez de um mock da funcao interna: um mock da propria funcao que se
quer testar prova apenas que ela foi chamada. Status, headers, corpo nao-JSON,
``Retry-After`` e timeout so aparecem quando ha HTTP no caminho.

Consequencia: os testes daqui **nao** usam a fixture ``_no_network``, que bloqueia
socket por completo. A garantia no lugar dela e ``assert_loopback_only()``.

Nenhum token real, nenhum dominio de producao, nenhuma dependencia de EasyPanel,
de screen-db ou de internet.
"""

import json

import pytest

from app.cinerie import outcomes as O
from app.cinerie.client import CinerieClient, CinerieConfig, _safe_url
from app.cinerie.contract import local_identity
from app.cinerie.errors import (
    AuthenticationError,
    CinerieTimeoutError,
    ConfigurationError,
    ContractMismatchError,
    InvalidRemoteResponseError,
    NonRetryableServerError,
    OperationalError,
    PermissionError_,
    RateLimitedError,
    ServerError,
)
from app.cinerie.preflight import ContractPreflight
from app.cinerie_service import (
    MODE_AUTO_PUBLISH,
    MODE_DRAFT,
    CinerieService,
    resolve_delivery_mode,
)
from app.cinerie_store import (
    STATUS_AWAITING_HUMAN,
    STATUS_BLOCKED,
    STATUS_COMPLETED,
    STATUS_FAILED_PERMANENT,
    STATUS_NEEDS_RECONCILIATION,
    CinerieStore,
)
from app.delivery.retry import FakeClock, FakeSleeper, RetryPolicy
from app.editorial import DraftContent, DraftProvenance, EditorialDraft, SourceReference
from app.editorial_gate import EditorialGate
from app.factual_builder import build_factual_assessment
from tests.fake_cinerie_server import FakeCinerieServer, ScriptedResponse, assert_loopback_only

#: Token SENTINELA. Fora do formato de qualquer credencial real e usado para
#: provar que ele NAO aparece em log, excecao, repr ou relatorio.
SENTINEL_KEY = "fake-cinerie-sentinel-key-do-not-log-0001"

AUTHOR_ID = "author-redacao-cinerie"
FILLER = " ".join(["palavra"] * 120)

SOURCES = [
    {
        "url": "https://variety.example/a",
        "domain": "variety.example",
        "content": "O estudio confirmou a estreia para 29 de julho de 2026 conforme anunciado.",
    },
    {
        "url": "https://deadline.example/b",
        "domain": "deadline.example",
        "content": "A estreia acontece em 29 de julho de 2026 segundo o estudio.",
    },
]


def make_draft(event_key="cluster-4711", revision=3, draft_id="draft-1") -> EditorialDraft:
    draft = EditorialDraft(
        draft_id=draft_id,
        article_id=1,
        content=DraftContent(
            title="Estudio confirma a data de estreia da nova serie",
            body_html=(
                "<h2>O anuncio</h2>"
                "<p>O estudio confirmou a data de estreia da nova serie nesta terca-feira.</p>"
                f"<p>{FILLER}</p>"
            ),
            subtitle="A producao chega ao catalogo em julho com oito episodios no total.",
            meta_description=(
                "O estudio confirmou a data de estreia da nova serie, que chega ao catalogo "
                "em julho com oito episodios e lancamento semanal na plataforma."
            ),
            focus_keyphrase="data de estreia",
            related_keyphrases=["nova serie", "catalogo de julho"],
            slug_suggestion="nova-serie-data-de-estreia",
            category_suggestions=["news"],
            tag_suggestions=["serie", "estreia", "streaming"],
        ),
        provenance=DraftProvenance(
            input_hash="a" * 64,
            output_hash="b" * 64,
            input_contract_version="rss-prime-event-v1",
            input_event_key=event_key,
            input_revision=revision,
            prompt_version="seo@2026.07",
            model_name="modelo-de-teste",
        ),
        event_key=event_key,
        revision=revision,
        sources=[
            SourceReference(
                url="https://variety.example/a",
                domain="variety.example",
                is_primary=True,
                extraction_status="OK",
            ),
            SourceReference(
                url="https://deadline.example/b",
                domain="deadline.example",
                extraction_status="OK",
            ),
        ],
    )
    draft.factual_assessment = build_factual_assessment(draft, SOURCES, mode="deterministic")
    draft.editorial_gate = EditorialGate().evaluate(
        draft, context={"output_mode": "local", "factual_assessment": draft.factual_assessment}
    )
    return draft


def published_body(article_id="article-8801", **overrides):
    body = {
        "outcome": O.PUBLISHED,
        "requestId": "req-x",
        "idempotencyKey": "mnscr:cluster-4711:rev-3",
        "reasons": [],
        "warnings": [],
        "articleId": article_id,
        "canonicalSlug": "nova-serie-data-de-estreia",
        "publicAuthorId": AUTHOR_ID,
        "technicalActorId": "service-account-1",
    }
    body.update(overrides)
    return body


@pytest.fixture
def server():
    instance = FakeCinerieServer().start()
    assert_loopback_only(instance.base_url)
    yield instance
    instance.stop()


@pytest.fixture
def store(tmp_path):
    instance = CinerieStore(db_path=str(tmp_path / "app.db"))
    yield instance
    instance.close()


def make_client(server, *, timeout=5.0):
    return CinerieClient(
        CinerieConfig(base_url=server.base_url, api_key=SENTINEL_KEY, timeout_seconds=timeout)
    )


def make_service(server, store, **overrides):
    client = overrides.pop("client", None) or make_client(server)
    kwargs = dict(
        store=store,
        client=client,
        public_author_id=AUTHOR_ID,
        attribution_mode="newsroom",
        delivery_mode=MODE_AUTO_PUBLISH,
        preflight=ContractPreflight(client, clock=FakeClock()),
        retry_policy=RetryPolicy(max_attempts=3, base_seconds=1),
        sleeper=FakeSleeper(),
        clock=FakeClock(),
    )
    kwargs.update(overrides)
    return CinerieService(**kwargs)


# ===========================================================================
# Configuracao
# ===========================================================================


def test_missing_api_key_is_refused_before_any_socket():
    with pytest.raises(ConfigurationError, match="MNSCR_PAYLOAD_API_KEY"):
        CinerieClient(CinerieConfig(base_url="http://127.0.0.1:9", api_key=""))


def test_url_with_embedded_credentials_is_refused():
    issues = CinerieConfig(
        base_url="https://user:senha@cms.example", api_key=SENTINEL_KEY
    ).issues()
    assert any("credencial embutida" in issue for issue in issues)


def test_url_pointing_at_an_endpoint_is_refused():
    issues = CinerieConfig(
        base_url="https://cms.example/api/internal/editorial-publications", api_key=SENTINEL_KEY
    ).issues()
    assert any("nao um endpoint" in issue for issue in issues)


def test_non_http_url_is_refused():
    issues = CinerieConfig(base_url="ftp://cms.example", api_key=SENTINEL_KEY).issues()
    assert any("http(s)" in issue for issue in issues)


def test_valid_config_has_no_issue():
    """Controle NEGATIVO: sem ele a validacao poderia recusar tudo."""
    assert CinerieConfig(base_url="https://cms.example", api_key=SENTINEL_KEY).issues() == []


@pytest.mark.parametrize("mode", ["DRAFT", "AUTO_PUBLISH"])
def test_delivery_mode_is_accepted(mode):
    assert resolve_delivery_mode(mode) == mode


def test_missing_delivery_mode_blocks_in_production():
    """Um default do codigo decidiria sozinho se a materia sai publicada."""
    from app.cinerie.errors import BlockedByPolicyError

    with pytest.raises(BlockedByPolicyError):
        resolve_delivery_mode(None, environment="production")


def test_invalid_delivery_mode_blocks_in_production():
    from app.cinerie.errors import BlockedByPolicyError

    with pytest.raises(BlockedByPolicyError):
        resolve_delivery_mode("PUBLISH_EVERYTHING", environment="production")


def test_missing_delivery_mode_falls_back_to_draft_outside_production():
    assert resolve_delivery_mode(None, environment="development") == MODE_DRAFT


# ===========================================================================
# Preflight
# ===========================================================================


def test_compatible_preflight_passes(server):
    result = ContractPreflight(make_client(server), clock=FakeClock()).check()
    assert result.compatible
    assert result.remote_version == local_identity().contract_version


def test_divergent_hash_fails_closed(server):
    server.contracts = {
        "contracts": [
            {
                "contractName": local_identity().contract_name,
                "contractVersion": "1.0.0",
                "schemaHash": "sha256:" + "0" * 64,
                "compatibility": "stable",
                "direction": "inbound",
            }
        ]
    }
    result = ContractPreflight(make_client(server), clock=FakeClock()).check()
    assert not result.compatible
    assert "schemaHash" in result.reason


def test_divergent_version_fails_closed(server):
    server.contracts = {
        "contracts": [
            {
                "contractName": local_identity().contract_name,
                "contractVersion": "2.0.0",
                "schemaHash": local_identity().schema_hash,
            }
        ]
    }
    result = ContractPreflight(make_client(server), clock=FakeClock()).check()
    assert not result.compatible
    assert "versao" in result.reason


def test_unknown_contract_fails_closed(server):
    server.contracts = {"contracts": []}
    result = ContractPreflight(make_client(server), clock=FakeClock()).check()
    assert not result.compatible
    assert "nao publica" in result.reason


def test_preflight_reason_never_shows_a_full_hash(server):
    server.contracts = {
        "contracts": [
            {
                "contractName": local_identity().contract_name,
                "contractVersion": "1.0.0",
                "schemaHash": "sha256:" + "f" * 64,
            }
        ]
    }
    result = ContractPreflight(make_client(server), clock=FakeClock()).check()
    assert "f" * 64 not in result.reason


def test_preflight_is_cached_within_the_ttl(server):
    clock = FakeClock()
    preflight = ContractPreflight(make_client(server), ttl_seconds=300, clock=clock)
    preflight.check()
    calls = server.call_count
    second = preflight.check()
    assert server.call_count == calls, "consultar o manifesto por materia e requisicao inutil"
    assert second.from_cache


def test_cache_expires_after_the_ttl(server):
    clock = FakeClock()
    preflight = ContractPreflight(make_client(server), ttl_seconds=60, clock=clock)
    preflight.check()
    calls = server.call_count
    clock.advance(61)
    preflight.check()
    assert server.call_count == calls + 1


def test_incompatible_result_is_never_cached(server):
    server.contracts = {"contracts": []}
    clock = FakeClock()
    preflight = ContractPreflight(make_client(server), ttl_seconds=300, clock=clock)
    preflight.check()
    calls = server.call_count
    preflight.check()
    assert server.call_count == calls + 1, "descobrir incompatibilidade e quando NAO se pode cachear"


def test_forced_preflight_ignores_the_cache(server):
    preflight = ContractPreflight(make_client(server), ttl_seconds=300, clock=FakeClock())
    preflight.check()
    calls = server.call_count
    preflight.check(force=True)
    assert server.call_count == calls + 1


def test_ensure_compatible_raises_on_divergence(server):
    server.contracts = {"contracts": []}
    with pytest.raises(ContractMismatchError):
        ContractPreflight(make_client(server), clock=FakeClock()).ensure_compatible()


# ===========================================================================
# Desfechos
# ===========================================================================


def test_published_is_recorded(server, store):
    server.script = [ScriptedResponse(201, published_body())]
    result = make_service(server, store).publish_draft(make_draft())
    assert result.outcome == O.PUBLISHED
    assert result.status == STATUS_COMPLETED
    assert result.article_id == "article-8801"
    assert store.get_by_request_id(result.request_id).delivered_at


def test_routed_to_review_is_success_with_a_wait(server, store):
    """202 nao e falha: o conteudo passou por todas as validacoes de forma."""
    server.script = [
        ScriptedResponse(
            202,
            published_body(
                outcome=O.ROUTED_TO_REVIEW,
                articleId="article-9001",
                reasons=[{"code": "QA_FAILED", "detail": "fato sem segunda fonte"}],
            ),
        )
    ]
    result = make_service(server, store).publish_draft(make_draft())
    assert result.outcome == O.ROUTED_TO_REVIEW
    assert result.status == STATUS_AWAITING_HUMAN
    assert result.reason_codes == ["QA_FAILED"]
    assert store.get_by_request_id(result.request_id).delivered_at


def test_conflict_goes_to_reconciliation(server, store):
    server.script = [
        ScriptedResponse(
            409,
            published_body(outcome=O.CONFLICT, reasons=[{"code": "STALE_REVISION", "detail": ""}]),
        )
    ]
    result = make_service(server, store).publish_draft(make_draft())
    assert result.outcome == O.CONFLICT
    assert result.status == STATUS_NEEDS_RECONCILIATION


def test_blocked_is_permanent(server, store):
    server.script = [
        ScriptedResponse(
            422,
            published_body(outcome=O.BLOCKED, reasons=[{"code": "MEDIA_NOT_AUTHORIZED", "detail": ""}]),
        )
    ]
    result = make_service(server, store).publish_draft(make_draft())
    assert result.status == STATUS_FAILED_PERMANENT
    assert server.publication_count == 1, "BLOCKED reenviado repete o defeito"


@pytest.mark.parametrize(
    "outcome,expected",
    [
        (O.PUBLISHED, False),
        (O.ROUTED_TO_REVIEW, False),
        (O.BLOCKED, False),
        (O.CONFLICT, False),
    ],
)
def test_no_outcome_is_worth_resending(outcome, expected):
    result = O.PublicationResult(outcome=outcome, http_status=O.OUTCOME_STATUS[outcome])
    assert O.should_resend(result)[0] is expected


def test_next_eligible_at_is_recorded_but_not_scheduled(server, store):
    """O horario e informativo: a materia ja esta na fila de um humano."""
    server.script = [
        ScriptedResponse(
            202,
            published_body(
                outcome=O.ROUTED_TO_REVIEW,
                reasons=[{"code": "DAILY_LIMIT_REACHED", "detail": "dimensao global"}],
                nextEligibleAt="2026-07-30T03:00:00.000Z",
            ),
        )
    ]
    result = make_service(server, store).publish_draft(make_draft())
    assert result.next_eligible_at == "2026-07-30T03:00:00.000Z"
    assert server.publication_count == 1


def test_author_change_requires_human_is_not_retried(server, store):
    server.script = [
        ScriptedResponse(
            202,
            published_body(
                outcome=O.ROUTED_TO_REVIEW,
                reasons=[{"code": O.AUTHOR_CHANGE_REQUIRES_HUMAN, "detail": ""}],
            ),
        )
    ]
    result = make_service(server, store).publish_draft(make_draft())
    assert result.status == STATUS_AWAITING_HUMAN
    assert server.publication_count == 1


def test_idempotent_replay_is_flagged(server, store):
    server.script = [ScriptedResponse(201, published_body(idempotent=True))]
    result = make_service(server, store).publish_draft(make_draft())
    assert store.get_by_request_id(result.request_id).idempotent_replay


# ===========================================================================
# Retry
# ===========================================================================


def test_retryable_503_is_retried_then_succeeds(server, store):
    server.script = [
        ScriptedResponse(503, {"outcome": O.OPERATIONAL_ERROR, "retryable": True, "code": "X"}),
        ScriptedResponse(201, published_body()),
    ]
    result = make_service(server, store).publish_draft(make_draft())
    assert result.outcome == O.PUBLISHED
    assert result.attempts == 2
    assert len(store.list_attempts(result.request_id)) == 2


def test_503_without_the_retryable_flag_is_not_retried(server, store):
    """Sem a marca nao ha promessa de que nada foi persistido."""
    server.script = [ScriptedResponse(503, {"error": "indisponivel"})]
    result = make_service(server, store).publish_draft(make_draft())
    assert result.error_code == "SERVER_ERROR_NOT_RETRYABLE"
    assert result.status == STATUS_NEEDS_RECONCILIATION
    assert server.publication_count == 1


def test_422_is_never_retried(server, store):
    server.script = [ScriptedResponse(422, published_body(outcome=O.BLOCKED))]
    make_service(server, store).publish_draft(make_draft())
    assert server.publication_count == 1


def test_retry_stops_at_the_configured_limit(server, store):
    server.default_response = ScriptedResponse(
        503, {"outcome": O.OPERATIONAL_ERROR, "retryable": True}
    )
    server.script = []
    result = make_service(
        server, store, retry_policy=RetryPolicy(max_attempts=2, base_seconds=1)
    ).publish_draft(make_draft())
    assert result.attempts == 2
    assert server.publication_count == 2, "nao existe retry infinito"


def test_retry_reuses_the_same_request_id(server, store):
    """Retry de rede repete o requestId: o Cinerie o reconhece e nao reconsome teto."""
    server.script = [
        ScriptedResponse(502, {"error": "bad gateway"}),
        ScriptedResponse(201, published_body()),
    ]
    make_service(server, store).publish_draft(make_draft())
    sent = [item for item in server.requests if item.path.endswith("editorial-publications")]
    assert len({item.body["requestId"] for item in sent}) == 1
    assert len({item.body["idempotencyKey"] for item in sent}) == 1


def test_retry_after_header_wins_over_the_local_guess(server, store):
    sleeper = FakeSleeper()
    server.script = [
        ScriptedResponse(429, {"error": "rate limited"}, headers={"Retry-After": "7"}),
        ScriptedResponse(201, published_body()),
    ]
    make_service(server, store, sleeper=sleeper).publish_draft(make_draft())
    assert 7.0 in sleeper.slept


def test_timeout_goes_to_reconciliation(server, store):
    """Timeout de LEITURA e ambiguo: o pedido pode ter sido aplicado."""
    server.default_response = ScriptedResponse(201, published_body(), delay_seconds=1.0)
    server.script = []
    result = make_service(
        server,
        store,
        client=make_client(server, timeout=0.15),
        retry_policy=RetryPolicy(max_attempts=1, base_seconds=0),
    ).publish_draft(make_draft())
    assert result.error_code == "TIMEOUT"
    assert result.status == STATUS_NEEDS_RECONCILIATION


# ===========================================================================
# Classificacao de erro no cliente
# ===========================================================================


@pytest.mark.parametrize(
    "status,expected",
    [
        (401, AuthenticationError),
        (403, PermissionError_),
        (429, RateLimitedError),
        (500, ServerError),
        (502, ServerError),
        (504, ServerError),
    ],
)
def test_http_status_is_classified(server, status, expected):
    server.script = [ScriptedResponse(status, {"error": "x"})]
    with pytest.raises(expected):
        make_client(server).submit_publication({"contractName": "x"})


def test_retryable_503_is_operational(server):
    server.script = [ScriptedResponse(503, {"outcome": O.OPERATIONAL_ERROR, "retryable": True})]
    with pytest.raises(OperationalError) as exc:
        make_client(server).submit_publication({})
    assert exc.value.retryable


def test_non_retryable_503_is_distinct(server):
    server.script = [ScriptedResponse(503, {"error": "x"})]
    with pytest.raises(NonRetryableServerError) as exc:
        make_client(server).submit_publication({})
    assert not exc.value.retryable


def test_non_json_body_is_an_invalid_response(server):
    server.script = [ScriptedResponse(200, raw_body=b"<html>gateway</html>")]
    with pytest.raises(InvalidRemoteResponseError):
        make_client(server).submit_publication({})


def test_2xx_without_an_outcome_never_becomes_presumed_success(server):
    """Presumir sucesso publicaria as cegas; retentar duplicaria."""
    server.script = [ScriptedResponse(201, {"ok": True})]
    with pytest.raises(InvalidRemoteResponseError):
        make_client(server).submit_publication({})


def test_unknown_outcome_is_refused(server):
    server.script = [ScriptedResponse(201, {"outcome": "PUBLICADO_TALVEZ"})]
    with pytest.raises(InvalidRemoteResponseError):
        make_client(server).submit_publication({})


def test_timeout_raises_a_timeout_error(server):
    server.default_response = ScriptedResponse(201, published_body(), delay_seconds=1.0)
    server.script = []
    with pytest.raises(CinerieTimeoutError):
        make_client(server, timeout=0.15).submit_publication({})


# ===========================================================================
# O que sai no corpo
# ===========================================================================


def test_authorization_uses_the_service_account_scheme(server, store):
    server.script = [ScriptedResponse(201, published_body())]
    make_service(server, store).publish_draft(make_draft())
    assert server.last_request.authorization == f"service-accounts API-Key {SENTINEL_KEY}"


def test_body_declares_the_contract_identity(server, store):
    server.script = [ScriptedResponse(201, published_body())]
    make_service(server, store).publish_draft(make_draft())
    body = server.last_request.body
    identity = local_identity()
    assert body["contractName"] == identity.contract_name
    assert body["contractVersion"] == identity.contract_version
    assert body["schemaHash"] == identity.schema_hash


def test_body_never_declares_technical_identity(server, store):
    """O ator tecnico e derivado da credencial, do lado do Cinerie."""
    server.script = [ScriptedResponse(201, published_body())]
    make_service(server, store).publish_draft(make_draft())
    flat = json.dumps(server.last_request.body).lower()
    for forbidden in (
        "technicalactor",
        "serviceaccount",
        "publishedby",
        "createdby",
        '"scopes"',
        "post_status",
        "yoast",
        "canonical",
        "robots",
        "jsonld",
        "datepublished",
    ):
        assert forbidden not in flat, f"campo proibido no corpo: {forbidden}"


def test_body_uses_publication_intent_only(server, store):
    server.script = [ScriptedResponse(201, published_body())]
    make_service(server, store).publish_draft(make_draft())
    body = server.last_request.body
    assert body["publicationIntent"] == "publish"
    assert "proposedAction" not in body
    assert "contractHash" not in body
    assert "schemaVersion" not in body


def test_publish_intent_carries_no_target(server, store):
    server.script = [ScriptedResponse(201, published_body())]
    make_service(server, store).publish_draft(make_draft())
    assert "targetArticleId" not in server.last_request.body


def test_body_carries_the_public_author_and_attribution(server, store):
    server.script = [ScriptedResponse(201, published_body())]
    make_service(server, store).publish_draft(make_draft())
    body = server.last_request.body
    assert body["publicAuthorId"] == AUTHOR_ID
    assert body["attributionMode"] == "newsroom"


def test_body_carries_sources_and_provenance(server, store):
    server.script = [ScriptedResponse(201, published_body())]
    make_service(server, store).publish_draft(make_draft())
    body = server.last_request.body
    assert len(body["externalSources"]) == 2
    assert body["externalSources"][0]["role"] == "primary"
    assert body["provenance"]["inputHash"] == "a" * 64
    assert body["sourcePayloadHash"] == "b" * 64


def test_generated_at_is_the_pipeline_time_not_now(server, store):
    server.script = [ScriptedResponse(201, published_body())]
    draft = make_draft()
    make_service(server, store).publish_draft(draft)
    assert server.last_request.body["generatedAt"] == draft.provenance.created_at


# ===========================================================================
# Bloqueio antes de qualquer socket
# ===========================================================================


def test_missing_gate_never_reaches_the_network(server, store):
    """Gate AUSENTE e pipeline que nao rodou, nao conteudo que precisa de revisao."""
    draft = make_draft()
    draft.editorial_gate = None
    result = make_service(server, store).publish_draft(draft)
    assert result.status == STATUS_BLOCKED
    assert "EDITORIAL_GATE_NAO_EXECUTADO" in result.blocked_reasons
    assert server.publication_count == 0


def test_missing_factual_assessment_never_reaches_the_network(server, store):
    draft = make_draft()
    draft.factual_assessment = None
    result = make_service(server, store).publish_draft(draft)
    assert result.status == STATUS_BLOCKED
    assert "AVALIACAO_FACTUAL_NAO_EXECUTADA" in result.blocked_reasons
    assert server.publication_count == 0


def test_blocked_gate_produces_a_failed_qa_not_a_silent_send(server, store):
    """Gate bloqueado nao vira silencio: vira `qa.passed=false` declarado."""
    server.script = [ScriptedResponse(202, published_body(outcome=O.ROUTED_TO_REVIEW))]
    draft = make_draft()
    draft.blocking_errors = ["FATO_SEM_SEGUNDA_FONTE"]
    make_service(server, store).publish_draft(draft)
    qa = server.last_request.body["qa"]
    assert qa["passed"] is False
    assert qa["blockingErrors"], "qa.passed=false exige um blockingError declarado"


def test_contract_mismatch_blocks_before_the_publication_endpoint(server, store):
    server.contracts = {"contracts": []}
    result = make_service(server, store).publish_draft(make_draft())
    assert result.error_code == "CONTRACT_MISMATCH"
    assert result.status == STATUS_BLOCKED
    assert server.publication_count == 0, "fail-closed: nada e enviado"


def test_unbuildable_draft_never_reaches_the_network(server, store):
    draft = make_draft()
    draft.content.body_html = ""
    result = make_service(server, store).publish_draft(draft)
    assert result.status == STATUS_BLOCKED
    assert server.publication_count == 0


# ===========================================================================
# Idempotencia e reconciliacao
# ===========================================================================


def test_the_same_work_is_not_published_twice(server, store):
    server.script = [ScriptedResponse(201, published_body())]
    service = make_service(server, store)
    first = service.publish_draft(make_draft())
    calls = server.publication_count

    second = service.publish_draft(make_draft())
    assert server.publication_count == calls, "nenhum HTTP na reexecucao"
    assert second.request_id == first.request_id
    assert second.outcome == O.PUBLISHED
    assert len(store.list_for_draft("draft-1")) == 1


def test_a_new_revision_is_a_new_work(server, store):
    server.script = [
        ScriptedResponse(201, published_body()),
        ScriptedResponse(201, published_body(articleId="article-8801")),
    ]
    service = make_service(server, store)
    service.publish_draft(make_draft(revision=3, draft_id="draft-1"))
    service.publish_draft(make_draft(revision=4, draft_id="draft-2"))
    assert server.publication_count == 2


def test_second_revision_becomes_an_update_of_the_same_article(server, store):
    server.script = [
        ScriptedResponse(201, published_body(articleId="article-8801")),
        ScriptedResponse(201, published_body(articleId="article-8801")),
    ]
    service = make_service(server, store)
    service.publish_draft(make_draft(revision=3, draft_id="draft-1"))
    service.publish_draft(make_draft(revision=4, draft_id="draft-2"))
    body = server.last_request.body
    assert body["publicationIntent"] == "update"
    assert body["targetArticleId"] == "article-8801"


def test_reconciliation_never_overwrites_a_newer_revision(server, store):
    server.script = [
        ScriptedResponse(201, published_body(articleId="article-1")),
        ScriptedResponse(409, published_body(outcome=O.CONFLICT)),
    ]
    service = make_service(server, store)
    service.publish_draft(make_draft(revision=5, draft_id="draft-new"))
    stale = service.publish_draft(make_draft(revision=2, draft_id="draft-old"))
    reconciled = service.reconcile(stale.request_id)
    assert reconciled.delivery_status == STATUS_FAILED_PERMANENT
    assert "REVISAO_SUPERADA" in reconciled.reason_codes


# ===========================================================================
# Persistencia
# ===========================================================================


def test_schema_is_created(store):
    cursor = store.conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row["name"] for row in cursor.fetchall()}
    assert {"cinerie_publications", "cinerie_publication_attempts"} <= tables


def test_migration_is_idempotent(tmp_path):
    path = str(tmp_path / "app.db")
    for _ in range(3):
        CinerieStore(db_path=path).close()
    store = CinerieStore(db_path=path)
    cursor = store.conn.cursor()
    cursor.execute("PRAGMA table_info(cinerie_publications)")
    names = [row["name"] for row in cursor.fetchall()]
    assert len(names) == len(set(names))
    store.close()


def test_migration_does_not_touch_earlier_phase_tables(store):
    """A fase do Cinerie e ADITIVA: nada de MS-1..MS-5 e criado ou alterado."""
    cursor = store.conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row["name"] for row in cursor.fetchall()}
    for foreign in (
        "rssprime_events",
        "ingestion_cursors",
        "editorial_gate_results",
        "factual_assessments",
        "editorial_deliveries",
        "seen_articles",
    ):
        assert foreign not in tables


def test_state_survives_a_restart(tmp_path):
    path = str(tmp_path / "app.db")
    first = CinerieStore(db_path=path)
    first.create_publication(
        request_id="req-1",
        idempotency_key="mnscr:c:rev-1",
        draft_id="d",
        source_cluster_id="c",
        source_revision=1,
        delivery_mode=MODE_AUTO_PUBLISH,
        contract_name="editorial-publication-request-v1",
        contract_version="1.0.0",
        schema_hash="sha256:" + "a" * 64,
        public_author_id=AUTHOR_ID,
        publication_intent="publish",
    )
    first.update_result(
        "req-1",
        delivery_status=STATUS_COMPLETED,
        payload_outcome=O.PUBLISHED,
        payload_article_id="article-1",
        delivered=True,
    )
    first.close()

    reopened = CinerieStore(db_path=path)
    record = reopened.get_by_request_id("req-1")
    assert record.payload_article_id == "article-1"
    assert record.is_published
    reopened.close()


def test_duplicate_idempotency_key_is_refused_by_the_database(store):
    args = dict(
        idempotency_key="mnscr:c:rev-1",
        draft_id="d",
        source_cluster_id="c",
        source_revision=1,
        delivery_mode=MODE_AUTO_PUBLISH,
        contract_name="editorial-publication-request-v1",
        contract_version="1.0.0",
        schema_hash="sha256:" + "a" * 64,
        public_author_id=AUTHOR_ID,
        publication_intent="publish",
    )
    assert store.create_publication(request_id="req-1", **args) is not None
    assert store.create_publication(request_id="req-2", **args) is None


def test_attempts_are_append_only(server, store):
    server.script = [
        ScriptedResponse(502, {"error": "x"}),
        ScriptedResponse(201, published_body()),
    ]
    result = make_service(server, store).publish_draft(make_draft())
    attempts = store.list_attempts(result.request_id)
    assert [item["attempt"] for item in attempts] == [1, 2]
    assert attempts[0]["error_code"] == "SERVER_ERROR"
    assert attempts[1]["outcome"] == O.PUBLISHED


def test_the_api_key_is_never_persisted(server, store):
    server.script = [ScriptedResponse(201, published_body())]
    result = make_service(server, store).publish_draft(make_draft())
    cursor = store.conn.cursor()
    cursor.execute("SELECT * FROM cinerie_publications WHERE request_id = ?", (result.request_id,))
    assert SENTINEL_KEY not in str(dict(cursor.fetchone()))


def test_unknown_status_is_refused(store):
    with pytest.raises(ValueError):
        store.update_result("req-x", delivery_status="TALVEZ")


# ===========================================================================
# Segredo
# ===========================================================================


def test_the_sentinel_key_never_appears_in_logs(server, store, caplog):
    """Injeta uma chave sentinela e prova que ela nao vaza em lugar nenhum."""
    import logging

    caplog.set_level(logging.DEBUG)
    server.script = [
        ScriptedResponse(502, {"error": "x"}),
        ScriptedResponse(201, published_body()),
    ]
    result = make_service(server, store).publish_draft(make_draft())

    assert SENTINEL_KEY not in caplog.text
    assert SENTINEL_KEY not in str(result.safe_log_fields())
    for attempt in store.list_attempts(result.request_id):
        assert SENTINEL_KEY not in str(attempt)


def test_the_key_never_appears_in_an_exception(server):
    server.script = [ScriptedResponse(401, {"error": "unauthenticated"})]
    with pytest.raises(AuthenticationError) as exc:
        make_client(server).submit_publication({})
    assert SENTINEL_KEY not in str(exc.value)
    assert SENTINEL_KEY not in repr(exc.value)


def test_config_repr_masks_the_key():
    config = CinerieConfig(base_url="https://cms.example", api_key=SENTINEL_KEY)
    assert SENTINEL_KEY not in repr(config)
    assert "***" in repr(config)


def test_client_repr_masks_the_key(server):
    assert SENTINEL_KEY not in repr(make_client(server))


def test_safe_url_drops_the_query_string():
    """Uma querystring pode carregar credencial."""
    assert _safe_url("https://cms.example/api?token=segredo") == "https://cms.example/api"


def test_safe_url_drops_embedded_credentials():
    assert "senha" not in _safe_url("https://user:senha@cms.example/api")


def test_error_paths_never_echo_the_article_body(server, store, caplog):
    import logging

    caplog.set_level(logging.DEBUG)
    server.script = [ScriptedResponse(422, published_body(outcome=O.BLOCKED))]
    draft = make_draft()
    make_service(server, store).publish_draft(draft)
    assert FILLER not in caplog.text
