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
from app.editorial.models import OUTPUT_CONTRACT_VERSION, own_cms_upload_urls


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


class TestWordPressUploadGuardIsDomainAware:
    """A guarda de upload pergunta de QUEM é a URL, não que forma ela tem.

    O ciclo de 05/08 12:03 matou 3 de 4 drafts com WORDPRESS_UPLOAD_URL_PRESENT
    porque ScreenRant, ComicBook e MovieWeb são WordPress e servem imagem por
    `/wp-content/uploads/`. A regra bloqueava dado de terceiro por parecer com o
    defeito que ela caça — o vazamento do nosso próprio legado.
    """

    OURS = ["cinerie-legado.example"]

    def _draft_with(self, body_html):
        return _draft(content=DraftContent(title="Título válido", body_html=body_html))

    @pytest.fixture(autouse=True)
    def _fixed_own_domains(self, monkeypatch):
        """A lista vem de configuração; o teste não depende do .env da máquina."""
        import app.config

        monkeypatch.setattr(app.config, "OWN_CMS_DOMAINS", self.OURS)

    @pytest.mark.parametrize(
        "host",
        ["screenrant.com", "comicbook.com", "movieweb.com"],
        ids=["screenrant", "comicbook", "movieweb"],
    )
    def test_upload_url_of_external_wordpress_source_does_not_block(self, host):
        draft = self._draft_with(
            f'<p>x</p><figure><img src="https://{host}/wp-content/uploads/2026/08/a.jpg" alt=""></figure>'
        )
        assert validate_draft(draft) == []

    def test_upload_url_of_our_own_wordpress_still_blocks(self):
        draft = self._draft_with(
            '<p>x</p><img src="https://cinerie-legado.example/wp-content/uploads/2026/08/a.jpg">'
        )
        assert "WORDPRESS_UPLOAD_URL_PRESENT" in validate_draft(draft)

    def test_subdomain_of_our_domain_still_blocks(self):
        draft = self._draft_with(
            '<p>x</p><img src="https://cdn.cinerie-legado.example/wp-content/uploads/a.jpg">'
        )
        assert "WORDPRESS_UPLOAD_URL_PRESENT" in validate_draft(draft)

    def test_site_relative_upload_url_blocks_because_it_resolves_to_us(self):
        """Sem host, a URL resolve contra o site que renderiza — nós."""
        draft = self._draft_with('<p>x</p><img src="/wp-content/uploads/2026/08/a.jpg">')
        assert "WORDPRESS_UPLOAD_URL_PRESENT" in validate_draft(draft)

    def test_our_url_blocks_even_ao_lado_de_uma_da_fonte(self):
        draft = self._draft_with(
            '<img src="https://screenrant.com/wp-content/uploads/a.jpg">'
            '<img src="https://cinerie-legado.example/wp-content/uploads/b.jpg">'
        )
        assert "WORDPRESS_UPLOAD_URL_PRESENT" in validate_draft(draft)


class TestOwnCmsUploadUrls:
    """Unidade da decisão de host, sem passar pelo draft inteiro."""

    OURS = ["cinerie-legado.example"]

    @pytest.mark.parametrize(
        "url",
        [
            "https://cinerie-legado.example/wp-content/uploads/a.jpg",
            "http://www.cinerie-legado.example/wp-content/uploads/a.jpg",
            "//cinerie-legado.example/wp-content/uploads/a.jpg",
            "cinerie-legado.example/wp-content/uploads/a.jpg",
            "/wp-content/uploads/a.jpg",
        ],
        ids=["https", "www", "sem-esquema", "host-nu", "relativa"],
    )
    def test_our_urls_are_found(self, url):
        assert own_cms_upload_urls(f'<img src="{url}">', self.OURS) == [url]

    @pytest.mark.parametrize(
        "url",
        [
            "https://screenrant.com/wp-content/uploads/a.jpg",
            "https://static0.comicbook.com/wp-content/uploads/a.jpg",
            "screenrant.com/wp-content/uploads/a.jpg",
        ],
        ids=["https", "subdominio-de-terceiro", "host-nu"],
    )
    def test_third_party_urls_are_not_found(self, url):
        assert own_cms_upload_urls(f'<img src="{url}">', self.OURS) == []

    def test_a_url_that_only_mentions_our_name_is_not_ours(self):
        """`cinerie-legado.example.evil.example` não é subdomínio nosso."""
        body = '<img src="https://cinerie-legado.example.evil.example/wp-content/uploads/a.jpg">'
        assert own_cms_upload_urls(body, self.OURS) == []

    def test_empty_own_domain_list_blocks_nothing(self):
        """Sem lista a guarda fica cega — documentado em .env.example."""
        body = '<img src="https://cinerie-legado.example/wp-content/uploads/a.jpg">'
        assert own_cms_upload_urls(body, []) == []


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
