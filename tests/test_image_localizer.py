from app.html_utils import final_pre_publish_cleanup, html_to_gutenberg_blocks, validate_and_fix_figures
import app.image_localizer as image_localizer
from app.image_localizer import _build_caption, finalize_content_images, localize_and_place_body_images
from app.pipeline import _filter_body_images_against_featured


class _FakeResponse:
    def raise_for_status(self):
        return None


class _FakeSession:
    def __init__(self):
        self.posts = []

    def post(self, url, json=None, timeout=None):
        self.posts.append({"url": url, "json": json, "timeout": timeout})
        return _FakeResponse()


class _FakeWordPressClient:
    api_url = "https://example.test/wp-json/wp/v2"

    def __init__(self):
        self.session = _FakeSession()
        self.uploaded = []
        self.alt_updates = []

    def upload_media_from_url(self, image_url, alt_text="", max_attempts=3):
        media_id = 100 + len(self.uploaded)
        self.uploaded.append({"url": image_url, "alt": alt_text})
        return {
            "id": media_id,
            "source_url": f"https://www.maquinanerd.com.br/wp-content/uploads/{media_id}.jpg",
            "media_details": {
                "width": 1600,
                "height": 1067,
                "sizes": {
                    "medium": {
                        "source_url": f"https://www.maquinanerd.com.br/wp-content/uploads/{media_id}-300x200.jpg",
                        "width": 300,
                        "height": 200,
                    },
                    "large": {
                        "source_url": f"https://www.maquinanerd.com.br/wp-content/uploads/{media_id}-1024x683.jpg",
                        "width": 1024,
                        "height": 683,
                    },
                },
            },
        }

    def set_media_alt_text(self, media_id, alt_text):
        self.alt_updates.append({"id": media_id, "alt": alt_text})
        return True


def test_localize_and_place_body_images_uploads_and_places_after_headings():
    wp = _FakeWordPressClient()
    content = """
    <p>Lead sem imagem.</p>
    <h2>Krypto entra na jornada de Kara</h2>
    <p>Texto da seção.</p>
    <h2>Lobo amplia o caos no DC Universe</h2>
    <p>Outro texto.</p>
    """
    images = [
        {
            "url": "https://static0.srcdn.com/krypto.jpg",
            "alt": "sgrl trl 101",
            "caption": "Supergirl and Krypto in the DCU",
        },
        {
            "url": "https://static0.srcdn.com/lobo.jpg",
            "alt": "Jason Momoa as Lobo in DCU Supergirl",
            "caption": "Jason Momoa as Lobo Riding Spacehog in DCU Supergirl",
        },
    ]

    html, snapshot = localize_and_place_body_images(
        content_html=content,
        images=images,
        image_captions={
            "krypto.jpg": "Supergirl and Krypto in the DCU",
            "lobo.jpg": "Jason Momoa as Lobo Riding Spacehog in DCU Supergirl",
        },
        wp_client=wp,
        article_title="Supergirl revela bastidores",
        source_name="ScreenRant",
    )

    assert "static0.srcdn.com" not in html
    assert "https://www.maquinanerd.com.br/wp-content/uploads/100-1024x683.jpg" in html
    assert 'width="1024"' in html
    assert 'height="683"' in html
    assert "Crédito:" not in html
    assert 'data-wp-media-id="100"' in html
    assert "wp-image-100" in html
    assert len(snapshot) == 2
    assert snapshot[0]["media_id"] == 100
    assert snapshot[0]["url_local"].startswith("https://www.maquinanerd.com.br/")
    assert wp.alt_updates[0]["id"] == 100
    assert len(wp.alt_updates[0]["alt"].split()) >= 5

    first_heading = html.index("<h2>Krypto entra")
    first_figure = html.index("<figure", first_heading)
    first_paragraph = html.index("<p>Texto da seção", first_heading)
    assert first_heading < first_figure < first_paragraph


def test_html_to_gutenberg_blocks_preserves_local_media_id():
    html = """
    <h2>Krypto entra na jornada de Kara</h2>
    <figure class="wp-block-image size-large" data-wp-media-id="123">
      <img src="https://www.maquinanerd.com.br/wp-content/uploads/123.jpg"
           alt="Kara Zor-El e Krypto em Supergirl"
           class="wp-image-123"/>
      <figcaption>Kara e Krypto. <em>Crédito: DC Studios.</em></figcaption>
    </figure>
    """

    blocks = html_to_gutenberg_blocks(html)

    assert '<!-- wp:image {"id": 123, "sizeSlug": "large"' in blocks
    assert 'class="wp-image-123"' in blocks
    assert "https://www.maquinanerd.com.br/wp-content/uploads/123.jpg" in blocks
    assert "Crédito: DC Studios." in blocks


