from scripts import reindex_all_posts


def test_plan_selects_only_noindex_by_default():
    plan = reindex_all_posts.ReindexPlan()
    posts = [
        {"id": 1, "link": "https://example.com/index", "yoast_head_json": {"robots": {"index": "index"}}},
        {"id": 2, "link": "https://example.com/noindex", "yoast_head_json": {"robots": {"index": "noindex"}}},
        {"id": 3, "link": "https://example.com/unknown", "yoast_head_json": {}},
    ]

    for post in posts:
        reindex_all_posts.add_post_to_plan(plan, post, include_all=False)

    assert plan.scanned == 3
    assert plan.already_index == 1
    assert plan.no_info == 1
    assert [candidate.post_id for candidate in plan.worklist] == [2]


def test_plan_all_includes_index_noindex_and_unknown():
    plan = reindex_all_posts.ReindexPlan()
    posts = [
        {"id": 1, "link": "https://example.com/index", "yoast_head_json": {"robots": {"index": "index"}}},
        {"id": 2, "link": "https://example.com/noindex", "yoast_head_json": {"robots": {"index": "noindex"}}},
        {"id": 3, "link": "https://example.com/unknown", "yoast_head_json": {}},
    ]

    for post in posts:
        reindex_all_posts.add_post_to_plan(plan, post, include_all=True)

    assert [candidate.post_id for candidate in plan.worklist] == [1, 2, 3]


def test_plan_tracks_pages_as_wordpress_post_type():
    plan = reindex_all_posts.ReindexPlan()

    reindex_all_posts.add_post_to_plan(
        plan,
        {"id": 10, "link": "https://example.com/pagina", "yoast_head_json": {"robots": {"index": "noindex"}}},
        include_all=False,
        post_type="pages",
    )

    assert len(plan.worklist) == 1
    assert plan.worklist[0].post_type == "pages"
    assert plan.worklist[0].post_id == 10


def test_update_post_robots_writes_only_yoast_noindex_meta():
    class FakeResponse:
        status_code = 200
        headers = {}

        def raise_for_status(self):
            return None

    class FakeSession:
        def __init__(self):
            self.calls = []

        def request(self, method, url, **kwargs):
            self.calls.append((method, url, kwargs))
            return FakeResponse()

    session = FakeSession()

    reindex_all_posts.update_post_robots(session, "https://example.com/wp-json/wp/v2", 123, value="2")

    assert session.calls == [
        (
            "POST",
            "https://example.com/wp-json/wp/v2/posts/123",
            {
                "json": {"meta": {"_yoast_wpseo_meta-robots-noindex": "2"}},
                "timeout": (
                    reindex_all_posts.DEFAULT_CONNECT_TIMEOUT,
                    reindex_all_posts.DEFAULT_READ_TIMEOUT,
                ),
            },
        )
    ]


def test_update_page_robots_uses_pages_endpoint():
    class FakeResponse:
        status_code = 200
        headers = {}

        def raise_for_status(self):
            return None

    class FakeSession:
        def __init__(self):
            self.calls = []

        def request(self, method, url, **kwargs):
            self.calls.append((method, url, kwargs))
            return FakeResponse()

    session = FakeSession()

    reindex_all_posts.update_post_robots(
        session,
        "https://example.com/wp-json/wp/v2",
        456,
        post_type="pages",
        value="2",
    )

    assert session.calls[0][1] == "https://example.com/wp-json/wp/v2/pages/456"


def test_parse_post_types_defaults_to_posts_and_pages():
    assert reindex_all_posts.parse_post_types("") == ["posts", "pages"]
    assert reindex_all_posts.parse_post_types("post,page,posts") == ["posts", "pages"]
