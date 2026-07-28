from bs4 import BeautifulSoup

from app.cluster_extractor import collect_cluster_images
from app.extractor import (
    ContentExtractor,
    collect_images_from_article,
    youtube_thumbnail_video_id,
)

VIDEO_ID = "Ser0jvljAo8"


def _player_html():
    return f"""
    <article>
      <figure><img src="https://cdn.example.com/editorial.jpg" alt="Editorial"></figure>
      <div class="youtube-player">
        <picture>
          <source srcset="https://img.youtube.com/vi/{VIDEO_ID}/maxresdefault.jpg">
          <img src="https://img.youtube.com/vi/{VIDEO_ID}/hqdefault.jpg" alt="Play">
        </picture>
        <iframe src="https://www.youtube.com/embed/{VIDEO_ID}"></iframe>
      </div>
    </article>
    """


def test_youtube_thumbnail_identity_ignores_quality_variant():
    assert youtube_thumbnail_video_id(
        f"https://img.youtube.com/vi/{VIDEO_ID}/hqdefault.jpg"
    ) == VIDEO_ID
    assert youtube_thumbnail_video_id(
        f"https://i.ytimg.com/vi/{VIDEO_ID}/maxresdefault.jpg"
    ) == VIDEO_ID


def test_collect_images_excludes_youtube_player_thumbnails():
    images = collect_images_from_article(
        BeautifulSoup(_player_html(), "html.parser"),
        "https://comicbook.com/story",
    )

    assert images == ["https://cdn.example.com/editorial.jpg"]


def test_youtube_iframe_remains_available_as_video():
    videos = ContentExtractor()._extract_youtube_videos(
        BeautifulSoup(_player_html(), "html.parser")
    )

    assert [video["id"] for video in videos] == [VIDEO_ID]


def test_cluster_image_defense_filters_thumbnail_strings_and_dicts():
    docs = [
        {
            "images": [
                f'<figure><img src="https://img.youtube.com/vi/{VIDEO_ID}/hqdefault.jpg"></figure>',
                {"url": f"https://i.ytimg.com/vi/{VIDEO_ID}/maxresdefault.jpg"},
                {"url": "https://cdn.example.com/editorial.jpg"},
            ],
            "featured_image_url": "https://cdn.example.com/featured.jpg",
        }
    ]

    images = collect_cluster_images(docs)

    assert images == [
        {"url": "https://cdn.example.com/editorial.jpg"},
        {"url": "https://cdn.example.com/featured.jpg", "alt": "", "caption": ""},
    ]


def test_non_youtube_image_is_not_filtered():
    assert not youtube_thumbnail_video_id("https://cdn.example.com/maxresdefault.jpg")
