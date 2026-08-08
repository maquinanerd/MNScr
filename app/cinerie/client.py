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

import base64
import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple
from urllib.parse import urlparse, urlunparse

import requests

from .entity_resolve import ENTITY_RESOLVE_PATH, MAX_RESOLVE_ITEMS
from .errors import (
    AuthenticationError,
    BadRequestError,
    CinerieConnectionError,
    CinerieTimeoutError,
    ConfigurationError,
    EndpointUnavailableError,
    InvalidRemoteResponseError,
    MediaHeroError,
    MediaIngestError,
    NonRetryableServerError,
    OperationalError,
    PermissionError_,
    RateLimitedError,
    RequestTooLargeError,
    ServerError,
)
from .media_client import (
    HERO_OUTCOMES,
    HERO_STATE_CONFLICTS,
    MAX_MEDIA_BYTES,
    MAX_MEDIA_REQUEST_BYTES,
    MEDIA_INGEST_PATH,
    MediaHeroResult,
    MediaIngestResult,
    media_hero_path,
    sniff_ingestible_mime,
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

    def _headers(self, *, api_key: Optional[str] = None) -> Dict[str, str]:
        return {
            "Authorization": f"{AUTH_SCHEME} {api_key or self.config.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: Optional[Mapping[str, Any]] = None,
        max_body_bytes: Optional[int] = None,
        api_key: Optional[str] = None,
    ) -> Tuple[int, Any, Mapping[str, str]]:
        url = self.config.url_for(path)
        caller = self._session if self._session is not None else requests

        # Serializa UMA vez e mede o que sera realmente enviado. Medir o dict, ou
        # medir uma segunda serializacao, mediria outra coisa.
        encoded = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        cap = MAX_REQUEST_BYTES if max_body_bytes is None else max_body_bytes
        if encoded is not None and len(encoded) > cap:
            raise RequestTooLargeError(
                f"corpo de {len(encoded)} bytes acima do teto de {cap} "
                f"do Cinerie; nada foi enviado"
            )

        try:
            response = caller.request(
                method,
                url,
                headers=self._headers(api_key=api_key),
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

    # -- ingestao de midia ---------------------------------------------------

    def ingest_editorial_media(
        self,
        *,
        article_id: str,
        source_url: str,
        source_name: str,
        rights_holder: str,
        credit: str,
        alt: str,
        content_bytes: bytes,
        caption: Optional[str] = None,
        api_key: Optional[str] = None,
    ) -> MediaIngestResult:
        """``POST /api/internal/editorial-media``. Levanta ``MediaIngestError`` em qualquer recusa.

        Rota SEPARADA de ``submit_publication`` — outro caminho, outra conta
        tecnica (escopo ``editorial_media_ingest``, e so ele). ``api_key``
        aceita um override deliberado: misturar a credencial de midia na MESMA
        ``CinerieConfig`` da publicacao alargaria o raio de estrago de uma
        chave vazada sem ganho nenhum.

        Nunca levanta nada alem de ``MediaIngestError`` — quem chama trata
        qualquer falha como aviso e publica a materia sem foto.
        """
        if not content_bytes:
            raise MediaIngestError("nenhum byte de imagem recebido", remote_code="image_empty")
        if len(content_bytes) > MAX_MEDIA_BYTES:
            raise MediaIngestError(
                f"imagem com {len(content_bytes)} bytes acima do teto de {MAX_MEDIA_BYTES}",
                remote_code="image_too_large",
            )
        content_type = sniff_ingestible_mime(content_bytes)
        if content_type is None:
            raise MediaIngestError(
                "assinatura de bytes nao corresponde a jpeg/png/webp", remote_code="bytes_mismatch"
            )
        if not (api_key or self.config.api_key):
            raise MediaIngestError(
                "credencial de midia ausente (MNSCR_CINERIE_MEDIA_API_KEY)",
                remote_code="missing_media_api_key",
            )

        body: Dict[str, Any] = {
            "articleId": str(article_id),
            "sourceUrl": source_url,
            "sourceName": source_name,
            "rightsHolder": rights_holder,
            "credit": credit,
            "alt": alt,
            "contentType": content_type,
            "contentBase64": base64.b64encode(content_bytes).decode("ascii"),
        }
        if caption:
            body["caption"] = caption

        try:
            status, parsed, _ = self._request(
                "POST",
                MEDIA_INGEST_PATH,
                body=body,
                max_body_bytes=MAX_MEDIA_REQUEST_BYTES,
                api_key=api_key,
            )
        except (CinerieConnectionError, CinerieTimeoutError) as exc:
            raise MediaIngestError(str(exc), remote_code=exc.code, retryable=True) from exc
        except RequestTooLargeError as exc:
            raise MediaIngestError(str(exc), remote_code="payload_too_large") from exc
        except InvalidRemoteResponseError as exc:
            raise MediaIngestError(str(exc), remote_code="invalid_response") from exc

        if status in (200, 201):
            if not isinstance(parsed, Mapping):
                raise MediaIngestError("resposta 2xx sem corpo reconhecivel", remote_code="invalid_response")
            outcome = str(parsed.get("outcome") or "").strip()
            media_id = str(parsed.get("mediaId") or "").strip()
            content_hash = str(parsed.get("contentHash") or "").strip()
            if not outcome or not media_id:
                raise MediaIngestError("resposta 2xx sem outcome/mediaId", remote_code="invalid_response")
            return MediaIngestResult(outcome=outcome, media_id=media_id, content_hash=content_hash)

        remote_code = parsed.get("error") if isinstance(parsed, Mapping) else None
        issues = parsed.get("issues") if isinstance(parsed, Mapping) else None
        issues_list = [str(i) for i in issues] if isinstance(issues, list) else []
        raise MediaIngestError(
            f"Cinerie recusou a ingestao de midia (HTTP {status}, {remote_code or 'sem codigo'})",
            remote_code=str(remote_code) if remote_code else None,
            issues=issues_list,
            retryable=(status == 503),
        )

    def point_media_as_hero(
        self,
        *,
        media_id: str,
        article_id: str,
        api_key: Optional[str] = None,
    ) -> MediaHeroResult:
        """``PATCH /api/internal/editorial-media/:mediaId/hero``. Terceiro e ultimo passo.

        Fecha a sequencia que a ordem das dependencias abria: a materia precisa
        existir para a foto ser ingerida, e a foto precisa existir para virar
        capa — logo, nunca ha as duas coisas na mesma chamada. Os tres passos
        sao idempotentes e podem ser repetidos na integra a cada revisao.

        Mesma credencial da ingestao de bytes (escopo ``editorial_media_ingest``,
        nenhuma variavel de ambiente nova). ``api_key`` continua sendo passado
        explicitamente pelo mesmo motivo de la: guardar a chave de midia dentro
        da ``CinerieConfig`` de publicacao alargaria o raio de estrago de um
        vazamento sem ganho nenhum.

        Levanta ``MediaHeroError`` em qualquer recusa — e SO ela. Quem chama
        trata como aviso e a materia segue publicada, sem foto de capa.

        Os desfechos nomeados sao tratados pelo CODIGO, nunca pelo texto: o
        ``error`` do corpo decide, e a mensagem so acompanha para o log. Os
        quatro ``409`` sao marcados com ``state_conflict=True``, porque um
        ``409`` diz que a CAPA nao pode ser apontada — e nao que o ``mediaId``
        estava errado.
        """
        media_text = str(media_id or "").strip()
        article_text = str(article_id or "").strip()
        if not media_text or not article_text:
            raise MediaHeroError(
                "mediaId e articleId sao obrigatorios para apontar a capa",
                remote_code="validation_failed",
            )
        if not (api_key or self.config.api_key):
            raise MediaHeroError(
                "credencial de midia ausente (MNSCR_CINERIE_MEDIA_API_KEY)",
                remote_code="missing_media_api_key",
            )

        path = media_hero_path(media_text)
        try:
            status, parsed, _ = self._request(
                "PATCH", path, body={"articleId": article_text}, api_key=api_key
            )
        except (CinerieConnectionError, CinerieTimeoutError) as exc:
            raise MediaHeroError(str(exc), remote_code=exc.code, retryable=True) from exc
        except RequestTooLargeError as exc:
            raise MediaHeroError(str(exc), remote_code="payload_too_large") from exc
        except InvalidRemoteResponseError as exc:
            raise MediaHeroError(str(exc), remote_code="invalid_response") from exc

        if status == 200:
            if not isinstance(parsed, Mapping):
                raise MediaHeroError(
                    "resposta 200 sem corpo reconhecivel", remote_code="invalid_response"
                )
            outcome = str(parsed.get("outcome") or "").strip()
            if outcome not in HERO_OUTCOMES:
                # Um desfecho fora dos tres conhecidos pode ter escrito ou nao.
                # Presumir sucesso registraria uma capa que talvez nao exista.
                raise MediaHeroError(
                    f"desfecho desconhecido na resposta 200: {outcome or '(vazio)'}",
                    remote_code="invalid_response",
                )
            previous = parsed.get("previousMediaId")
            return MediaHeroResult(
                outcome=outcome,
                article_id=str(parsed.get("articleId") or article_text),
                media_id=str(parsed.get("mediaId") or media_text),
                previous_media_id=str(previous) if previous not in (None, "") else None,
            )

        remote_code = _remote_error(parsed)
        issues = parsed.get("issues") if isinstance(parsed, Mapping) else None
        issues_list = [str(item)[:200] for item in issues] if isinstance(issues, list) else []

        # O `409` e classificado pelo CODIGO. O status sozinho ja diria "conflito
        # de estado", mas o codigo e o que separa as quatro causas — e cada uma
        # se corrige num lugar diferente.
        state_conflict = status == 409
        explanation = HERO_STATE_CONFLICTS.get(remote_code or "")
        raise MediaHeroError(
            f"Cinerie nao apontou a capa (HTTP {status}, {remote_code or 'sem codigo'})"
            + (f": {explanation}" if explanation else ""),
            remote_code=remote_code,
            issues=issues_list,
            retryable=(status == 503 or status in (500, 502, 504)),
            state_conflict=state_conflict,
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


# ===========================================================================
# Resolucao de entidade — OUTRO servico, OUTRA credencial
# ===========================================================================


@dataclass
class CatalogResolveConfig:
    """Endereco e credencial do resolvedor de entidade (screen-app).

    Separado de ``CinerieConfig`` porque nao e o mesmo servico: o catalogo vive
    no `screen-db`, o CMS nao alcanca aquele banco, e a rota atende no
    `screen-app`. Juntar as duas configuracoes numa so faria uma credencial
    vazada abrir os DOIS lados da fronteira — que e exatamente o motivo pelo
    qual o escopo `catalog_resolve` nao reaproveita os escopos do CMS.
    """

    base_url: str
    api_key: str
    timeout_seconds: float = 10.0
    verify_tls: bool = True

    def __post_init__(self) -> None:
        self.base_url = str(self.base_url or "").strip().rstrip("/")
        self.api_key = str(self.api_key or "").strip()

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return (
            f"CatalogResolveConfig(base_url={_safe_url(self.base_url)!r}, "
            f"api_key={'***' if self.api_key else None!r})"
        )

    def issues(self) -> list[str]:
        problems: list[str] = []
        if not self.base_url:
            problems.append("MNSCR_CINERIE_CATALOG_RESOLVE_URL ausente")
        else:
            parsed = urlparse(self.base_url)
            if parsed.scheme not in ("http", "https"):
                problems.append("MNSCR_CINERIE_CATALOG_RESOLVE_URL precisa ser http(s)")
            if not parsed.hostname:
                problems.append("MNSCR_CINERIE_CATALOG_RESOLVE_URL sem host")
            if parsed.username or parsed.password:
                problems.append(
                    "MNSCR_CINERIE_CATALOG_RESOLVE_URL nao pode conter credencial embutida"
                )
        if not self.api_key:
            problems.append("MNSCR_CINERIE_CATALOG_RESOLVE_API_KEY ausente")
        if self.timeout_seconds <= 0:
            problems.append("timeout precisa ser positivo")
        return problems


class CatalogResolveClient:
    """``POST /api/internal/entity-resolve``. Uma chamada por materia, em lote.

    Autentica com ``Bearer`` — a forma que a rota documenta primeiro. Nada aqui
    interpreta o resultado: a decisao do que vira bloco mora em
    ``entity_resolve``, sem transporte.

    ``resolve`` **nunca levanta**. Toda falha vira ``None``, e ``None`` significa
    "nenhuma ficha nesta materia" — nunca "publique assim mesmo". A razao esta
    no proprio contrato: `503 resolve_failed` existe para que falha de leitura
    NAO chegue ao emissor como "nao existe", porque "nao existe" e exatamente a
    resposta que ele nao pode receber por engano. Como aqui nada re-tenta, a
    unica leitura segura de uma falha e a ausencia de ficha.
    """

    def __init__(self, config: CatalogResolveConfig, *, session: Optional[Any] = None) -> None:
        problems = config.issues()
        if problems:
            raise ConfigurationError(
                "configuracao do resolvedor de entidade invalida: " + "; ".join(problems)
            )
        self.config = config
        self._session = session

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return f"CatalogResolveClient({_safe_url(self.config.base_url)!r})"

    def resolve(self, items: Sequence[Mapping[str, Any]]) -> Optional[list]:
        """Devolve ``results`` na MESMA ordem dos itens, ou ``None`` em qualquer recusa."""
        lote = [dict(item) for item in items or []]
        if not lote:
            return []
        if len(lote) > MAX_RESOLVE_ITEMS:
            # O corte cabe a quem monta o lote; chegar aqui acima do teto e
            # defeito nosso, e mandar assim reprovaria o pedido INTEIRO.
            logger.warning(
                "[CINERIE_ENTITY] lote com %s itens acima do teto de %s; nada foi enviado",
                len(lote), MAX_RESOLVE_ITEMS,
            )
            return None

        url = f"{self.config.base_url}{ENTITY_RESOLVE_PATH}"
        encoded = json.dumps({"items": lote}, ensure_ascii=False).encode("utf-8")
        caller = self._session if self._session is not None else requests
        try:
            response = caller.request(
                "POST",
                url,
                headers={
                    "Authorization": f"Bearer {self.config.api_key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                data=encoded,
                timeout=self.config.timeout_seconds,
                verify=self.config.verify_tls,
            )
        except Exception as exc:  # noqa: BLE001 - ficha nunca custa a materia
            logger.warning(
                "[CINERIE_ENTITY] resolucao indisponivel (%s) em %s: %s; materia sem ficha",
                type(exc).__name__, _safe_url(url), exc,
            )
            return None

        raw = response.content or b""
        if len(raw) > MAX_RESPONSE_BYTES:
            logger.warning("[CINERIE_ENTITY] resposta acima do limite (%s bytes)", len(raw))
            return None
        try:
            parsed = json.loads(raw.decode("utf-8")) if raw else None
        except (UnicodeDecodeError, json.JSONDecodeError):
            parsed = None

        if response.status_code != 200:
            # Classificado pelo CODIGO, nunca pelo texto. `resolver_disabled`
            # (503, sem chave configurada DO OUTRO LADO) e `rate_limited` (429)
            # sao estados do servico, e nao defeito do lote — o log separa os
            # dois para que uma chave que falta nao pareca um nome que nao
            # existe.
            remoto = _remote_error(parsed) or "sem codigo"
            logger.warning(
                "[CINERIE_ENTITY] resolucao recusada (HTTP %s, %s); materia sem ficha",
                response.status_code, remoto,
            )
            return None

        resultados = parsed.get("results") if isinstance(parsed, Mapping) else None
        if not isinstance(resultados, list):
            logger.warning("[CINERIE_ENTITY] resposta 200 sem `results`; materia sem ficha")
            return None
        if len(resultados) != len(lote):
            # A rota promete um resultado por item, na mesma ordem. Um tamanho
            # diferente quebra o alinhamento, e alinhar errado publicaria a
            # ficha de uma entidade sob o nome de outra.
            logger.warning(
                "[CINERIE_ENTITY] %s resultados para %s itens; alinhamento quebrado, materia sem ficha",
                len(resultados), len(lote),
            )
            return None
        return resultados


__all__ = [
    "AUTH_SCHEME",
    "CONTRACTS_PATH",
    "CatalogResolveClient",
    "CatalogResolveConfig",
    "CinerieClient",
    "CinerieConfig",
    "MAX_REQUEST_BYTES",
    "MAX_RESPONSE_BYTES",
    "MEDIA_INGEST_PATH",
    "PUBLICATIONS_PATH",
]
