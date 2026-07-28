from bs4 import BeautifulSoup

from app.cluster_extractor import _safe_extract
from app.content_limiter import truncate_html_by_visible_chars
from app.multi_source_builder import MAX_CONTENT_CHARS, build_multi_source_payload


class FakeExtractor:
    def __init__(self, content):
        self.content = content

    def _fetch_html(self, url):
        return "<html></html>"

    def extract(self, html, url=""):
        return {
            "title": "Title",
            "content": self.content,
            "featured_image_url": None,
            "images": [],
            "videos": [],
        }


def _visible_text(html):
    return BeautifulSoup(html, "html.parser").get_text(" ", strip=True)


def test_truncates_visible_text_instead_of_raw_html():
    heavy_markup = '<div data-padding="{}"></div>'.format("x" * 12000)
    content = heavy_markup + "<article><p>" + ("body " * 2200) + "</p></article>"

    limited = truncate_html_by_visible_chars(content, 8000)

    assert "body" in _visible_text(limited)
    assert len(_visible_text(limited)) <= 8000
    assert len(_visible_text(limited)) > 7900


def test_preserves_structure_and_embed_before_limit():
    content = (
        "<h2>Heading</h2>"
        '<blockquote class="twitter-tweet"><p>Reaction text</p></blockquote>'
        "<p>" + ("article " * 2000) + "</p>"
    )

    limited = truncate_html_by_visible_chars(content, 8000)
    soup = BeautifulSoup(limited, "html.parser")

    assert soup.find("h2").get_text(strip=True) == "Heading"
    assert soup.select_one("blockquote.twitter-tweet").get_text(" ", strip=True) == "Reaction text"
    assert len(soup.get_text(" ", strip=True)) <= 8000


def test_multi_source_builder_uses_visible_text_limit():
    content = '<div data-padding="{}"></div><p>{}</p>'.format(
        "x" * 12000,
        "useful " * 2000,
    )
    urls = ["https://deadline.com/a", "https://screenrant.com/b"]
    cluster = {
        "url": urls[0],
        "additional_urls": [urls[1]],
        "event_key": "event",
        "primary_source": "deadline",
        "all_sources": ["deadline", "screenrant"],
    }

    payload = build_multi_source_payload(cluster, FakeExtractor(content), min_chars=50)

    assert payload is not None
    assert len(payload["cluster_docs"]) == 2
    assert all("useful" in _visible_text(doc["content"]) for doc in payload["cluster_docs"])
    assert all(len(_visible_text(doc["content"])) <= MAX_CONTENT_CHARS for doc in payload["cluster_docs"])


def test_legacy_cluster_extractor_uses_same_visible_text_limit():
    content = '<div data-padding="{}"></div><p>{}</p>'.format(
        "x" * 12000,
        "legacy " * 2000,
    )

    doc = _safe_extract(FakeExtractor(content), "https://example.com/a", "source")

    assert doc is not None
    assert "legacy" in _visible_text(doc["content"])
    assert len(_visible_text(doc["content"])) <= MAX_CONTENT_CHARS
