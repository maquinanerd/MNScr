from bs4 import BeautifulSoup

from app.extractor import (
    ContentExtractor,
    _append_missing_x_embeds,
    _collect_x_embeds,
)


TWEET = """
<figure class="wp-block-embed is-provider-x">
  <div class="wp-block-embed__wrapper">
    <blockquote class="twitter-tweet">
      <p>Supergirl reaction text</p>
      <a href="https://x.com/reviewer/status/2067691380781035601">June 18, 2026</a>
    </blockquote>
    <script async src="https://platform.twitter.com/widgets.js"></script>
  </div>
</figure>
"""


def test_collect_x_embeds_keeps_static_text_without_script():
    soup = BeautifulSoup(TWEET, "html.parser")

    embeds = _collect_x_embeds(soup)

    assert len(embeds) == 1
    assert "Supergirl reaction text" in embeds[0]
    assert "platform.twitter.com" not in embeds[0]


def test_append_x_embeds_deduplicates_by_status_id():
    container = BeautifulSoup("<article></article>", "html.parser").article
    embeds = _collect_x_embeds(BeautifulSoup(TWEET + TWEET, "html.parser"))

    _append_missing_x_embeds(container, embeds)
    _append_missing_x_embeds(container, embeds)

    assert len(container.select("blockquote.twitter-tweet")) == 1


def test_comicbook_cleaner_output_restores_tweet(monkeypatch):
    extractor = ContentExtractor()
    html = f"""
    <html><head><meta property="og:title" content="Title"></head><body>
      <article>
        <div class="article-body">
          <p>{"Article body text " * 30}</p>
          {TWEET}
        </div>
      </article>
    </body></html>
    """
    monkeypatch.setattr(extractor, "_pick_featured_image", lambda soup, url: None)

    result = extractor.extract(html, url="https://comicbook.com/movies/news/story")

    soup = BeautifulSoup(result["content"], "html.parser")
    assert soup.select_one("blockquote.twitter-tweet")
    assert "Supergirl reaction text" in soup.get_text(" ", strip=True)
    assert not soup.find("script")


def test_variety_cleaner_output_restores_tweet(monkeypatch):
    extractor = ContentExtractor()
    html = f"""
    <html><head><meta property="og:title" content="Title"></head><body>
      <article><div data-testid="article-body">
        <p>{"Article body text " * 30}</p>
        {TWEET}
      </div></article>
    </body></html>
    """
    monkeypatch.setattr(extractor, "_pick_featured_image", lambda soup, url: None)

    result = extractor.extract(html, url="https://variety.com/2026/film/news/story")

    soup = BeautifulSoup(result["content"], "html.parser")
    assert len(soup.select("blockquote.twitter-tweet")) == 1
    assert "Supergirl reaction text" in soup.get_text(" ", strip=True)


def test_generic_fallback_restores_tweet(monkeypatch):
    extractor = ContentExtractor()
    html = f"""
    <html><head><meta property="og:title" content="Title"></head><body>
      <article><p>{"Article body text " * 30}</p>{TWEET}</article>
    </body></html>
    """
    monkeypatch.setattr(extractor, "_pick_featured_image", lambda soup, url: None)
    monkeypatch.setattr(
        extractor,
        "_extract_generic_content_html",
        lambda soup, url: "<article><p>Generic article body text</p></article>",
    )

    result = extractor.extract(html, url="https://example.com/story")

    soup = BeautifulSoup(result["content"], "html.parser")
    assert len(soup.select("blockquote.twitter-tweet")) == 1
    assert "Supergirl reaction text" in soup.get_text(" ", strip=True)


def test_tweets_do_not_mask_too_short_specific_extraction(monkeypatch):
    extractor = ContentExtractor()
    long_tweet = TWEET.replace(
        "Supergirl reaction text",
        " ".join(["reaction"] * 220),
    )
    html = f"""
    <html><head><meta property="og:title" content="Title"></head><body>
      <article><div class="article-body">
        <p>{"short body " * 10}</p>
        {long_tweet}
      </div></article>
    </body></html>
    """
    monkeypatch.setattr(extractor, "_pick_featured_image", lambda soup, url: None)
    monkeypatch.setattr(
        extractor,
        "_extract_generic_content_html",
        lambda soup, url: "<article><p>GENERIC FALLBACK BODY</p></article>",
    )

    result = extractor.extract(html, url="https://comicbook.com/movies/news/story")

    soup = BeautifulSoup(result["content"], "html.parser")
    assert "GENERIC FALLBACK BODY" in soup.get_text(" ", strip=True)
    assert len(soup.select("blockquote.twitter-tweet")) == 1
