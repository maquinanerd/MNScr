"""Busca externa com destino verificado e corpo limitado.

O MNScr busca URLs que ele NÃO escolheu: endereço de feed, `<loc>` de sitemap,
link de artigo. Tudo isso é entrada de fora, e uma requisição que sai sem
verificação transforma o pipeline em procurador de quem escreveu a URL — é o
padrão SSRF. Numa VM de nuvem o alvo clássico é o serviço de metadados em
``169.254.169.254``, que serve credencial de instância a quem perguntar.

Três defesas, porque uma só não cobre:

1. **Destino resolvido.** Bloquear pelo texto da URL não basta: um nome público
   pode resolver para IP privado. Aqui a resolução é feita e CADA endereço
   devolvido é conferido.
2. **Cada salto.** Um destino honesto pode responder ``302`` para
   ``http://127.0.0.1``. Por isso o redirect não é delegado ao ``requests``: ele
   é seguido à mão, e cada salto passa pela mesma verificação do primeiro.
3. **Tamanho.** Resposta sem ``Content-Length`` pode não terminar nunca, e 20 KB
   de gzip viram gigabytes ao descomprimir. O corpo é lido em pedaços com teto, e
   o teto do texto DESCOMPRIMIDO é separado do teto dos bytes recebidos.

Limite honesto desta implementação: entre a resolução e a conexão existe uma
janela em que o DNS pode mudar de resposta (*DNS rebinding*). Fechá-la exige
conectar no IP já validado e carregar o ``Host`` à mão, o que quebra TLS por
nome. A janela fica registrada aqui em vez de ser silenciada — o que esta camada
promete é bloquear alvo interno declarado e alvo interno resolvido, não derrotar
um resolvedor hostil.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
import zlib
from dataclasses import dataclass
from typing import Final, Iterable, Optional
from urllib.parse import urlsplit, urlunsplit

import requests

logger = logging.getLogger(__name__)

#: Só estes esquemas saem. `file://`, `ftp://` e `gopher://` não são busca de
#: notícia; são leitura de disco e de serviço interno com outra roupa.
ALLOWED_SCHEMES: Final[frozenset] = frozenset({"http", "https"})

#: Teto dos bytes RECEBIDOS.
DEFAULT_MAX_BYTES: Final[int] = 8 * 1024 * 1024

#: Teto do conteúdo DESCOMPRIMIDO. Separado de propósito: a compressão é
#: justamente o que permite caber uma bomba dentro do primeiro teto.
DEFAULT_MAX_DECOMPRESSED_BYTES: Final[int] = 32 * 1024 * 1024

DEFAULT_CONNECT_TIMEOUT: Final[float] = 10.0
DEFAULT_READ_TIMEOUT: Final[float] = 20.0
DEFAULT_MAX_REDIRECTS: Final[int] = 5


class UnsafeUrlError(ValueError):
    """A URL não pode sair: esquema, credencial ou destino proibido."""


class ResponseTooLargeError(ValueError):
    """O corpo passou do teto e a leitura foi abortada."""


@dataclass(frozen=True)
class FetchResult:
    url: str
    status_code: int
    content: bytes
    headers: dict


def _is_blocked_address(ip: ipaddress._BaseAddress) -> bool:
    """Endereços que nunca são destino legítimo de busca de notícia.

    `is_global` cobriria quase tudo, mas deixa passar casos que importam aqui, e
    ser explícito documenta a intenção para quem mexer nisto depois.
    """
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        # `::ffff:127.0.0.1` é loopback escrito de outro jeito.
        ip = ip.ipv4_mapped
    return (
        ip.is_private          # 10/8, 172.16/12, 192.168/16, fc00::/7
        or ip.is_loopback      # 127/8, ::1
        or ip.is_link_local    # 169.254/16 (metadados de nuvem), fe80::/10
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def resolved_addresses(host: str, port: int) -> list[str]:
    """Todos os endereços do host, IPv4 e IPv6.

    Todos, e não o primeiro: um host que devolve um endereço público e um
    privado precisa ser recusado, senão a escolha do sistema operacional decide
    a segurança.
    """
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise UnsafeUrlError(f"host nao resolve: {host}") from exc
    return [info[4][0] for info in infos]


def assert_safe_url(url: str, *, resolve: bool = True) -> str:
    """Recusa a URL, ou devolve ela normalizada."""
    parsed = urlsplit((url or "").strip())

    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        raise UnsafeUrlError(f"esquema nao permitido: {parsed.scheme!r}")

    # Credencial em URL vaza em log, em Referer e em histórico de proxy.
    if parsed.username or parsed.password:
        raise UnsafeUrlError("URL com credencial embutida")

    host = parsed.hostname
    if not host:
        raise UnsafeUrlError("URL sem host")

    porta = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)

    # Host escrito como IP é conferido direto, sem passar pelo resolvedor.
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if _is_blocked_address(literal):
            raise UnsafeUrlError(f"destino interno: {host}")
        return url

    if resolve:
        for endereco in resolved_addresses(host, porta):
            try:
                ip = ipaddress.ip_address(endereco)
            except ValueError:
                continue
            if _is_blocked_address(ip):
                raise UnsafeUrlError(f"{host} resolve para destino interno ({endereco})")

    return url


def _read_capped(
    response: requests.Response,
    *,
    max_bytes: int,
    max_decompressed_bytes: int,
) -> bytes:
    """Lê o corpo em pedaços, com teto — e com teto separado se vier comprimido.

    `Content-Length` não é usado como garantia: ele é informado pelo servidor e
    pode mentir, ou faltar. O que vale é o que já foi lido.
    """
    declarado = response.headers.get("Content-Length")
    if declarado and declarado.isdigit() and int(declarado) > max_bytes:
        raise ResponseTooLargeError(
            f"Content-Length {declarado} acima do teto de {max_bytes} bytes"
        )

    codificacao = (response.headers.get("Content-Encoding") or "").lower()
    comprimido = "gzip" in codificacao or "deflate" in codificacao
    descompressor = None
    if comprimido:
        # `wbits` com 32 aceita cabeçalho gzip e zlib.
        descompressor = zlib.decompressobj(32 + zlib.MAX_WBITS)

    recebidos = 0
    saida = bytearray()
    # `decode_content=False` de proposito: `iter_content` do requests JA
    # descomprime, e ai os dois tetos viram um so — o de "recebidos" passaria a
    # medir bytes ja expandidos, que e exatamente o que a bomba de compressao
    # precisa para caber. Lendo cru, `recebidos` conta o que veio pelo fio e a
    # expansao acontece aqui, sob teto proprio.
    for pedaco in response.raw.stream(64 * 1024, decode_content=False):
        if not pedaco:
            continue
        recebidos += len(pedaco)
        if recebidos > max_bytes:
            raise ResponseTooLargeError(
                f"corpo passou de {max_bytes} bytes recebidos"
            )
        if descompressor is None:
            saida.extend(pedaco)
            continue

        # Descomprime incrementalmente: esperar o fim para descomprimir seria
        # exatamente o que a bomba de compressão explora.
        expandido = descompressor.decompress(pedaco, max_decompressed_bytes - len(saida) + 1)
        saida.extend(expandido)
        if len(saida) > max_decompressed_bytes:
            raise ResponseTooLargeError(
                f"conteudo descomprimido passou de {max_decompressed_bytes} bytes"
            )

    return bytes(saida)


def safe_get(
    url: str,
    *,
    headers: Optional[dict] = None,
    connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
    read_timeout: float = DEFAULT_READ_TIMEOUT,
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_decompressed_bytes: int = DEFAULT_MAX_DECOMPRESSED_BYTES,
    max_redirects: int = DEFAULT_MAX_REDIRECTS,
    session: Optional[requests.Session] = None,
    allow_private_destination: bool = False,
) -> FetchResult:
    """GET com destino verificado a cada salto e corpo limitado.

    ``allow_private_destination`` existe só para o teste local poder falar com um
    servidor em loopback. Ele nunca é ligado por configuração de produção — é
    argumento de chamada, e quem o passa está dizendo explicitamente que aquele
    destino é o servidor falso do próprio teste.
    """
    cliente = session or requests.Session()
    atual = url
    vistos: list[str] = []

    for salto in range(max_redirects + 1):
        if not allow_private_destination:
            assert_safe_url(atual)
        vistos.append(atual)

        resposta = cliente.get(
            atual,
            headers=headers or {},
            timeout=(connect_timeout, read_timeout),
            allow_redirects=False,   # o salto é conferido aqui, não pelo requests
            stream=True,             # sem stream não há como impor teto
        )

        if resposta.is_redirect or resposta.status_code in (301, 302, 303, 307, 308):
            destino = resposta.headers.get("Location")
            resposta.close()
            if not destino:
                raise UnsafeUrlError(f"redirect sem Location a partir de {atual}")
            atual = requests.compat.urljoin(atual, destino)
            if salto == max_redirects:
                raise UnsafeUrlError(f"redirects demais (>{max_redirects}): {vistos}")
            continue

        try:
            corpo = _read_capped(
                resposta,
                max_bytes=max_bytes,
                max_decompressed_bytes=max_decompressed_bytes,
            )
        finally:
            resposta.close()

        return FetchResult(
            url=atual,
            status_code=resposta.status_code,
            content=corpo,
            headers=dict(resposta.headers),
        )

    raise UnsafeUrlError(f"redirects demais (>{max_redirects}): {vistos}")


def normalized_url(url: str) -> str:
    """Sem fragmento: ele nunca chega ao servidor e só suja chave de cache."""
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))


__all__ = [
    "ALLOWED_SCHEMES",
    "FetchResult",
    "ResponseTooLargeError",
    "UnsafeUrlError",
    "assert_safe_url",
    "normalized_url",
    "resolved_addresses",
    "safe_get",
]


def _unused(_: Iterable) -> None:  # pragma: no cover - ancora de tipagem
    return None
