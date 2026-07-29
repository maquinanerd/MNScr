"""Erros tipados da entrega ao Cinerie, com classificacao de retry.

A classificacao vive junto do erro de proposito. O servico so precisa perguntar
``exc.retryable``; espalhar `if status in (...)` pelo orquestrador faria a
politica divergir do lugar onde ela e conhecida.

Nada aqui carrega segredo: as mensagens sao construidas com nome de campo,
codigo e host — nunca com token, corpo do pedido ou trecho de materia inedita.
"""

from __future__ import annotations

from typing import Final, FrozenSet, List, Optional, Sequence


class CinerieError(Exception):
    """Base de tudo que a entrega ao Cinerie pode levantar."""

    #: Vale a pena repetir a MESMA requisicao?
    retryable: bool = False
    #: Codigo curto e estavel, seguro para log e persistencia.
    code: str = "CINERIE_ERROR"


# --- Defeitos de repositorio / configuracao --------------------------------


class ContractArtifactError(CinerieError):
    """Artefato contratual ausente, ilegivel ou inconsistente."""

    code = "CONTRACT_ARTIFACT_INVALID"


class ConfigurationError(CinerieError):
    """Configuracao ausente ou invalida. Nunca se resolve retentando."""

    code = "CONFIGURATION_INVALID"


# --- Defeitos de construcao (permanentes) ----------------------------------


class RequestBuildError(CinerieError):
    """O pedido nao pode ser montado a partir deste draft."""

    code = "REQUEST_BUILD_FAILED"


class SchemaValidationError(CinerieError):
    """O pedido montado nao satisfaz o JSON Schema do Cinerie.

    Erro PERMANENTE de construcao: reenviar repete o defeito. Os caminhos dos
    campos invalidos sao preservados; os valores, nunca — um erro de validacao
    que ecoa o payload vira vetor de vazamento de materia inedita no log.
    """

    code = "CONTRACT_INVALID"

    def __init__(self, message: str, *, paths: Optional[Sequence[str]] = None) -> None:
        super().__init__(message)
        self.paths: List[str] = list(paths or [])


class ForbiddenFieldError(SchemaValidationError):
    """Campo recusado pelo contrato (WordPress/Yoast, estado publico, identidade)."""

    code = "FORBIDDEN_FIELD"


class ContractMismatchError(CinerieError):
    """Preflight divergente: nome, versao ou hash diferentes do destino.

    Fail-closed. Um schema diferente pode ter campo de mesmo nome com outra
    semantica, e "quase compativel" e como se publica materia errada.
    """

    code = "CONTRACT_MISMATCH"


class BlockedByPolicyError(CinerieError):
    """Politica local recusou a entrega antes de qualquer socket."""

    code = "BLOCKED_BY_LOCAL_POLICY"


# --- Erros de transporte ---------------------------------------------------


class TransportError(CinerieError):
    """Base dos erros que envolvem a rede."""

    code = "TRANSPORT_ERROR"


class CinerieTimeoutError(TransportError):
    """Timeout aguardando a resposta. O pedido pode ter chegado."""

    retryable = True
    code = "TIMEOUT"


class CinerieConnectionError(TransportError):
    """Falha ao conectar. A requisicao nao chegou — nada foi criado."""

    retryable = True
    code = "CONNECTION_FAILED"


class AuthenticationError(TransportError):
    """401. Credencial ausente ou invalida; retentar repete a recusa."""

    code = "UNAUTHENTICATED"


class PermissionError_(TransportError):
    """403. A service account nao tem o escopo `editorial_auto_publish`."""

    code = "FORBIDDEN_SCOPE"


class RateLimitedError(TransportError):
    """429. Retentavel, respeitando ``Retry-After`` quando presente."""

    retryable = True
    code = "RATE_LIMITED"

    def __init__(self, message: str, *, retry_after_seconds: Optional[float] = None) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class ServerError(TransportError):
    """5xx generico. Retentavel."""

    retryable = True
    code = "SERVER_ERROR"


class OperationalError(TransportError):
    """503 marcado pelo Cinerie como ``retryable``.

    O Cinerie distingue 503 operacional retentavel (fuso invalido, persistencia
    que falhou e foi desfeita) de 503 sem essa marca. Sem a marca nao ha
    promessa de que nada foi persistido, e retentar as cegas poderia duplicar.
    """

    retryable = True
    code = "OPERATIONAL_ERROR"

    def __init__(
        self,
        message: str,
        *,
        remote_code: Optional[str] = None,
        retry_after_seconds: Optional[float] = None,
    ) -> None:
        super().__init__(message)
        self.remote_code = remote_code
        self.retry_after_seconds = retry_after_seconds


class NonRetryableServerError(TransportError):
    """503 SEM ``retryable: true``. Nao se repete as cegas."""

    code = "SERVER_ERROR_NOT_RETRYABLE"


class InvalidRemoteResponseError(TransportError):
    """Resposta ilegivel: corpo nao-JSON, ou JSON sem ``outcome`` conhecido.

    Nao retentavel: o pedido pode ter sido aplicado e nao ha como saber. Vai
    para reconciliacao humana em vez de virar uma segunda publicacao.
    """

    code = "INVALID_REMOTE_RESPONSE"


RETRYABLE_STATUS_CODES: Final[FrozenSet[int]] = frozenset({408, 429, 500, 502, 504})


__all__ = [
    "AuthenticationError",
    "BlockedByPolicyError",
    "CinerieConnectionError",
    "CinerieError",
    "CinerieTimeoutError",
    "ConfigurationError",
    "ContractArtifactError",
    "ContractMismatchError",
    "ForbiddenFieldError",
    "InvalidRemoteResponseError",
    "NonRetryableServerError",
    "OperationalError",
    "PermissionError_",
    "RETRYABLE_STATUS_CODES",
    "RateLimitedError",
    "RequestBuildError",
    "SchemaValidationError",
    "ServerError",
    "TransportError",
]
