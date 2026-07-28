"""
Tests for domain-specific extractors: hollywoodreporter.com and variety.com
All tests use HTML fixtures — no real HTTP requests.
Run directly: python tests/test_extractor_hollywood_variety.py
"""
import sys

sys.path.insert(0, ".")

from app.extractor import ContentExtractor
from app.multi_source_builder import detect_extraction_status

extractor = ContentExtractor()

VARIETY_HTML_VALID = """<!DOCTYPE html>
<html>
<head>
<title>Test Article | Variety</title>
<meta property="og:title" content="Jimmy Kimmel Accuses Trump of Calling for His Firing">
<meta property="og:image" content="https://variety.com/wp-content/uploads/2026/04/kimmel.jpg">
</head>
<body>
<aside class="sidebar related">Should be removed</aside>
<nav>Nav removed</nav>
<div class="c-entry-content">
  <h1>Jimmy Kimmel Accuses Trump of Calling for His Firing</h1>
  <p>President Trump seems determined to get Jimmy Kimmel off the air.</p>
  <p>The late-night host responded on his show Wednesday night, dedicating a segment to Trump's repeated calls for ABC to fire him.</p>
  <p>Kimmel said that the president's efforts to pressure the network are nothing more than a distraction from the ongoing Epstein file disclosures.</p>
  <p>The host added that Trump has called for his firing multiple times this year alone, and that ratings, not politics, should determine who stays on television.</p>
  <p>ABC has not commented on the matter, and sources familiar with the network's plans say there is no intention to part ways with Kimmel at this time.</p>
  <p>The segment drew significant social media attention, with many viewers praising Kimmel for his directness on the issue.</p>
  <div class="newsletter-signup">Subscribe to our newsletter!</div>
  <p class="sponsored">Sponsored content block to remove</p>
</div>
<footer>Footer removed</footer>
</body>
</html>"""

VARIETY_HTML_SHORT = """<!DOCTYPE html>
<html>
<body>
<div class="c-entry-content">
  <p>Short.</p>
</div>
</body>
</html>"""

HOLLYWOODREPORTER_HTML_VALID = """<!DOCTYPE html>
<html>
<head>
<title>Test Article | The Hollywood Reporter</title>
<meta property="og:title" content="Jimmy Kimmel Says If He Should Be Fired So Should Trump">
<meta property="og:image" content="https://hollywoodreporter.com/wp-content/uploads/kimmel2.jpg">
</head>
<body>
<aside class="sidebar">Should be removed</aside>
<div class="article-content">
  <h1>Jimmy Kimmel Says If He Should Be Fired, So Should Trump</h1>
  <p>Jimmy Kimmel fired back at Trump's Thursday comments encouraging ABC to fire the late night host.</p>
  <p>The host devoted a portion of his Wednesday monologue to addressing the president's repeated calls for the network to remove him from air.</p>
  <p>Kimmel said that if poor ratings were the standard for being fired, Trump himself would have been let go long ago based on his approval numbers.</p>
  <p>The monologue touched on the Epstein files, with Kimmel suggesting that Trump's media pressure campaign is designed to deflect attention from that investigation.</p>
  <p>In a clip shared widely on social media, Kimmel also referenced Melania Trump, calling the entire situation an attempt to distract the public from more serious matters.</p>
  <p>Industry watchers say the back-and-forth represents an unusually direct political conflict between a sitting president and a late-night television personality.</p>
  <div class="ad-banner">Ad content to remove</div>
  <div class="recommended-articles">Recommended to remove</div>
</div>
<nav class="sidebar-nav">Nav removed</nav>
</body>
</html>"""

HOLLYWOODREPORTER_HTML_SHORT = """<!DOCTYPE html>
<html>
<body>
<div class="article-content">
  <p>Too short.</p>
</div>
</body>
</html>"""

OTHER_DOMAIN_HTML = """<!DOCTYPE html>
<html>
<body>
<article>
  <p>Some content from another domain entirely.</p>
</article>
</body>
</html>"""


results = []


def check(name, condition):
    results.append((name, condition))


# ============================================================
# VARIETY TESTS
# ============================================================

def test_variety_extract_returns_result():
    result = extractor.extract(VARIETY_HTML_VALID, url="https://variety.com/2026/tv/news/test-1234")
    check("variety: extract() returns dict", result is not None and isinstance(result, dict))


def test_variety_title_extracted():
    result = extractor.extract(VARIETY_HTML_VALID, url="https://variety.com/2026/tv/news/test-1234")
    title = (result or {}).get("title", "")
    check("variety: title is non-empty", len(title) > 0)


def test_variety_content_above_800():
    result = extractor.extract(VARIETY_HTML_VALID, url="https://variety.com/2026/tv/news/test-1234")
    content = (result or {}).get("content", "")
    check("variety: content_chars >= 800", len(content) >= 800)


def test_variety_detection_ok():
    result = extractor.extract(VARIETY_HTML_VALID, url="https://variety.com/2026/tv/news/test-1234")
    content = (result or {}).get("content", "")
    status = detect_extraction_status(content, "https://variety.com/2026/tv/news/test-1234")
    check("variety: detect_extraction_status == ARTICLE_BODY_OK", status == "ARTICLE_BODY_OK")


def test_variety_noise_removed():
    result = extractor.extract(VARIETY_HTML_VALID, url="https://variety.com/2026/tv/news/test-1234")
    content = (result or {}).get("content", "")
    check("variety: newsletter-signup removed", "Subscribe to our newsletter" not in content)


