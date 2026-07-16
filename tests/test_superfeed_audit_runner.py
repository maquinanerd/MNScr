import json

import pytest

from scripts.run_superfeed_audit import _final_report, validate_safe_environment, write_audit_bundle


def test_validate_safe_environment_blocks_publish_with_ignore_seen_case_insensitive(monkeypatch):
    monkeypatch.setenv("WORDPRESS_STATUS", "PUBLISH")
    monkeypatch.setenv("IGNORE_ALREADY_SEEN_FOR_AUDIT", "TRUE")

    with pytest.raises(RuntimeError):
        validate_safe_environment()


def test_validate_safe_environment_blocks_publish_with_ignore_seen_lowercase(monkeypatch):
    monkeypatch.setenv("WORDPRESS_STATUS", "publish")
    monkeypatch.setenv("IGNORE_ALREADY_SEEN_FOR_AUDIT", "true")

    with pytest.raises(RuntimeError):
        validate_safe_environment()


def test_validate_safe_environment_blocks_values_with_spaces(monkeypatch):
    monkeypatch.setenv("WORDPRESS_STATUS", " publish ")
    monkeypatch.setenv("IGNORE_ALREADY_SEEN_FOR_AUDIT", " true ")

    with pytest.raises(RuntimeError):
        validate_safe_environment()


def test_validate_safe_environment_allows_draft_with_ignore_seen(monkeypatch):
    monkeypatch.setenv("WORDPRESS_STATUS", "draft")
    monkeypatch.setenv("IGNORE_ALREADY_SEEN_FOR_AUDIT", "true")

    validate_safe_environment()


def test_final_report_contains_required_sections():
    report = _final_report(
        {
            "event_key": "event-1",
            "topic": "movies",
            "sources_used_count": 2,
            "sources_skipped_count": 1,
            "wordpress_id": 123,
            "wordpress_status": "draft",
            "indexing": "noindex",
        }
    )

    assert "Identifica" in report
    assert "Verifica" in report
    assert "editorial" in report
    assert "Decis" in report


def test_write_audit_bundle_creates_all_required_outputs(tmp_path):
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    write_audit_bundle(
        audit_dir,
        {
            "raw_cluster": {"event_key": "event-1"},
            "sources_extracted": {"status": "ERROR", "error": "boom"},
            "report_payload": {
                "event_key": "event-1",
                "failure": {
                    "stage": "multi_source_builder",
                    "error": "boom",
                    "recommended_action": "corrigir pipeline",
                },
            },
        },
    )

    expected = [
        "01_raw_cluster.json",
        "02_policy_check.json",
        "03_sources_extracted.json",
        "04_sources_used.json",
        "05_sources_skipped.json",
        "06_ai_payload.json",
        "07_ai_response.json",
        "08_normalized_article.json",
        "09_wordpress_payload.json",
        "10_wordpress_response.json",
        "11_final_audit_report.md",
    ]
    for filename in expected:
        assert (audit_dir / filename).exists()

    not_executed = json.loads((audit_dir / "04_sources_used.json").read_text(encoding="utf-8"))
    assert not_executed["status"] == "NOT_EXECUTED"

    error_data = json.loads((audit_dir / "03_sources_extracted.json").read_text(encoding="utf-8"))
    assert error_data["status"] == "ERROR"

    report = (audit_dir / "11_final_audit_report.md").read_text(encoding="utf-8")
    assert "## Falha" in report
    assert "- [x] REPROVADO_NAO_INDEXAR" in report
    assert "- [x] REPROVADO_CORRIGIR_PIPELINE" in report
