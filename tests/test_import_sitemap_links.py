from scripts.import_sitemap_links import NEWS_SITEMAP_URL


def test_import_uses_only_news_sitemap():
    assert NEWS_SITEMAP_URL == "https://www.maquinanerd.com.br/news-sitemap.xml"
