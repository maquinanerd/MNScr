"""O MNScr busca URLs que ele nao escolheu, e isso tem consequencia.

Endereco de feed, `<loc>` de sitemap e link de artigo sao entrada de fora. Uma
requisicao que sai sem verificacao faz do pipeline um procurador de quem escreveu
a URL. Em VM de nuvem o alvo classico e `169.254.169.254`, que serve credencial
de instancia a quem perguntar.

Os casos aqui sao os que o mandato A4 exige: IPv4, IPv6, DNS e redirect
apontando para IP privado, corpo sem `Content-Length`, bomba de compressao e
corpo acima do teto.
"""

from __future__ import annotations

import gzip
import http.server
import threading
import zlib

import pytest

from app.safe_http import (
    ResponseTooLargeError,
    UnsafeUrlError,
    assert_safe_url,
    safe_get,
)

# ---------------------------------------------------------------------------
# Destino
# ---------------------------------------------------------------------------


class TestEsquemaECredencial:
    @pytest.mark.parametrize(
        "url",
        [
            "file:///etc/passwd",
            "ftp://exemplo.test/x",
            "gopher://exemplo.test/x",
            "//exemplo.test/sem-esquema",
        ],
    )
    def test_esquema_fora_de_http_e_recusado(self, url):
        with pytest.raises(UnsafeUrlError):
            assert_safe_url(url, resolve=False)

    def test_credencial_embutida_e_recusada(self):
        """Credencial em URL vaza em log, em Referer e em historico de proxy."""
        with pytest.raises(UnsafeUrlError, match="credencial"):
            assert_safe_url("https://usuario:senha@exemplo.test/x", resolve=False)


class TestEnderecoInternoLiteral:
    @pytest.mark.parametrize(
        "url, rotulo",
        [
            ("http://127.0.0.1/x", "loopback v4"),
            ("http://localhost.localdomain./x", "nome que resolve para loopback"),
            ("http://10.0.0.5/x", "privado 10/8"),
            ("http://172.16.3.4/x", "privado 172.16/12"),
            ("http://192.168.1.1/x", "privado 192.168/16"),
            ("http://169.254.169.254/latest/meta-data/", "metadados de nuvem"),
            ("http://0.0.0.0/x", "nao especificado"),
            ("http://[::1]/x", "loopback v6"),
            ("http://[fe80::1]/x", "link-local v6"),
            ("http://[fc00::1]/x", "privado v6"),
            ("http://[::ffff:127.0.0.1]/x", "loopback v4 mapeado em v6"),
            ("http://224.0.0.1/x", "multicast"),
        ],
    )
    def test_destino_interno_nao_sai(self, url, rotulo):
        with pytest.raises(UnsafeUrlError):
            assert_safe_url(url)

    def test_endereco_publico_passa(self):
        assert assert_safe_url("https://93.184.216.34/x") is not None


class TestResolucaoDeNome:
    def test_nome_que_resolve_para_privado_e_recusado(self, monkeypatch):
        """Bloquear pelo TEXTO da URL nao basta: o nome pode ser publico.

        Este e o caso que separa uma checagem cosmetica de uma real.
        """
        monkeypatch.setattr(
            "app.safe_http.resolved_addresses",
            lambda host, port: ["10.1.2.3"],
        )
        with pytest.raises(UnsafeUrlError, match="interno"):
            assert_safe_url("https://parece-publico.test/x")

    def test_um_endereco_privado_entre_varios_ja_recusa(self, monkeypatch):
        """Nao basta o PRIMEIRO ser publico.

        Se um host devolve um endereco publico e um privado, deixar passar faria
        a escolha do sistema operacional decidir a seguranca.
        """
        monkeypatch.setattr(
            "app.safe_http.resolved_addresses",
            lambda host, port: ["93.184.216.34", "127.0.0.1"],
        )
        with pytest.raises(UnsafeUrlError):
            assert_safe_url("https://misto.test/x")

    def test_nome_que_resolve_para_publico_passa(self, monkeypatch):
        monkeypatch.setattr(
            "app.safe_http.resolved_addresses",
            lambda host, port: ["93.184.216.34"],
        )
        assert assert_safe_url("https://publico.test/x")


# ---------------------------------------------------------------------------
# Servidor local para os casos de corpo e redirect
# ---------------------------------------------------------------------------


class _Handler(http.server.BaseHTTPRequestHandler):
    rota = {}

    def do_GET(self):  # noqa: N802 - assinatura da stdlib
        acao = self.rota.get(self.path)
        if acao is None:
            self.send_response(404)
            self.end_headers()
            return
        acao(self)

    def log_message(self, *args):  # silencia o log do servidor de teste
        return


@pytest.fixture
def servidor():
    servidor = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=servidor.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{servidor.server_address[1]}"
    servidor.shutdown()
    servidor.server_close()


def _responder(handler, corpo: bytes, headers: dict = None, status: int = 200):
    handler.send_response(status)
    for chave, valor in (headers or {}).items():
        handler.send_header(chave, valor)
    handler.end_headers()
    handler.wfile.write(corpo)


