#!/usr/bin/env python3
"""
Custom exception classes for the RSS-to-WordPress automation app.
"""

class AIProcessorError(Exception):
    """
    Custom exception for errors related to the AI content processor.
    This can be raised for issues like invalid configuration, prompt loading
    failures, or problems parsing the AI's response.
    """
    pass


class AllKeysFailedError(AIProcessorError):
    """
    Raised when all available API keys for a specific category have been tried
    and have failed, preventing further AI processing for that category.
    """
    pass


class BlockedPromptError(AIProcessorError):
    """
    Raised when the model blocks a prompt for policy/safety reasons.

    This should be treated as a terminal failure for the current prompt,
    without penalizing the API key or retrying indefinitely.
    """

    def __init__(self, message: str, block_reason: str | None = None):
        super().__init__(message)
        self.block_reason = block_reason


class WordPressPublisherError(Exception):
    """Custom exception for errors related to publishing content to WordPress."""
    pass


class ArticleProcessingError(Exception):
    """
    Generic error for failures during the article processing pipeline,
    such as content extraction or data validation issues.
    """
    pass
