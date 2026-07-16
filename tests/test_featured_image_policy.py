from app.pipeline import _resolve_featured_image_strategy


def test_cluster_prefers_non_blocked_source_image():
    art_data = {
        "is_cluster": True,
        "url": "https://valor.globo.com/empresas/noticia-teste.ghtml",
        "feed_config": {"source_name": "RSSPRIME Superfeed"},
        "cluster_docs": [
            {
                "url": "https://valor.globo.com/empresas/noticia-teste.ghtml",
                "source": "Valor",
                "featured_image_url": "https://valor.globo.com/imagem-bloqueada.jpg",
            },
            {
                "url": "https://screenrant.com/test-article/",
                "source": "ScreenRant",
                "featured_image_url": "https://screenrant.com/image-ok.jpg",
            },
        ],
    }
    extracted = {"featured_image_url": "https://valor.globo.com/imagem-bloqueada.jpg"}

    featured_image_url, use_default = _resolve_featured_image_strategy(art_data, extracted)

    assert featured_image_url == "https://screenrant.com/image-ok.jpg"
    assert use_default is False


def test_single_blocked_source_uses_default_image():
    art_data = {
        "is_cluster": False,
        "url": "https://www.estadao.com.br/cultura/noticia-teste/",
        "feed_config": {"source_name": "Estadão"},
        "cluster_docs": [],
    }
    extracted = {"featured_image_url": "https://www.estadao.com.br/imagem.jpg"}

    featured_image_url, use_default = _resolve_featured_image_strategy(art_data, extracted)

    assert featured_image_url is None
    assert use_default is True
