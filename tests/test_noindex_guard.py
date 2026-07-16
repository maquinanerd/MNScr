import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.wordpress import WordPressClient
from scripts.run_superfeed_audit import _apply_noindex_guard


class FakeGuardClient:
    def __init__(self, observed="noindex", applied=True):
        self.observed = observed
        self.applied = applied
        self.apply_calls = []
        self.verify_calls = []

    def apply_noindex(self, post_id):
        self.apply_calls.append(post_id)
        return self.applied

    def verify_noindex(self, post_id):
        self.verify_calls.append(post_id)
        return self.observed == "noindex", self.observed


class FakeResponse:
    ok = True
    status_code = 200
    text = "{}"

    class elapsed:
        @staticmethod
        def total_seconds():
            return 0.01

    def raise_for_status(self):
        return None

    def json(self):
        return {}


class FakeSession:
    def __init__(self):
        self.posts = []

    def post(self, endpoint, json, timeout):
        self.posts.append({"endpoint": endpoint, "json": json, "timeout": timeout})
        return FakeResponse()


def test_publish_disable_indexing_is_blocked():
    client = FakeGuardClient(observed="noindex")

    ok, observed = _apply_noindex_guard(client, 101807, required=True)

    assert ok is False
    assert observed == "blocked_by_force_index_policy"
    assert client.apply_calls == []
    assert client.verify_calls == []


def test_verified_index_blocks_execution():
    client = FakeGuardClient(observed="index")

    ok, observed = _apply_noindex_guard(client, 123, required=True)

    assert ok is False
    assert observed == "blocked_by_force_index_policy"
    assert client.apply_calls == []
    assert client.verify_calls == []


def test_disable_indexing_false_does_not_apply_noindex():
    client = FakeGuardClient(observed="index")

    ok, observed = _apply_noindex_guard(client, 123, required=False)

    assert ok is True
    assert observed == "not_required"
    assert client.apply_calls == []
    assert client.verify_calls == []


def test_apply_noindex_is_blocked_without_rest_write():
    client = WordPressClient.__new__(WordPressClient)
    client.api_url = "https://example.test/wp-json/wp/v2"
    client.session = FakeSession()

    ok = client.apply_noindex(101807)

    assert ok is False
    assert client.session.posts == []


def test_update_yoast_seo_forces_index_even_when_noindex_requested():
    client = WordPressClient.__new__(WordPressClient)
    client.api_url = "https://example.test/wp-json/wp/v2"
    client.session = FakeSession()

    ok_noindex = client.update_post_yoast_seo(123, 0, {"noindex": "1"})
    ok_index = client.update_post_yoast_seo(124, 0, {"noindex": "0"})

    assert ok_noindex is True
    assert ok_index is True
    noindex_meta = client.session.posts[0]["json"]["meta"]
    index_meta = client.session.posts[1]["json"]["meta"]
    assert noindex_meta["_yoast_wpseo_meta-robots-noindex"] == "0"
    assert noindex_meta["_yoast_wpseo_meta-robots-nofollow"] == "0"
    assert index_meta["_yoast_wpseo_meta-robots-noindex"] == "0"
    assert index_meta["_yoast_wpseo_meta-robots-nofollow"] == "0"


if __name__ == "__main__":
    test_publish_disable_indexing_is_blocked()
    test_verified_index_blocks_execution()
    test_disable_indexing_false_does_not_apply_noindex()
    test_apply_noindex_is_blocked_without_rest_write()
    test_update_yoast_seo_forces_index_even_when_noindex_requested()
    print("noindex guard direct checks: OK")
