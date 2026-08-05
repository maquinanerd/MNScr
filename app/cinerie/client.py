"""Cliente HTTP do Cinerie — o UNICO modulo de `app/cinerie` com transporte.

Tudo o mais neste pacote e dominio puro e testavel sem socket. A fronteira e
deliberada e verificada por teste: contrato, SEO, blocos, identidade e
refinamentos nunca importam cliente HTTP, o que permite exercitar o caminho
inteiro de construcao e recusa sem abrir uma conexao.

Sobre o segredo: a API key nunca aparece em log, em excecao, em ``repr`` ou em
mensagem de retry. As URLs que entram em mensagem de erro passam por
``_safe_url``, que descarta querystring — uma querystring pode carregar
credencial, e uma mensagem de erro costuma ser o lugar menos vigiado do sistema.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Tuple
from urllib.parse import urlparse, urlunparse

import requests

from .errors import (
    AuthenticationError,
    BadRequestError,
    CinerieConnectionError,
    CinerieTimeoutError,
    ConfigurationError,
    EndpointUnavailableError,
    InvalidRemoteResponseError,
    NonRetryableServerError,
    OperationalError,
    PermissionError_,
    RateLimitedError,
    RequestTooLargeError,
    ServerError,
)
from .outcomes import OPERATIONAL_ERROR, PublicationResult, parse_result

logger = logging.getLogger(__name__)

PUBLICATIONS_PATH: str = "/api/internal/editorial-publications"
CONTRACTS_PATH: str = "/api/internal/contracts"

#: Formato de autenticacao do Payload para contas tecnicas.
AUTH_SCHEME: str = "service-accounts API-Key"

#: Teto do corpo aceito na resposta. Uma resposta gigante e defeito, e ler tudo
#: antes de descobrir isso e como um cliente vira vetor de exaustao de memoria.
MAX_RESPONSE_BYTES: int = 1 * 1024 * 1024

#: Teto do corpo do PEDIDO — espelha o `MAX_REQUEST_BYTES` do Cinerie.
#:
#: Distinto de `MAX_RESPONSE_BYTES`, e nao por simetria: este numero nao e nosso.
#: E a regra do destino, que acima dele responde `400 invalid_body` sem olhar o
#: conteudo. Medir antes de enviar troca um round-trip e uma mensagem generica
#: por uma recusa local que diz o tamanho — e um corpo de 2 MiB e caro de subir
#: so para ouvir "nao".
MAX_REQUEST_BYTES: int = 2 * 1024 * 1024


def _remote_error(body: Any) -> Optional[str]:
    """O campo ``error`` da recusa (``invalid_json``, ``invalid_body``).

    So o codigo, nunca o corpo inteiro: a mensagem de erro vai para o log, e o
    corpo de um pedido recusado carrega materia inedita.
    """
    if not isinstance(body, Mapping):
        return None
    value = body.get("error")
    return value.strip()[:60] if isinstance(value, str) and value.strip() else None


def _safe_url(url: str) -> str:
    """Esquema + host + caminho. Sem querystring, sem credencial embutida."""
    try:
        parsed = urlparse(str(url or ""))
    except ValueError:
        return "(url invalida)"
    host = parsed.hostname or ""
    if parsed.port:
        host = f"{host}:{parsed.port}"
    return urlunparse((parsed.scheme, host, parsed.path, "", "", "")) or "(url vazia)"


@dataclass
class CinerieConfig:
    """Endereco e credencial do Cinerie."""

    base_url: str
    api_key: str
    timeout_seconds: float = 20.0
    verify_tls: bool = True

    def __post_init__(self) -> None:
        self.base_url = str(self.base_url or "").strip().rstrip("/")
        self.api_key = str(self.api_key or "").strip()

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return (
            f"CinerieConfig(base_url={_safe_url(self.base_url)!r}, "
            f"api_key={'***' if self.api_key else None!r}, "
            f"timeout_seconds={self.timeout_seconds})"
        )

    def issues(self) -> list[str]:
        """Problemas de configuracao, em forma segura para log."""
        problems: list[str] = []
        if not self.base_url:
            problems.append("PAYLOAD_INTERNAL_SERVICE_URL ausente")
        else:
            parsed = urlparse(self.base_url)
            if parsed.scheme not in ("http", "https"):
                problems.append("PAYLOAD_INTERNAL_SERVICE_URL precisa ser http(s)")
            if not parsed.hostname:
                problems.append("PAYLOAD_INTERNAL_SERVICE_URL sem host")
            if parsed.username or parsed.password:
                # Credencial embutida vazaria em qualquer log que registre a URL.
                problems.append("PAYLOAD_INTERNAL_SERVICE_URL nao pode conter credencial embutida")
            if parsed.query or parsed.fragment:
                problems.append("PAYLOAD_INTERNAL_SERVICE_URL deve ser apenas a origem do servico")
            if parsed.path.rstrip("/").endswith(
                (PUBLICATIONS_PATH.rstrip("/"), CONTRACTS_PATH.rstrip("/"))
            ):
                problems.append(
                    "PAYLOAD_INTERNAL_SERVICE_URL deve ser a base do servico, nao um endpoint"
                )
        if not self.api_key:
            problems.append("MNSCR_PAYLOAD_API_KEY ausente")
        if self.timeout_seconds <= 0:
            problems.append("timeout precisa ser positivo")
        return problems

    def url_for(self, path: str) -> str:
        return f"{self.base_url}{path}"


class CinerieClient:
    """POST de publicacao e GET de contratos. Nada alem disso."""

    def __init__(self, config: CinerieConfig, *, session: Optional[Any] = None) -> None:
        problems = config.issues()
        if problems:
            raise ConfigurationError("configuracao do Cinerie invalida: " + "; ".join(problems))
        self.config = config
        self._session = session

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return f"CinerieClient({_safe_url(self.config.base_url)!r})"

    # -- transporte --------------------------------------------------------

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"{AUTH_SCHEME} {self.config.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: Optional[Mapping[str, Any]] = None,
    ) -> Tuple[int, Any, Mapping[str, str]]:
        url = self.config.url_for(path)
        caller = self._session if self._session is not None else requests

        # Serializa UMA vez e mede o que sera realmente enviado. Medir o dict, ou
        # medir uma segunda serializacao, mediria outra coisa.
        encoded = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        if encoded is not None and len(encoded) > MAX_REQUEST_BYTES:
            raise RequestTooLargeError(
                f"corpo de {len(encoded)} bytes acima do teto de {MAX_REQUEST_BYTES} "
                f"do Cinerie; nada foi enviado"
            )

        try:
            response = caller.request(
                method,
                url,
                headers=self._headers(),
                data=encoded,
                timeout=self.config.timeout_seconds,
                verify=self.config.verify_tls,
            )
        except requests.ConnectTimeout as exc:
            # Antes de `Timeout`: `ConnectTimeout` herda dos dois. Um timeout ao
            # CONECTAR significa que a requisicao nunca chegou — nada foi criado,
            # e o retry e limpo. So o timeout de LEITURA e ambiguo.
            raise CinerieConnectionError(f"falha ao conectar em {_safe_url(url)}") from exc
        except requests.Timeout as exc:
            raise CinerieTimeoutError(f"timeout aguardando resposta de {_safe_url(url)}") from exc
        except requests.ConnectionError as exc:
            raise CinerieConnectionError(f"falha de conexao com {_safe_url(url)}") from exc
        except requests.RequestException as exc:
            raise CinerieConnectionError(f"falha de transporte com {_safe_url(url)}") from exc

        raw = response.content or b""
        if len(raw) > MAX_RESPONSE_BYTES:
            raise InvalidRemoteResponseError(
                f"resposta de {_safe_url(url)} acima do limite ({len(raw)} bytes)"
            )
        try:
            parsed = json.loads(raw.decode("utf-8")) if raw else None
        except (UnicodeDecodeError, json.JSONDecodeError):
            parsed = None

        return response.status_code, parsed, dict(response.headers or {})

    # -- preflight ---------------------------------------------------------

    def fetch_contracts(self) -> Dict[str, Any]:
        """Manifesto de contratos do Cinerie."""
        status, body, _ = self._request("GET", CONTRACTS_PATH)
        self._raise_for_transport_status(status, body, headers={}, path=CONTRACTS_PATH)
        if not isinstance(body, Mapping) or not isinstance(body.get("contracts"), list):
            raise InvalidRemoteResponseError("manifesto de contratos em formato inesperado")
        return dict(body)

    # -- publicacao --------------------------------------------------------

    def submit_publication(self, payload: Mapping[str, Any]) -> PublicationResult:
        """Envia o pedido e devolve o desfecho normalizado.

        Os desfechos do portao **nao sao erro de transporte**: 201, 202, 429,
        409 e 422 voltam como resultado. Erro operacional e de rede viram
        excecao, que e o que a camada de retry entende.

        O 429 esta nessa lista, e e o unico codigo da faixa de erro que um
        cliente HTTP normal trataria sozinho. Aqui ele nao pode: um ``DEFERRED``
        e o portao dizendo "o teto do dia acabou", com ``nextEligibleAt`` e um
        ``Retry-After`` de horas. Deixa-lo virar ``RateLimitedError`` faria o
        laco de tentativas queimar as tres chances contra uma parede que so cai
        na virada do dia, e o desfecho — com a dimensao de teto que estourou —
        se perderia numa mensagem de erro de transporte.
        """
        status, body, headers = self._request("POST", PUBLICATIONS_PATH, body=payload)

        result = parse_result(body, status, retry_after_seconds=self._retry_after(headers))
        if result is not None and result.outcome != OPERATIONAL_ERROR:
            return result

        self._raise_for_transport_status(status, body, headers=headers, path=PUBLICATIONS_PATH)

        # 2xx sem desfecho reconhecivel: o pedido pode ter sido aplicado.
        # Presumir sucesso publicaria as cegas; presumir falha e retentar
        # duplicaria. Vai para reconciliacao.
        raise InvalidRemoteResponseError(
            f"resposta HTTP {status} sem desfecho reconhecivel do Cinerie"
        )

    # -- classificacao -----------------------------------------------------

    @staticmethod
    def _retry_after(headers: Mapping[str, str]) -> Optional[float]:
        from app.delivery.retry import parse_retry_after

        for key, value in (headers or {}).items():
            if key.lower() == "retry-after":
                return parse_retry_after(value)
        return None

    def _raise_for_transport_status(
        self, status: int, body: Any, *, headers: Mapping[str, str], path: str = ""
    ) -> None:
        if 200 <= status < 300:
            return

        retry_after = self._retry_after(headers)
        remote_code = body.get("code") if isinstance(body, Mapping) else None

        if status == 400:
            # `invalid_json` ou `invalid_body`. O contrato garante que nada foi
            # persistido, entao isto e defeito PERMANENTE nosso — e nao um
            # estado remoto incerto que precise de reconciliacao humana.
            raise BadRequestError(
                f"Cinerie recusou o pedido (400, {_remote_error(body) or 'sem codigo'})"
            )
        if status == 401:
            raise AuthenticationError("Cinerie recusou a credencial (401)")
        if status == 403:
            raise PermissionError_(
                "service account sem o escopo editorial_auto_publish (403)"
            )
        if status in (404, 405):
            raise EndpointUnavailableError(
                f"Cinerie nao expoe {path or 'o caminho pedido'} ({status}); confira "
                f"PAYLOAD_INTERNAL_SERVICE_URL e se o endpoint interno esta publicado"
            )
        if status == 429:
            # Um 429 que chega ate aqui NAO e `DEFERRED`: o desfecho do portao
            # traz corpo com `outcome` e ja voltou como resultado. Sobra o 429
            # sem desfecho — proxy, limitador de taxa, borda — e esse sim e
            # transporte, retentavel com backoff curto.
            raise RateLimitedError("Cinerie limitou a taxa (429)", retry_after_seconds=retry_after)
        if status == 503:
            # A marca `retryable` e o que separa "tente de novo" de "nao sei o
            # que aconteceu". Sem ela nao ha promessa de que nada foi persistido.
            if isinstance(body, Mapping) and body.get("retryable") is True:
                raise OperationalError(
                    f"Cinerie indisponivel de forma retentavel (503, {remote_code or 'sem codigo'})",
                    remote_code=str(remote_code) if remote_code else None,
                    retry_after_seconds=retry_after,
                )
            raise NonRetryableServerError(
                "Cinerie respondeu 503 sem marca de retentavel; nao ha promessa de que "
                "nada foi persistido"
            )
        if status in (408, 500, 502, 504):
            raise ServerError(f"Cinerie respondeu {status}")

        raise InvalidRemoteResponseError(f"Cinerie respondeu HTTP {status} sem desfecho conhecido")


__all__ = [
    "AUTH_SCHEME",
    "CONTRACTS_PATH",
    "CinerieClient",
    "CinerieConfig",
    "MAX_REQUEST_BYTES",
    "MAX_RESPONSE_BYTES",
    "PUBLICATIONS_PATH",
]
