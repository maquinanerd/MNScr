"""Domain tests for the EditorialDraft contract."""

import json

import pytest

from app.editorial import (
    DRAFT_FAILED,
    DRAFT_GENERATED,
    DraftContent,
    DraftProvenance,
    EditorialDraft,
    EvidenceRecord,
    ForbiddenStateError,
    MediaCandidate,
    SourceReference,
    build_draft_id,
    canonical_json,
    dumps_pretty,
    stable_hash,
    validate_draft,
    validate_draft_status,
)
from app.editorial.models import OUTPUT_CONTRACT_VERSION


def _draft(**overrides):
    content = overrides.pop(
        "content",
        DraftContent(
            title="Cinerie confirma estreia da nova temporada",
            body_html="<p>Texto editorial com fatos.</p><h2>Contexto</h2><p>Mais contexto.</p>",
            subtitle="Um subtítulo editorial com tamanho razoável para o excerpt.",
            meta_description="Meta description factual com tamanho adequado para o CMS editorial.",
            slug_suggestion="nova-temporada-estreia",
            focus_keyphrase="nova temporada",
            related_keyphrases=["estreia", "temporada"],
            category_suggestions=["Séries"],
            tag_suggestions=["temporada", "estreia"],
        ),
    )
    sources = overrides.pop(
        "sources",
        [SourceReference(url="https://deadline.example/materia", name="Deadline", is_primary=True)],
    )
    provenance = overrides.pop(
        "provenance",
        DraftProvenance(input_hash="a" * 64, output_hash="b" * 64),
    )
    params = dict(
        draft_id="draft-test",
        article_id=42,
        content=content,
        provenance=provenance,
        sources=sources,
    )
    params.update(overrides)
    return EditorialDraft(**params)


class TestSerialization:
    def test_draft_serializes_to_valid_json(self):
        payload = dumps_pretty(_draft())
        parsed = json.loads(payload)
        assert parsed["status"] == DRAFT_GENERATED
        assert parsed["content"]["title"]
        assert parsed["provenance"]["output_contract_version"] == OUTPUT_CONTRACT_VERSION

    def test_canonical_json_is_sorted_and_compact(self):
        text = canonical_json({"b": 1, "a": 2})
        assert text == '{"a":2,"b":1}'

    def test_serialization_keeps_utf8(self):
        draft = _draft()
        assert "estreia" in dumps_pretty(draft)
        assert "\\u" not in dumps_pretty(draft)


class TestDeterminism:
    def test_same_content_same_hash(self):
        assert _draft().content_hash() == _draft().content_hash()

    def test_different_content_different_hash(self):
        other = _draft(
            content=DraftContent(title="Outro título completamente diferente", body_html="<p>Outro corpo.</p>")
        )
        assert _draft().content_hash() != other.content_hash()

    def test_hash_ignores_volatile_fields(self):
        first = stable_hash({"title": "x", "created_at": "2026-01-01T00:00:00Z"})
        second = stable_hash({"title": "x", "created_at": "2030-12-31T23:59:59Z"})
        assert first == second

    def test_draft_id_is_deterministic_from_event_key(self):
        a = build_draft_id(event_key="evt-1", revision=1)
        b = build_draft_id(event_key="evt-1", revision=1)
        assert a == b and a.startswith("draft-")

    def test_draft_id_changes_with_revision(self):
        assert build_draft_id(event_key="evt-1", revision=1) != build_draft_id(event_key="evt-1", revision=2)

    def test_draft_id_falls_back_to_article_and_url(self):
        a = build_draft_id(article_id=7, canonical_url="site.example/x", revision=1)
        b = build_draft_id(article_id=7, canonical_url="site.example/x", revision=1)
        c = build_draft_id(article_id=8, canonical_url="site.example/x", revision=1)
        assert a == b and a != c

    def test_draft_id_requires_some_identity(self):
        with pytest.raises(ValueError):
            build_draft_id(revision=1)


class TestStates:
    def test_generated_and_failed_are_allowed(self):
        assert validate_draft_status(DRAFT_GENERATED) == DRAFT_GENERATED
        assert validate_draft_status(DRAFT_FAILED) == DRAFT_FAILED

    @pytest.mark.parametrize("forbidden", ["PUBLISHED", "APPROVED", "PUBLICATION_READY", "LIVE", "SCHEDULED"])
    def test_forbidden_states_are_rejected(self, forbidden):
        with pytest.raises(ForbiddenStateError):
            validate_draft_status(forbidden)

    def test_draft_cannot_be_constructed_as_published(self):
        with pytest.raises(ForbiddenStateError):
            _draft(status="PUBLISHED")

    def test_unknown_state_is_rejected(self):
        with pytest.raises(ValueError):
            validate_draft_status("WHATEVER")


class TestValidation:
    def test_valid_draft_has_no_blocking_errors(self):
        assert validate_draft(_draft()) == []

    def test_empty_title_blocks(self):
        draft = _draft(content=DraftContent(title="", body_html="<p>corpo</p>"))
        assert "EMPTY_TITLE" in validate_draft(draft)

    def test_empty_body_blocks(self):
        draft = _draft(content=DraftContent(title="Título", body_html=""))
        assert "EMPTY_BODY" in validate_draft(draft)

    def test_missing_sources_blocks(self):
        assert "NO_SOURCES" in validate_draft(_draft(sources=[]))

    def test_missing_hash_blocks(self):
        draft = _draft(provenance=DraftProvenance(input_hash="", output_hash=""))
        errors = validate_draft(draft)
        assert "MISSING_INPUT_HASH" in errors and "MISSING_OUTPUT_HASH" in errors

    def test_wordpress_upload_url_blocks(self):
        draft = _draft(
            content=DraftContent(
                title="Título válido",
                body_html='<p>x</p><img src="https://site.example/wp-content/uploads/a.jpg">',
            )
        )
        assert "WORDPRESS_UPLOAD_URL_PRESENT" in validate_draft(draft)

    def test_wordpress_media_class_blocks(self):
        draft = _draft(
            content=DraftContent(title="Título válido", body_html='<img class="wp-image-123" src="https://x.example/a.jpg">')
        )
        assert "WORDPRESS_MEDIA_CLASS_PRESENT" in validate_draft(draft)

    def test_gutenberg_markup_blocks(self):
        draft = _draft(
            content=DraftContent(title="Título válido", body_html="<!-- wp:paragraph --><p>x</p><!-- /wp:paragraph -->")
        )
        assert "WORDPRESS_BLOCK_MARKUP_PRESENT" in validate_draft(draft)


class TestValueObjects:
    def test_media_candidate_defaults_to_unverified_and_unknown_rights(self):
        media = MediaCandidate(source_url="https://cdn.example/a.jpg")
        assert media.status == "unverified"
        assert media.license is None
        assert media.credit is None

    def test_media_candidate_rejects_unknown_status(self):
        with pytest.raises(ValueError):
            MediaCandidate(source_url="https://cdn.example/a.jpg", status="published")

    def test_evidence_rejects_unknown_status(self):
        with pytest.raises(ValueError):
            EvidenceRecord(evidence_id="e1", source_url="https://a.example", status="confirmed")

    def test_source_reference_requires_url(self):
        with pytest.raises(ValueError):
            SourceReference(url="")
