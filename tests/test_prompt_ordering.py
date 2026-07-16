from app.ai_processor import AIProcessor


def test_prompt_anchor_appears_after_content_and_repeats_target_min_words():
    template = (
        "DADOS PARA PROCESSAMENTO\n"
        "- Tamanho mínimo obrigatório da matéria final: {target_min_words} palavras\n"
        "- Conteúdo Original HTML:\n"
        "{content}\n"
    )
    content = "<p>CONTEUDO_EXTRAIDO_SENTINELA</p>"
    fields = AIProcessor._build_prompt_fields(
        {
            "title": "Titulo de teste",
            "source_url": "https://example.com/original",
            "content_html": content,
            "target_min_words": 700,
        }
    )

    prompt = AIProcessor._build_article_prompt(template, fields)
    anchor = "Com base no conteúdo extraído acima"

    assert anchor in prompt
    assert prompt.index(anchor) > prompt.index(content)
    assert prompt.count("700 palavras") == 2
    assert "wrapper 'resultados'" in prompt


def test_internal_link_block_is_appended_only_when_candidates_exist():
    template = "Conteudo:\n{content}\n"
    content = "<p>Materia sobre Star Wars</p>"

    fields_without_links = AIProcessor._build_prompt_fields(
        {
            "title": "Star Wars retorna ao cinema",
            "content_html": content,
        }
    )
    prompt_without_links = AIProcessor._build_article_prompt(template, fields_without_links)
    assert "LINKS INTERNOS DISPONIVEIS" not in prompt_without_links

    fields_with_links = AIProcessor._build_prompt_fields(
        {
            "title": "Star Wars retorna ao cinema",
            "content_html": content,
            "internal_link_candidates": [
                {
                    "titulo": "Star Wars: ordem cronologica dos filmes",
                    "url": "https://maquinanerd.com.br/star-wars-ordem-cronologica/",
                }
            ],
        }
    )
    prompt_with_links = AIProcessor._build_article_prompt(template, fields_with_links)

    assert "LINKS INTERNOS DISPONIVEIS" in prompt_with_links
    assert "- [Star Wars: ordem cronologica dos filmes](https://maquinanerd.com.br/star-wars-ordem-cronologica/)" in prompt_with_links
    assert "<a href=\"URL\">texto ancora</a>" in prompt_with_links
