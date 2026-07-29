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
    CinerieConnectionError,
    CinerieTimeoutError,
    ConfigurationError,
    InvalidRemoteResponseError,
    NonRetryableServerError,
    OperationalError,
    PermissionError_,
    RateLimitedError,
    ServerError,
)
from .outcomes import PublicationResult, parse_result

logger = logging.getLogger(__name__)

PUBLICATIONS_PATH: str = "/api/internal/editorial-publications"
CONTRACTS_PATH: str = "/api/internal/contracts"

#: Formato de autenticacao do Payload para contas tecnicas.
AUTH_SCHEME: str = "service-accounts API-Key"

#: Teto do corpo aceito na resposta. Uma resposta gigante e defeito, e ler tudo
#: antes de descobrir isso e como um cliente vira vetor de exaustao de memoria.
MAX_RESPONSE_BYTES: int = 1 * 1024 * 1024


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

        try:
            response = caller.request(
                method,
                url,
                headers=self._headers(),
                data=json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None,
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
        self._raise_for_transport_status(status, body, headers={})
        if not isinstance(body, Mapping) or not isinstance(body.get("contracts"), list):
            raise InvalidRemoteResponseError("manifesto de contratos em formato inesperado")
        return dict(body)

    # -- publicacao --------------------------------------------------------

    def submit_publication(self, payload: Mapping[str, Any]) -> PublicationResult:
        """Envia o pedido e devolve o desfecho normalizado.

        Os quatro desfechos do contrato **nao sao erro de transporte**: 201, 202,
        409 e 422 voltam como resultado. Erro operacional e de rede viram
        excecao, que e o que a camada de retry entende.
        """
        status, body, headers = self._request("POST", PUBLICATIONS_PATH, body=payload)

        result = parse_result(body, status)
        if result is not None and result.outcome != "OPERATIONAL_ERROR":
            return result

        self._raise_for_transport_status(status, body, headers=headers)

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
        self, status: int, body: Any, *, headers: Mapping[str, str]
    ) -> None:
        if 200 <= status < 300:
            return

        retry_after = self._retry_after(headers)
        remote_code = body.get("code") if isinstance(body, Mapping) else None

        if status == 401:
            raise AuthenticationError("Cinerie recusou a credencial (401)")
        if status == 403:
            raise PermissionError_(
                "service account sem o escopo editorial_auto_publish (403)"
            )
        if status == 429:
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
    "MAX_RESPONSE_BYTES",
    "PUBLICATIONS_PATH",
]
