from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from app.ai_client_gemini import AIClient
from app.exceptions import BlockedPromptError
from app.pipeline import _is_terminal_ai_failure


def _blocked_response(reason: str = "PROHIBITED_CONTENT"):
    return SimpleNamespace(
        candidates=[],
        prompt_feedback=SimpleNamespace(block_reason=reason),
        usage_metadata=SimpleNamespace(
            prompt_token_count=4745,
            candidates_token_count=0,
        ),
    )


def test_generate_text_raises_blocked_prompt_without_penalizing_key():
    client = AIClient(keys=["test-key-1234"], min_interval_s=0)
    slot = SimpleNamespace(key="test-key-1234")

    client.pool.next_ready = Mock(return_value=slot)
    client.pool.penalize = Mock()
    client.rl.wait = Mock()

    with patch("app.ai_client_gemini._generate_content", return_value=_blocked_response()):
        with pytest.raises(BlockedPromptError) as exc_info:
            client.generate_text("blocked prompt")

    assert exc_info.value.block_reason == "PROHIBITED_CONTENT"
    client.pool.penalize.assert_not_called()


def test_terminal_ai_failure_detects_blocked_prompt_reason():
    assert _is_terminal_ai_failure("Prompt blocked by model policy: PROHIBITED_CONTENT") is True
    assert _is_terminal_ai_failure("Batch JSON parsing failed") is False
