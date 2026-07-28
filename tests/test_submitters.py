"""Submitter tests. Nothing here may touch the network."""

import json
import socket

import pytest

from app.editorial import DraftContent, DraftProvenance, EditorialDraft, SourceReference
from app.submitters import (
    LocalDraftSubmitter,
    OutputModeError,
    PayloadConfig,
    PayloadDraftSubmitter,
    SubmitterNotEnabledError,
    build_submitter,
    resolve_output_mode,
)
from app.submitters.payload import NOT_ENABLED_MESSAGE


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Any socket use inside a submitter test is a hard failure."""

    def _blocked(*args, **kwargs):
        raise AssertionError("Teste tentou acessar a rede")

    monkeypatch.setattr(socket, "socket", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)


def _draft(output_hash="b" * 64, title="Título do draft editorial", draft_id="draft-fixture"):
    return EditorialDraft(
        draft_id=draft_id,
        article_id=1,
        content=DraftContent(title=title, body_html="<p>corpo editorial</p>"),
        provenance=DraftProvenance(input_hash="a" * 64, output_hash=output_hash),
        sources=[SourceReference(url="https://variety.example/materia", is_primary=True)],
    )


class TestLocalDraftSubmitter:
    def test_writes_artifact(self, tmp_path):
        submitter = LocalDraftSubmitter(output_dir=tmp_path)
        result = submitter.submit(_draft())
        assert result.success is True
        assert result.status == "WRITTEN"
        assert result.destination == "local"
        assert (tmp_path / "draft-fixture.json").exists()

    def test_artifact_is_valid_utf8_json(self, tmp_path):
        submitter = LocalDraftSubmitter(output_dir=tmp_path)
        submitter.submit(_draft(title="Estreia com acentuação: ação, coração"))
        payload = json.loads((tmp_path / "draft-fixture.json").read_text(encoding="utf-8"))
        assert payload["content"]["title"].startswith("Estreia com acentuação")
        assert payload["status"] == "DRAFT_GENERATED"

    def test_creates_directory(self, tmp_path):
        target = tmp_path / "deep" / "nested"
        LocalDraftSubmitter(output_dir=target).submit(_draft())
        assert (target / "draft-fixture.json").exists()

    def test_is_idempotent_for_identical_hash(self, tmp_path):
        submitter = LocalDraftSubmitter(output_dir=tmp_path)
        first = submitter.submit(_draft())
        second = submitter.submit(_draft())
        assert first.status == "WRITTEN"
        assert second.status == "UNCHANGED"
        assert second.success is True
        assert len(list(tmp_path.glob("*.json"))) == 1

    def test_conflicting_hash_does_not_overwrite(self, tmp_path):
        submitter = LocalDraftSubmitter(output_dir=tmp_path)
        submitter.submit(_draft(output_hash="b" * 64, title="Título original"))
        original = (tmp_path / "draft-fixture.json").read_text(encoding="utf-8")

        result = submitter.submit(_draft(output_hash="c" * 64, title="Título divergente"))

        assert result.success is False
        assert result.status == "CONFLICT"
        assert (tmp_path / "draft-fixture.json").read_text(encoding="utf-8") == original
        assert "Título divergente" in (tmp_path / result.artifact_path.rsplit("\\", 1)[-1].rsplit("/", 1)[-1]).read_text(
            encoding="utf-8"
        )

    def test_atomic_write_leaves_no_temp_files(self, tmp_path):
        LocalDraftSubmitter(output_dir=tmp_path).submit(_draft())
        assert [p.name for p in tmp_path.iterdir() if p.suffix == ".tmp"] == []


class TestPayloadDraftSubmitter:
    def test_disabled_submitter_reports_disabled_and_does_not_transmit(self):
        submitter = PayloadDraftSubmitter(PayloadConfig(enabled=False))
        result = submitter.submit(_draft())
        assert result.success is False
        assert result.status == "DISABLED"
        assert result.error == NOT_ENABLED_MESSAGE

    def test_enabled_submitter_fails_explicitly(self):
        submitter = PayloadDraftSubmitter(
            PayloadConfig(enabled=True, base_url="https://cms.example", api_token="tok")
        )
        with pytest.raises(SubmitterNotEnabledError) as exc:
            submitter.submit(_draft())
        assert str(exc.value) == NOT_ENABLED_MESSAGE

    def test_document_is_cms_neutral_and_never_published(self):
        document = PayloadDraftSubmitter(PayloadConfig()).build_document(_draft())
        assert document["_status"] == "draft"
        assert document["mnscrStatus"] == "DRAFT_GENERATED"
        assert "wp_post_id" not in document
        assert "featured_media" not in document

    def test_config_reports_missing_values_when_enabled(self):
        issues = PayloadConfig(enabled=True).validate()
        assert any("PAYLOAD_BASE_URL" in issue for issue in issues)
        assert any("PAYLOAD_API_TOKEN" in issue for issue in issues)


class TestOutputMode:
    def test_local_is_default(self, monkeypatch):
        monkeypatch.delenv("MNSCR_OUTPUT_MODE", raising=False)
        assert resolve_output_mode() == "local"

    def test_wordpress_is_rejected(self):
        with pytest.raises(OutputModeError) as exc:
            resolve_output_mode("wordpress")
        assert str(exc.value) == "WordPress output is disabled in MNScr."

    def test_unknown_mode_is_rejected(self):
        with pytest.raises(OutputModeError):
            resolve_output_mode("ftp")

    def test_build_submitter_local(self, tmp_path):
        submitter = build_submitter("local", local_dir=str(tmp_path))
        assert isinstance(submitter, LocalDraftSubmitter)

    def test_build_submitter_payload_is_blocked(self, monkeypatch):
        monkeypatch.setenv("PAYLOAD_ENABLED", "true")
        monkeypatch.setenv("PAYLOAD_BASE_URL", "https://cms.example")
        monkeypatch.setenv("PAYLOAD_API_TOKEN", "tok")
        with pytest.raises(OutputModeError) as exc:
            build_submitter("payload")
        assert str(exc.value) == NOT_ENABLED_MESSAGE

    def test_build_submitter_wordpress_is_blocked(self):
        with pytest.raises(OutputModeError):
            build_submitter("wordpress")
