"""Orquestracao da publicacao no Cinerie.

A ordem e o produto:

1. **preflight contratual** — nome, versao e hash. Divergiu, nada sai;
2. **montagem** do pedido a partir do draft;
3. **validacao** local: campos recusados, JSON Schema, refinamentos;
4. **persistencia** do registro em ``PENDING``;
5. **envio** com retry governado;
6. **persistencia** do desfecho.

Os passos 1-3 acontecem **antes de qualquer socket**. Um pedido invalido nao
custa conexao, nao consome teto de publicacao do dia e nao aparece na auditoria
do Cinerie como tentativa.

Uma decisao explicita: ``ROUTED_TO_REVIEW`` **nao e falha**. Ele conclui o item
com estado ``AWAITING_HUMAN`` e nao gera reenvio. Tratar 202 como erro faria o
pipeline reenviar material perfeitamente aceito, gastar o teto do dia e encher a
auditoria de tentativas que nao eram tentativas.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from app.cinerie import outcomes as O
from app.cinerie.client import CinerieClient
from app.cinerie.contract import ContractIdentity, local_identity
from app.cinerie.errors import (
    BlockedByPolicyError,
    CinerieError,
    ContractMismatchError,
    RequestBuildError,
    SchemaValidationError,
)
from app.cinerie.preflight import ContractPreflight, PreflightResult
from app.cinerie.request import INTENT_PUBLISH, INTENT_UPDATE, BuiltRequest, build_publication_request
from app.cinerie.validation import ensure_valid
from app.cinerie_store import (
    STATUS_AWAITING_HUMAN,
    STATUS_BLOCKED,
    STATUS_COMPLETED,
    STATUS_DEFERRED,
    STATUS_FAILED_PERMANENT,
    STATUS_FAILED_RETRYABLE,
    STATUS_IN_PROGRESS,
    STATUS_NEEDS_RECONCILIATION,
    CinerieStore,
    PublicationRecord,
)
from app.delivery.retry import Clock, RetryPolicy, Sleeper

logger = logging.getLogger(__name__)

MODE_DRAFT: str = "DRAFT"
MODE_AUTO_PUBLISH: str = "AUTO_PUBLISH"
DELIVERY_MODES = frozenset({MODE_DRAFT, MODE_AUTO_PUBLISH})

#: Falhas em que o pedido PODE ter sido aplicado do outro lado.
#:
#: Todas acontecem depois de os bytes terem saido: o timeout de LEITURA, uma
#: resposta que nao da para interpretar e um erro de servidor nao retentavel. A
#: diferenca para `CONNECTION_ERROR` importa — la a conexao nem se estabeleceu,
#: nada foi aplicado, e o reenvio e limpo.
AMBIGUOUS_ERROR_CODES = frozenset(
    {"TIMEOUT", "INVALID_REMOTE_RESPONSE", "SERVER_ERROR_NOT_RETRYABLE"}
)

#: Estados em que perguntar de novo so custa cota.
TERMINAL_DELIVERY_STATUSES = frozenset(
    {STATUS_COMPLETED, STATUS_BLOCKED, STATUS_FAILED_PERMANENT, STATUS_AWAITING_HUMAN}
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class PublicationAttemptResult:
    """O que aconteceu com uma tentativa de publicacao."""

    status: str
    request_id: Optional[str] = None
    idempotency_key: Optional[str] = None
    outcome: Optional[str] = None
    article_id: Optional[str] = None
    reason_codes: List[str] = field(default_factory=list)
    blocked_reasons: List[str] = field(default_factory=list)
    next_eligible_at: Optional[str] = None
    attempts: int = 0
    error_code: Optional[str] = None
    record: Optional[PublicationRecord] = None
    preflight: Optional[PreflightResult] = None

    @property
    def sent(self) -> bool:
        return self.attempts > 0

    @property
    def published(self) -> bool:
        return self.outcome == O.PUBLISHED

    @property
    def deferred(self) -> bool:
        """Teto diario esgotado: reenviar este mesmo pedido depois da virada."""
        return self.outcome == O.DEFERRED

    def safe_log_fields(self) -> Dict[str, Any]:
        return {
            "delivery_status": self.status,
            "request_id": self.request_id,
            "outcome": self.outcome,
            "article_id": self.article_id,
            "reason_codes": self.reason_codes,
            "next_eligible_at": self.next_eligible_at,
            "attempts": self.attempts,
            "error_code": self.error_code,
        }


class CinerieService:
    """Publica um draft no Cinerie, ou explica por que nao publicou."""

    def __init__(
        self,
        *,
        store: CinerieStore,
        client: CinerieClient,
        public_author_id: str,
        attribution_mode: str,
        delivery_mode: str = MODE_AUTO_PUBLISH,
        preflight: Optional[ContractPreflight] = None,
        retry_policy: Optional[RetryPolicy] = None,
        sleeper: Optional[Sleeper] = None,
        clock: Optional[Clock] = None,
        identity: Optional[ContractIdentity] = None,
        pipeline_version: Optional[str] = None,
    ) -> None:
        if delivery_mode not in DELIVERY_MODES:
            raise ValueError(f"delivery_mode desconhecido: {delivery_mode!r}")
        self.store = store
        self.client = client
        self.public_author_id = public_author_id
        self.attribution_mode = attribution_mode
        self.delivery_mode = delivery_mode
        self.identity = identity or local_identity()
        self.preflight = preflight or ContractPreflight(client, identity=self.identity)
        self.retry_policy = retry_policy or RetryPolicy()
        self.sleeper = sleeper or Sleeper()
        self.clock = clock or Clock()
        self.pipeline_version = pipeline_version

    # -- caminho principal -------------------------------------------------

    def publish_draft(
        self,
        draft: Any,
        *,
        intent: Optional[str] = None,
        target_article_id: Optional[str] = None,
        seo_source: Optional[Dict[str, Any]] = None,
        internal_links: Sequence[Dict[str, Any]] = (),
        media: Sequence[Dict[str, Any]] = (),
        entity_links: Sequence[Dict[str, Any]] = (),
    ) -> PublicationAttemptResult:
        draft_id = str(getattr(draft, "draft_id", "") or "")

        # 0. Pre-condicoes LOCAIS. Ver `_local_preconditions`.
        missing = _local_preconditions(draft)
        if missing:
            logger.error(
                "[cinerie] draft %s sem pre-condicao local (%s); nada enviado", draft_id, missing
            )
            return PublicationAttemptResult(
                status=STATUS_BLOCKED,
                blocked_reasons=missing,
                error_code="LOCAL_PRECONDITION_MISSING",
            )

        # 1. Preflight. Fail-closed e ANTES de montar: se o contrato divergiu,
        #    montar o pedido seria trabalho jogado fora.
        try:
            preflight = self.preflight.ensure_compatible()
        except ContractMismatchError as exc:
            self.preflight.invalidate()
            logger.error(
                "[cinerie] preflight incompativel — nenhuma publicacao enviada: %s", exc
            )
            return PublicationAttemptResult(
                status=STATUS_BLOCKED,
                blocked_reasons=["CONTRACT_MISMATCH"],
                error_code="CONTRACT_MISMATCH",
            )
        except CinerieError as exc:
            logger.warning("[cinerie] preflight indisponivel: %s", exc)
            return PublicationAttemptResult(
                status=STATUS_FAILED_RETRYABLE if exc.retryable else STATUS_FAILED_PERMANENT,
                blocked_reasons=[exc.code],
                error_code=exc.code,
            )

        # 2-3. Montagem e validacao — ainda sem socket.
        resolved_intent, resolved_target = self._resolve_intent(draft, intent, target_article_id)
        try:
            built = build_publication_request(
                draft,
                public_author_id=self.public_author_id,
                attribution_mode=self.attribution_mode,
                intent=resolved_intent,
                target_article_id=resolved_target,
                seo_source=seo_source,
                internal_links=internal_links,
                media=media,
                entity_links=entity_links,
                pipeline_version=self.pipeline_version,
                contract=self.identity,
            )
            ensure_valid(built.payload, identity=self.identity)
        except (RequestBuildError, SchemaValidationError, BlockedByPolicyError) as exc:
            paths = getattr(exc, "paths", []) or []
            logger.error(
                "[cinerie] pedido invalido para o draft %s (%s): campos %s",
                draft_id,
                exc.code,
                paths[:8],
            )
            return PublicationAttemptResult(
                status=STATUS_BLOCKED,
                blocked_reasons=[exc.code, *paths[:8]],
                error_code=exc.code,
                preflight=preflight,
            )

        # 4. Registro local ANTES do envio. Um envio sem rastro e um envio que
        #    nao se sabe se aconteceu.
        record = self.store.create_publication(
            request_id=built.identity.request_id,
            idempotency_key=built.identity.idempotency_key,
            draft_id=draft_id,
            source_cluster_id=built.identity.source_cluster_id,
            source_revision=built.identity.source_revision,
            delivery_mode=self.delivery_mode,
            contract_name=self.identity.contract_name,
            contract_version=self.identity.contract_version,
            schema_hash=self.identity.schema_hash,
            public_author_id=self.public_author_id,
            publication_intent=resolved_intent,
        )

        if record is None:
            existing = self.store.get_by_request_id(
                built.identity.request_id
            ) or self.store.get_by_idempotency_key(built.identity.idempotency_key)
            replay = self._replay(existing, built, preflight)
            if replay is not None:
                return replay
            record = existing

        logger.info("[cinerie] publicando draft %s", built.safe_log_fields())

        # 5-6. Envio com retry, e persistencia do desfecho.
        return self._send(built, preflight=preflight)

    # -- envio -------------------------------------------------------------

    def _send(self, built: BuiltRequest, *, preflight: PreflightResult) -> PublicationAttemptResult:
        request_id = built.identity.request_id
        last_error: Optional[CinerieError] = None
        # Ambiguidade nao expira. Depois de um timeout de LEITURA o pedido pode
        # ter sido aplicado do outro lado, e nenhuma tentativa posterior desfaz
        # esse fato: um 500 na tentativa 2 nao prova que a 1 nao entrou. Sem esta
        # memoria o desfecho final era o do ULTIMO erro, e o registro terminava
        # como FAILED_RETRYABLE — "pode reenviar" — quando a verdade era
        # "consulte o destino antes de decidir".
        saw_ambiguous = False

        for attempt in range(1, self.retry_policy.max_attempts + 1):
            delay = self.retry_policy.delay_for(
                attempt,
                seed=request_id,
                retry_after_seconds=getattr(last_error, "retry_after_seconds", None),
            )
            if delay > 0:
                self.sleeper.sleep(delay)

            started_at = _utc_now_iso()
            started = self.clock.now()
            self.store.update_result(request_id, delivery_status=STATUS_IN_PROGRESS)

            try:
                result = self.client.submit_publication(built.payload)
            except CinerieError as exc:
                duration = self.clock.elapsed_ms_since(started)
                self.store.record_attempt(
                    request_id=request_id,
                    attempt=attempt,
                    started_at=started_at,
                    duration_ms=duration,
                    error_code=exc.code,
                    error_message=str(exc),
                    retry_after_seconds=getattr(exc, "retry_after_seconds", None),
                )
                last_error = exc
                if exc.code in AMBIGUOUS_ERROR_CODES:
                    saw_ambiguous = True

                if exc.code == "CONTRACT_MISMATCH":
                    # O contrato mudou no meio do caminho: o cache guardado nao
                    # vale mais nada.
                    self.preflight.invalidate()

                if not exc.retryable or attempt >= self.retry_policy.max_attempts:
                    return self._finish_with_error(
                        built, exc, attempts=attempt, preflight=preflight,
                        ambiguous=saw_ambiguous,
                    )
                logger.warning(
                    "[cinerie] tentativa %s/%s falhou (%s); nova tentativa",
                    attempt,
                    self.retry_policy.max_attempts,
                    exc.code,
                )
                continue

            duration = self.clock.elapsed_ms_since(started)
            result.attempts = attempt
            self.store.record_attempt(
                request_id=request_id,
                attempt=attempt,
                started_at=started_at,
                duration_ms=duration,
                http_status=result.http_status,
                outcome=result.outcome,
                retry_after_seconds=result.retry_after_seconds,
            )
            return self._finish_with_result(built, result, preflight=preflight)

        # Inalcancavel: o laco sempre retorna. Mantido para que uma mudanca
        # futura na condicao nao caia em `None` silencioso.
        return self._finish_with_error(
            built,
            last_error or CinerieError("retry esgotado sem erro registrado"),
            attempts=self.retry_policy.max_attempts,
            preflight=preflight,
        )

    # -- desfechos ---------------------------------------------------------

    def _finish_with_result(
        self, built: BuiltRequest, result: O.PublicationResult, *, preflight: PreflightResult
    ) -> PublicationAttemptResult:
        request_id = built.identity.request_id
        status = self._status_for(result)

        record = self.store.update_result(
            request_id,
            delivery_status=status,
            payload_outcome=result.outcome,
            payload_article_id=result.article_id,
            canonical_slug=result.canonical_slug,
            reason_codes=result.reason_codes,
            next_eligible_at=result.next_eligible_at,
            http_status=result.http_status,
            retry_count=max(0, result.attempts - 1),
            idempotent_replay=result.idempotent,
            delivered=result.accepted,
        )

        logger.info("[cinerie] desfecho %s", result.safe_log_fields())

        if result.author_change_requires_human:
            # Troca de autor recusada: NADA do update foi aplicado, nem o corpo.
            # Reenviar so repetiria a recusa.
            logger.warning(
                "[cinerie] troca de autor exige humano; nada do update foi aplicado (%s)",
                request_id,
            )

        if result.deferred:
            # Teto diario da redacao. Nada foi persistido e nenhum contador foi
            # consumido: o MESMO corpo, com o MESMO requestId, passa depois da
            # virada. Gerar identidade nova no reenvio seria declarar uma
            # publicacao nova e gastar uma vaga do teto de amanha.
            logger.info(
                "[cinerie] teto diario esgotado (%s); reenvio do mesmo requestId "
                "so a partir de %s",
                ", ".join(result.deferral_codes) or "dimensao nao declarada",
                result.next_eligible_at or "horario nao informado",
            )
        elif result.next_eligible_at and result.outcome == O.ROUTED_TO_REVIEW:
            # Aqui o horario e informativo. Transforma-lo em agendamento faria o
            # pipeline reenviar uma materia que ja esta na fila de um humano.
            logger.info(
                "[cinerie] nextEligibleAt=%s num desfecho ja aceito; sem reenvio automatico",
                result.next_eligible_at,
            )

        if result.article_update_limit_reached:
            # Teto de reescrita da MESMA materia: vem como BLOCKED, sem horario
            # prometido. O contador reabre a meia-noite, mas prometer isso seria
            # autorizar reescrever a mesma materia todo dia — exatamente o que o
            # teto existe para conter.
            logger.warning(
                "[cinerie] teto de reescrita da materia atingido (%s); "
                "outra revisao automatica passa por decisao humana",
                request_id,
            )

        return PublicationAttemptResult(
            status=status,
            request_id=request_id,
            idempotency_key=built.identity.idempotency_key,
            outcome=result.outcome,
            article_id=result.article_id,
            reason_codes=result.reason_codes,
            next_eligible_at=result.next_eligible_at,
            attempts=result.attempts,
            record=record,
            preflight=preflight,
        )

    @staticmethod
    def _status_for(result: O.PublicationResult) -> str:
        if result.outcome == O.PUBLISHED:
            return STATUS_COMPLETED
        if result.outcome == O.ROUTED_TO_REVIEW:
            # Sucesso com espera humana. Nao e falha do pipeline.
            return STATUS_AWAITING_HUMAN
        if result.outcome == O.DEFERRED:
            # Espera de relogio, nao de humano nem de conserto. O pedido esta
            # correto; o dia e que acabou.
            return STATUS_DEFERRED
        if result.outcome == O.CONFLICT:
            return STATUS_NEEDS_RECONCILIATION
        if result.outcome == O.BLOCKED:
            return STATUS_FAILED_PERMANENT
        return STATUS_FAILED_RETRYABLE

    def _finish_with_error(
        self,
        built: BuiltRequest,
        exc: CinerieError,
        *,
        attempts: int,
        preflight: PreflightResult,
        ambiguous: bool = False,
    ) -> PublicationAttemptResult:
        request_id = built.identity.request_id

        # Timeout de LEITURA esgotado e ambiguo: o pedido pode ter sido aplicado.
        # Recriar seria duplicar; declarar falha esconderia uma publicacao real.
        # `ambiguous` chega True quando QUALQUER tentativa desta rodada foi
        # ambigua, e nao so a ultima.
        ambiguous = ambiguous or exc.code in AMBIGUOUS_ERROR_CODES
        status = (
            STATUS_NEEDS_RECONCILIATION
            if ambiguous
            else (STATUS_FAILED_RETRYABLE if exc.retryable else STATUS_FAILED_PERMANENT)
        )

        # O ultimo erro nem sempre e o que importa. Quando alguma tentativa foi
        # ambigua, o motivo do estado e a ambiguidade — nao o 500 que veio
        # depois. Sem esta marca o operador le "SERVER_ERROR" e conclui que basta
        # reenviar, que e exatamente a decisao errada.
        reason_codes = [exc.code]
        if ambiguous:
            reason_codes.append("RESULTADO_AMBIGUO")
        record = self.store.update_result(
            request_id,
            delivery_status=status,
            reason_codes=reason_codes,
            error_code=exc.code,
            retry_count=max(0, attempts - 1),
        )
        logger.error(
            "[cinerie] publicacao nao concluida (%s) apos %s tentativa(s): %s",
            exc.code,
            attempts,
            exc,
        )
        return PublicationAttemptResult(
            status=status,
            request_id=request_id,
            idempotency_key=built.identity.idempotency_key,
            attempts=attempts,
            error_code=exc.code,
            reason_codes=list(reason_codes),
            record=record,
            preflight=preflight,
        )

    # -- idempotencia e reconciliacao --------------------------------------

    def _replay(
        self,
        existing: Optional[PublicationRecord],
        built: BuiltRequest,
        preflight: PreflightResult,
    ) -> Optional[PublicationAttemptResult]:
        """Este trabalho ja teve desfecho? Entao nao ha o que reenviar."""
        if existing is None or existing.payload_outcome is None:
            return None
        if existing.payload_outcome not in O.TERMINAL:
            return None

        logger.info(
            "[cinerie] trabalho ja concluido (%s / %s); nada reenviado",
            existing.request_id,
            existing.payload_outcome,
        )
        return PublicationAttemptResult(
            status=existing.delivery_status,
            request_id=existing.request_id,
            idempotency_key=existing.idempotency_key,
            outcome=existing.payload_outcome,
            article_id=existing.payload_article_id,
            reason_codes=list(existing.reason_codes or []),
            next_eligible_at=existing.next_eligible_at,
            attempts=0,
            record=existing,
            preflight=preflight,
        )

    def _resolve_intent(
        self, draft: Any, intent: Optional[str], target_article_id: Optional[str]
    ) -> tuple[str, Optional[str]]:
        """``publish`` ou ``update``, decidido pelo historico local.

        Quando ja existe artigo publicado para o mesmo agrupamento, a revisao
        seguinte e um ``update`` do MESMO artigo — a identidade e preservada, e o
        autor publico nao muda por conta propria.
        """
        if intent is not None:
            return intent, target_article_id

        cluster_id = None
        try:
            from app.cinerie.identity import normalize_cluster_id

            cluster_id = normalize_cluster_id(getattr(draft, "event_key", None))
        except RequestBuildError:
            return INTENT_PUBLISH, None

        article_id = self.store.published_article_for_cluster(cluster_id)
        if article_id:
            return INTENT_UPDATE, article_id
        return INTENT_PUBLISH, None

    def reconcile_ambiguous(
        self,
        request_id: str,
        *,
        draft: Any,
        **build_kwargs: Any,
    ) -> Optional[PublicationRecord]:
        """Descobre o que aconteceu com um pedido de desfecho desconhecido.

        Nao existe endpoint de consulta neste contrato. Quem responde "isto
        chegou?" e o proprio POST, desde que ele seja IDENTICO: com o mesmo
        ``requestId`` e a mesma ``idempotencyKey``, o Cinerie reconhece o pedido,
        devolve o desfecho original com ``idempotent: true`` e **nao** reaplica
        nem consome teto.

        Por isso a unica coisa que esta funcao nao pode fazer e inventar
        identidade. Se o pedido for remontado a partir de um corpo diferente, o
        hash muda, a chave muda, e o que era uma pergunta vira uma publicacao
        nova — segundo artigo e mais uma fatia do teto diario.

        Item ja terminado nao e reconciliado: perguntar de novo custa cota e nao
        acrescenta nada.
        """
        record = self.store.get_by_request_id(request_id)
        if record is None:
            return None

        if record.delivery_status in TERMINAL_DELIVERY_STATUSES:
            logger.info(
                "[cinerie] reconciliacao dispensada: %s ja esta em %s",
                request_id, record.delivery_status,
            )
            return record

        try:
            preflight = self.preflight.ensure_compatible()
        except CinerieError as exc:
            logger.warning("[cinerie] reconciliacao adiada, preflight indisponivel: %s", exc)
            return record

        resolved_intent, resolved_target = self._resolve_intent(draft, None, None)
        built = build_publication_request(
            draft,
            public_author_id=self.public_author_id,
            attribution_mode=self.attribution_mode,
            intent=resolved_intent,
            target_article_id=resolved_target,
            pipeline_version=self.pipeline_version,
            contract=self.identity,
            **build_kwargs,
        )

        # A guarda que sustenta a seguranca de todo o resto.
        if (
            built.identity.request_id != record.request_id
            or built.identity.idempotency_key != record.idempotency_key
        ):
            logger.error(
                "[cinerie] reconciliacao ABORTADA: a remontagem gerou outra "
                "identidade (gravado=%s remontado=%s). Reenviar criaria um "
                "segundo artigo e queimaria cota.",
                record.request_id, built.identity.request_id,
            )
            return self.store.update_result(
                request_id,
                delivery_status=STATUS_NEEDS_RECONCILIATION,
                reason_codes=[*(record.reason_codes or []), "IDENTIDADE_NAO_REPRODUZIVEL"],
            )

        result = self._send(built, preflight=preflight)
        return result.record or self.store.get_by_request_id(request_id)

    def reconcile(self, request_id: str) -> Optional[PublicationRecord]:
        """Reconciliacao de ``CONFLICT``, sem sobrescrever revisao mais nova.

        A regra e uma so e nao tem excecao: **nunca sobrescrever uma revisao
        maior**. Um conflito costuma ser pedido fora de ordem, e insistir nele
        substituiria o texto novo pelo velho.
        """
        record = self.store.get_by_request_id(request_id)
        if record is None:
            return None
        applied = self.store.highest_applied_revision(record.source_cluster_id)
        if applied is not None and record.source_revision <= applied:
            logger.info(
                "[cinerie] conflito reconciliado: revisao %s <= aplicada %s; nada a reenviar",
                record.source_revision,
                applied,
            )
            return self.store.update_result(
                request_id,
                delivery_status=STATUS_FAILED_PERMANENT,
                reason_codes=[*(record.reason_codes or []), "REVISAO_SUPERADA"],
            )
        return record


def _local_preconditions(draft: Any) -> List[str]:
    """Etapas do MNScr que precisam ter RODADO antes de pedir publicacao.

    A distincao aqui e sutil e vale explicitar, porque ela muda em relacao a
    entrega de rascunho da fase anterior:

    * um gate que **avaliou e reprovou** nao bloqueia o envio. Esse e o canal
      que o contrato desenhou: o pedido sai com ``qa.passed = false`` e os erros
      declarados, e o Cinerie o encaminha para revisao humana. Jogar fora um
      conteudo que so precisa de um par de olhos seria perder trabalho valido.
    * um gate **ausente** bloqueia. Nao e conteudo que precisa de revisao: e o
      pipeline que nao rodou. Declarar QA sobre algo que nunca foi avaliado
      seria afirmar o que nao se sabe — e o campo ``qa`` do contrato existe
      exatamente para carregar esse julgamento.
    """
    missing: List[str] = []
    if getattr(draft, "editorial_gate", None) is None:
        missing.append("EDITORIAL_GATE_NAO_EXECUTADO")
    if getattr(draft, "factual_assessment", None) is None:
        missing.append("AVALIACAO_FACTUAL_NAO_EXECUTADA")
    if getattr(getattr(draft, "content", None), "body_html", "") in (None, ""):
        missing.append("CORPO_AUSENTE")
    return missing


def resolve_delivery_mode(value: Optional[str], *, environment: str = "development") -> str:
    """Modo de entrega, sem default silencioso em producao.

    Em ``production`` a variavel ausente ou invalida **bloqueia**: um default
    escolhido pelo codigo decidiria sozinho se a materia sai publicada ou como
    rascunho, e essa e a decisao mais consequente da configuracao inteira.
    """
    candidate = str(value or "").strip().upper()
    if candidate in DELIVERY_MODES:
        return candidate
    if environment.lower().startswith("prod"):
        raise BlockedByPolicyError(
            "MNSCR_DELIVERY_MODE ausente ou invalido em producao; "
            f"defina explicitamente {sorted(DELIVERY_MODES)}"
        )
    return MODE_DRAFT


__all__ = [
    "CinerieService",
    "DELIVERY_MODES",
    "MODE_AUTO_PUBLISH",
    "MODE_DRAFT",
    "PublicationAttemptResult",
    "resolve_delivery_mode",
]
