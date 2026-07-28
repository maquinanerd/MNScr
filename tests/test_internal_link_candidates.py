from app.internal_linking import add_internal_links
from app.pipeline import select_internal_links


def test_select_internal_links_scores_real_link_map_shape_and_excludes_current_url():
    link_map = {
        "posts": [
            {
                "link": "https://cinerie.example/star-wars-ordem-cronologica/",
                "keywords": ["Star Wars", "Star Wars: ordem cronologica dos filmes"],
            },
            {
                "link": "https://cinerie.example/marvel-fase-6/",
                "keywords": ["Marvel", "Marvel: Fase 6 ganha novas datas"],
            },
            {
                "link": "https://cinerie.example/star-wars-atual/",
                "keywords": ["Star Wars: filme atual"],
            },
        ]
    }

    selected = select_internal_links(
        link_map,
        article_title="Star Wars retorna aos cinemas com novo filme",
        entities=["Star Wars"],
        tags=["cinema"],
        topic="movies",
        k=3,
        current_url="https://cinerie.example/star-wars-atual/",
    )

    assert selected == [
        {
            "titulo": "Star Wars: ordem cronologica dos filmes",
            "url": "https://cinerie.example/star-wars-ordem-cronologica/",
        }
    ]


def test_select_internal_links_supports_title_url_maps_and_does_not_force_zero_score():
    selected = select_internal_links(
        {
            "Batman: guia de filmes": "https://cinerie.example/batman-guia/",
            "https://cinerie.example/mario-guia/": "Mario: guia dos jogos",
        },
        article_title="Metroid Prime ganha janela de lancamento",
        entities=["Samus"],
        tags=["Nintendo"],
        topic="games",
        k=6,
    )

    assert selected == []


def test_add_internal_links_falls_back_to_same_category_when_keyword_match_fails():
    html = "<p>Asia Argento participou de um festival europeu.</p><p>A noticia segue curta.</p>"
    link_map = {
        "posts": [
            {
                "link": "https://cinerie.example/cinema/",
                "title": "Cinema",
                "keywords": ["Christopher Nolan"],
                "categories": [7],
            }
        ]
    }

    result = add_internal_links(html, link_map, current_post_categories=[7], max_links=6)

    assert 'href="https://cinerie.example/cinema/"' in result
    assert "Leia tambem" in result
