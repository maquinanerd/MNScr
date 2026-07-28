"""Draft submitters for MNScr.

MNScr hands drafts to a destination controlled by a human editor. There is no
publishing submitter and there will not be one: WordPress output is disabled.
"""

from __future__ import annotations

import logging
import os

from .base import (
    DraftSubmitter,
    SubmissionResult,
    SubmitterError,
    SubmitterNotEnabledError,
)
from .local import DEFAULT_DRAFT_DIR, LocalDraftSubmitter
from .payload import NOT_ENABLED_MESSAGE, PayloadConfig, PayloadDraftSubmitter

logger = logging.getLogger(__name__)

OUTPUT_MODE_LOCAL = "local"
OUTPUT_MODE_PAYLOAD = "payload"
ALLOWED_OUTPUT_MODES = (OUTPUT_MODE_LOCAL, OUTPUT_MODE_PAYLOAD)

WORDPRESS_DISABLED_MESSAGE = "WordPress output is disabled in MNScr."


class OutputModeError(ValueError):
    """Raised when MNSCR_OUTPUT_MODE is invalid or forbidden."""


def resolve_output_mode(raw_mode: str | None = None) -> str:
    """Normalize and validate the configured output mode."""
    mode = (raw_mode if raw_mode is not None else os.getenv("MNSCR_OUTPUT_MODE", OUTPUT_MODE_LOCAL))
    mode = (mode or OUTPUT_MODE_LOCAL).strip().lower()
    if mode == "wordpress":
        raise OutputModeError(WORDPRESS_DISABLED_MESSAGE)
    if mode not in ALLOWED_OUTPUT_MODES:
        raise OutputModeError(
            f"MNSCR_OUTPUT_MODE invalido: {mode!r}. "
            f"Modos aceitos: {', '.join(ALLOWED_OUTPUT_MODES)}"
        )
    return mode


def build_submitter(
    mode: str | None = None,
    *,
    local_dir: str | None = None,
) -> DraftSubmitter:
    """Return the submitter for the configured output mode.

    ``payload`` is rejected while the Cinerie Editorial contract is pending, so
    a misconfiguration fails at startup instead of mid-cycle.
    """
    resolved = resolve_output_mode(mode)

    if resolved == OUTPUT_MODE_LOCAL:
        directory = local_dir or os.getenv("MNSCR_LOCAL_DRAFT_DIR", DEFAULT_DRAFT_DIR)
        return LocalDraftSubmitter(output_dir=directory)

    config = PayloadConfig.from_env()
    if not config.enabled:
        raise OutputModeError(NOT_ENABLED_MESSAGE)
    raise OutputModeError(NOT_ENABLED_MESSAGE)


__all__ = [
    "ALLOWED_OUTPUT_MODES",
    "DEFAULT_DRAFT_DIR",
    "DraftSubmitter",
    "LocalDraftSubmitter",
    "NOT_ENABLED_MESSAGE",
    "OUTPUT_MODE_LOCAL",
    "OUTPUT_MODE_PAYLOAD",
    "OutputModeError",
    "PayloadConfig",
    "PayloadDraftSubmitter",
    "SubmissionResult",
    "SubmitterError",
    "SubmitterNotEnabledError",
    "WORDPRESS_DISABLED_MESSAGE",
    "build_submitter",
    "resolve_output_mode",
]
