import re

from app.ai_validator import ADDITIVE_EXPANSION_PROMPT, expand_article_if_too_short


def test_additive_expansion_prompt_returns_only_new_html():
    assert "APENAS blocos HTML novos" in ADDITIVE_EXPANSION_PROMPT
    assert "NAO reescreva" in ADDITIVE_EXPANSION_PROMPT
    assert "Retorne SOMENTE o HTML" in ADDITIVE_EXPANSION_PROMPT


def test_additive_expansion_prompt_keeps_target_placeholders():
    assert "{target_min_words}" in ADDITIVE_EXPANSION_PROMPT
    assert "{missing_words}" in ADDITIVE_EXPANSION_PROMPT
    assert "{requested_additional_words}" in ADDITIVE_EXPANSION_PROMPT


def test_additive_expansion_prompt_keeps_source_content_placeholder():
    assert "{source_content}" in ADDITIVE_EXPANSION_PROMPT


def test_additive_expansion_prompt_format_replaces_all_placeholders():
    prompt = ADDITIVE_EXPANSION_PROMPT.format(
        target_min_words=650,
        target_words=800,
        titulo_atual="Titulo atual",
        conteudo_atual="<p>Conteudo atual.</p>",
        current_word_count=120,
        missing_words=530,
        requested_additional_words=689,
        source_content="<p>Fonte original.</p>",
    )

    assert not re.search(
        r"\{(?:target_min_words|titulo_atual|conteudo_atual|current_word_count|missing_words|requested_additional_words|source_content)\}",
        prompt,
    )


def test_additive_expansion_appends_new_html_and_preserves_attempt_tokens():
    class FakeClient:
        def generate_text(self, prompt, generation_config=None, **kwargs):
            return (
                "<h2>Contexto adicional</h2><p>" + " ".join(["extra"] * 90) + "</p>",
                {"prompt_tokens": 123, "completion_tokens": 45},
            )

    original_content = "<p>" + " ".join(["palavra"] * 120) + "</p>"
    result = expand_article_if_too_short(
        rewritten_data={"conteudo_final": original_content},
        original_article={
            "db_id": 123,
            "content_html": "<p>" + " ".join(["fonte"] * 300) + "</p>",
            "source_word_count": 300,
            "article_type": "guide",
            "origin": "fallback",
        },
        word_policy={
            "source_words": 300,
            "min_acceptable_words": 200,
            "target_words": 260,
            "allow_expansion": True,
            "article_type": "guide",
        },
        api_client=FakeClient(),
    )

    assert result["conteudo_final"].startswith(original_content)
    assert "<h2>Contexto adicional</h2>" in result["conteudo_final"]
    assert result["conteudo_final"] != original_content
    assert result["_expansion_input_tokens"] == 123
    assert result["_expansion_output_tokens"] == 45
    assert result["_expansion_total_tokens"] == 168


def test_expansion_duplicate_additive_output_preserves_original_and_attempt_tokens():
    class FakeClient:
        def __init__(self, content):
            self.content = content

        def generate_text(self, prompt, generation_config=None, **kwargs):
            return (
                self.content,
                {"prompt_tokens": 123, "completion_tokens": 45},
            )

    original_content = "<p>" + " ".join(["palavra"] * 120) + "</p>"
    result = expand_article_if_too_short(
        rewritten_data={"conteudo_final": original_content},
        original_article={
            "db_id": 123,
            "content_html": "<p>" + " ".join(["fonte"] * 300) + "</p>",
            "source_word_count": 300,
            "article_type": "guide",
            "origin": "fallback",
        },
        word_policy={
            "source_words": 300,
            "min_acceptable_words": 200,
            "target_words": 260,
            "allow_expansion": True,
            "article_type": "guide",
        },
        api_client=FakeClient(original_content),
    )

    assert result["conteudo_final"] == original_content
    assert result["_expansion_input_tokens"] == 123
    assert result["_expansion_output_tokens"] == 45
    assert result["_expansion_total_tokens"] == 168


def test_expansion_removes_duplicate_headings_only():
    class FakeClient:
        def generate_text(self, prompt, generation_config=None, **kwargs):
            return (
                "<h2>Contexto adicional</h2>"
                "<p>" + " ".join(["extra"] * 90) + "</p>"
                "<h2>Contexto adicional</h2>"
                "<p>Paragrafo abaixo do heading duplicado deve ficar.</p>",
                {"prompt_tokens": 123, "completion_tokens": 45},
            )

    original_content = "<p>" + " ".join(["palavra"] * 120) + "</p>"
    result = expand_article_if_too_short(
        rewritten_data={"conteudo_final": original_content},
        original_article={
            "db_id": 123,
            "content_html": "<p>" + " ".join(["fonte"] * 300) + "</p>",
            "source_word_count": 300,
            "article_type": "guide",
            "origin": "fallback",
        },
        word_policy={
            "source_words": 300,
            "min_acceptable_words": 200,
            "target_words": 260,
            "allow_expansion": True,
            "article_type": "guide",
        },
        api_client=FakeClient(),
    )

    assert result["conteudo_final"].count("<h2>Contexto adicional</h2>") == 1
    assert "Paragrafo abaixo do heading duplicado deve ficar." in result["conteudo_final"]
