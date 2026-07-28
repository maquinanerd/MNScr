"""Write editorial drafts to the local filesystem.

This is the default and only fully-wired destination in MS-1. It performs no
network I/O whatsoever.

Idempotency contract:
- same ``draft_id`` and same ``output_hash``  -> UNCHANGED (success, no write)
- same ``draft_id`` and different ``output_hash`` -> CONFLICT (no overwrite),
  the incoming draft is preserved beside the original as ``.conflict-<hash>.json``
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

from app.editorial.models import EditorialDraft
from app.editorial.serialization import dumps_pretty
from app.editorial.states import (
    SUBMISSION_CONFLICT,
    SUBMISSION_UNCHANGED,
    SUBMISSION_WRITTEN,
)

from .base import SubmissionResult

logger = logging.getLogger(__name__)

DEFAULT_DRAFT_DIR = "artifacts/local-drafts"
DESTINATION = "local"


class LocalDraftSubmitter:
    """Persist a draft as a pretty-printed UTF-8 JSON artifact."""

    destination = DESTINATION

    def __init__(self, output_dir: str | os.PathLike[str] = DEFAULT_DRAFT_DIR) -> None:
        self.output_dir = Path(output_dir)

    # -- paths --------------------------------------------------------------

    def artifact_path(self, draft: EditorialDraft) -> Path:
        return self.output_dir / f"{draft.draft_id}.json"

    def _conflict_path(self, draft: EditorialDraft) -> Path:
        suffix = (draft.provenance.output_hash or "unknown")[:16]
        return self.output_dir / f"{draft.draft_id}.conflict-{suffix}.json"

    # -- io -----------------------------------------------------------------

    @staticmethod
    def _read_output_hash(path: Path) -> str | None:
        """Read the recorded output_hash of an existing artifact, if readable."""
        try:
            import json

            with path.open("r", encoding="utf-8") as handle:
                existing = json.load(handle)
        except (OSError, ValueError) as exc:
            logger.warning("[LOCAL_DRAFT] artefato ilegivel path=%s erro=%s", path, exc)
            return None
        provenance = existing.get("provenance") if isinstance(existing, dict) else None
        if isinstance(provenance, dict):
            recorded = provenance.get("output_hash")
            return str(recorded) if recorded else None
        return None

    def _atomic_write(self, path: Path, payload: str) -> None:
        """Write via a temp file in the same directory, then replace."""
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=str(path.parent),
            prefix=f".{path.stem}.",
            suffix=".tmp",
            delete=False,
        )
        temp_path = Path(handle.name)
        try:
            with handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, path)
        except BaseException:
            temp_path.unlink(missing_ok=True)
            raise

    # -- submitter ----------------------------------------------------------

    def submit(self, draft: EditorialDraft) -> SubmissionResult:
        target = self.artifact_path(draft)
        payload = dumps_pretty(draft)
        incoming_hash = draft.provenance.output_hash

        if target.exists():
            existing_hash = self._read_output_hash(target)
            if existing_hash and existing_hash == incoming_hash:
                logger.info(
                    "[LOCAL_DRAFT] idempotente draft_id=%s path=%s",
                    draft.draft_id,
                    target,
                )
                return SubmissionResult(
                    success=True,
                    destination=self.destination,
                    status=SUBMISSION_UNCHANGED,
                    submission_id=draft.draft_id,
                    artifact_path=str(target),
                    details={"output_hash": incoming_hash},
                )

            conflict_target = self._conflict_path(draft)
            self._atomic_write(conflict_target, payload)
            logger.error(
                "[LOCAL_DRAFT] conflito draft_id=%s existente=%s recebido=%s salvo_em=%s",
                draft.draft_id,
                existing_hash,
                incoming_hash,
                conflict_target,
            )
            return SubmissionResult(
                success=False,
                destination=self.destination,
                status=SUBMISSION_CONFLICT,
                submission_id=draft.draft_id,
                artifact_path=str(conflict_target),
                error=(
                    "Artefato existente diverge do draft recebido; "
                    "nada foi sobrescrito."
                ),
                details={
                    "existing_output_hash": existing_hash,
                    "incoming_output_hash": incoming_hash,
                    "original_path": str(target),
                },
            )

        self._atomic_write(target, payload)
        logger.info(
            "[LOCAL_DRAFT] gravado draft_id=%s path=%s bytes=%s",
            draft.draft_id,
            target,
            len(payload.encode("utf-8")),
        )
        return SubmissionResult(
            success=True,
            destination=self.destination,
            status=SUBMISSION_WRITTEN,
            submission_id=draft.draft_id,
            artifact_path=str(target),
            details={"output_hash": incoming_hash},
        )
