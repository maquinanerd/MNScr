"""Legenda montada a partir de varios elementos nao pode sair com palavras coladas.

O defeito, no log real de 07/08 (Hollywood Reporter, ``Made in Korea``):

    INFO (HollywoodReporter): Removendo legenda em inglês:
        'Made in Korea' Season 2Disney/Hulu

``Season 2`` e ``Disney/Hulu`` sao dois elementos irmaos dentro da mesma
``<figcaption>``: a legenda e o credito. ``get_text(strip=True)``, sem
separador, devolve os pedacos EMENDADOS — e essa string vira texto publicado,
porque a legenda capturada alimenta o ``alt`` da imagem e o credito.

A funcao vizinha ``collect_image_captions_from_article`` ja fazia certo, com
``get_text(" ", strip=True)``. As duas que faltavam estao cobertas aqui.
"""

from bs4 import BeautifulSoup

from app.extractor import ContentExtractor, _clean_english_captions, _collapse_ws

#: Markup equivalente ao da Hollywood Reporter que produziu o log acima.
FIGCAPTION_REAL = (
    "<article>"
    '<figure><img src="https://cdn.example/made-in-korea.jpg"/>'
    "<figcaption>"
    "<span>'Made in Korea' Season 2</span>"
    "<span>Disney/Hulu</span>"
    "</figcaption></figure>"
    "</article>"
)


def test_sibling_elements_in_a_figcaption_are_not_glued_together():
    soup = BeautifulSoup(FIGCAPTION_REAL, "html.parser")
    captured = _clean_english_captions(soup, "hollywoodreporter.com", "https://example.test/x")

    assert captured, "a legenda em ingles tem de ser capturada antes de ser apagada"
    legenda = next(iter(captured.values()))
    assert "2Disney" not in legenda
    assert legenda == "'Made in Korea' Season 2 Disney/Hulu"


def test_a_single_text_node_caption_is_unchanged():
    """O conserto nao pode inventar espaco onde nao havia dois elementos."""
    soup = BeautifulSoup(
        "<article><figure><img src=\"https://cdn.example/a.jpg\"/>"
        "<figcaption>Tom Holland in Spider-Man</figcaption></figure></article>",
        "html.parser",
    )
    captured = _clean_english_captions(soup, "example.com", "https://example.test/x")
    assert next(iter(captured.values())) == "Tom Holland in Spider-Man"


def test_data_img_div_caption_is_not_glued_into_the_alt_text():
    """``_convert_data_img_to_figure`` copia a legenda direto para o ``alt``.

    Sem separador, o texto alternativo publicado sai com as duas palavras
    coladas — e ``alt`` e exatamente o campo que o Cinerie leva para a pagina.
    """
    soup = BeautifulSoup(
        "<article>"
        '<div data-img-url="https://cdn.example/b.jpg">'
        "<span>Primeira parte</span><span>Segunda parte</span>"
        "</div>"
        "</article>",
        "html.parser",
    )
    ContentExtractor()._convert_data_img_to_figure(soup)

    img = soup.find("img")
    assert img is not None
    assert "parteSegunda" not in img["alt"]
    assert img["alt"] == "Primeira parte Segunda parte"


def test_collapse_ws_leaves_a_single_space_between_words():
    assert _collapse_ws("  Season 2 \n\t Disney/Hulu  ") == "Season 2 Disney/Hulu"
    assert _collapse_ws("") == ""
