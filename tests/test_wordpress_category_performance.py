"""
Tests for WordPress category resolution performance.
Verifies that _get_all_category_ids() is NOT called in the normal publish path.
All HTTP calls are mocked.
"""
import sys
import os
import unittest
from unittest.mock import MagicMock, patch, call

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.wordpress import WordPressClient


def _make_client():
    """Helper: create a WordPressClient with fake credentials."""
    config = {
        "url": "https://example.com/wp-json/wp/v2",
        "user": "user",
        "password": "pass",
    }
    categories_map = {"Notícias": 20, "Filmes": 24, "Séries": 21}
    client = WordPressClient(config=config, categories_map=categories_map)
    return client


def _mock_response(status_code, json_data):
    """Helper: build a mock requests.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.ok = status_code < 400
    resp.json.return_value = json_data
    resp.elapsed = MagicMock()
    resp.elapsed.total_seconds.return_value = 0.1
    resp.raise_for_status = MagicMock()
    return resp


class TestCategoryExistsNoGlobalPagination(unittest.TestCase):

    def test_returns_true_from_id_cache(self):
        """_category_exists returns True immediately when ID is in _category_ids_cache."""
        client = _make_client()
        client._category_ids_cache = {100, 200, 300}
        # Session.get should never be called
        client.session.get = MagicMock()

        result = client._category_exists(200)

        self.assertTrue(result)
        client.session.get.assert_not_called()

    def test_does_not_call_get_all_category_ids_when_id_cached(self):
        """_category_exists must not call _get_all_category_ids when ID is in cache."""
        client = _make_client()
        client._category_ids_cache = {42}
        with patch.object(client, "_get_all_category_ids") as mock_all:
            result = client._category_exists(42)
        mock_all.assert_not_called()
        self.assertTrue(result)

    def test_uses_direct_get_when_id_not_cached(self):
        """_category_exists uses GET /categories/{id} when not in cache."""
        client = _make_client()
        client._category_ids_cache = set()  # empty — ID not present
        client.session.get = MagicMock(
            return_value=_mock_response(200, {"id": 99, "name": "Action", "slug": "action"})
        )

        result = client._category_exists(99)

        self.assertTrue(result)
        client.session.get.assert_called_once()
        call_url = client.session.get.call_args[0][0]
        self.assertIn("/categories/99", call_url)

    def test_direct_get_404_returns_false(self):
        """_category_exists returns False for 404 without raising."""
        client = _make_client()
        client._category_ids_cache = set()
        client.session.get = MagicMock(return_value=_mock_response(404, {"code": "term_not_found"}))

        result = client._category_exists(999)

        self.assertFalse(result)

    def test_direct_get_populates_cache(self):
        """A 200 response from direct GET adds the ID to the in-memory cache."""
        client = _make_client()
        client._category_ids_cache = set()
        client.session.get = MagicMock(
            return_value=_mock_response(200, {"id": 77, "name": "Anime", "slug": "anime"})
        )

        client._category_exists(77)

        self.assertIn(77, client._category_ids_cache)


class TestResolveCategoryNamesNoGlobalPagination(unittest.TestCase):

    def test_uses_name_cache_hit_no_http(self):
        """resolve_category_names_to_ids returns cached ID without any HTTP call."""
        client = _make_client()
        client._category_name_cache["naruto"] = 17905
        client._category_ids_cache = {17905}
        client.session.get = MagicMock()
        client.session.post = MagicMock()

        result = client.resolve_category_names_to_ids(["Naruto"])

        self.assertEqual(result, [17905])
        # No HTTP calls should be made
        client.session.get.assert_not_called()
        client.session.post.assert_not_called()

    def test_uses_local_map_no_http_for_known_categories(self):
        """resolve_category_names_to_ids uses local categories_map without HTTP when cached."""
        client = _make_client()
        # Pre-populate the id cache so _category_exists doesn't need HTTP
        client._category_ids_cache = {21}
        client.session.get = MagicMock()
        client.session.post = MagicMock()

        result = client.resolve_category_names_to_ids(["Séries"])

        self.assertEqual(result, [21])
        client.session.get.assert_not_called()

    def test_search_endpoint_used_not_list_all(self):
        """resolve_category_names_to_ids calls search?search=name, not /categories with pagination."""
        client = _make_client()
        client._category_ids_cache = set()

        # Mock: search returns the category; direct GET validates it
        search_resp = _mock_response(200, [{"id": 555, "name": "Running Point", "slug": "running-point"}])
        direct_get_resp = _mock_response(200, {"id": 555, "name": "Running Point", "slug": "running-point"})

        call_count = [0]

        def fake_get(url, **kwargs):
            call_count[0] += 1
            if "/categories/555" in url:
                return direct_get_resp
            # search call
            return search_resp

        client.session.get = MagicMock(side_effect=fake_get)

        result = client.resolve_category_names_to_ids(["Running Point"])

        self.assertEqual(result, [555])

        # Verify NO call was made with a bare `page` key (paginated listing of all categories)
        for c in client.session.get.call_args_list:
            kwargs_params = c[1].get("params", {})
            args_params = c[0][1] if len(c[0]) > 1 and isinstance(c[0][1], dict) else {}
            params = kwargs_params or args_params
            self.assertNotIn("page", params, "Paginated listing of all categories must not occur")

    def test_found_category_saved_in_cache(self):
        """A category found via search is saved in _category_name_cache."""
        client = _make_client()
        client._category_ids_cache = set()

        search_resp = _mock_response(200, [{"id": 888, "name": "Comedy", "slug": "comedy"}])
        direct_get_resp = _mock_response(200, {"id": 888, "name": "Comedy", "slug": "comedy"})

        def fake_get(url, **kwargs):
            if "/categories/888" in url:
                return direct_get_resp
            return search_resp

        client.session.get = MagicMock(side_effect=fake_get)

        client.resolve_category_names_to_ids(["Comedy"])

        # Name cache should now hold this entry
        self.assertIn("comedy", client._category_name_cache)
        self.assertEqual(client._category_name_cache["comedy"], 888)

    def test_created_category_saved_in_cache(self):
        """A newly created category is saved in cache via _remember_category."""
        client = _make_client()
        client._category_ids_cache = set()

        # Search returns empty → triggers create
        search_resp = _mock_response(200, [])
        create_resp = _mock_response(201, {"id": 999, "name": "NewCat", "slug": "newcat"})
        direct_get_resp = _mock_response(200, {"id": 999, "name": "NewCat", "slug": "newcat"})

        def fake_get(url, **kwargs):
            if "/categories/999" in url:
                return direct_get_resp
            return search_resp

        client.session.get = MagicMock(side_effect=fake_get)
        client.session.post = MagicMock(return_value=create_resp)

        client.resolve_category_names_to_ids(["NewCat"])

        self.assertIn(999, client._category_ids_cache)

    def test_get_all_category_ids_not_called_in_normal_flow(self):
        """_get_all_category_ids must NOT be called during normal category resolution."""
        client = _make_client()
        client._category_ids_cache = set()

        search_resp = _mock_response(200, [{"id": 123, "name": "Thriller", "slug": "thriller"}])
        direct_get_resp = _mock_response(200, {"id": 123, "name": "Thriller", "slug": "thriller"})

        def fake_get(url, **kwargs):
            if "/categories/123" in url:
                return direct_get_resp
            return search_resp

        client.session.get = MagicMock(side_effect=fake_get)

        with patch.object(client, "_get_all_category_ids") as mock_all:
            client.resolve_category_names_to_ids(["Thriller"])

        mock_all.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
