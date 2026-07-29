"""Os quatro identificadores do pedido — que **nao** sao a mesma coisa.

Derivar todos de um unico valor e o erro classico deste tipo de integracao, e
ele custa caro nas duas direcoes: ou o retry de rede vira uma segunda
publicacao, ou uma revisao nova e confundida com um replay e nunca chega.

``sourceClusterId``
    o agrupamento editorial de origem. Um acontecimento, varias fontes.

``sourceRevision``
    ordena as revisoes do MESMO trabalho. E o que distingue uma atualizacao
    legitima de um pedido que chegou fora de ordem.

``idempotencyKey``
    identifica **o trabalho editorial**. Estavel entre retries do mesmo
    trabalho — e o que o Cinerie usa para achar o artigo ja existente.

``requestId``
    identifica **a tentativa**. Repetido no retry de transporte: o Cinerie
    responde ``idempotent: true`` e nao reaplica nada nem consome teto de novo.
    Um requestId novo declara "conte isto como publicacao nova".

Por isso ``requestId`` e derivado de ``idempotencyKey + sourcePayloadHash``, e
nao sorteado: um valor aleatorio a cada tentativa faria cada retry de rede
consumir uma vaga do teto diario, e o teto passaria a proteger contra a coisa
errada. Conteudo diferente muda o hash, muda o requestId, e ai sim conta como
tentativa nova — que e exatamente a semantica desejada.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Final, Optional

from .errors import RequestBuildError

#: `stableId` do contrato: `^[A-Za-z0-9][A-Za-z0-9._:-]*$`, ate 200 caracteres.
STABLE_ID: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
MAX_ID_LENGTH: Final[int] = 200

#: Prefixo do namespace deste produtor. Torna a chave legivel na auditoria do
#: Cinerie sem precisar consultar o MNScr para saber quem a gerou.
NAMESPACE: Final[str] = "mnscr"


def is_stable_id(value: Optional[str]) -> bool:
    return bool(value) and len(value) <= MAX_ID_LENGTH and bool(STABLE_ID.match(value))


def _digest(value: str, *, length: int = 32) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]


def normalize_cluster_id(event_key: Optional[str]) -> str:
    """``sourceClusterId`` valido e **estavel** para um event_key qualquer.

    Quando o event_key ja e um id aceitavel, ele passa intacto — a auditoria do
    Cinerie fica legivel. Quando nao e, o valor vira um digest em vez de ser
    "limpo" caractere a caractere: dois event_keys diferentes poderiam ser
    limpos para o MESMO id, e ai duas materias distintas compartilhariam
    idempotencia.
    """
    text = str(event_key or "").strip()
    if not text:
        raise RequestBuildError("draft sem event_key: nao ha agrupamento editorial de origem")
    if is_stable_id(text):
        return text
    return f"cluster-{_digest(text)}"


def build_idempotency_key(*, cluster_id: str, revision: int) -> str:
    """``mnscr:<cluster>:rev-<n>`` — o TRABALHO, nao a tentativa."""
    if not is_stable_id(cluster_id):
        raise RequestBuildError(f"sourceClusterId invalido: {cluster_id[:40]}")
    key = f"{NAMESPACE}:{cluster_id}:rev-{int(revision)}"
    if not is_stable_id(key):
        raise RequestBuildError("idempotencyKey resultante fora do formato do contrato")
    if len(key) > MAX_ID_LENGTH:
        # Cluster longo demais: preserva a semantica encurtando de forma estavel.
        key = f"{NAMESPACE}:{_digest(cluster_id)}:rev-{int(revision)}"
    return key


def build_request_id(*, idempotency_key: str, payload_hash: str) -> str:
    """``req-<digest>`` — a TENTATIVA.

    Deterministico de proposito: o mesmo trabalho com o mesmo conteudo produz o
    mesmo requestId em qualquer processo, inclusive depois de um restart. E o que
    torna o retry de transporte reconhecivel como retry do outro lado.
    """
    if not idempotency_key:
        raise RequestBuildError("requestId exige idempotencyKey")
    if not payload_hash:
        raise RequestBuildError("requestId exige sourcePayloadHash")
    return f"req-{_digest(f'{idempotency_key}|{payload_hash}')}"


def build_source_id(url: str, *, index: int = 0) -> str:
    """Id estavel para uma fonte externa, derivado da URL.

    Derivado da URL, e nao da posicao: a mesma fonte mantem o mesmo id quando a
    ordem das fontes muda entre revisoes.
    """
    text = str(url or "").strip()
    if not text:
        return f"src-{index}"
    return f"src-{_digest(text, length=24)}"


@dataclass(frozen=True)
class RequestIdentity:
    """Os quatro identificadores juntos, ja validados."""

    source_cluster_id: str
    source_revision: int
    idempotency_key: str
    request_id: str
    source_payload_hash: str

    def as_fields(self) -> dict:
        return {
            "requestId": self.request_id,
            "idempotencyKey": self.idempotency_key,
            "sourceClusterId": self.source_cluster_id,
            "sourceRevision": self.source_revision,
            "sourcePayloadHash": self.source_payload_hash,
        }

    def safe_log_fields(self) -> dict:
        """Campos seguros para log. A chave de idempotencia vai truncada."""
        return {
            "request_id": self.request_id,
            "idempotency_key": self.idempotency_key[:64],
            "source_cluster_id": self.source_cluster_id,
            "source_revision": self.source_revision,
        }


def build_identity(
    *,
    event_key: Optional[str],
    revision: int,
    payload_hash: str,
) -> RequestIdentity:
    cluster_id = normalize_cluster_id(event_key)
    revision = int(revision or 1)
    if revision < 0:
        raise RequestBuildError("sourceRevision nao pode ser negativa")
    idempotency_key = build_idempotency_key(cluster_id=cluster_id, revision=revision)
    return RequestIdentity(
        source_cluster_id=cluster_id,
        source_revision=revision,
        idempotency_key=idempotency_key,
        request_id=build_request_id(idempotency_key=idempotency_key, payload_hash=payload_hash),
        source_payload_hash=payload_hash,
    )


__all__ = [
    "MAX_ID_LENGTH",
    "NAMESPACE",
    "RequestIdentity",
    "STABLE_ID",
    "build_idempotency_key",
    "build_identity",
    "build_request_id",
    "build_source_id",
    "is_stable_id",
    "normalize_cluster_id",
]
