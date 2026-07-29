"""Dominio factual: modelos, vocabulario, hashes e normalizacao.

Nenhum teste aqui acessa rede, RSS Prime, Gemini ou Payload.
"""

import json
import socket

import pytest

from app.factual import states as S
from app.factual.errors import InvalidClaimError, InvalidEvidenceError
from app.factual.models import (
    DEFAULT_MAX_EVIDENCE_EXCERPT_CHARS,
    MAX_SOURCE_SENTENCE_CHARS,
    ClaimEvidenceLink,
    FactualAssessment,
    FactualClaim,
    FactualConflict,
    FactualCoverage,
    FactualEvidence,
)
from app.factual.normalization import (
    dates_conflict,
    normalize_date,
    normalize_for_comparison,
    normalize_number,
    normalize_text,
    numbers_conflict,
    token_overlap,
    truncate_at_safe_boundary,
)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _blocked(*args, **kwargs):
        raise AssertionError("Teste factual tentou acessar a rede")

    monkeypatch.setattr(socket, "socket", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)


def claim(**kw):
    base = dict(display_text="Estreia confirmada para 2026", claim_type=S.CLAIM_DATE,
                normalized_value="2026", draft_id="draft-1")
    base.update(kw)
    return FactualClaim(**base)


def evidence(**kw):
    base = dict(source_url="https://variety.example/a",
                excerpt="O estudio confirmou que a estreia acontece em 2026 conforme anunciado.")
    base.update(kw)
    return FactualEvidence(**base)


# ---------------------------------------------------------------------------
# Vocabulario
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", sorted(S.CLAIM_TYPES))
def test_valid_claim_types_are_accepted(value):
    assert S.validate_claim_type(value) == value


@pytest.mark.parametrize("value", sorted(S.CLAIM_STATUSES))
def test_valid_claim_statuses_are_accepted(value):
    assert S.validate_claim_status(value) == value


@pytest.mark.parametrize("value", sorted(S.EVIDENCE_RELATIONS))
def test_valid_relations_are_accepted(value):
    assert S.validate_relation(value) == value


@pytest.mark.parametrize("value", sorted(S.EVIDENCE_STRENGTHS))
def test_valid_strengths_are_accepted(value):
    assert S.validate_strength(value) == value


@pytest.mark.parametrize("value", sorted(S.CONFLICT_TYPES))
def test_valid_conflict_types_are_accepted(value):
    assert S.validate_conflict_type(value) == value


@pytest.mark.parametrize(
    "validator, bad",
    [
        (S.validate_claim_type, "INVENTADO"),
        (S.validate_claim_status, "TALVEZ"),
        (S.validate_relation, "PROVES"),
        (S.validate_strength, "STRONGEST"),
        (S.validate_conflict_type, "QUALQUER"),
        (S.validate_mode, "magico"),
    ],
)
def test_unknown_vocabulary_is_rejected(validator, bad):
    with pytest.raises(ValueError):
        validator(bad)


def test_unresolved_is_the_only_resolution_status():
    """A MS-4 nao resolve conflito automaticamente."""
    assert S.RESOLUTION_STATUSES == {"UNRESOLVED"}
    with pytest.raises(ValueError):
        S.validate_resolution_status("RESOLVED_AUTOMATICALLY")


def test_mentions_is_not_a_supporting_relation():
    """Citar o mesmo assunto nao confirma a afirmacao."""
    assert S.MENTIONS not in S.SUPPORTING_RELATIONS
    assert S.SUPPORTING_RELATIONS == {S.SUPPORTS, S.PARTIALLY_SUPPORTS}


# ---------------------------------------------------------------------------
# FactualClaim
# ---------------------------------------------------------------------------


def test_claim_requires_display_text():
    with pytest.raises(InvalidClaimError):
        claim(display_text="   ")


def test_claim_id_is_deterministic():
    assert claim().claim_id == claim().claim_id


def test_claim_hash_is_deterministic():
    assert claim().claim_hash == claim().claim_hash


def test_claim_id_changes_with_the_value():
    assert claim(normalized_value="2026").claim_id != claim(normalized_value="2027").claim_id