def test_variety_sponsored_removed():
    result = extractor.extract(VARIETY_HTML_VALID, url="https://variety.com/2026/tv/news/test-1234")
    content = (result or {}).get("content", "")
    check("variety: sponsored block removed", "Sponsored content block to remove" not in content)


def test_variety_too_short_detection():
    result = extractor.extract(VARIETY_HTML_SHORT, url="https://variety.com/2026/tv/news/short-1234")
    content = (result or {}).get("content", "")
    status = detect_extraction_status(content, "https://variety.com/2026/tv/news/short-1234")
    check("variety: short HTML -> TOO_SHORT status", status == "TOO_SHORT")


# ============================================================
# HOLLYWOOD REPORTER TESTS
# ============================================================

def test_hollywoodreporter_extract_returns_result():
    result = extractor.extract(HOLLYWOODREPORTER_HTML_VALID, url="https://www.hollywoodreporter.com/tv/tv-news/test-1234")
    check("hollywoodreporter: extract() returns dict", result is not None and isinstance(result, dict))


def test_hollywoodreporter_title_extracted():
    result = extractor.extract(HOLLYWOODREPORTER_HTML_VALID, url="https://www.hollywoodreporter.com/tv/tv-news/test-1234")
    title = (result or {}).get("title", "")
    check("hollywoodreporter: title is non-empty", len(title) > 0)


def test_hollywoodreporter_content_above_800():
    result = extractor.extract(HOLLYWOODREPORTER_HTML_VALID, url="https://www.hollywoodreporter.com/tv/tv-news/test-1234")
    content = (result or {}).get("content", "")
    check("hollywoodreporter: content_chars >= 800", len(content) >= 800)


def test_hollywoodreporter_detection_ok():
    result = extractor.extract(HOLLYWOODREPORTER_HTML_VALID, url="https://www.hollywoodreporter.com/tv/tv-news/test-1234")
    content = (result or {}).get("content", "")
    status = detect_extraction_status(content, "https://www.hollywoodreporter.com/tv/tv-news/test-1234")
    check("hollywoodreporter: detect_extraction_status == ARTICLE_BODY_OK", status == "ARTICLE_BODY_OK")


def test_hollywoodreporter_noise_removed():
    result = extractor.extract(HOLLYWOODREPORTER_HTML_VALID, url="https://www.hollywoodreporter.com/tv/tv-news/test-1234")
    content = (result or {}).get("content", "")
    check("hollywoodreporter: ad-banner removed", "Ad content to remove" not in content)
    check("hollywoodreporter: recommended block removed", "Recommended to remove" not in content)


def test_hollywoodreporter_too_short_detection():
    result = extractor.extract(HOLLYWOODREPORTER_HTML_SHORT, url="https://www.hollywoodreporter.com/tv/tv-news/short-1234")
    content = (result or {}).get("content", "")
    status = detect_extraction_status(content, "https://www.hollywoodreporter.com/tv/tv-news/short-1234")
    check("hollywoodreporter: short HTML -> TOO_SHORT status", status == "TOO_SHORT")


# ============================================================
# ROUTING TESTS
# ============================================================

def test_variety_uses_specific_extractor():
    """Verify the routing block in extractor.py references variety.com"""
    with open("app/extractor.py", encoding="utf-8") as f:
        src = f.read()
    check(
        "extractor.py: variety.com routing present",
        "'variety.com' in domain" in src and "_clean_html_for_variety" in src
    )


def test_hollywoodreporter_uses_specific_extractor():
    """Verify the routing block in extractor.py references hollywoodreporter.com"""
    with open("app/extractor.py", encoding="utf-8") as f:
        src = f.read()
    check(
        "extractor.py: hollywoodreporter.com routing present",
        "'hollywoodreporter.com' in domain" in src and "_clean_html_for_hollywoodreporter" in src
    )


def test_other_domain_still_uses_fallback():
    """Domains without specific rules still fall through to trafilatura fallback"""
    with open("app/extractor.py", encoding="utf-8") as f:
        src = f.read()
    check(
        "extractor.py: trafilatura fallback still present",
        "Falling back to generic (trafilatura) extractor" in src
    )


def test_extractor_py_compiles():
    import py_compile
    try:
        py_compile.compile("app/extractor.py", doraise=True)
        check("extractor.py py_compile passes", True)
    except py_compile.PyCompileError as e:
        check(f"extractor.py py_compile passes: {e}", False)


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    import logging
    logging.disable(logging.CRITICAL)

    test_variety_extract_returns_result()
    test_variety_title_extracted()
    test_variety_content_above_800()
    test_variety_detection_ok()
    test_variety_noise_removed()
    test_variety_sponsored_removed()
    test_variety_too_short_detection()
    test_hollywoodreporter_extract_returns_result()
    test_hollywoodreporter_title_extracted()
    test_hollywoodreporter_content_above_800()
    test_hollywoodreporter_detection_ok()
    test_hollywoodreporter_noise_removed()
    test_hollywoodreporter_too_short_detection()
    test_variety_uses_specific_extractor()
    test_hollywoodreporter_uses_specific_extractor()
    test_other_domain_still_uses_fallback()
    test_extractor_py_compiles()

    print()
    all_ok = True
    for name, ok in results:
        status = "OK" if ok else "FAIL"
        if not ok:
            all_ok = False
        print(f"  [{status}] {name}")
    print()
    if all_ok:
        print("ALL CHECKS PASSED")
    else:
        print("SOME CHECKS FAILED")
        sys.exit(1)
