from unittest.mock import Mock, patch

from app.wordpress import WordPressClient


def _client():
    return WordPressClient(
        config={"url": "https://example.com/wp-json/wp/v2", "user": "u", "password": "p"},
        categories_map={},
    )


@patch("requests.get")
def test_upload_media_infers_extension_from_content_type(mock_get):
    client = _client()
    img_response = Mock()
    img_response.content = b"image-bytes"
    img_response.headers = {"Content-Type": "image/webp; charset=binary"}
    img_response.raise_for_status = Mock()
    mock_get.return_value = img_response

    wp_response = Mock()
    wp_response.json.return_value = {"id": 123}
    wp_response.raise_for_status = Mock()
    wp_response.elapsed.total_seconds.return_value = 0.1
    client.session.post = Mock(return_value=wp_response)

    result = client.upload_media_from_url("https://cdn.example.com/assets/image", "Alt")

    assert result == {"id": 123}
    headers = client.session.post.call_args.kwargs["headers"]
    assert headers["Content-Disposition"] == 'attachment; filename="image.webp"'
    assert headers["Content-Type"] == "image/webp"


@patch("requests.get")
def test_upload_media_keeps_existing_extension(mock_get):
    client = _client()
    img_response = Mock()
    img_response.content = b"image-bytes"
    img_response.headers = {"Content-Type": "image/png"}
    img_response.raise_for_status = Mock()
    mock_get.return_value = img_response

    wp_response = Mock()
    wp_response.json.return_value = {"id": 456}
    wp_response.raise_for_status = Mock()
    wp_response.elapsed.total_seconds.return_value = 0.1
    client.session.post = Mock(return_value=wp_response)

    client.upload_media_from_url("https://cdn.example.com/assets/photo.jpg", "Alt")

    headers = client.session.post.call_args.kwargs["headers"]
    assert headers["Content-Disposition"] == 'attachment; filename="photo.jpg"'
