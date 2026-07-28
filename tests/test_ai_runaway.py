import logging
from inspect import getsource
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app import pipeline
from app.ai_client_gemini import AIClient
from app.ai_processor import (
    RUNAWAY_FAILURE_REASON,
    TRUNCATED_BY_TOKEN_CEILING,
    AIProcessor,
)
from app.pipeline import _handle_ai_processing_failure


def test_ai_client_marks_usage_at_95_percent_of_output_ceiling():
    client = AIClient(keys=["test-key-1234"], min_interval_s=0)
    client.pool.next_ready = Mock(return_value=SimpleNamespace(key="test-key-1234"))
    client.rl.wait = Mock()
    response = SimpleNamespace(
        text='{"resultados":[]}',
        candidates=[],
        usage_metadata=SimpleNamespace(
            prompt_token_count=12590,
            candidates_token_count=30400,
            cached_content_token_count=0,
        ),
    )

    with patch("app.ai_client_gemini._generate_content", return_value=response):
        _, tokens = client.generate_text(
            "prompt",
            generation_config={"max_output_tokens": 32000},
        )

    assert tokens["max_output_tokens"] == 32000
    assert tokens["completion_tokens"] == 30400
    assert tokens["truncated_by_token_ceiling"] is True


class _FakeAIClient:
    def __init__(self, *, completion_tokens, ceiling, truncated):
        self.completion_tokens = completion_tokens
        self.ceiling = ceiling
        self.truncated = truncated

    def generate_text(self, prompt, generation_config=None):
        return (
            '{"resultados":[{"titulo_final":"truncado"',
            {
                "prompt_tokens": 12590,
                "completion_tokens": self.completion_tokens,
                "max_output_tokens": self.ceiling,
                "truncated_by_token_ceiling": self.truncated,
                "model": "gemini-test",
            },
        )

    def get_last_used_key(self):
        return "test-key"

    def get_last_used_model(self):
        return "gemini-test"


class _FakeStatusDb:
    def __init__(self):
        self.updates = []
        self.fail_count_reads = 0

    def update_article_status(self, article_id, status, retry_at=None, reason=None):
        self.updates.append((article_id, status, reason))
        return True

    def get_article_fail_count(self, article_id):
        self.fail_count_reads += 1
        return 0


def _processor(fake_client):
    processor = object.__new__(AIProcessor)
    processor._ai_client = fake_client
    processor._prompt_template = "{content}"
    return processor


def _batch_item():
    return {
        "db_id": 18906,
        "title": "Runaway test",
        "content_html": "<p>Fonte curta.</p>",
        "source_url": "https://example.com/runaway",
        "source_word_count": 650,
        "target_words": 650,
        "target_min_words": 520,
        "min_acceptable_words": 520,
        "max_recommended_words": 780,
    }


def test_token_ceiling_parse_failure_is_terminal_and_never_queued(monkeypatch, caplog):
    processor = _processor(
        _FakeAIClient(completion_tokens=31985, ceiling=32000, truncated=True)
    )
    monkeypatch.setattr("app.ai_processor.log_tokens", lambda **kwargs: True)

    with caplog.at_level(logging.ERROR):
        result = processor.rewrite_batch([_batch_item()])

    assert result == [(None, RUNAWAY_FAILURE_REASON)]
    assert TRUNCATED_BY_TOKEN_CEILING in caplog.text
    assert "[AI_RUNAWAY]" in caplog.text

    db = _FakeStatusDb()
    status = _handle_ai_processing_failure(db, 18906, result[0][1])

    assert status == "FAILED_PERMANENT"
    assert db.updates == [(18906, "FAILED_PERMANENT", "runaway_token_ceiling")]
    assert db.fail_count_reads == 0
    assert all(update[1] != "QUEUED" for update in db.updates)


def test_non_ceiling_parse_failure_keeps_existing_retry_behavior(monkeypatch):
    processor = _processor(
        _FakeAIClient(completion_tokens=1200, ceiling=32000, truncated=False)
    )
    monkeypatch.setattr("app.ai_processor.log_tokens", lambda **kwargs: True)

    result = processor.rewrite_batch([_batch_item()])

    assert result == [(
        None,
        "Batch JSON parsing failed (insufficient quota budget to retry individually)",
    )]

    db = _FakeStatusDb()
    status = _handle_ai_processing_failure(db, 18906, result[0][1])

    assert status == "QUEUED"
    assert db.updates == [(
        18906,
        "QUEUED",
        "Batch JSON parsing failed (insufficient quota budget to retry individually)",
    )]
    assert db.fail_count_reads == 1


def test_runaway_failure_stops_before_draft_submission():
    source = getsource(pipeline.process_batch)
    failure_handler = source.index("_handle_ai_processing_failure")
    stop_processing = source.index("continue", failure_handler)
    draft_submit = source.index("submitter.submit(draft)")

    assert failure_handler < stop_processing < draft_submit
