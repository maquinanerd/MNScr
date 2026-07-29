"""A scriptable fake Payload CMS, bound to loopback only.

Not a test module (``python_files = ["test_*.py"]`` never collects it). It gives
the delivery tests a real HTTP round-trip — status codes, headers, malformed
bodies, timeouts — without touching anything outside ``127.0.0.1``.

The `_no_network` fixture used elsewhere in the suite blocks ``socket.socket``
outright, so tests that use this server cannot use that fixture. They assert
loopback instead: :meth:`FakePayloadServer.assert_loopback_only`.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional


@dataclass
class RecordedRequest:
    """What the fake server actually received."""

    path: str
    headers: Dict[str, str]
    body: Optional[Dict[str, Any]]
    raw_body: str

    @property
    def authorization(self) -> Optional[str]:
        return self.headers.get("Authorization")

    @property
    def idempotency_key(self) -> Optional[str]:
        return self.headers.get("Idempotency-Key")


@dataclass
class ScriptedResponse:
    """One canned reply.

    ``delay_seconds`` exists so a client timeout can be exercised for real;
    keep it just above the client's timeout and nothing else waits.
    """

    status: int = 201
    body: Any = field(default_factory=lambda: {"doc": {"id": "remote-draft-1"}})
    headers: Dict[str, str] = field(default_factory=dict)
    raw_body: Optional[str] = None
    delay_seconds: float = 0.0


class FakePayloadServer:
    """A loopback HTTP server that replies from a script."""

    def __init__(self, responses: Optional[List[ScriptedResponse]] = None):
        # When the script runs out the last response repeats, so a test only
        # has to describe the interesting prefix.
        self.responses: List[ScriptedResponse] = list(responses or [ScriptedResponse()])
        self.requests: List[RecordedRequest] = []
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    # --- lifecycle ---------------------------------------------------------

    def start(self) -> "FakePayloadServer":
        outer = self

        class _Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self) -> None:  # noqa: N802 - nome exigido por BaseHTTPRequestHandler
                length = int(self.headers.get("Content-Length", 0) or 0)
                raw = self.rfile.read(length).decode("utf-8") if length else ""
                try:
                    parsed = json.loads(raw) if raw else None
                except ValueError:
                    parsed = None
                outer.requests.append(
                    RecordedRequest(
                        path=self.path,
                        headers={k: v for k, v in self.headers.items()},
                        body=parsed,
                        raw_body=raw,
                    )
                )
                outer._reply(self, len(outer.requests))

            def do_GET(self) -> None:  # noqa: N802
                outer.requests.append(
                    RecordedRequest(
                        path=self.path,
                        headers={k: v for k, v in self.headers.items()},
                        body=None,
                        raw_body="",
                    )
                )
                outer._reply(self, len(outer.requests))

            def log_message(self, *args: Any) -> None:
                """Silence the default stderr access log."""

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def _reply(self, handler: BaseHTTPRequestHandler, call_number: int) -> None:
        index = min(call_number - 1, len(self.responses) - 1)
        scripted = self.responses[index]

        if scripted.delay_seconds:
            time.sleep(scripted.delay_seconds)

        if scripted.raw_body is not None:
            payload = scripted.raw_body.encode("utf-8")
        elif scripted.body is None:
            payload = b""
        else:
            payload = json.dumps(scripted.body, ensure_ascii=False).encode("utf-8")

        try:
            handler.send_response(scripted.status)
            handler.send_header("Content-Type", "application/json")
            handler.send_header("Content-Length", str(len(payload)))
            for name, value in scripted.headers.items():
                handler.send_header(name, value)
            handler.end_headers()
            if payload:
                handler.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            # The timeout tests hang up on purpose; the resulting write failure
            # is the expected outcome, not a server fault worth a traceback.
            pass

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        self._thread = None

    def __enter__(self) -> "FakePayloadServer":
        return self.start()

    def __exit__(self, *exc_info: Any) -> None:
        self.stop()

    # --- inspection --------------------------------------------------------

    @property
    def base_url(self) -> str:
        if self._server is None:
            raise RuntimeError("servidor falso nao foi iniciado")
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    @property
    def call_count(self) -> int:
        return len(self.requests)

    @property
    def last_request(self) -> Optional[RecordedRequest]:
        return self.requests[-1] if self.requests else None

    def assert_loopback_only(self) -> None:
        """The stand-in for the ``_no_network`` fixture.

        These tests need a real socket, so instead of forbidding sockets they
        prove the only reachable address is loopback.
        """
        host = self._server.server_address[0] if self._server else ""
        assert host in ("127.0.0.1", "::1"), f"servidor falso escutando fora do loopback: {host}"
        assert self.base_url.startswith("http://127.0.0.1:")


__all__ = ["FakePayloadServer", "RecordedRequest", "ScriptedResponse"]
