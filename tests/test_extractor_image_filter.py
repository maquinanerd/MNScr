from bs4 import BeautifulSoup

from app.extractor import collect_images_from_article, is_valid_article_image


def test_small_width_thumbnail_urls_are_rejected():
    urls = [
        "https://static1.cbrimages.com/related/yona.jpg?w=300",
        "https://static1.cbrimages.com/related/yona.jpg?w=400",
        "https://static1.cbrimages.com/related/yona.jpg?quality=80&w=300",
        "https://static1.cbrimages.com/related/yona.jpg?quality=80&w=400",
    ]

    for url in urls:
        assert is_valid_article_image(url) is False


def test_larger_width_image_url_remains_valid():
    url = "https://static1.cbrimages.com/article/whalefall.jpg?w=800"

    assert is_valid_article_image(url) is True


def test_embedded_absolute_valnet_image_url_is_repaired():
    malformed = (
        "https://static0.moviewebimages.com/wordpress"
        "https://static0.srcdn.com/wordpress/wp-content/uploads/2025/08/house-of-the-dragon.jpg"
    )
    soup = BeautifulSoup(
        f"<article><figure><img src='{malformed}'></figure></article>",
        "html.parser",
    )

    images = collect_images_from_article(soup, "https://movieweb.com/story/")

    assert images == [
        "https://static0.srcdn.com/wordpress/wp-content/uploads/2025/08/house-of-the-dragon.jpg"
    ]


def test_normal_absolute_valnet_image_url_is_unchanged():
    expected = (
        "https://static0.moviewebimages.com/wordpress/"
        "wp-content/uploads/2025/08/house-of-the-dragon.jpg"
    )
    soup = BeautifulSoup(
        f"<article><figure><img src='{expected}'></figure></article>",
        "html.parser",
    )

    images = collect_images_from_article(soup, "https://movieweb.com/story/")

    assert images == [expected]
