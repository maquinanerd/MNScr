"""Ingestao de midia editorial: sniff, selecao de capa e falha nunca bloqueia.

Cobre o item pedido na tarefa: falha na ingestao de imagem NAO impede a
publicacao da materia. `ingest_hero_image` e a fronteira que garante isso —
ela NUNCA levanta, para qualquer motivo de falha.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

import pytest

from app.cinerie.client import CinerieClient, CinerieConfig
from app.cinerie.errors import MediaIngestError
from app.cinerie.media_client import sniff_ingestible_mime
from app.cinerie.media_selection import ingest_hero_image, select_hero_candidate

JPEG_BYTES = b"\xff\xd8\xff" + b"\x00" * 32
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
WEBP_BYTES = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 16


def test_sniff_recognizes_the_three_ingestible_formats():
    assert sniff_ingestible_mime(JPEG_BYTES) == "image/jpeg"
    assert sniff_ingestible_mime(PNG_BYTES) == "image/png"
    assert sniff_ingestible_mime(WEBP_BYTES) == "image/webp"


def test_sniff_rejects_unknown_bytes():
    assert sniff_ingestible_mime(b"not an image") is None


def test_select_hero_candidate_prefers_the_larger_declared_dimension():
    candidates = [
        {"url": "https://example.com/img-700x700.jpg", "alt": "menor"},
        {"url": "https://example.com/img-1600x900.jpg", "alt": "maior"},
    ]
    chosen = select_hero_candidate(candidates)
    assert chosen is not None
    assert chosen["url"].endswith("1600x900.jpg")


def test_select_hero_candidate_falls_back_to_cluster_order_without_dimensions():
    candidates = [
        {"url": "https://example.com/primary.jpg"},
        {"url": "https://example.com/secondary.jpg"},
    ]
    chosen = select_hero_candidate(candidates)
    assert chosen["url"].endswith("primary.jpg")


def test_select_hero_candidate_filters_non_editorial_images():
    candidates = [
        {"url": "https://example.com/tracking-pixel.jpg"},
        {"url": "https://example.com/real-photo.jpg"},
    ]
    chosen = select_hero_candidate(candidates)
    assert chosen["url"].endswith("real-photo.jpg")


def test_select_hero_candidate_returns_none_without_usable_images():
    assert select_hero_candidate([{"url": "https://example.com/logo.png"}]) is None
    assert select_hero_candidate([]) is None


def test_ingest_hero_image_returns_none_without_media_api_key():
    client = CinerieClient(CinerieConfig(base_url="https://cinerie.example", api_key="pub-key"))
    result = ingest_hero_image(
        article_id="9",
        image_candidates=[{"url": "https://example.com/real-photo.jpg"}],
        source_name="Variety",
        client=client,
        media_api_key="",
    )
    assert result is None


def test_ingest_hero_image_returns_none_without_a_usable_candidate():
    client = CinerieClient(CinerieConfig(base_url="https://cinerie.example", api_key="pub-key"))
    result = ingest_hero_image(
        article_id="9",
        image_candidates=[{"url": "https://example.com/logo.png"}],
        source_name="Variety",
        client=client,
        media_api_key="media-key",
    )
    assert result is None


def test_ingest_hero_image_returns_none_when_download_is_unsafe(monkeypatch):
    """URL que resolve para destino interno: `safe_get` recusa, e a materia segue sem foto."""
    client = CinerieClient(CinerieConfig(base_url="https://cinerie.example", api_key="pub-key"))
    result = ingest_hero_image(
        article_id="9",
        image_candidates=[{"url": "http://169.254.169.254/real-photo.jpg"}],
        source_name="Variety",
        client=client,
        media_api_key="media-key",
    )
    assert result is None


class _FakeResponse:
    def __init__(self, status_code: int, body: Mapping[str, Any]):
        import json

        self.status_code = status_code
        self.content = json.dumps(body).encode("utf-8")
        self.headers: Mapping[str, str] = {}


class _FakeSession:
    """Simula `requests` sem rede: devolve exatamente o que o teste programou."""

    def __init__(self, response: _FakeResponse):
        self._response = response
        self.last_request: Optional[Mapping[str, Any]] = None

    def request(self, method, url, *, headers=None, data=None, timeout=None, verify=None):
        self.last_request = {"method": method, "url": url, "headers": headers, "data": data}
        return self._response


class _FakeFetchResult:
    def __init__(self, content: bytes, status_code: int = 200):
        self.content = content
        self.status_code = status_code


def test_ingest_hero_image_returns_none_when_cinerie_rejects_it(monkeypatch):
    """422 do Cinerie vira `MediaIngestError` dentro do client — `ingest_hero_image` engole e devolve `None`."""
    session = _FakeSession(_FakeResponse(422, {"error": "validation_failed", "issues": ["alt ausente"]}))
    client = CinerieClient(
        CinerieConfig(base_url="https://cinerie.example", api_key="pub-key"), session=session
    )
    monkeypatch.setattr("app.safe_http.safe_get", lambda *a, **k: _FakeFetchResult(JPEG_BYTES))

    result = ingest_hero_image(
        article_id="9",
        image_candidates=[{"url": "https://example.com/real-photo.jpg"}],
        source_name="Variety",
        client=client,
        media_api_key="media-key",
    )
    assert result is None


def test_ingest_hero_image_succeeds_and_returns_the_media_id(monkeypatch):
    session = _FakeSession(_FakeResponse(201, {"outcome": "created", "mediaId": "42", "contentHash": "sha256:abc"}))
    client = CinerieClient(
        CinerieConfig(base_url="https://cinerie.example", api_key="pub-key"), session=session
    )
    monkeypatch.setattr("app.safe_http.safe_get", lambda *a, **k: _FakeFetchResult(JPEG_BYTES))

    result = ingest_hero_image(
        article_id="9",
        image_candidates=[{"url": "https://example.com/real-photo.jpg"}],
        source_name="Variety",
        client=client,
        media_api_key="media-key",
    )
    assert result is not None
    assert result.media_id == "42"
    assert result.outcome == "created"
    assert session.last_request["headers"]["Authorization"] == "service-accounts API-Key media-key"


def test_media_hook_never_costs_a_publication_that_already_succeeded(monkeypatch, caplog):
    """A regra inegociavel, no ponto onde ela pode ser quebrada.

    `_ingest_hero_media_best_effort` roda DENTRO do `try` de
    `_publish_to_cinerie`, e o `except` de la devolve `None`. Se a ingestao
    vazasse uma excecao, o `article_id` de uma materia JA ACEITA pelo Cinerie
    seria descartado e a publicacao apareceria como falha — o pior desfecho
    possivel, porque o estado remoto e o local passariam a discordar.

    Este teste forca a ingestao a levantar (o modo mais violento de falhar) e
    exige que a funcao volte em silencio, com aviso no log.
    """
    import logging

    from app import pipeline

    monkeypatch.setattr(pipeline, "MNSCR_CINERIE_MEDIA_UPLOAD_ENABLED", True)
    monkeypatch.setattr(pipeline, "MNSCR_CINERIE_MEDIA_API_KEY", "media-key")

    def _explode(**kwargs):
        raise RuntimeError("falha catastrofica na ingestao")

    monkeypatch.setattr("app.cinerie.media_selection.ingest_hero_image", _explode)

    class _Result:
        article_id = "9"

    class _Draft:
        draft_id = "draft-1"
        media_candidates = [{"url": "https://example.com/real-photo.jpg"}]
        sources = []
        content = None

    with caplog.at_level(logging.WARNING):
        # A afirmacao E o fato de nao levantar: qualquer excecao aqui reprova.
        pipeline._ingest_hero_media_best_effort(_Draft(), _Result(), object())

    assert any("CINERIE_MEDIA" in r.message or "CINERIE_MEDIA" in r.getMessage()
               for r in caplog.records), "a falha precisa ficar registrada, nao sumir"


def test_media_hook_is_a_noop_while_the_flag_is_off(monkeypatch):
    """Com `MNSCR_CINERIE_MEDIA_UPLOAD_ENABLED=false` nada e baixado nem enviado."""
    from app import pipeline

    monkeypatch.setattr(pipeline, "MNSCR_CINERIE_MEDIA_UPLOAD_ENABLED", False)

    def _must_not_run(**kwargs):  # pragma: no cover - a chamada e o defeito
        raise AssertionError("ingestao rodou com a flag desligada")

    monkeypatch.setattr("app.cinerie.media_selection.ingest_hero_image", _must_not_run)

    class _Result:
        article_id = "9"

    pipeline._ingest_hero_media_best_effort(object(), _Result(), object())


def test_client_ingest_editorial_media_raises_media_ingest_error_on_rejection():
    """Chamando o client diretamente (sem passar por `ingest_hero_image`): a excecao aparece."""
    session = _FakeSession(_FakeResponse(403, {"error": "forbidden_scope"}))
    client = CinerieClient(
        CinerieConfig(base_url="https://cinerie.example", api_key="pub-key"), session=session
    )
    with pytest.raises(MediaIngestError) as excinfo:
        client.ingest_editorial_media(
            article_id="9",
            source_url="https://example.com/real-photo.jpg",
            source_name="Variety",
            rights_holder="Variety",
            credit="Divulgacao/Variety",
            alt="Cena da materia",
            content_bytes=JPEG_BYTES,
            api_key="media-key",
        )
    assert excinfo.value.remote_code == "forbidden_scope"
