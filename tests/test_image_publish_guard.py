from unittest.mock import Mock, patch

import app.wordpress as wordpress
from app.wordpress import WordPressClient, _external_img_srcs


def _response(payload, status_code=201):
    response = Mock()
    response.ok = status_code < 400
    response.status_code = status_code
    response.json.return_value = payload
    response.elapsed.total_seconds.return_value = 0.01
    return response


def test_publish_payload_has_local_featured_zero_external_images_and_dimensions(caplog):
    caplog.set_level("INFO")
    client = WordPressClient(
        {
            "url": "https://maquinanerd.com.br/wp-json/wp/v2",
            "user": "user",
            "password": "pass",
        },
        {},
    )
    content = (
        '<!-- wp:image {"id":900,"sizeSlug":"large"} -->'
        '<figure class="wp-block-image size-large">'
        '<img src="https://maquinanerd.com.br/wp-content/uploads/post-1024x683.webp" '
        'alt="Cena do filme" class="wp-image-900" width="1024" height="683"/>'
        '</figure><!-- /wp:image -->'
        '<p>' + ("Conteúdo editorial válido. " * 8) + "</p>"
    )

    with patch.object(client, "wait_media_available", return_value=True), patch.object(
        client.session,
        "post",
        return_value=_response(
            {
                "id": 456,
                "featured_media": 900,
                "content": {"rendered": content},
            }
        ),
    ) as post:
        post_id = client.create_post(
            {
                "title": "Post com imagens locais",
                "slug": "post-com-imagens-locais",
                "content": content,
                "featured_media": 900,
            }
        )

    sent = post.call_args.kwargs["json"]
    assert post_id == 456
    assert sent["featured_media"] == 900
    assert _external_img_srcs(sent["content"]) == []
    assert 'width="1024"' in sent["content"]
    assert 'height="683"' in sent["content"]
    assert "[EXTERNAL_IMAGE_GUARD]" not in caplog.text
    assert (
        "[IMAGE_PUBLISH_AUDIT] post=post-com-imagens-locais "
        "featured=local_attachment:900 img_total=1 img_external=0 "
        "img_missing_dimensions=0"
    ) in caplog.text


def test_publish_is_blocked_when_external_img_src_survives(monkeypatch, caplog):
    monkeypatch.setattr(wordpress, "BLOCK_EXTERNAL_IMAGES_ON_PUBLISH", True)
    client = WordPressClient(
        {"url": "https://maquinanerd.com.br/wp-json/wp/v2"},
        {},
    )
    content = '<p>' + ("Texto válido. " * 12) + '</p><img src="https://variety.com/image.jpg">'

    with patch.object(client.session, "post") as post:
        post_id = client.create_post(
            {
                "title": "Post bloqueado",
                "slug": "post-bloqueado",
                "content": content,
            }
        )

    assert post_id is None
    post.assert_not_called()
    assert "https://variety.com/image.jpg" in caplog.text
    assert "publication_blocked" in caplog.text