def test_claim_id_ignores_the_location():
    """A mesma afirmacao no titulo e no corpo continua sendo uma afirmacao."""
    a = claim(draft_locations=["title"])
    b = claim(draft_locations=["body:p:3"])
    assert a.claim_id == b.claim_id


def test_claim_hash_excludes_the_status():
    """Status e um veredito sobre a afirmacao, nao parte do que ela diz."""
    a = claim(status=S.UNVERIFIED)
    b = claim(status=S.SUPPORTED)
    assert a.claim_hash == b.claim_hash


@pytest.mark.parametrize("value", [-0.1, 1.5, 2])
def test_claim_confidence_outside_zero_to_one_is_rejected(value):
    with pytest.raises(InvalidClaimError):
        claim(confidence=value)


@pytest.mark.parametrize("value", [0.0, 0.5, 1.0])
def test_claim_confidence_within_range_is_accepted(value):
    assert claim(confidence=value).confidence == value


def test_claim_source_sentence_is_truncated():
    long_sentence = "palavra " * 200
    result = claim(source_sentence=long_sentence)
    assert len(result.source_sentence) <= MAX_SOURCE_SENTENCE_CHARS + 1
    assert "SOURCE_SENTENCE_TRUNCATED" in result.warnings


def test_other_claim_type_records_its_own_uncertainty():
    """Nao forcar classificacao quando nao ha certeza."""
    assert "CLAIM_TYPE_UNCERTAIN" in claim(claim_type=S.CLAIM_OTHER).warnings


@pytest.mark.parametrize("status", sorted(S.STATUSES_REQUIRING_REVIEW))
def test_uncertain_statuses_require_review(status):
    assert claim(status=status).requires_review is True


def test_claim_in_a_critical_location_is_flagged():
    assert claim(draft_locations=["title"]).is_in_critical_location
    assert not claim(draft_locations=["body:p:9"]).is_in_critical_location


def test_claim_round_trips():
    original = claim(draft_locations=["title"], confidence=0.8)
    restored = FactualClaim.from_dict(original.to_dict())
    assert restored.claim_id == original.claim_id
    assert restored.claim_hash == original.claim_hash
    assert restored.draft_locations == ["title"]


# ---------------------------------------------------------------------------
# FactualEvidence
# ---------------------------------------------------------------------------


def test_evidence_requires_a_url():
    with pytest.raises(InvalidEvidenceError):
        FactualEvidence(source_url="", excerpt="algum trecho valido da fonte recebida")


def test_evidence_requires_an_excerpt():
    with pytest.raises(InvalidEvidenceError):
        FactualEvidence(source_url="https://variety.example/a", excerpt="   ")


def test_evidence_id_is_deterministic():
    assert evidence().evidence_id == evidence().evidence_id


def test_evidence_hash_is_deterministic():
    assert evidence().evidence_hash == evidence().evidence_hash


def test_evidence_excerpt_is_truncated_at_the_limit():
    """Evidencia e ponteiro para a fonte, nunca copia dela."""
    whole_article = "frase da materia. " * 200
    item = evidence(excerpt=whole_article, max_excerpt_chars=200)
    assert len(item.excerpt) <= 201
    assert "EVIDENCE_EXCERPT_TRUNCATED" in item.warnings


def test_default_excerpt_limit_is_bounded():
    assert DEFAULT_MAX_EVIDENCE_EXCERPT_CHARS == 500


def test_evidence_derives_its_domain_from_the_url():
    assert evidence(source_url="https://www.variety.example/x").source_domain == "variety.example"


def test_evidence_never_invents_a_licence_or_credit():
    item = evidence()
    payload = item.to_dict()
    assert "license" not in payload
    assert "credit" not in payload


@pytest.mark.parametrize("strength", sorted(S.EVIDENCE_STRENGTHS))
def test_evidence_accepts_every_valid_strength(strength):
    assert evidence(strength=strength).strength == strength


def test_evidence_round_trips():
    original = evidence(strength=S.PRIMARY)
    restored = FactualEvidence.from_dict(original.to_dict())
    assert restored.evidence_id == original.evidence_id
    assert restored.strength == S.PRIMARY