def test_portuguese_caption_with_as_is_not_corrupted():
    wp = _FakeWordPressClient()
    content = """
    <h2>Kara observa o desfecho</h2>
    <p>Texto da seção.</p>
    """
    images = [
        {
            "url": "https://static0.srcdn.com/kara.jpg",
            "alt": "Kara Zor-El observa as cenas finais",
            "caption": "Kara e Krypto observam as cenas finais",
        }
    ]

    html, snapshot = localize_and_place_body_images(
        content_html=content,
        images=images,
        image_captions={"kara.jpg": "Kara e Krypto observam as cenas finais"},
        wp_client=wp,
        article_title="Supergirl revela bastidores",
        source_name="ScreenRant",
    )

    assert "Kara e Krypto observam as cenas finais" in html
    assert "Kara e Krypto observam como cenas finais" not in html
    assert snapshot[0]["caption"].startswith("Kara e Krypto observam as cenas finais")


def test_featured_image_is_filtered_before_body_localization():
    wp = _FakeWordPressClient()
    featured = "https://static0.srcdn.com/featured.jpg?w=1600&h=900"
    images = [
        {"url": featured, "alt": "Imagem destacada de Supergirl"},
        {"url": "https://static0.srcdn.com/body.jpg", "alt": "Kara Zor-El e Krypto em Supergirl"},
    ]

    body_images = _filter_body_images_against_featured(images, featured)
    html, snapshot = localize_and_place_body_images(
        content_html="<h2>Krypto entra na jornada</h2><p>Texto.</p>",
        images=body_images,
        wp_client=wp,
        article_title="Supergirl revela bastidores",
        source_name="ScreenRant",
    )

    assert len(wp.uploaded) == 1
    assert wp.uploaded[0]["url"] == "https://static0.srcdn.com/body.jpg"
    assert featured not in html
    assert all(item["origem"] != featured for item in snapshot)


def test_localize_and_place_body_images_discards_lazyload_placeholder_url():
    wp = _FakeWordPressClient()
    html, snapshot = localize_and_place_body_images(
        content_html="<h2>Krypto entra na jornada</h2><p>Texto.</p>",
        images=[
            {
                "url": "https://static.example.com/lazyload-fallback.gif",
                "alt": "Placeholder",
                "caption": "Placeholder",
            },
            {
                "url": "https://static0.srcdn.com/body.jpg",
                "alt": "Kara Zor-El e Krypto em Supergirl",
                "caption": "Kara e Krypto em Supergirl",
            },
        ],
        image_captions={"body.jpg": "Kara e Krypto em Supergirl"},
        wp_client=wp,
        article_title="Supergirl revela bastidores",
        source_name="ScreenRant",
    )

    assert len(wp.uploaded) == 1
    assert wp.uploaded[0]["url"] == "https://static0.srcdn.com/body.jpg"
    assert "lazyload-fallback.gif" not in html
    assert all("lazyload" not in item["origem"] for item in snapshot)