class TestTamanhoDoCorpo:
    def test_corpo_sem_content_length_ainda_respeita_o_teto(self, servidor):
        """Sem `Content-Length` nao ha promessa nenhuma sobre o tamanho.

        O que vale e o que ja foi lido — por isso o teto e aplicado durante a
        leitura, e nao a partir do cabecalho.
        """
        _Handler.rota = {
            "/grande": lambda h: _responder(h, b"x" * (200 * 1024))
        }
        with pytest.raises(ResponseTooLargeError):
            safe_get(
                f"{servidor}/grande",
                max_bytes=50 * 1024,
                allow_private_destination=True,
            )

    def test_content_length_acima_do_teto_e_recusado_antes_de_ler(self, servidor):
        """Corpo declarado grande demais nao precisa ser baixado para ser negado.

        O caminho inverso — declarar MENOS do que se envia — nao e risco aqui: o
        urllib3 trunca a leitura no valor declarado, entao o excesso nunca chega.
        Quem protege contra corpo sem declaracao nenhuma e o teto aplicado
        durante a leitura, no teste acima.
        """
        _Handler.rota = {
            "/enorme": lambda h: _responder(
                h, b"y" * 32, {"Content-Length": str(500 * 1024 * 1024)}
            )
        }
        with pytest.raises(ResponseTooLargeError, match="Content-Length"):
            safe_get(
                f"{servidor}/enorme",
                max_bytes=50 * 1024,
                allow_private_destination=True,
            )

    def test_bomba_de_compressao_para_no_teto_do_descomprimido(self, servidor):
        """20 KB comprimidos viram gigabytes ao descomprimir.

        O teto do recebido nao pega isto: a bomba cabe folgada nele. Por isso o
        teto do DESCOMPRIMIDO e separado, e a descompressao e incremental —
        esperar o fim para descomprimir seria exatamente o que a bomba explora.
        """
        bomba = gzip.compress(b"\0" * (64 * 1024 * 1024))
        assert len(bomba) < 1 * 1024 * 1024, "a bomba precisa caber no teto de recebidos"

        _Handler.rota = {
            "/bomba": lambda h: _responder(h, bomba, {"Content-Encoding": "gzip"})
        }
        with pytest.raises(ResponseTooLargeError, match="descomprimido"):
            safe_get(
                f"{servidor}/bomba",
                max_bytes=8 * 1024 * 1024,
                max_decompressed_bytes=1 * 1024 * 1024,
                allow_private_destination=True,
            )

    def test_corpo_dentro_do_teto_chega_inteiro(self, servidor):
        _Handler.rota = {"/ok": lambda h: _responder(h, b"conteudo pequeno")}
        resultado = safe_get(f"{servidor}/ok", allow_private_destination=True)
        assert resultado.content == b"conteudo pequeno"
        assert resultado.status_code == 200

    def test_gzip_dentro_do_teto_e_descomprimido(self, servidor):
        _Handler.rota = {
            "/gz": lambda h: _responder(
                h, gzip.compress(b"texto do feed"), {"Content-Encoding": "gzip"}
            )
        }
        resultado = safe_get(f"{servidor}/gz", allow_private_destination=True)
        assert resultado.content == b"texto do feed"


class TestRedirect:
    def test_cada_salto_e_conferido(self, monkeypatch, servidor):
        """Destino honesto pode responder 302 para o loopback.

        Delegar o redirect ao `requests` entregaria o salto sem verificacao —
        a primeira URL passaria no exame e a segunda nao seria examinada.
        """
        _Handler.rota = {
            "/salta": lambda h: _responder(
                h, b"", {"Location": "http://169.254.169.254/latest/meta-data/"}, status=302
            )
        }
        with pytest.raises(UnsafeUrlError):
            # `allow_private_destination` vale so para a PRIMEIRA URL, que e o
            # servidor de teste; o salto seguinte volta a ser examinado.
            safe_get(f"{servidor}/salta", allow_private_destination=False)

    def test_cadeia_longa_demais_e_interrompida(self, servidor):
        _Handler.rota = {
            "/loop": lambda h: _responder(h, b"", {"Location": "/loop"}, status=302)
        }
        with pytest.raises(UnsafeUrlError, match="redirects demais"):
            safe_get(
                f"{servidor}/loop",
                max_redirects=2,
                allow_private_destination=True,
            )

    def test_redirect_sem_location_e_erro_e_nao_silencio(self, servidor):
        _Handler.rota = {"/vazio": lambda h: _responder(h, b"", {}, status=302)}
        with pytest.raises(UnsafeUrlError, match="Location"):
            safe_get(f"{servidor}/vazio", allow_private_destination=True)


def test_deflate_tambem_e_limitado(servidor):
    """`deflate` e o mesmo risco com outro nome."""
    compressor = zlib.compressobj()
    bruto = compressor.compress(b"\0" * (32 * 1024 * 1024)) + compressor.flush()
    _Handler.rota = {
        "/deflate": lambda h: _responder(h, bruto, {"Content-Encoding": "deflate"})
    }
    with pytest.raises(ResponseTooLargeError):
        safe_get(
            f"{servidor}/deflate",
            max_decompressed_bytes=512 * 1024,
            allow_private_destination=True,
        )
