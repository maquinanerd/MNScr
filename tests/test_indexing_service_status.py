from app import indexing_service


def _patch_indexing_calls(monkeypatch, *, google_ok, indexnow_ok, sitemap_ok, rss_ok, yoast_present):
    monkeypatch.setattr(
        indexing_service,
        "send_to_google_indexing_api",
        lambda post: (google_ok, "google-result"),
    )
    monkeypatch.setattr(
        indexing_service,
        "send_to_indexnow",
        lambda post: (indexnow_ok, "indexnow-result"),
    )
    monkeypatch.setattr(
        indexing_service,
        "ping_sitemap",
        lambda post: [("google_sitemap", sitemap_ok, "sitemap-result")],
    )
    monkeypatch.setattr(
        indexing_service,
        "ping_rss_feed",
        lambda post: (rss_ok, "rss-result"),
    )
    monkeypatch.setattr(
        indexing_service,
        "validate_post_in_yoast_news_sitemap",
        lambda url, date_published=None: (yoast_present, "present" if yoast_present else "missing"),
    )
    monkeypatch.setattr(
        indexing_service,
        "inspect_google_url_status",
        lambda post: (False, {"message": "inspection mocked"}),
    )


def test_search_console_property_prefers_gsc_property_sc_domain(monkeypatch):
    monkeypatch.setattr(indexing_service, "GSC_PROPERTY", "sc-domain:maquinanerd.com.br")
    monkeypatch.setattr(indexing_service, "GOOGLE_URL_INSPECTION_SITE_URL", "https://www.maquinanerd.com.br/")
    monkeypatch.setattr(indexing_service, "_get_site_url", lambda: "https://www.maquinanerd.com.br")

    assert indexing_service._get_search_console_site_url() == "sc-domain:maquinanerd.com.br"


def test_search_console_property_accepts_legacy_sc_domain(monkeypatch):
    monkeypatch.setattr(indexing_service, "GSC_PROPERTY", "")
    monkeypatch.setattr(indexing_service, "GOOGLE_URL_INSPECTION_SITE_URL", "sc-domain:maquinanerd.com.br")
    monkeypatch.setattr(indexing_service, "_get_site_url", lambda: "https://www.maquinanerd.com.br")

    assert indexing_service._get_search_console_site_url() == "sc-domain:maquinanerd.com.br"


def test_search_console_property_normalizes_plain_domain(monkeypatch):
    monkeypatch.setattr(indexing_service, "GOOGLE_URL_INSPECTION_SITE_URL", "maquinanerd.com.br")
    monkeypatch.setattr(indexing_service, "_get_site_url", lambda: "https://www.maquinanerd.com.br")

    assert indexing_service._get_search_console_site_url() == "sc-domain:maquinanerd.com.br"


def test_search_console_property_falls_back_to_domain_property_from_wp_site(monkeypatch):
    monkeypatch.setattr(indexing_service, "GOOGLE_URL_INSPECTION_SITE_URL", "")
    monkeypatch.setattr(indexing_service, "_get_site_url", lambda: "https://www.maquinanerd.com.br")

    assert indexing_service._get_search_console_site_url() == "sc-domain:maquinanerd.com.br"


def test_indexing_api_error_does_not_block_supported_notification(monkeypatch):
    _patch_indexing_calls(
        monkeypatch,
        google_ok=False,
        indexnow_ok=False,
        sitemap_ok=False,
        rss_ok=True,
        yoast_present=True,
    )

    result = indexing_service._process_post(
        {
            "id": 123,
            "url": "https://www.maquinanerd.com.br/post/",
            "date_published": "2026-05-28T13:00:00+00:00",
            "title": "Post",
        },
        trigger="test",
    )

    assert result["status"] == "sent"
    assert result["google_ok"] is False
    assert result["google_indexing_role"] == "best_effort_diagnostic"
    assert result["supported_notification_ok"] is True
    assert result["discovery_ok"] is True
    assert result["yoast_news_present"] is True


def test_indexing_api_success_counts_as_notification_success(monkeypatch):
    _patch_indexing_calls(
        monkeypatch,
        google_ok=True,
        indexnow_ok=False,
        sitemap_ok=False,
        rss_ok=False,
        yoast_present=False,
    )

    result = indexing_service._process_post(
        {
            "id": 123,
            "url": "https://www.maquinanerd.com.br/post/",
            "date_published": "2026-05-28T13:00:00+00:00",
            "title": "Post",
        },
        trigger="test",
    )

    assert result["status"] == "sent"
    assert result["google_ok"] is True
    assert result["google_indexing_role"] == "best_effort_diagnostic"
    assert result["supported_notification_ok"] is True
    assert result["discovery_ok"] is False
    assert result["yoast_news_present"] is False


