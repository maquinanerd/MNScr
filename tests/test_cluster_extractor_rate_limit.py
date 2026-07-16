"""
Testes para o rate limiting por domínio em app/cluster_extractor.py — Bloco 8.
"""
import time
from unittest.mock import MagicMock, patch

import pytest

from app.cluster_extractor import (
    DEFAULT_DELAY,
    DOMAIN_DELAYS,
    _get_domain_delay,
    collect_cluster_images,
    extract_cluster_documents,
)


# ---------------------------------------------------------------------------
# TASK 8.4 — Testes de _get_domain_delay()
# ---------------------------------------------------------------------------

def test_screenrant_delay():
    assert _get_domain_delay("https://screenrant.com/some-article") == 2.0


def test_collider_delay():
    assert _get_domain_delay("https://collider.com/some-article") == 2.0


def test_ign_delay():
    assert _get_domain_delay("https://ign.com/some-article") == 1.5


def test_deadline_delay():
    assert _get_domain_delay("https://deadline.com/some-article") == 1.5


def test_variety_delay():
    assert _get_domain_delay("https://variety.com/some-article") == 1.5


def test_unknown_domain_returns_default():
    assert _get_domain_delay("https://unknown-publisher.com/article") == DEFAULT_DELAY


def test_invalid_url_returns_default():
    assert _get_domain_delay("not_a_url_at_all") == DEFAULT_DELAY


def test_empty_url_returns_default():
    assert _get_domain_delay("") == DEFAULT_DELAY


# ---------------------------------------------------------------------------
# Verificar que DEFAULT_DELAY é 1.0 e DOMAIN_DELAYS contém as entradas esperadas
# ---------------------------------------------------------------------------

def test_default_delay_value():
    assert DEFAULT_DELAY == 1.0


def test_domain_delays_entries():
    assert DOMAIN_DELAYS["screenrant.com"] == 2.0
    assert DOMAIN_DELAYS["collider.com"] == 2.0
    assert DOMAIN_DELAYS["ign.com"] == 1.5
    assert DOMAIN_DELAYS["deadline.com"] == 1.5
    assert DOMAIN_DELAYS["variety.com"] == 1.5


# ---------------------------------------------------------------------------
# Verificar que time.sleep é chamado somente em URLs secundárias
# ---------------------------------------------------------------------------

def _make_extractor(return_content: bool = True):
    """Cria um extractor mock que retorna conteúdo ou None."""
    extractor = MagicMock()
    if return_content:
        extractor._fetch_html.return_value = "<html>content</html>"
        extractor.extract.return_value = {
            "content": "some content",
            "title": "Test Title",
            "featured_image_url": None,
            "videos": [],
        }
    else:
        extractor._fetch_html.return_value = None
    return extractor


def test_sleep_called_for_each_secondary_url():
    """time.sleep deve ser chamado uma vez por URL secundária."""
    item = {
        "url": "https://primary-source.com/main",
        "primary_source": "PrimarySource",
        "event_key": "test_event",
        "additional_urls": [
            "https://screenrant.com/secondary-1",
            "https://collider.com/secondary-2",
        ],
    }
    extractor = _make_extractor()

    with patch("app.cluster_extractor.time.sleep") as mock_sleep:
        docs = extract_cluster_documents(item, extractor)

    # sleep chamado exatamente 2 vezes — uma por URL secundária
    assert mock_sleep.call_count == 2

    calls = [c.args[0] for c in mock_sleep.call_args_list]
    assert calls[0] == 2.0  # screenrant.com
    assert calls[1] == 2.0  # collider.com


def test_sleep_not_called_for_primary_url():
    """time.sleep NÃO deve ser chamado para a URL primária."""
    item = {
        "url": "https://screenrant.com/primary-article",
        "primary_source": "ScreenRant",
        "event_key": "test_primary_only",
        "additional_urls": [],  # sem URLs secundárias
    }
    extractor = _make_extractor()

    with patch("app.cluster_extractor.time.sleep") as mock_sleep:
        extract_cluster_documents(item, extractor)

    mock_sleep.assert_not_called()


def test_sleep_uses_default_delay_for_unknown_secondary():
    """URL secundária com domínio desconhecido deve usar DEFAULT_DELAY."""
    item = {
        "url": "https://primary-source.com/main",
        "primary_source": "PrimarySource",
        "event_key": "test_unknown",
        "additional_urls": ["https://niche-blog.com/article"],
    }
    extractor = _make_extractor()

    with patch("app.cluster_extractor.time.sleep") as mock_sleep:
        extract_cluster_documents(item, extractor)

    mock_sleep.assert_called_once_with(DEFAULT_DELAY)


def test_max_secondary_docs_not_changed():
    """Confirma que apenas MAX_SECONDARY_DOCS URLs secundárias são processadas."""
    from app.cluster_extractor import MAX_SECONDARY_DOCS
    assert MAX_SECONDARY_DOCS == 2

    item = {
        "url": "https://primary-source.com/main",
        "primary_source": "PrimarySource",
        "event_key": "test_max",
        "additional_urls": [
            "https://ign.com/sec-1",
            "https://variety.com/sec-2",
            "https://deadline.com/sec-3",  # deve ser ignorada
        ],
    }
    extractor = _make_extractor()

    with patch("app.cluster_extractor.time.sleep") as mock_sleep:
        extract_cluster_documents(item, extractor)

    # Apenas 2 sleeps — a terceira URL deve ser ignorada
    assert mock_sleep.call_count == 2


def test_collect_cluster_images_defaults_to_three_images():
    docs = [
        {
            "images": [
                {"url": "https://cdn.example.com/one.jpg"},
                {"url": "https://cdn.example.com/two.jpg"},
            ],
            "featured_image_url": "https://cdn.example.com/featured-one.jpg",
        },
        {
            "images": [
                {"url": "https://cdn.example.com/three.jpg"},
                {"url": "https://cdn.example.com/four.jpg"},
            ],
            "featured_image_url": "https://cdn.example.com/featured-two.jpg",
        },
    ]

    images = collect_cluster_images(docs)

    assert len(images) == 3
    assert [image["url"] for image in images] == [
        "https://cdn.example.com/one.jpg",
        "https://cdn.example.com/two.jpg",
        "https://cdn.example.com/featured-one.jpg",
    ]
