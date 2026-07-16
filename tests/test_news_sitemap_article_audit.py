from bs4 import BeautifulSoup

from scripts.news_sitemap_article_audit import (
    assess_content_quality_from_soup,
    canonical_matches,
    compute_article_score,
    extract_schema_types,
    parse_news_sitemap_xml,
    select_content_root,
)


def test_assess_content_quality_matches_pipeline_rules():
    html = """
    <article>
      <h2>Bloco 1</h2>
      <h2>Bloco 2</h2>
      <h2>Bloco 3</h2>
      <h3>Detalhe</h3>
      <p>{words}</p>
      <a href="https://www.maquinanerd.com.br/post-1/">Link 1</a>
      <a href="/post-2/">Link 2</a>
    </article>
    """.format(words="palavra " * 620)
    soup = BeautifulSoup(html, "html.parser")

    quality = assess_content_quality_from_soup(soup)

    assert quality["word_count"] >= 600
    assert quality["h2_count"] == 3
    assert quality["h3_count"] == 1
    assert quality["internal_links"] == 2
    assert quality["qa_score"] == 80
    assert quality["should_index"] is True


def test_extract_schema_types_reads_graph_and_article_types():
    html = """
    <html><head>
      <script type="application/ld+json">
      {
        "@context": "https://schema.org",
        "@graph": [
          {"@type": "WebSite"},
          {"@type": "NewsArticle"}
        ]
      }
      </script>
    </head></html>
    """
    soup = BeautifulSoup(html, "html.parser")

    schema_types = extract_schema_types(soup)

    assert "WebSite" in schema_types
    assert "NewsArticle" in schema_types


def test_parse_news_sitemap_xml_extracts_entry():
    xml_content = b"""
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
            xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">
      <url>
        <loc>https://www.maquinanerd.com.br/post-exemplo/</loc>
        <news:news>
          <news:title>Post Exemplo</news:title>
          <news:publication_date>2026-04-01T10:00:00+00:00</news:publication_date>
          <news:keywords>Marvel, Disney, MCU</news:keywords>
        </news:news>
      </url>
    </urlset>
    """

    entries = parse_news_sitemap_xml(xml_content)

    assert len(entries) == 1
    assert entries[0].url == "https://www.maquinanerd.com.br/post-exemplo/"
    assert entries[0].news_title == "Post Exemplo"
    assert entries[0].keywords == "Marvel, Disney, MCU"


def test_compute_article_score_rewards_strong_article():
    metrics = {
        "title_length": 60,
        "meta_description_length": 150,
        "robots_indexable": True,
        "canonical_self": True,
        "h1_count": 1,
        "h2_count": 3,
        "h3_count": 1,
        "internal_links": 2,
        "word_count": 700,
        "has_news_article_schema": True,
        "has_og_image": True,
    }

    score, issues = compute_article_score(metrics)

    assert score == 100
    assert issues == []


def test_canonical_matches_ignores_trailing_slash():
    assert canonical_matches(
        "https://www.maquinanerd.com.br/post-exemplo/",
        "https://www.maquinanerd.com.br/post-exemplo",
    )


def test_select_content_root_prefers_entry_content_over_summary_article():
    html = """
    <html><body>
      <article><h3>Resumo</h3><p>curto demais</p></article>
      <main>
        <div class="entry-content">
          <h2>Bloco 1</h2>
          <p>{words}</p>
        </div>
      </main>
    </body></html>
    """.format(words="palavra " * 200)
    soup = BeautifulSoup(html, "html.parser")

    selected = select_content_root(soup)

    assert "entry-content" in (selected.get("class") or [])
    assert len(selected.get_text(" ", strip=True).split()) >= 200
