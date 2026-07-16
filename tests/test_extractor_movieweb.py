from app.extractor import ContentExtractor
from app.multi_source_builder import detect_extraction_status


VALID_BODY = " ".join(
    [
        "MovieWeb reports that the Marvel story includes enough article body text for extraction validation.",
        "The article explains the context around Disney layoffs and the reaction from a former MCU star.",
        "This paragraph is repeated to ensure the fixture stays above the minimum extraction threshold.",
    ]
    * 8
)


MOVIEWEB_HTML_VALID = f"""
<html>
  <head>
    <meta property="og:title" content="MovieWeb OG Title">
    <meta property="og:image" content="https://static1.moviewebimages.com/image.jpg">
  </head>
  <body>
    <div class="article-content">
      <p>This wrapper should not be preferred over the Valnet article body.</p>
    </div>
    <div class="w-login"><p>Create an account to continue with MovieWeb.</p></div>
    <div class="valnet-login"><p>Log In with your account.</p></div>
    <div class="popup"><p>Subscribe to our Newsletter.</p></div>
    <article>
      <h1>Former MCU Star Slams Disney Layoffs</h1>
      <div class="article-body">
        <p>ADVERTISEMENT</p>
        <p>{VALID_BODY}</p>
        <script>console.log("noise")</script>
        <div class="newsletter"><p>Subscribe to our Newsletter</p></div>
        <div class="vs-video-modal"><p>Video popup</p></div>
        <p>Sign Up for updates</p>
        <p>Keep Reading</p>
        <p>{VALID_BODY}</p>
      </div>
    </article>
  </body>
</html>
"""


MOVIEWEB_HTML_OG_TITLE_ONLY = f"""
<html>
  <head>
    <meta property="og:title" content="MovieWeb OG Fallback Title">
    <meta property="og:image" content="https://static1.moviewebimages.com/og.jpg">
  </head>
  <body>
    <article>
      <div class="article-body">
        <p>{VALID_BODY}</p>
        <p>{VALID_BODY}</p>
      </div>
    </article>
  </body>
</html>
"""


MOVIEWEB_HTML_SHORT = """
<html>
  <body>
    <article>
      <h1>Short MovieWeb Article</h1>
      <p>Short body only.</p>
    </article>
  </body>
</html>
"""


MOVIEWEB_HTML_WITH_DISPLAY_CARD_JUNK = f"""
<html>
  <head>
    <meta property="og:title" content="MovieWeb Display Card Test">
    <meta property="og:image" content="https://static0.moviewebimages.com/wordpress/wp-content/uploads/2025/09/superman-david-corenswet-1.jpg?w=1600&h=900&fit=crop">
  </head>
  <body>
    <article>
      <h1>Superman Suit Footage Revealed</h1>
      <section id="article-body" class="article-body" itemprop="articleBody">
        <p>{VALID_BODY}</p>
        <div class="display-card type-screen large">
          <img src="https://static0.moviewebimages.com/wordpress/wp-content/uploads/sharedimages/2025/01/instar51972662.jpg?q=49&fit=crop&w=50&h=65&dpr=2" alt="David Corenswet headshot">
          <h3>Superman</h3>
          <dl>
            <dt><strong>Release Date</strong></dt><dd>July 11, 2025</dd>
            <dt><strong>Runtime</strong></dt><dd>130 minutes</dd>
            <dt><strong>Director</strong></dt><dd>James Gunn</dd>
            <dt><strong>Writers</strong></dt><dd>James Gunn, Joe Shuster, Jerry Siegel</dd>
            <dt><strong>Producers</strong></dt><dd>Peter Safran</dd>
          </dl>
          <div class="w-display-card-cast">
            <h3>Cast</h3>
            <img src="https://static0.moviewebimages.com/wordpress/wp-content/uploads/sharedimages/2025/01/instar53365758.jpg?q=49&fit=crop&w=50&h=65&dpr=2" alt="Rachel Brosnahan">
          </div>
        </div>
        <div class="ww-sensa-quiz-widget"><p>Report Error Start Quiz Lars Eidinger Nicholas Hoult</p></div>
        <p>{VALID_BODY}</p>
      </section>
    </article>
  </body>
</html>
"""


def test_movieweb_extracts_h1_title():
    result = ContentExtractor().extract(MOVIEWEB_HTML_VALID, url="https://movieweb.com/test")

    assert result is not None
    assert result["title"] == "Former MCU Star Slams Disney Layoffs"