def test_news_sitemap_presence_counts_as_discovery_success(monkeypatch):
    _patch_indexing_calls(
        monkeypatch,
        google_ok=False,
        indexnow_ok=False,
        sitemap_ok=False,
        rss_ok=False,
        yoast_present=True,
    )

    result = indexing_service._process_post(
        {
            "id": 123,
            "url": "https://www.maquinanerd.com.br/post/",
            "date_published": "2026-05-28T13:00:00+00:00",
            "title": "Post",
        },
        trigger="test",
    )

    assert result["status"] == "sent"
    assert result["supported_notification_ok"] is False
    assert result["discovery_ok"] is True
    assert result["yoast_news_present"] is True


def test_rss_success_counts_as_supported_notification(monkeypatch):
    _patch_indexing_calls(
        monkeypatch,
        google_ok=False,
        indexnow_ok=True,
        sitemap_ok=False,
        rss_ok=True,
        yoast_present=False,
    )

    result = indexing_service._process_post(
        {
            "id": 123,
            "url": "https://www.maquinanerd.com.br/post/",
            "date_published": "2026-05-28T13:00:00+00:00",
            "title": "Post",
        },
        trigger="test",
    )

    assert result["status"] == "sent"
    assert result["supported_notification_ok"] is True
    assert result["discovery_ok"] is False


def test_indexnow_success_does_not_drive_news_status_when_discovery_and_rss_fail(monkeypatch):
    _patch_indexing_calls(
        monkeypatch,
        google_ok=False,
        indexnow_ok=True,
        sitemap_ok=False,
        rss_ok=False,
        yoast_present=False,
    )

    result = indexing_service._process_post(
        {
            "id": 123,
            "url": "https://www.maquinanerd.com.br/post/",
            "date_published": "2026-05-28T13:00:00+00:00",
            "title": "Post",
        },
        trigger="test",
    )

    assert result["status"] == "error"
    assert result["indexnow_ok"] is True
    assert result["supported_notification_ok"] is False
    assert result["discovery_ok"] is False


def test_no_notification_and_no_discovery_is_error(monkeypatch):
    _patch_indexing_calls(
        monkeypatch,
        google_ok=False,
        indexnow_ok=False,
        sitemap_ok=False,
        rss_ok=False,
        yoast_present=False,
    )

    result = indexing_service._process_post(
        {
            "id": 123,
            "url": "https://www.maquinanerd.com.br/post/",
            "date_published": "2026-05-28T13:00:00+00:00",
            "title": "Post",
        },
        trigger="test",
    )

    assert result["status"] == "error"
    assert result["supported_notification_ok"] is False
    assert result["discovery_ok"] is False


class FakeXmlRpcServer:
    def __init__(self, response):
        self.weblogUpdates = self
        self.response = response

    def extendedPing(self, *args):
        return self.response

    def ping(self, *args):
        return self.response


def test_rss_ping_throttled_response_is_not_sent(monkeypatch):
    logs = []
    response = {"flerror": True, "message": "Pinging too fast. Slow down cowboy."}
    monkeypatch.setattr(indexing_service, "_already_sent", lambda url, engine: False)
    monkeypatch.setattr(indexing_service, "_get_site_url", lambda: "https://www.maquinanerd.com.br")
    monkeypatch.setattr(indexing_service, "_get_feed_url", lambda: "https://www.maquinanerd.com.br/feed/")
    monkeypatch.setattr(indexing_service.xmlrpc.client, "ServerProxy", lambda url: FakeXmlRpcServer(response))
    monkeypatch.setattr(
        indexing_service,
        "save_log",
        lambda url, date_published, status, engine, response, date_sent=None: logs.append(status),
    )

    ok, message = indexing_service.ping_rss_feed(
        {"url": "https://www.maquinanerd.com.br/post/", "date_published": "2026-05-28T13:00:00+00:00"}
    )

    assert ok is False
    assert "too fast" in message.lower()
    assert "sent" not in logs
    assert "throttled" in logs