def test_evidence_can_be_viewed_as_the_legacy_record():
    """Compatibilidade com o EvidenceRecord da MS-1."""
    record = evidence().to_evidence_record()
    assert record.evidence_id == evidence().evidence_id
    assert record.source_url == "https://variety.example/a"


# ---------------------------------------------------------------------------
# ClaimEvidenceLink
# ---------------------------------------------------------------------------


def test_link_id_is_deterministic():
    a = ClaimEvidenceLink(claim_id="c1", evidence_id="e1", relation=S.SUPPORTS)
    b = ClaimEvidenceLink(claim_id="c1", evidence_id="e1", relation=S.SUPPORTS)
    assert a.link_id == b.link_id


def test_link_id_changes_with_the_relation():
    a = ClaimEvidenceLink(claim_id="c1", evidence_id="e1", relation=S.SUPPORTS)
    b = ClaimEvidenceLink(claim_id="c1", evidence_id="e1", relation=S.CONTRADICTS)
    assert a.link_id != b.link_id


def test_link_rationale_is_truncated():
    link = ClaimEvidenceLink(
        claim_id="c1", evidence_id="e1", relation=S.SUPPORTS, rationale="motivo " * 200
    )
    assert len(link.rationale) <= 241


def test_only_supporting_relations_count_as_support():
    assert ClaimEvidenceLink(claim_id="c", evidence_id="e", relation=S.SUPPORTS).is_supporting
    assert not ClaimEvidenceLink(claim_id="c", evidence_id="e", relation=S.MENTIONS).is_supporting


# ---------------------------------------------------------------------------
# FactualConflict
# ---------------------------------------------------------------------------


def conflict(**kw):
    base = dict(conflict_type=S.DATE_MISMATCH, claim_ids=["c1", "c2"],
                subject="estudio", values=["2026-07-29", "2026-08-05"])
    base.update(kw)
    return FactualConflict(**base)


def test_conflict_id_is_deterministic():
    assert conflict().conflict_id == conflict().conflict_id


def test_conflict_id_ignores_value_order():
    a = conflict(values=["2026-07-29", "2026-08-05"])
    b = conflict(values=["2026-08-05", "2026-07-29"])
    assert a.conflict_id == b.conflict_id


def test_conflict_always_requires_review():
    """Divergencia entre fontes e sempre decisao humana."""
    assert conflict(requires_review=False).requires_review is True


def test_conflict_is_always_unresolved():
    assert conflict().resolution_status == S.UNRESOLVED


def test_conflict_preserves_both_values():
    """Nenhuma versao e escolhida silenciosamente."""
    result = conflict()
    assert "2026-07-29" in result.values
    assert "2026-08-05" in result.values


@pytest.mark.parametrize("conflict_type", sorted(S.CRITICAL_CONFLICT_TYPES))
def test_critical_conflict_types_are_flagged(conflict_type):
    assert conflict(conflict_type=conflict_type).is_critical


def test_non_critical_conflict_is_not_flagged():
    assert not conflict(conflict_type=S.SOURCE_DISAGREEMENT).is_critical


# ---------------------------------------------------------------------------
# FactualAssessment
# ---------------------------------------------------------------------------


def assessment(**kw):
    base = dict(draft_id="draft-1", claims=[claim()], evidence=[evidence()],
                event_key="evt-1", revision=1)
    base.update(kw)
    return FactualAssessment(**base)


def test_assessment_requires_a_draft_id():
    with pytest.raises(InvalidClaimError):
        FactualAssessment(draft_id="")


def test_assessment_hash_is_deterministic():
    assert assessment().assessment_hash == assessment().assessment_hash


def test_assessment_id_is_deterministic():
    assert assessment().assessment_id == assessment().assessment_id


def test_assessment_hash_excludes_the_timestamp():
    a = assessment(created_at="2020-01-01T00:00:00Z")
    b = assessment(created_at="2030-12-31T23:59:59Z")
    assert a.assessment_hash == b.assessment_hash


def test_assessment_hash_changes_when_a_status_changes():
    a = assessment(claims=[claim(status=S.SUPPORTED)])
    b = assessment(claims=[claim(status=S.UNSUPPORTED)])
    assert a.assessment_hash != b.assessment_hash