def test_movieweb_extracts_og_title_when_h1_is_missing():
    result = ContentExtractor().extract(MOVIEWEB_HTML_OG_TITLE_ONLY, url="https://movieweb.com/test")

    assert result is not None
    assert result["title"] == "MovieWeb OG Fallback Title"


def test_movieweb_extracts_body_from_article_paragraphs():
    result = ContentExtractor().extract(MOVIEWEB_HTML_VALID, url="https://movieweb.com/test")

    assert result is not None
    assert "Marvel story includes enough article body text" in result["content"]
    assert "This wrapper should not be preferred" not in result["content"]


def test_movieweb_uses_article_body_as_primary_selector():
    result = ContentExtractor().extract(MOVIEWEB_HTML_VALID, url="https://movieweb.com/test")

    assert "This wrapper should not be preferred" not in result["content"]
    assert "Marvel story includes enough article body text" in result["content"]


def test_movieweb_removes_valnet_login_popup_newsletter_scripts_and_ctas():
    result = ContentExtractor().extract(MOVIEWEB_HTML_VALID, url="https://movieweb.com/test")
    content = result["content"].lower()

    assert "advertisement" not in content
    assert "newsletter" not in content
    assert "subscribe" not in content
    assert "sign up" not in content
    assert "create an account" not in content
    assert "log in" not in content
    assert "keep reading" not in content
    assert "console.log" not in content
    assert "video popup" not in content


def test_movieweb_extracts_og_image():
    result = ContentExtractor().extract(MOVIEWEB_HTML_VALID, url="https://movieweb.com/test")

    assert result["featured_image_url"] == "https://static1.moviewebimages.com/image.jpg"


def test_movieweb_valid_fixture_has_enough_content():
    result = ContentExtractor().extract(MOVIEWEB_HTML_VALID, url="https://movieweb.com/test")

    assert len(result["content"]) >= 800


def test_movieweb_valid_fixture_is_article_body_ok():
    result = ContentExtractor().extract(MOVIEWEB_HTML_VALID, url="https://movieweb.com/test")

    assert detect_extraction_status(result["content"], "https://movieweb.com/test") == "ARTICLE_BODY_OK"


def test_movieweb_short_html_stays_too_short():
    result = ContentExtractor().extract(MOVIEWEB_HTML_SHORT, url="https://movieweb.com/short")

    assert detect_extraction_status(result["content"], "https://movieweb.com/short") == "TOO_SHORT"


def test_movieweb_removes_display_cards_cast_quiz_and_tiny_card_images():
    result = ContentExtractor().extract(
        MOVIEWEB_HTML_WITH_DISPLAY_CARD_JUNK,
        url="https://movieweb.com/superman-new-suit-man-tomorrow-footage/",
    )

    assert result is not None
    content = result["content"].lower()
    images = " ".join(result["images"]).lower()

    assert "movieweb reports that the marvel story includes enough article body text" in content
    assert "display-card" not in content
    assert "release date" not in content
    assert "runtime" not in content
    assert "director" not in content
    assert "writers" not in content
    assert "producers" not in content
    assert "cast" not in content
    assert "report error" not in content
    assert "start quiz" not in content
    assert "instar51972662" not in content
    assert "instar51972662" not in images
    assert "w=50" not in images


def test_other_domain_specific_routes_remain_present():
    source = open("app/extractor.py", encoding="utf-8").read()

    assert "'screenrant.com' in domain" in source
    assert "'collider.com' in domain" in source
    assert "'hollywoodreporter.com' in domain" in source
    assert "'variety.com' in domain" in source
    assert "'movieweb.com' in domain" in source


if __name__ == "__main__":
    tests = [
        test_movieweb_extracts_h1_title,
        test_movieweb_extracts_og_title_when_h1_is_missing,
        test_movieweb_extracts_body_from_article_paragraphs,
        test_movieweb_uses_article_body_as_primary_selector,
        test_movieweb_removes_valnet_login_popup_newsletter_scripts_and_ctas,
        test_movieweb_extracts_og_image,
        test_movieweb_valid_fixture_has_enough_content,
        test_movieweb_valid_fixture_is_article_body_ok,
        test_movieweb_short_html_stays_too_short,
        test_movieweb_removes_display_cards_cast_quiz_and_tiny_card_images,
        test_other_domain_specific_routes_remain_present,
    ]
    all_ok = True
    for test in tests:
        try:
            test()
            print(f"  [OK] {test.__name__}")
        except Exception as exc:
            print(f"  [FAIL] {test.__name__}: {exc}")
            all_ok = False
    print("ALL CHECKS PASSED" if all_ok else "SOME CHECKS FAILED")
    raise SystemExit(0 if all_ok else 1)