def test_rss_ping_success_response_is_sent(monkeypatch):
    logs = []
    response = {"flerror": False, "message": "Thanks for the ping."}
    monkeypatch.setattr(indexing_service, "_already_sent", lambda url, engine: False)
    monkeypatch.setattr(indexing_service, "_get_site_url", lambda: "https://www.maquinanerd.com.br")
    monkeypatch.setattr(indexing_service, "_get_feed_url", lambda: "https://www.maquinanerd.com.br/feed/")
    monkeypatch.setattr(indexing_service.xmlrpc.client, "ServerProxy", lambda url: FakeXmlRpcServer(response))
    monkeypatch.setattr(
        indexing_service,
        "save_log",
        lambda url, date_published, status, engine, response, date_sent=None: logs.append(status),
    )

    ok, message = indexing_service.ping_rss_feed(
        {"url": "https://www.maquinanerd.com.br/post/", "date_published": "2026-05-28T13:00:00+00:00"}
    )

    assert ok is True
    assert "thanks" in message.lower()
    assert "sent" in logs


def test_obsolete_sitemap_ping_endpoints_are_ignored(monkeypatch):
    monkeypatch.setattr(indexing_service, "SITEMAP_PING_ENABLED", True)
    monkeypatch.setattr(
        indexing_service,
        "SITEMAP_PING_ENDPOINTS",
        "google_sitemap=https://www.google.com/ping?sitemap={sitemap};"
        "bing_sitemap=https://www.bing.com/ping?sitemap={sitemap}",
    )
    monkeypatch.setattr(indexing_service, "_get_sitemap_url", lambda: "https://www.maquinanerd.com.br/sitemap.xml")
    monkeypatch.setattr(
        indexing_service.requests,
        "get",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("obsolete ping should not be called")),
    )

    assert indexing_service.ping_sitemap({"url": "https://www.maquinanerd.com.br/post/"}) == []


class FakeIndexNowResponse:
    def __init__(self, status_code):
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self.text = "response"


def test_indexnow_accepts_only_200_or_202(monkeypatch):
    logs = []
    monkeypatch.setattr(indexing_service, "INDEXNOW_ENABLED", True)
    monkeypatch.setattr(indexing_service, "INDEXNOW_KEY", "abc123")
    monkeypatch.setattr(indexing_service, "_already_sent", lambda url, engine: False)
    monkeypatch.setattr(
        indexing_service,
        "save_log",
        lambda url, date_published, status, engine, response, date_sent=None: logs.append(status),
    )
    monkeypatch.setattr(indexing_service.requests, "post", lambda *args, **kwargs: FakeIndexNowResponse(202))

    ok, _ = indexing_service.send_to_indexnow(
        {"url": "https://www.maquinanerd.com.br/post/", "date_published": "2026-05-28T13:00:00+00:00"}
    )

    assert ok is True
    assert logs[-1] == "sent"


def test_indexnow_rejects_other_2xx_statuses(monkeypatch):
    logs = []
    monkeypatch.setattr(indexing_service, "INDEXNOW_ENABLED", True)
    monkeypatch.setattr(indexing_service, "INDEXNOW_KEY", "abc123")
    monkeypatch.setattr(indexing_service, "_already_sent", lambda url, engine: False)
    monkeypatch.setattr(
        indexing_service,
        "save_log",
        lambda url, date_published, status, engine, response, date_sent=None: logs.append(status),
    )
    monkeypatch.setattr(indexing_service.requests, "post", lambda *args, **kwargs: FakeIndexNowResponse(204))

    ok, _ = indexing_service.send_to_indexnow(
        {"url": "https://www.maquinanerd.com.br/post/", "date_published": "2026-05-28T13:00:00+00:00"}
    )

    assert ok is False
    assert logs[-1] == "error"


def test_indexnow_disabled_returns_false_without_network(monkeypatch):
    logs = []
    monkeypatch.setattr(indexing_service, "INDEXNOW_ENABLED", False)
    monkeypatch.setattr(indexing_service, "_already_sent", lambda url, engine: False)
    monkeypatch.setattr(
        indexing_service.requests,
        "post",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("IndexNow disabled should not call network")),
    )
    monkeypatch.setattr(
        indexing_service,
        "save_log",
        lambda url, date_published, status, engine, response, date_sent=None: logs.append(status),
    )

    ok, message = indexing_service.send_to_indexnow(
        {"url": "https://www.maquinanerd.com.br/post/", "date_published": "2026-05-28T13:00:00+00:00"}
    )

    assert ok is False
    assert "desabilitado" in message.lower()
    assert logs[-1] == "disabled"
