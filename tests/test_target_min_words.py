from app.ai_processor import AIProcessor


def test_prompt_fields_target_min_words_falls_back_to_target_words():
    fields = AIProcessor._build_prompt_fields(
        {
            "title": "Titulo",
            "content_html": "<p>Texto</p>",
            "source_url": "https://example.com/post",
            "target_words": 700,
        }
    )

    assert fields["target_min_words"] == 700
    assert fields["target_words"] == 700


def test_prompt_fields_target_words_falls_back_to_target_min_words():
    fields = AIProcessor._build_prompt_fields(
        {
            "title": "Titulo",
            "content_html": "<p>Texto</p>",
            "source_url": "https://example.com/post",
            "target_min_words": 650,
        }
    )

    assert fields["target_min_words"] == 650
    assert fields["target_words"] == 650
