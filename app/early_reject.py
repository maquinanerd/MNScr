"""Recusa LOCAL e BARATA, antes de a IA escrever uma linha.

O motivo desta camada existir e um numero: numa rodada real, 30 itens entraram
e 1 publicou. Os 29 restantes nao morreram por falta de qualidade — morreram em
``normalize_cluster_id`` por falta de ``event_key``, DEPOIS de o Gemini ter
escrito a materia inteira. O custo foi pago tres vezes por item (sanitize,
rewrite, seo_pack) para descobrir algo que estava decidido antes da primeira
chamada.

A regra desta camada e, por isso, estreita de proposito: **so entra aqui o que
da para saber com o item na mao, sem rede e sem modelo**. Um cheque que precise
do corpo escrito, do gate ou da avaliacao factual nao pertence a este arquivo —
tentar adivinha-lo cedo trocaria dinheiro por materia perdida, que e o erro
oposto e pior.

Isso e rede de seguranca, nao substituto de correcao. Um item que chega aqui e
recusado continua sendo um defeito a investigar; a diferenca e que ele passa a
custar zero token para ser descoberto.

Sobre ``_local_preconditions`` (``cinerie_service``): das tres pre-condicoes que
ela cobra — gate executado, avaliacao factual executada e corpo presente —
NENHUMA e avaliavel antes da IA, porque as tres sao produzidas depois. Elas
seguem cobradas la, no lugar certo. O que da para antecipar do mesmo caminho de
publicacao e o que esta abaixo.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional, Tuple

from .cinerie.errors import RequestBuildError
from .cinerie.identity import is_public_author_id, normalize_cluster_id
from .cluster_feed_adapter import is_superfeed_item
from .superfeed_policy import check_superfeed_policy

logger = logging.getLogger(__name__)

#: Codigos emitidos por esta camada. Sao proprios, e nao reaproveitados dos
#: codigos do Cinerie, para que uma busca no log distinga sem ambiguidade "o
#: contrato recusou" de "nos recusamos antes de perguntar".
EARLY_REJECT_CODES = (
    "EARLY_EVENT_KEY_AUSENTE",
    "EARLY_SUPERFEED_POLICY",
    "EARLY_PUBLIC_AUTHOR_ID_INVALIDO",
)


def allow_single_source() -> bool:
    return os.getenv("ALLOW_SINGLE_SOURCE", "false").strip().lower() == "true"


def _event_key_is_usable(event_key: Any) -> bool:
    """Mesma regra de ``identity.py`` — chamada, nao reescrita.

    Reimplementar a condicao aqui criaria duas verdades sobre o que e um
    ``event_key`` aproveitavel, e a que fica para tras e sempre a desta camada:
    ela e a que ninguem lembra de atualizar.
    """
    try:
        normalize_cluster_id(event_key)
    except RequestBuildError:
        return False
    return True


def evaluate_early_rejection(
    item: Dict[str, Any],
    *,
    publishes_to_cinerie: bool,
    public_author_id: Optional[str] = None,
) -> Optional[Tuple[str, str]]:
    """``(codigo, motivo)`` se o item ja esta condenado; ``None`` se pode seguir.

    ``publishes_to_cinerie`` decide o escopo dos cheques de contrato. Fora do
    modo ``AUTO_PUBLISH`` nao existe pedido de publicacao, e um item de
    ``fallback`` legitimamente nao tem ``event_key``: recusa-lo ali seria
    inventar uma exigencia que ninguem faz, e desligaria o caminho de fallback
    inteiro em nome de uma economia que nao existe.
    """
    if is_superfeed_item(item) or item.get("origin") == "superfeed":
        status, reason = check_superfeed_policy(
            item, allow_single_source=allow_single_source()
        )
        if status != "OK":
            return "EARLY_SUPERFEED_POLICY", f"{status}: {reason}"

    if not publishes_to_cinerie:
        return None

    event_key = item.get("event_key") or (item.get("cluster_item") or {}).get("event_key")
    if not _event_key_is_usable(event_key):
        return (
            "EARLY_EVENT_KEY_AUSENTE",
            "sem agrupamento editorial de origem aproveitavel; "
            "o pedido de publicacao morreria em normalize_cluster_id",
        )

    if not is_public_author_id(public_author_id):
        return (
            "EARLY_PUBLIC_AUTHOR_ID_INVALIDO",
            f"MNSCR_PUBLIC_AUTHOR_ID={public_author_id!r} nao e id numerico de "
            "Author; o Cinerie devolveria author_not_found em toda materia",
        )

    return None


class EarlyRejectLedger:
    """Contagem viva das recusas, para a economia aparecer no log.

    Uma recusa silenciosa e indistinguivel de um item que nunca existiu. O
    numero que interessa nao e "quantos falharam", e sim **quantas chamadas de
    IA deixaram de ser feitas** — que e o que este registro carrega.
    """

    #: Chamadas de modelo por artigo no caminho de 3 fases (sanitize, rewrite,
    #: seo_pack). Usado so para dimensionar a economia no log.
    AI_CALLS_PER_ARTICLE = 3

    def __init__(self) -> None:
        self.by_code: Dict[str, int] = {}
        self.reached_ai = 0

    @property
    def rejected(self) -> int:
        return sum(self.by_code.values())

    def record_rejection(self, code: str) -> None:
        self.by_code[code] = self.by_code.get(code, 0) + 1

    def record_reached_ai(self, count: int = 1) -> None:
        self.reached_ai += count

    def log_summary(self, *, scope: str = "batch") -> None:
        if not self.rejected and not self.reached_ai:
            return
        detalhe = " ".join(
            f"{code}={count}" for code, count in sorted(self.by_code.items())
        ) or "-"
        logger.info(
            "[EARLY_REJECT_SUMMARY] scope=%s recusados_cedo=%s chegaram_a_ia=%s "
            "chamadas_de_ia_economizadas=%s motivos=%s",
            scope,
            self.rejected,
            self.reached_ai,
            self.rejected * self.AI_CALLS_PER_ARTICLE,
            detalhe,
        )

    def reset(self) -> None:
        self.by_code.clear()
        self.reached_ai = 0


#: Registro do processo. ``--once`` e o worker compartilham o mesmo, de proposito:
#: a pergunta "quanto isto economizou" e sobre a rodada, nao sobre o lote.
LEDGER = EarlyRejectLedger()


__all__ = [
    "EARLY_REJECT_CODES",
    "EarlyRejectLedger",
    "LEDGER",
    "allow_single_source",
    "evaluate_early_rejection",
]
