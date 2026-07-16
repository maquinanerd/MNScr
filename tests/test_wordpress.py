"""
Unit tests for the wordpress module
"""

import unittest
from unittest.mock import Mock, patch
from app.wordpress import WordPressClient

class TestWordPressClient(unittest.TestCase):
    """Test cases for the WordPressClient class"""

    def setUp(self):
        """Set up test fixtures"""
        self.wp_config = {
            'url': 'https://example.com/wp-json/wp/v2',
            'user': 'testuser',
            'password': 'testpass'
        }
        self.wp_categories = {
            'Futebol': 8,
            'Futebol Internacional': 9,
            'Outros Esportes': 10,
        }
        self.client = WordPressClient(self.wp_config, self.wp_categories)

    def test_get_domain(self):
        """Test domain extraction from the WordPress URL."""
        self.assertEqual(self.client.get_domain(), 'example.com')

    @patch('requests.Session.get')
    def test_resolve_category_names_to_ids(self, mock_get):
        """Test resolving category names to IDs, using the local map and fetching."""
        # Mock API response for a category not in the local map
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = [{'id': 99, 'name': 'New Category'}]
        mock_get.return_value = mock_response

        # Test with a mix of existing, non-existing, and mapped categories
        category_names = ['Futebol', 'New Category', '  Futebol Internacional  ']
        resolved_ids = self.client.resolve_category_names_to_ids(category_names)

        self.assertIn(8, resolved_ids)
        self.assertIn(9, resolved_ids)
        self.assertIn(99, resolved_ids)
        self.assertEqual(len(resolved_ids), 3)

    @patch('requests.get')
    @patch('requests.Session.post')
    def test_upload_media_from_url_success(self, mock_wp_post, mock_requests_get):
        """Test successful media upload from a URL."""
        # Mock the image download
        mock_img_response = Mock()
        mock_img_response.status_code = 200
        mock_img_response.content = b'fake-image-data' * 400
        mock_img_response.headers = {'Content-Type': 'image/jpeg'}
        mock_requests_get.return_value = mock_img_response

        # Mock the WordPress media upload
        mock_wp_response = Mock()
        mock_wp_response.status_code = 201
        mock_wp_response.json.return_value = {'id': 123, 'source_url': '...'}
        mock_wp_response.elapsed.total_seconds.return_value = 0.1
        mock_wp_post.return_value = mock_wp_response

        image_url = 'https://example.com/image.jpg'
        result = self.client.upload_media_from_url(image_url, alt_text="Test Alt")

        self.assertIsNotNone(result)
        self.assertEqual(result['id'], 123)
        mock_requests_get.assert_called_once_with(image_url, timeout=25)
        mock_wp_post.assert_called_once()

    @patch('requests.get')
    @patch('requests.Session.post')
    def test_upload_media_from_url_rejects_tiny_placeholder(self, mock_wp_post, mock_requests_get):
        mock_img_response = Mock()
        mock_img_response.status_code = 200
        mock_img_response.content = b'GIF89a'
        mock_img_response.headers = {'Content-Type': 'image/gif'}
        mock_requests_get.return_value = mock_img_response

        result = self.client.upload_media_from_url(
            'https://example.com/lazyload-fallback.gif',
            alt_text="Placeholder",
        )

        self.assertIsNone(result)
        mock_wp_post.assert_not_called()

    @patch('requests.Session.post')
    def test_set_media_alt_text(self, mock_post):
        """Test setting alt text for a media item."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        success = self.client.set_media_alt_text(123, "New alt text")

        self.assertTrue(success)
        mock_post.assert_called_once_with(
            f"{self.client.api_url}/media/123",
            json={'alt_text': 'New alt text'},
            timeout=20
        )

    @patch('requests.Session.get')
    def test_find_related_posts(self, mock_get):
        """Test finding related posts via search."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {
                'title': 'Related Post 1',
                '_embedded': {'self': [{'link': 'https://example.com/post-1'}]}
            }
        ]
        mock_get.return_value = mock_response

        posts = self.client.find_related_posts("some term")

        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0]['title'], 'Related Post 1')
        self.assertEqual(posts[0]['url'], 'https://example.com/post-1')

    @patch('app.wordpress.WordPressClient._ensure_tag_ids')
    @patch('requests.Session.post')
    def test_create_post_success(self, mock_post, mock_ensure_tags):
        """Test successful post creation."""
        mock_ensure_tags.return_value = [101, 102]
        mock_response = Mock()
        mock_response.status_code = 201
        mock_response.ok = True
        mock_response.json.return_value = {'id': 789}
        mock_response.elapsed.total_seconds.return_value = 0.1
        mock_post.return_value = mock_response

        post_payload = {
            'title': 'Test Title',
            'content': '<p>' + ('Content ' * 20) + '</p>',
            'tags': ['tag1', 'tag2']
        }

        post_id = self.client.create_post(post_payload)

        self.assertEqual(post_id, 789)
        mock_ensure_tags.assert_called_once_with(['tag1', 'tag2'])
        # Check that the final payload sent to WP has the integer tag IDs
        final_payload = mock_post.call_args.kwargs['json']
        self.assertEqual(final_payload['tags'], [101, 102])

    def test_close_session(self):
        """Test that the session is closed."""
        with patch.object(self.client.session, 'close') as mock_close:
            self.client.close()
            mock_close.assert_called_once()

if __name__ == '__main__':
    unittest.main()