def test_assessment_hash_ignores_claim_order():
    c1, c2 = claim(display_text="Primeira afirmacao"), claim(display_text="Segunda afirmacao")
    assert assessment(claims=[c1, c2]).assessment_hash == assessment(claims=[c2, c1]).assessment_hash


def test_assessment_round_trips():
    original = assessment()
    restored = FactualAssessment.from_dict(original.to_dict())
    assert restored.assessment_hash == original.assessment_hash
    assert restored.assessment_id == original.assessment_id


def test_assessment_serializes_to_json():
    json.dumps(assessment().to_dict())


def test_assessment_version_is_v1():
    assert assessment().version == S.ASSESSMENT_VERSION_V1


def test_assessment_never_uses_publication_vocabulary():
    rendered = json.dumps(assessment().to_dict())
    for forbidden in ("APPROVED", "PUBLISHED", "PUBLICATION_READY"):
        assert forbidden not in rendered


# ---------------------------------------------------------------------------
# Normalizacao
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("29 de julho de 2026", "2026-07-29"),
        ("29/07/2026", "2026-07-29"),
        ("2026-07-29", "2026-07-29"),
        ("July 29, 2026", "2026-07-29"),
    ],
)
def test_full_dates_normalize(raw, expected):
    assert normalize_date(raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [("julho de 2026", "2026-07"), ("2026-07", "2026-07"), ("2026", "2026")],
)
def test_partial_dates_keep_their_precision(raw, expected):
    """Nao inventar dia ou mes ausente."""
    assert normalize_date(raw) == expected


@pytest.mark.parametrize("raw", ["ontem", "em breve", "", None, "sem data"])
def test_undatable_text_returns_none(raw):
    assert normalize_date(raw) is None


def test_partial_and_full_dates_do_not_conflict():
    """2026-07 e menos preciso, nao divergente."""
    assert dates_conflict("2026-07", "2026-07-29") is False


def test_different_dates_conflict():
    assert dates_conflict("2026-07-29", "2026-08-05") is True


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("1,5 milhao", "1500000"),
        ("1,5 milhoes", "1500000"),
        ("R$ 2,5 milhoes", "BRL 2500000"),
        ("US$ 10 milhoes", "USD 10000000"),
        ("35%", "35%"),
        ("1.234.567", "1234567"),
    ],
)
def test_numbers_normalize(raw, expected):
    assert normalize_number(raw) == expected


def test_multiplier_is_not_truncated_to_mil():
    """'milhoes' nao pode ser lido como 'mil': erro de mil vezes."""
    assert normalize_number("R$ 2,5 milhoes") == "BRL 2500000"


def test_currency_is_never_converted():
    """Taxa de cambio e um fato que o MNScr nao recebeu."""
    assert normalize_number("US$ 10") == "USD 10"
    assert normalize_number("R$ 10") == "BRL 10"
    assert numbers_conflict("USD 10", "BRL 10") is False


def test_units_are_preserved():
    assert normalize_number("90 minutos") == "90 minutos"


def test_numbers_with_the_same_unit_conflict():
    assert numbers_conflict("35%", "40%") is True
    assert numbers_conflict("35%", "35%") is False


def test_text_normalization_collapses_whitespace_and_unicode():
    assert normalize_text("  Estreia   confirmada  ") == "Estreia confirmada"
    assert normalize_for_comparison("Estreia Confirmada!") == "estreia confirmada"


def test_comparison_normalization_strips_accents():
    assert normalize_for_comparison("Produção") == normalize_for_comparison("Producao")


def test_truncation_reports_whether_it_cut():
    short, cut = truncate_at_safe_boundary("texto curto", 100)
    assert (short, cut) == ("texto curto", False)
    long_text, cut = truncate_at_safe_boundary("palavra " * 50, 30)
    assert cut is True and len(long_text) <= 31


def test_token_overlap_is_bounded():
    assert token_overlap("a b c", "a b c") == 1.0
    assert token_overlap("a b c", "x y z") == 0.0
    assert 0.0 < token_overlap("estreia confirmada 2026", "estreia em 2026") < 1.0


def test_coverage_defaults_require_review():
    """Um draft que nao afirma nada verificavel nao esta bem coberto."""
    assert FactualCoverage().review_required is True
    assert FactualCoverage().coverage_ratio == 0.0
