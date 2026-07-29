"""Os desfechos do Cinerie, e o que cada um significa para o pipeline.

O desfecho vem do servidor; o que muda aqui e a **reacao**. A confusao cara e
tratar ``ROUTED_TO_REVIEW`` como falha: o conteudo foi aceito, validado e
guardado — o que falta e julgamento humano. Reenviar nao acelera nada, consome
teto e polui a auditoria com tentativas que nao eram tentativas.

+---------------------+------+--------------------------------+-----------+
| desfecho            | HTTP | significa                      | reenviar? |
+---------------------+------+--------------------------------+-----------+
| PUBLISHED           | 201  | materia publicada              | nao       |
| ROUTED_TO_REVIEW    | 202  | aceito, aguardando humano      | nao       |
| CONFLICT            | 409  | estado divergente              | so apos   |
|                     |      |                                | reconciliar|
| BLOCKED             | 422  | defeito permanente no pedido   | nao       |
+---------------------+------+--------------------------------+-----------+

Alem desses, o Cinerie pode responder ``OPERATIONAL_ERROR`` com HTTP 503 e
``retryable: true`` — falha de plataforma em que nada foi persistido e nenhum
teto consumido.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Final, FrozenSet, List, Mapping, Optional, Tuple

PUBLISHED: Final[str] = "PUBLISHED"
ROUTED_TO_REVIEW: Final[str] = "ROUTED_TO_REVIEW"
CONFLICT: Final[str] = "CONFLICT"
BLOCKED: Final[str] = "BLOCKED"
OPERATIONAL_ERROR: Final[str] = "OPERATIONAL_ERROR"

OUTCOMES: Final[FrozenSet[str]] = frozenset(
    {PUBLISHED, ROUTED_TO_REVIEW, CONFLICT, BLOCKED, OPERATIONAL_ERROR}
)

#: Desfecho -> HTTP, conforme `outcomeHttpStatus` do Cinerie.
OUTCOME_STATUS: Final[Dict[str, int]] = {
    PUBLISHED: 201,
    ROUTED_TO_REVIEW: 202,
    CONFLICT: 409,
    BLOCKED: 422,
    OPERATIONAL_ERROR: 503,
}

#: Desfechos em que o conteudo FOI aceito pelo servidor.
ACCEPTED: Final[FrozenSet[str]] = frozenset({PUBLISHED, ROUTED_TO_REVIEW})

#: Desfechos que NUNCA devem ser reenviados automaticamente.
TERMINAL: Final[FrozenSet[str]] = frozenset({PUBLISHED, ROUTED_TO_REVIEW, BLOCKED})

#: Codigo que o Cinerie usa quando a troca de autor exige humano.
AUTHOR_CHANGE_REQUIRES_HUMAN: Final[str] = "AUTHOR_CHANGE_REQUIRES_HUMAN"


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
            "idempotent": self.idempotent,
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


def parse_result(body: Any, status: int, *, attempts: int = 1) -> Optional[PublicationResult]:
    """Le a resposta do Cinerie. ``None`` quando ela nao e reconhecivel.

    ``None`` importa: uma resposta 2xx que nao diz o desfecho nao pode virar
    sucesso presumido — o pedido pode ter sido aplicado, e adivinhar aqui e como
    se publica duas vezes.
    """
    if not isinstance(body, Mapping):
        return None
    outcome = _text(body.get("outcome"))
    if outcome not in OUTCOMES:
        return None

    warnings_raw = body.get("warnings")
    warnings = [item for item in warnings_raw if isinstance(item, str)] if isinstance(warnings_raw, list) else []

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
        attempts=attempts,
    )


def should_resend(result: PublicationResult) -> Tuple[bool, str]:
    """Reenviar este pedido resolve alguma coisa?

    Quase nunca. A funcao existe para que a resposta seja uma decisao explicita
    com motivo registrado, em vez de um `if` espalhado pelo orquestrador.
    """
    if result.outcome == PUBLISHED:
        return False, "materia publicada; reenviar criaria duplicata"
    if result.outcome == ROUTED_TO_REVIEW:
        return False, "conteudo aceito e aguardando humano; nao ha o que retentar"
    if result.outcome == BLOCKED:
        return False, "defeito permanente no pedido; reenviar igual repete o defeito"
    if result.outcome == CONFLICT:
        return False, "estado divergente; exige reconciliacao antes de qualquer reenvio"
    if result.outcome == OPERATIONAL_ERROR:
        return result.retryable, (
            "falha operacional marcada como retentavel"
            if result.retryable
            else "falha operacional sem marca de retentavel"
        )
    return False, "desfecho desconhecido"


__all__ = [
    "ACCEPTED",
    "AUTHOR_CHANGE_REQUIRES_HUMAN",
    "BLOCKED",
    "CONFLICT",
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
    "should_resend",
]
