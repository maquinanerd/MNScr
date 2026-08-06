"""Os desfechos do Cinerie, e o que cada um significa para o pipeline.

O desfecho vem do servidor; o que muda aqui e a **reacao**. A confusao cara e
tratar ``ROUTED_TO_REVIEW`` como falha: o conteudo foi aceito, validado e
guardado — o que falta e julgamento humano. Reenviar nao acelera nada, consome
teto e polui a auditoria com tentativas que nao eram tentativas.

+---------------------+------+--------------------------------+------------------+
| desfecho            | HTTP | significa                      | reenviar?        |
+---------------------+------+--------------------------------+------------------+
| PUBLISHED           | 201  | materia publicada              | nao              |
| ROUTED_TO_REVIEW    | 202  | aceito, aguardando humano      | nao              |
| DEFERRED            | 429  | teto diario esgotado           | so depois de     |
|                     |      | (nada persistido)              | nextEligibleAt   |
| CONFLICT            | 409  | estado divergente              | so apos          |
|                     |      |                                | reconciliar      |
| BLOCKED             | 422  | defeito permanente no pedido   | nao              |
| OPERATIONAL_ERROR   | 503  | falha de plataforma            | sim, com backoff |
+---------------------+------+--------------------------------+------------------+

``DEFERRED`` e o desfecho que mais engana, e por dois motivos opostos.

Ele chega em **429**, o codigo que qualquer cliente HTTP trata como "voce esta
rapido demais" — mas aqui ele nao fala de taxa, e sim de **teto diario da
redacao**. Retentar em segundos nao resolve: a janela e o dia civil no fuso da
redacao, e o ``Retry-After`` costuma vir em horas. Por isso ele **nao** entra no
laco de tentativas; ele sai daqui como desfecho, com ``nextEligibleAt``, para
que o agendador reenvie **o mesmo corpo com o mesmo requestId** depois da
virada. Um requestId novo contaria como publicacao nova e consumiria uma vaga
do teto do dia seguinte.

E, ao contrario de ``BLOCKED``, ele **nao** e defeito do pedido: nada foi
persistido, nenhum contador foi consumido, e o mesmo corpo passa amanha sem uma
linha alterada. Tratar isso como falha permanente joga fora uma materia boa.

O parente proximo e a armadilha: o teto de **reescrita da mesma materia**
(``AUTO_PUBLISH_ARTICLE_UPDATE_LIMIT_REACHED``) responde ``BLOCKED`` 422, sem
``Retry-After`` e sem horario prometido — decisao de produto, nao limitacao
tecnica. Ali o reenvio automatico para de vez.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Final, FrozenSet, List, Mapping, Optional, Tuple

PUBLISHED: Final[str] = "PUBLISHED"
ROUTED_TO_REVIEW: Final[str] = "ROUTED_TO_REVIEW"
DEFERRED: Final[str] = "DEFERRED"
CONFLICT: Final[str] = "CONFLICT"
BLOCKED: Final[str] = "BLOCKED"
OPERATIONAL_ERROR: Final[str] = "OPERATIONAL_ERROR"

OUTCOMES: Final[FrozenSet[str]] = frozenset(
    {PUBLISHED, ROUTED_TO_REVIEW, DEFERRED, CONFLICT, BLOCKED, OPERATIONAL_ERROR}
)

#: Desfecho -> HTTP, conforme `outcomeHttpStatus` do Cinerie.
OUTCOME_STATUS: Final[Dict[str, int]] = {
    PUBLISHED: 201,
    ROUTED_TO_REVIEW: 202,
    DEFERRED: 429,
    CONFLICT: 409,
    BLOCKED: 422,
    OPERATIONAL_ERROR: 503,
}

#: Desfechos em que o conteudo FOI aceito pelo servidor.
ACCEPTED: Final[FrozenSet[str]] = frozenset({PUBLISHED, ROUTED_TO_REVIEW})

#: Desfechos que NUNCA devem ser reenviados automaticamente.
#:
#: ``DEFERRED`` esta deliberadamente FORA: ele sera reenviado — so nao agora.
TERMINAL: Final[FrozenSet[str]] = frozenset({PUBLISHED, ROUTED_TO_REVIEW, BLOCKED})

#: Codigo que o Cinerie usa quando a troca de autor exige humano.
AUTHOR_CHANGE_REQUIRES_HUMAN: Final[str] = "AUTHOR_CHANGE_REQUIRES_HUMAN"

#: Teto de reescrita da MESMA materia. Vem como ``BLOCKED`` 422, sem horario
#: prometido: outra revisao automatica daquela materia passa por humano.
ARTICLE_UPDATE_LIMIT_REACHED: Final[str] = "AUTO_PUBLISH_ARTICLE_UPDATE_LIMIT_REACHED"

#: As quatro dimensoes de teto diario que produzem ``DEFERRED``. Registradas
#: nominalmente para que a auditoria diga QUAL teto estourou: "esgotou o teto do
#: autor" e "esgotou o teto global da redacao" pedem providencias diferentes.
DEFERRAL_CODES: Final[FrozenSet[str]] = frozenset(
    {
        "AUTO_PUBLISH_GLOBAL_DAILY_LIMIT_REACHED",
        "AUTO_PUBLISH_CONTENT_TYPE_DAILY_LIMIT_REACHED",
        "AUTO_PUBLISH_SECTION_DAILY_LIMIT_REACHED",
        "AUTO_PUBLISH_AUTHOR_DAILY_LIMIT_REACHED",
    }
)


@dataclass(frozen=True)
class OutcomeReason:
    """Motivo com codigo. Nunca conteudo, credencial ou caminho."""

    code: str
    detail: str = ""

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.code}: {self.detail}" if self.detail else self.code


@dataclass
class PublicationResult:
    """A resposta do Cinerie, normalizada e segura para persistir."""

    outcome: str
    http_status: int
    request_id: Optional[str] = None
    idempotency_key: Optional[str] = None
    article_id: Optional[str] = None
    canonical_slug: Optional[str] = None
    public_author_id: Optional[str] = None
    technical_actor_id: Optional[str] = None
    reasons: List[OutcomeReason] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    next_eligible_at: Optional[str] = None
    idempotent: bool = False
    remote_code: Optional[str] = None
    retryable: bool = False
    #: Segundos do header ``Retry-After``. Acompanha ``DEFERRED`` e vem do MESMO
    #: instante que ``nextEligibleAt``, entao os dois nunca se contradizem.
    retry_after_seconds: Optional[float] = None
    #: Contadores/tetos devolvidos pelo destino, quando presentes. O cliente não
    #: calcula cota local nem transforma ausência em zero.
    quota_remaining: Optional[Dict[str, Any]] = None
    attempts: int = 1

    @property
    def accepted(self) -> bool:
        return self.outcome in ACCEPTED

    @property
    def published(self) -> bool:
        return self.outcome == PUBLISHED

    @property
    def awaiting_human(self) -> bool:
        """202 e sucesso-com-espera. Nao e falha do pipeline."""
        return self.outcome == ROUTED_TO_REVIEW

    @property
    def deferred(self) -> bool:
        """429: teto diario esgotado. Nada persistido, nada consumido."""
        return self.outcome == DEFERRED

    @property
    def deferral_codes(self) -> List[str]:
        """Quais tetos estouraram, entre os quatro conhecidos."""
        return [code for code in self.reason_codes if code in DEFERRAL_CODES]

    @property
    def article_update_limit_reached(self) -> bool:
        """Teto de reescrita da mesma materia: para de reenviar aquele update."""
        return any(reason.code == ARTICLE_UPDATE_LIMIT_REACHED for reason in self.reasons)

    @property
    def author_change_requires_human(self) -> bool:
        return any(reason.code == AUTHOR_CHANGE_REQUIRES_HUMAN for reason in self.reasons)

    @property
    def reason_codes(self) -> List[str]:
        return [reason.code for reason in self.reasons]

    def safe_log_fields(self) -> Dict[str, Any]:
        return {
            "outcome": self.outcome,
            "http_status": self.http_status,
            "request_id": self.request_id,
            "article_id": self.article_id,
            "reason_codes": self.reason_codes,
            "next_eligible_at": self.next_eligible_at,
            "retry_after_seconds": self.retry_after_seconds,
            "idempotent": self.idempotent,
            "quota_remaining": self.quota_remaining,
            "attempts": self.attempts,
        }


def _text(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def parse_reasons(raw: Any) -> List[OutcomeReason]:
    reasons: List[OutcomeReason] = []
    if not isinstance(raw, list):
        return reasons
    for item in raw:
        if isinstance(item, Mapping):
            code = _text(item.get("code")) or "UNKNOWN"
            reasons.append(OutcomeReason(code=code, detail=_text(item.get("detail")) or ""))
        elif isinstance(item, str):
            reasons.append(OutcomeReason(code=item.strip() or "UNKNOWN"))
    return reasons


def parse_result(
    body: Any,
    status: int,
    *,
    attempts: int = 1,
    retry_after_seconds: Optional[float] = None,
) -> Optional[PublicationResult]:
    """Le a resposta do Cinerie. ``None`` quando ela nao e reconhecivel.

    ``None`` importa: uma resposta 2xx que nao diz o desfecho nao pode virar
    sucesso presumido — o pedido pode ter sido aplicado, e adivinhar aqui e como
    se publica duas vezes.

    ``None`` tambem e o que separa um ``DEFERRED`` de um 429 de verdade: o
    desfecho do portao vem com corpo declarando ``outcome``; um 429 de proxy ou
    de limitador de taxa nao vem, e ai continua sendo erro de transporte.
    """
    if not isinstance(body, Mapping):
        return None
    outcome = _text(body.get("outcome"))
    if outcome not in OUTCOMES:
        return None

    warnings_raw = body.get("warnings")
    warnings = [item for item in warnings_raw if isinstance(item, str)] if isinstance(warnings_raw, list) else []

    quota_raw = body.get("quotaRemaining")
    if not isinstance(quota_raw, Mapping):
        quota_raw = body.get("remainingQuota")
    quota_remaining = None
    if isinstance(quota_raw, Mapping):
        sanitized_quota = {
            str(key): value
            for key, value in list(quota_raw.items())[:20]
            if re.fullmatch(r"[A-Za-z0-9_]{1,64}", str(key))
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
        }
        quota_remaining = sanitized_quota or None

    return PublicationResult(
        outcome=outcome,
        http_status=status,
        request_id=_text(body.get("requestId")),
        idempotency_key=_text(body.get("idempotencyKey")),
        article_id=_text(body.get("articleId")),
        canonical_slug=_text(body.get("canonicalSlug")),
        public_author_id=_text(body.get("publicAuthorId")),
        technical_actor_id=_text(body.get("technicalActorId")),
        reasons=parse_reasons(body.get("reasons")),
        warnings=warnings,
        next_eligible_at=_text(body.get("nextEligibleAt")),
        idempotent=body.get("idempotent") is True,
        remote_code=_text(body.get("code")),
        retryable=body.get("retryable") is True,
        retry_after_seconds=retry_after_seconds,
        quota_remaining=quota_remaining,
        attempts=attempts,
    )


def should_resend(result: PublicationResult) -> Tuple[bool, str]:
    """Reenviar este pedido **agora**, no laco de tentativas, resolve alguma coisa?

    Quase nunca. A funcao existe para que a resposta seja uma decisao explicita
    com motivo registrado, em vez de um `if` espalhado pelo orquestrador.

    "Agora" e a palavra que carrega o peso. ``DEFERRED`` sera reenviado, e a
    resposta aqui ainda e ``False``: perguntar "retento?" a um teto que vira a
    meia-noite so gasta tentativa. Quem responde por ele e
    :func:`resend_not_before`.
    """
    if result.outcome == PUBLISHED:
        return False, "materia publicada; reenviar criaria duplicata"
    if result.outcome == ROUTED_TO_REVIEW:
        return False, "conteudo aceito e aguardando humano; nao ha o que retentar"
    if result.outcome == BLOCKED:
        return False, "defeito permanente no pedido; reenviar igual repete o defeito"
    if result.outcome == DEFERRED:
        return False, (
            "teto diario esgotado; o reenvio e agendado para depois de "
            "nextEligibleAt, nunca imediato"
        )
    if result.outcome == CONFLICT:
        return False, "estado divergente; exige reconciliacao antes de qualquer reenvio"
    if result.outcome == OPERATIONAL_ERROR:
        return result.retryable, (
            "falha operacional marcada como retentavel"
            if result.retryable
            else "falha operacional sem marca de retentavel"
        )
    return False, "desfecho desconhecido"


def resend_not_before(result: PublicationResult) -> Optional[str]:
    """Instante ISO a partir do qual reenviar este pedido faz sentido.

    ``None`` para todo desfecho que nao seja ``DEFERRED`` — inclusive quando ha
    ``nextEligibleAt`` no corpo. Um horario informativo num desfecho ja aceito
    nao e uma promessa de que reenviar depois dele muda alguma coisa, e tratar
    os dois como a mesma coisa reenviaria materia que ja esta na fila de um
    humano.
    """
    if result.outcome != DEFERRED:
        return None
    return result.next_eligible_at


__all__ = [
    "ACCEPTED",
    "ARTICLE_UPDATE_LIMIT_REACHED",
    "AUTHOR_CHANGE_REQUIRES_HUMAN",
    "BLOCKED",
    "CONFLICT",
    "DEFERRAL_CODES",
    "DEFERRED",
    "OPERATIONAL_ERROR",
    "OUTCOMES",
    "OUTCOME_STATUS",
    "OutcomeReason",
    "PUBLISHED",
    "PublicationResult",
    "ROUTED_TO_REVIEW",
    "TERMINAL",
    "parse_reasons",
    "parse_result",
    "resend_not_before",
    "should_resend",
]
