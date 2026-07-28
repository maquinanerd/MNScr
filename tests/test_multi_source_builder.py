from app.multi_source_builder import (
    build_multi_source_payload,
    build_sources_audit_payload,
    detect_extraction_status,
    is_reliable_single_source,
)


class FakeExtractor:
    def __init__(self, pages):
        self.pages = pages

    def _fetch_html(self, url):
        page = self.pages.get(url)
        if page == "FETCH_ERROR":
            return None
        return page or "<html></html>"

    def extract(self, html, url=""):
        page = self.pages.get(url)
        if isinstance(page, dict):
            return page
        if page == "BODY_NOT_FOUND":
            return {"title": "", "content": ""}
        return {"title": url, "content": html}


def _cluster(urls):
    return {
        "url": urls[0],
        "additional_urls": urls[1:],
        "event_key": "event-1",
        "primary_source": "deadline.com",
        "all_sources": ["deadline.com", "screenrant.com", "collider.com"],
        "fonte_nome": "RSSPrime",
    }


def _content(label, repeat=40):
    return " ".join([label] * repeat)


def test_detect_extraction_status_article_body_ok():
    assert detect_extraction_status(_content("texto", 30), "https://example.com", min_chars=50) == "ARTICLE_BODY_OK"


def test_detect_extraction_status_too_short():
    assert detect_extraction_status("curto", "https://example.com", min_chars=50) == "TOO_SHORT"


def test_detect_extraction_status_paywalled():
    assert detect_extraction_status("Para continuar lendo, assine agora.", "https://example.com") == "PAYWALLED"


def test_detect_extraction_status_navigation_noise():
    content = "PUBLICIDADE Mais lidas Ultimas: Newsletter Cookies " + _content("menu", 20)
    assert detect_extraction_status(content, "https://example.com", min_chars=50) == "NAVIGATION_NOISE"


def test_build_multi_source_payload_returns_payload_for_two_ok_sources():
    urls = ["https://deadline.com/story", "https://screenrant.com/story"]
    extractor = FakeExtractor(
        {
            urls[0]: {"title": "A", "content": _content("fonte-a")},
            urls[1]: {"title": "B", "content": _content("fonte-b")},
        }
    )

    payload = build_multi_source_payload(_cluster(urls), extractor, min_chars=50)

    assert payload is not None
    assert len(payload["cluster_docs"]) == 2
    assert len(payload["sources_used"]) == 2
    assert payload["publication_basis"] == "multi_source"


def test_build_multi_source_payload_matches_source_names_by_url_domain():
    urls = [
        "https://variety.com/story",
        "https://www.thewrap.com/story",
        "https://www.hollywoodreporter.com/story",
    ]
    cluster = {
        "url": urls[0],
        "additional_urls": urls[1:],
        "event_key": "event-swapped-order",
        "primary_source": "variety",
        "all_sources": ["hollywoodreporter", "variety", "thewrap"],
        "fonte_nome": "The Hollywood Reporter + Variety + The Wrap",
    }
    extractor = FakeExtractor(
        {
            url: {"title": url, "content": _content(url)}
            for url in urls
        }
    )

    payload = build_multi_source_payload(cluster, extractor, min_chars=50)

    assert payload is not None
    assert [
        (doc["url"], doc["source_name"])
        for doc in payload["cluster_docs"]
    ] == [
        (urls[0], "variety"),
        (urls[1], "thewrap"),
        (urls[2], "hollywoodreporter"),
    ]


def test_build_multi_source_payload_uses_url_label_for_inconsistent_feed_name():
    urls = [
        "https://comicbook.com/story",
        "https://movieweb.com/story",
    ]
    cluster = {
        "url": urls[0],
        "additional_urls": urls[1:],
        "event_key": "event-inconsistent-name",
        "primary_source": "cbr",
        "all_sources": ["cbr", "movieweb"],
        "fonte_nome": "CBR + MovieWeb",
    }
    extractor = FakeExtractor(
        {
            url: {"title": url, "content": _content(url)}
            for url in urls
        }
    )

    payload = build_multi_source_payload(cluster, extractor, min_chars=50)

    assert payload is not None
    assert payload["cluster_docs"][0]["source_name"] == "ComicBook"
    assert payload["cluster_docs"][1]["source_name"] == "movieweb"


def test_build_multi_source_payload_returns_none_for_one_untrusted_source():
    """Uma unica fonte fora da allowlist nao sustenta um draft."""
    urls = ["https://blogaleatorio.example/story", "https://outroblog.example/story"]
    extractor = FakeExtractor(
        {
            urls[0]: {"title": "A", "content": _content("fonte-a")},
            urls[1]: {"title": "B", "content": "curto"},
        }
    )

    assert build_multi_source_payload(_cluster(urls), extractor, min_chars=50) is None


def test_build_multi_source_payload_allows_single_trusted_source():
    """Fonte unica confiavel e permitida, mas fica marcada na proveniencia."""
    urls = ["https://deadline.com/story", "https://screenrant.com/story"]
    extractor = FakeExtractor(
        {
            urls[0]: {"title": "A", "content": _content("fonte-a")},
            urls[1]: {"title": "B", "content": "curto"},
        }
    )

    payload = build_multi_source_payload(_cluster(urls), extractor, min_chars=50)

    assert payload is not None
    assert payload["publication_basis"] == "single_source_trusted"
    assert len(payload["sources_used"]) == 1
    assert payload["sources_used"][0]["domain"] == "deadline.com"
    assert payload["sources_used"][0]["status"] == "ARTICLE_BODY_OK"
    assert [s["status"] for s in payload["sources_skipped"]] == ["TOO_SHORT"]


