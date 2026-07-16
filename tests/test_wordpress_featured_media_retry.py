import requests
from unittest.mock import Mock, patch

from app.wordpress import WordPressClient


def _response(payload, ok=True, status_code=200):
    response = Mock()
    response.ok = ok
    response.status_code = status_code
    response.json.return_value = payload
    response.text = ""
    return response


def test_set_post_featured_media_attaches_and_retries_when_wordpress_returns_zero():
    client = WordPressClient(
        config={"url": "https://example.com/wp-json/wp/v2", "user": "u", "password": "p"},
        categories_map={},
    )
    client.session.get = Mock(
        return_value=_response(
            {
                "id": 456,
                "media_type": "image",
                "mime_type": "image/jpeg",
                "source_url": "https://example.com/uploads/image.jpg",
            }
        )
    )
    client.session.post = Mock(
        side_effect=[
            _response({"id": 123, "featured_media": 0}),
            _response({"id": 456, "post": 123}),
            _response({"id": 123, "featured_media": 456}),
        ]
    )

    with patch("app.wordpress.time.sleep"):
        assert client.set_post_featured_media(post_id=123, media_id=456, max_attempts=3) is True

    post_calls = client.session.post.call_args_list
    assert post_calls[0].args[0] == "https://example.com/wp-json/wp/v2/posts/123"
    assert post_calls[0].kwargs["json"] == {"featured_media": 456}
    assert post_calls[1].args[0] == "https://example.com/wp-json/wp/v2/media/456"
    assert post_calls[1].kwargs["json"] == {"post": 123}
    assert post_calls[2].kwargs["json"] == {"featured_media": 456}


def test_set_post_featured_media_retries_attach_after_connection_reset():
    client = WordPressClient(
        config={"url": "https://example.com/wp-json/wp/v2", "user": "u", "password": "p"},
        categories_map={},
    )
    client.session.get = Mock(
        return_value=_response(
            {
                "id": 456,
                "media_type": "file",
                "mime_type": "image/jpeg",
                "source_url": "https://example.com/uploads/image.jpg",
            }
        )
    )
    client.session.post = Mock(
        side_effect=[
            _response({"id": 123, "featured_media": 0}),
            requests.ConnectionError("connection reset"),
            _response({"id": 123, "featured_media": 0}),
            _response({"id": 456, "post": 123}),
            _response({"id": 123, "featured_media": 456}),
        ]
    )

    with patch("app.wordpress.time.sleep"):
        assert client.set_post_featured_media(post_id=123, media_id=456, max_attempts=4) is True

    post_calls = client.session.post.call_args_list
    media_attach_calls = [
        call for call in post_calls
        if call.args[0] == "https://example.com/wp-json/wp/v2/media/456"
    ]
    assert len(media_attach_calls) == 2
    assert post_calls[-1].kwargs["json"] == {"featured_media": 456}


def test_wait_for_media_ready_accepts_image_mime_even_when_media_type_is_file():
    client = WordPressClient(
        config={"url": "https://example.com/wp-json/wp/v2", "user": "u", "password": "p"},
        categories_map={},
    )
    client.session.get = Mock(
        return_value=_response(
            {
                "id": 456,
                "media_type": "file",
                "mime_type": "image/jpeg",
                "source_url": "https://example.com/uploads/image.jpg",
            }
        )
    )

    with patch("app.wordpress.time.sleep"):
        assert client._wait_for_media_ready(456, max_attempts=1) is True


def test_wait_media_available_uses_strict_media_ready_check():
    client = WordPressClient(
        config={"url": "https://example.com/wp-json/wp/v2", "user": "u", "password": "p"},
        categories_map={},
    )
    client.session.get = Mock(
        return_value=_response(
            {
                "id": 456,
                "media_type": "attachment",
                "mime_type": "application/octet-stream",
                "source_url": "",
            }
        )
    )

    with patch("app.wordpress.time.sleep"):
        assert client.wait_media_available(456, max_attempts=1) is False
