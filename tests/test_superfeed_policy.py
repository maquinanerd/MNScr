from app.superfeed_policy import check_superfeed_policy


def _item(**overrides):
    base = {
        "url": "https://example.com/noticia",
        "title": "Filme ganha novo trailer",
        "summary": "Resumo sem problemas.",
        "source_count": 2,
        "multi_source": True,
    }
    base.update(overrides)
    return base


def test_liveblog_url_is_blocked():
    status, _ = check_superfeed_policy(
        _item(url="https://example.com/ao-vivo/premiacao")
    )

    assert status == "LIVEBLOG_BLOCKED"


def test_opinion_url_is_blocked():
    status, _ = check_superfeed_policy(
        _item(url="https://example.com/blog/coluna-do-editor")
    )

    assert status == "OPINION_BLOCKED"


def test_broken_encoding_in_title_is_blocked():
    status, _ = check_superfeed_policy(
        _item(title="S\\xefo Paulo aparece em nova reportagem")
    )

    assert status == "BROKEN_ENCODING"


def test_single_source_without_flag_is_blocked():
    status, _ = check_superfeed_policy(
        _item(
            url="https://unknown-source.example/news",
            source_count=1,
            multi_source=False,
        )
    )

    assert status == "SINGLE_SOURCE_NOT_RELIABLE"


def test_single_source_trusted_domain_is_allowed():
    status, _ = check_superfeed_policy(
        _item(
            url="https://deadline.com/2026/05/movie-news",
            source_count=1,
            multi_source=False,
        )
    )

    assert status == "OK"


def test_allow_single_source_flag_allows_untrusted_domain():
    status, _ = check_superfeed_policy(
        _item(
            url="https://unknown-source.example/news",
            source_count=1,
            multi_source=False,
        ),
        allow_single_source=True,
    )

    assert status == "OK"