def test_build_multi_source_payload_discards_paywalled_source():
    urls = [
        "https://deadline.com/story",
        "https://screenrant.com/paywall-story",
        "https://collider.com/story",
    ]
    extractor = FakeExtractor(
        {
            urls[0]: {"title": "A", "content": _content("fonte-a")},
            urls[1]: {"title": "B", "content": "Para continuar lendo, assine agora."},
            urls[2]: {"title": "C", "content": _content("fonte-c")},
        }
    )

    payload = build_multi_source_payload(_cluster(urls), extractor, min_chars=50)

    assert payload is not None
    assert len(payload["sources_used"]) == 2
    assert any(source["status"] == "PAYWALLED" for source in payload["sources_skipped"])


def test_build_multi_source_payload_discards_navigation_noise_source():
    urls = [
        "https://deadline.com/story",
        "https://screenrant.com/noisy-story",
        "https://collider.com/story",
    ]
    noise = "PUBLICIDADE Mais lidas Ultimas: Newsletter Cookies " + _content("menu", 20)
    extractor = FakeExtractor(
        {
            urls[0]: {"title": "A", "content": _content("fonte-a")},
            urls[1]: {"title": "B", "content": noise},
            urls[2]: {"title": "C", "content": _content("fonte-c")},
        }
    )

    payload = build_multi_source_payload(_cluster(urls), extractor, min_chars=50)

    assert payload is not None
    assert len(payload["sources_used"]) == 2
    assert any(source["status"] == "NAVIGATION_NOISE" for source in payload["sources_skipped"])


def test_build_multi_source_payload_allows_reliable_single_source(monkeypatch):
    monkeypatch.setenv("ALLOW_SINGLE_SOURCE", "true")
    urls = ["https://deadline.com/story", "https://screenrant.com/story"]
    extractor = FakeExtractor(
        {
            urls[0]: {"title": "A", "content": _content("fonte-a")},
            urls[1]: {"title": "B", "content": "curto"},
        }
    )

    payload = build_multi_source_payload(_cluster(urls), extractor, min_chars=50)

    assert payload is not None
    assert payload["publication_basis"] == "single_source_trusted"


def test_build_multi_source_payload_rejects_unreliable_single_source(monkeypatch):
    monkeypatch.setenv("ALLOW_SINGLE_SOURCE", "true")
    urls = ["https://unknown.example/story", "https://screenrant.com/story"]
    extractor = FakeExtractor(
        {
            urls[0]: {"title": "A", "content": _content("fonte-a")},
            urls[1]: {"title": "B", "content": "curto"},
        }
    )

    assert build_multi_source_payload(_cluster(urls), extractor, min_chars=50) is None


def test_build_multi_source_payload_marks_duplicates_as_skipped():
    """URL duplicada apos canonicalizacao nao conta como segunda fonte."""
    urls = ["https://deadline.com/story", "https://www.deadline.com/story/"]
    extractor = FakeExtractor({urls[0]: {"title": "A", "content": _content("fonte-a")}})

    payload = build_multi_source_payload(_cluster(urls), extractor, min_chars=50)

    assert payload is not None, "deadline.com e fonte unica confiavel"
    assert len(payload["sources_used"]) == 1
    assert [s["status"] for s in payload["sources_skipped"]] == ["DUPLICATE"]
    assert payload["publication_basis"] == "single_source_trusted"


def test_duplicate_untrusted_source_does_not_sustain_a_draft():
    """Sem allowlist, a duplicata deixa o cluster com uma unica fonte valida."""
    urls = ["https://blogaleatorio.example/story", "https://www.blogaleatorio.example/story/"]
    extractor = FakeExtractor({urls[0]: {"title": "A", "content": _content("fonte-a")}})

    assert build_multi_source_payload(_cluster(urls), extractor, min_chars=50) is None


def test_build_sources_audit_payload_removes_body():
    audit = build_sources_audit_payload(
        [
            {
                "source_name": "A",
                "domain": "example.com",
                "url": "https://example.com",
                "title": "Title",
                "published_at": "",
                "status": "ARTICLE_BODY_OK",
                "chars": 1000,
                "source_type": "media",
                "content": "body",
            }
        ]
    )

    assert audit == [
        {
            "source_name": "A",
            "domain": "example.com",
            "url": "https://example.com",
            "title": "Title",
            "published_at": "",
            "status": "ARTICLE_BODY_OK",
            "chars": 1000,
            "source_type": "media",
        }
    ]


def test_is_reliable_single_source_checks_domain_and_status():
    assert is_reliable_single_source(
        {"url": "https://deadline.com/story", "domain": "deadline.com", "status": "ARTICLE_BODY_OK"}
    )
    assert not is_reliable_single_source(
        {"url": "https://deadline.com/story", "domain": "deadline.com", "status": "TOO_SHORT"}
    )
    assert not is_reliable_single_source(
        {"url": "https://unknown.example/story", "domain": "unknown.example", "status": "ARTICLE_BODY_OK"}
    )