def test_localize_and_place_body_images_stops_gracefully_on_time_budget(monkeypatch):
    wp = _FakeWordPressClient()
    ticks = iter([0, 0, 151])
    monkeypatch.setattr(image_localizer.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(image_localizer, "IMAGE_TIME_BUDGET_S", 150)

    html, snapshot = localize_and_place_body_images(
        content_html="<h2>Primeira secao</h2><p>Texto.</p><h2>Segunda secao</h2><p>Texto.</p>",
        images=[
            {
                "url": "https://static0.srcdn.com/first.jpg",
                "alt": "Primeira imagem da materia",
                "caption": "Primeira imagem da materia",
            },
            {
                "url": "https://static0.srcdn.com/second.jpg",
                "alt": "Segunda imagem da materia",
                "caption": "Segunda imagem da materia",
            },
        ],
        image_captions={
            "first.jpg": "Primeira imagem da materia",
            "second.jpg": "Segunda imagem da materia",
        },
        wp_client=wp,
        article_title="Materia com imagens",
        source_name="ScreenRant",
    )

    assert len(snapshot) == 1
    assert len(wp.uploaded) == 1
    assert wp.uploaded[0]["url"] == "https://static0.srcdn.com/first.jpg"
    assert "https://www.maquinanerd.com.br/wp-content/uploads/100-1024x683.jpg" in html
    assert "second.jpg" not in html


def test_final_image_pass_localizes_late_external_images_and_preserves_dimensions_in_gutenberg():
    wp = _FakeWordPressClient()
    html, snapshot = finalize_content_images(
        content_html=(
            '<p>Texto.</p><figure><img src="https://image.tmdb.org/t/p/w500/poster.jpg" '
            'alt="Pôster do filme" data-src="https://image.tmdb.org/t/p/original/poster.jpg"></figure>'
        ),
        wp_client=wp,
        article_title="Filme ganha novo pôster",
        post_ref=321,
    )

    blocks = html_to_gutenberg_blocks(html)

    assert "image.tmdb.org" not in blocks
    assert "https://www.maquinanerd.com.br/wp-content/uploads/100-1024x683.jpg" in blocks
    assert 'width="1024"' in blocks
    assert 'height="683"' in blocks
    assert "wp-image-100" in blocks
    assert snapshot[0]["origem"] == "https://image.tmdb.org/t/p/w500/poster.jpg"


def test_house_of_the_dragon_caption_does_not_infer_hbo_credit():
    wp = _FakeWordPressClient()
    html, snapshot = localize_and_place_body_images(
        content_html="<h2>Batalha ganha escala na HBO</h2><p>Texto.</p>",
        images=[
            {
                "url": "https://static0.srcdn.com/hotd.jpg",
                "alt": "Rhaenyra em House of the Dragon",
                "caption": "Rhaenyra em House of the Dragon",
            }
        ],
        image_captions={"hotd.jpg": "Rhaenyra em House of the Dragon"},
        wp_client=wp,
        article_title="House of the Dragon terá temporada final ambiciosa na HBO",
        source_name="ScreenRant",
    )

    assert "Crédito:" not in html
    assert snapshot[0]["caption"] == "Rhaenyra em House of the Dragon."


def test_image_without_original_caption_renders_without_figcaption():
    wp = _FakeWordPressClient()
    html, snapshot = localize_and_place_body_images(
        content_html="<h2>Krypto entra na jornada</h2><p>Texto.</p>",
        images=[
            {
                "url": "https://static0.srcdn.com/no-caption.jpg",
                "alt": "sgrl trl 101",
            }
        ],
        wp_client=wp,
        article_title="Supergirl revela bastidores",
        source_name="ScreenRant",
    )

    final_html = validate_and_fix_figures(html, ensure_figcaption=False)

    assert "<figure" in final_html
    assert "<figcaption" not in final_html
    assert snapshot[0]["caption"] == ""
    assert wp.alt_updates[0]["alt"] == "Supergirl"


def test_caption_omits_internal_superfeed_source_when_no_trusted_holder():
    wp = _FakeWordPressClient()
    html, snapshot = localize_and_place_body_images(
        content_html="<h2>Nova cena ganha destaque</h2><p>Texto.</p>",
        images=[
            {
                "url": "https://static0.srcdn.com/scene.jpg",
                "alt": "Personagem observa uma cena importante",
                "caption": "Personagem observa uma cena importante",
            }
        ],
        image_captions={"scene.jpg": "Personagem observa uma cena importante"},
        wp_client=wp,
        article_title="Nova cena ganha destaque",
        source_name="RSSPRIME Superfeed",
    )

    assert "RSSPRIME" not in html
    assert "Superfeed" not in html
    assert "Crédito:" not in html
    assert "RSSPRIME" not in snapshot[0]["caption"]
    assert "Superfeed" not in snapshot[0]["caption"]


def test_build_caption_returns_empty_when_no_caption_alt_or_holder():
    assert _build_caption("", "", "") == ""


def test_final_cleanup_strips_legacy_internal_credit_suffix():
    html = """
    <figure>
      <img src="https://www.maquinanerd.com.br/wp-content/uploads/123.jpg" alt="Kara e Krypto">
      <figcaption>Kara e Krypto. <em>Crédito: DC Studios/Warner Bros. Pictures via RSSPRIME Superfeed.</em></figcaption>
    </figure>
    """

    cleaned = final_pre_publish_cleanup(html, title="Supergirl revela bastidores")

    assert "RSSPRIME" not in cleaned
    assert "Superfeed" not in cleaned
    assert "Crédito: DC Studios/Warner Bros. Pictures." in cleaned


def test_collect_image_captions_returns_empty_when_article_tag_absent():
    """Regression: HTML without <article> and a broken-selector fallback must NOT raise.

    Before the fix, _find_article_body called soup.select() with a trailing comma
    (SelectorSyntaxError), which propagated through collect_image_captions_from_article
    and killed the entire extract() call. Now it must return {} instead of raising.
    """
    from app.extractor import collect_image_captions_from_article
    from bs4 import BeautifulSoup

    # HTML with no <article> tag — forces the fallback soup.select() path
    html = """
    <html><body>
      <div class="post-content">
        <figure>
          <img src="https://cdn.example.com/uploads/image.jpg">
          <figcaption>A caption here</figcaption>
        </figure>
        <p>Some text.</p>
      </div>
    </body></html>
    """
    soup = BeautifulSoup(html, "lxml")
    # Must not raise, must return a dict
    result = collect_image_captions_from_article(soup, base_url="https://cdn.example.com/")
    assert isinstance(result, dict)
