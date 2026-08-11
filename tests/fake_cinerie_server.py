"""Servidor HTTP local que finge ser o Cinerie. **Nao e um teste.**

Sem `test_` no nome de proposito: o pytest nao coleta este arquivo.

Existe para que os testes do cliente exercitem HTTP de verdade — status,
headers, corpo, timeout, `Retry-After`, corpo nao-JSON — em vez de um mock da
funcao interna. Um mock da propria funcao que se quer testar prova apenas que
ela foi chamada.

Escuta em ``127.0.0.1:0`` (porta efemera). ``assert_loopback_only()`` e a
garantia explicita de que nenhum host externo e alcancado: os testes que usam
este servidor nao podem usar a fixture ``_no_network``, que bloqueia socket por
completo.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

#: Manifesto compativel por padrao. Os testes o sobrescrevem para simular
#: divergencia de versao ou de hash.
DEFAULT_BUILD_IDENTIFIER = "fake-build-0000000"


@dataclass
class ScriptedResponse:
    """Uma resposta programada para a proxima requisicao."""

    status: int
    body: Any = None
    headers: Dict[str, str] = field(default_factory=dict)
    #: Corpo cru, quando o teste precisa de algo que NAO e JSON.
    raw_body: Optional[bytes] = None
    #: Segundos a dormir antes de responder, para provocar timeout no cliente.
    delay_seconds: float = 0.0


@dataclass
class RecordedRequest:
    """O que o servidor recebeu."""

    method: str
    path: str
    headers: Dict[str, str]
    body: Any = None
    raw_body: bytes = b""

    @property
    def authorization(self) -> Optional[str]:
        for key, value in self.headers.items():
            if key.lower() == "authorization":
                return value
        return None


class FakeCinerieServer:
    """Servidor de loopback com respostas programaveis."""

    def __init__(
        self,
        script: Optional[List[ScriptedResponse]] = None,
        *,
        contracts: Optional[Dict[str, Any]] = None,
        contracts_script: Optional[List[ScriptedResponse]] = None,
        default_response: Optional[ScriptedResponse] = None,
    ) -> None:
        self.script: List[ScriptedResponse] = list(script or [])
        self.contracts_script: List[ScriptedResponse] = list(contracts_script or [])
        self.contracts = contracts
        self.default_response = default_response
        self.requests: List[RecordedRequest] = []
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    # -- ciclo de vida -----------------------------------------------------

    def start(self) -> "FakeCinerieServer":
        server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(self))
        server.daemon_threads = True
        self._server = server
        self._thread = threading.Thread(target=server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def __enter__(self) -> "FakeCinerieServer":
        return self.start()

    def __exit__(self, *exc_info: Any) -> None:
        self.stop()

    # -- inspecao ----------------------------------------------------------

    @property
    def base_url(self) -> str:
        if self._server is None:
            raise RuntimeError("servidor nao iniciado")
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    @property
    def call_count(self) -> int:
        return len(self.requests)

    @property
    def publication_count(self) -> int:
        return sum(1 for item in self.requests if item.path.endswith("editorial-publications"))

    @property
    def last_request(self) -> RecordedRequest:
        if not self.requests:
            raise AssertionError("nenhuma requisicao recebida")
        return self.requests[-1]

    def contracts_manifest(self) -> Dict[str, Any]:
        if self.contracts is not None:
            return self.contracts
        from app.cinerie.contract import local_identity

        identity = local_identity()
        return {
            "contracts": [
                {
                    "contractName": identity.contract_name,
                    "contractVersion": identity.contract_version,
                    "schemaHash": identity.schema_hash,
                    "compatibility": "stable",
                    "direction": "inbound",
                }
            ],
            "buildIdentifier": DEFAULT_BUILD_IDENTIFIER,
        }

    def next_response(self, path: str) -> ScriptedResponse:
        # O manifesto tem script PROPRIO. Sem essa separacao, o GET do preflight
        # consumiria a primeira resposta programada para a publicacao — e cada
        # teste teria de saber que existe um preflight antes do POST.
        if path.endswith("/contracts"):
            if self.contracts_script:
                return self.contracts_script.pop(0)
            return ScriptedResponse(200, self.contracts_manifest())
        if self.script:
            return self.script.pop(0)
        if self.default_response is not None:
            return self.default_response
        return ScriptedResponse(500, {"error": "sem resposta programada"})


def _make_handler(server: FakeCinerieServer):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args: Any) -> None:  # noqa: A003
            """Silencio: o log padrao polui a saida do pytest."""

        def _handle(self, method: str) -> None:
            import time

            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            try:
                parsed = json.loads(raw.decode("utf-8")) if raw else None
            except (UnicodeDecodeError, json.JSONDecodeError):
                parsed = None

            server.requests.append(
                RecordedRequest(
                    method=method,
                    path=self.path,
                    headers={key: value for key, value in self.headers.items()},
                    body=parsed,
                    raw_body=raw,
                )
            )

            response = server.next_response(self.path)
            if response.delay_seconds:
                time.sleep(response.delay_seconds)

            payload = (
                response.raw_body
                if response.raw_body is not None
                else json.dumps(response.body if response.body is not None else {}).encode("utf-8")
            )
            try:
                self.send_response(response.status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                for key, value in response.headers.items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                # O cliente desistiu (timeout, por exemplo). Nao e defeito do
                # servidor, e o traceback so polui a saida do teste.
                pass

        def do_GET(self) -> None:  # noqa: N802
            self._handle("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._handle("POST")

    return Handler


def assert_loopback_only(url: str) -> None:
    """Garante que a URL aponta para loopback.

    Os testes de transporte nao podem usar a fixture ``_no_network``, que
    bloqueia socket por completo. Esta assercao e a garantia que fica no lugar
    dela: se algum dia um teste apontar para um host real, ele falha aqui em vez
    de sair para a internet.
    """
    host = urlparse(url).hostname or ""
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise AssertionError(f"teste tentou alcancar host externo: {host!r}")


__all__ = [
    "DEFAULT_BUILD_IDENTIFIER",
    "FakeCinerieServer",
    "RecordedRequest",
    "ScriptedResponse",
    "assert_loopback_only",
]
