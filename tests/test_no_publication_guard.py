"""Architectural guard.

These tests fail loudly if the canonical path ever reacquires the ability to
publish, index, upload media or create taxonomy in an external CMS.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_DIR = REPO_ROOT / "app"

# The canonical path: every module reachable from the pipeline entrypoint.
CANONICAL_MODULES = [
    "app/entrypoint.py",
    "app/main.py",
    "app/pipeline.py",
    "app/config.py",
    "app/store.py",
    "app/task_queue.py",
    "app/feeds.py",
    "app/extractor.py",
    "app/multi_source_builder.py",
    "app/superfeed_policy.py",
    "app/ai_processor.py",
    "app/ai_client_gemini.py",
    "app/ai_validator.py",
    "app/policy_engine.py",
    "app/editorial_validator.py",
    "app/entity_validator.py",
    "app/html_utils.py",
    "app/internal_linking.py",
    "app/link_store.py",
    "app/cluster_engine.py",
    "app/cluster_extractor.py",
    "app/cluster_feed_adapter.py",
    # MS-2: contrato de entrada, persistencia de eventos e replay.
    "app/ingestion.py",
    "app/event_store.py",
    "app/replay.py",
    "app/contracts/__init__.py",
    "app/contracts/errors.py",
    "app/contracts/hashing.py",
    "app/contracts/legacy_feed_v0.py",
    "app/contracts/revisions.py",
    "app/contracts/rssprime_event_v1.py",
    "app/contracts/states.py",
    "app/contracts/validation.py",
    # MS-3: Editorial Gate, persistencia e reavaliacao.
    "app/gate_service.py",
    "app/gate_store.py",
    "app/editorial_gate/__init__.py",
    "app/editorial_gate/engine.py",
    "app/editorial_gate/errors.py",
    "app/editorial_gate/models.py",
    "app/editorial_gate/policy.py",
    "app/editorial_gate/rules.py",
    "app/editorial_gate/serialization.py",
]

FORBIDDEN_SYMBOLS = [
    "WordPressClient",
    "create_post",
    "notify_post_published",
    "send_to_indexnow",
    "ping_sitemap",
    "upload_media_from_url",
    "set_post_featured_media",
    "resolve_category_names_to_ids",
    "_create_category",
    "_create_tag",
    "update_post_yoast_seo",
    "add_google_news_meta",
    "sanitize_published_post",
    "set_media_alt_text",
]

FORBIDDEN_MODULES = [
    "app.wordpress",
    "app.indexing_service",
    "app.image_localizer",
    "app.media",
    "app.evergreen_publisher",
    "scripts",
]


def _source(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


@pytest.mark.parametrize("module_path", CANONICAL_MODULES)
@pytest.mark.parametrize("symbol", FORBIDDEN_SYMBOLS)
def test_canonical_path_never_references_publishing_symbols(module_path, symbol):
    assert symbol not in _source(module_path), (
        f"{module_path} voltou a referenciar {symbol!r}: o caminho canonico nao pode publicar."
    )


@pytest.mark.parametrize("module_path", CANONICAL_MODULES)
def test_canonical_path_never_imports_removed_modules(module_path):
    tree = ast.parse(_source(module_path))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:  # relative import inside app/
                base = f"app.{base}" if base else "app"
            imported.add(base)
            imported.update(f"{base}.{alias.name}" for alias in node.names)

    for forbidden in FORBIDDEN_MODULES:
        offenders = {name for name in imported if name == forbidden or name.startswith(forbidden + ".")}
        assert not offenders, f"{module_path} importa modulo proibido: {offenders}"


def test_removed_modules_are_gone_from_the_tree():
    for relative in [
        "app/wordpress.py",
        "app/indexing_service.py",
        "app/image_localizer.py",
        "app/media.py",
        "app/evergreen_publisher.py",
        "app/batch_processor.py",
    ]:
        assert not (REPO_ROOT / relative).exists(), f"{relative} deveria ter sido removido na MS-1"


def test_indexer_entrypoint_is_disabled():
    source = _source("indexer.py")
    assert "Legacy indexing is disabled in MNScr." in source
    assert "indexing_service" not in source


def test_no_module_writes_a_published_state():
    """No module may transition an article into a PUBLISHED state."""
    offenders: list[str] = []
    for path in APP_DIR.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for line_number, line in enumerate(text.splitlines(), 1):
            if "status = 'PUBLISHED'" in line or 'status = "PUBLISHED"' in line:
                offenders.append(f"{path}:{line_number}")
    assert offenders == [], f"Estado PUBLISHED sendo gravado em: {offenders}"


def test_pipeline_terminal_state_is_draft_generated():
    source = _source("app/pipeline.py")
    assert "record_draft_generated" in source
    assert "submitter.submit(draft)" in source
    assert "DRAFT_GENERATED" in source
