"""Extracao, linking, status, conflitos e cobertura factual.

Sem rede, sem RSS Prime, sem Gemini, sem Payload. A camada de IA e sempre
injetada como resposta ja obtida, nunca chamada.
"""

import json
import socket
from pathlib import Path

import pytest

from app.editorial import DraftContent, DraftProvenance, EditorialDraft, SourceReference
from app.factual import states as S
from app.factual.conflicts import detect_conflicts
from app.factual.coverage import (
    compute_coverage,
    compute_editorial_origin_key,
    count_editorial_origins,
    unsupported_claims_in_critical_locations,
)
from app.factual.errors import InvalidLinkError, InvalidModelResponseError
from app.factual.extraction import (
    extract_deterministic_claims,
    is_material_text,
    merge_claims,
    parse_claim_response,
    segment_draft,
)
from app.factual.matching import (
    apply_statuses,
    classify_relation,
    compute_claim_status,
    link_claims_to_evidence,
    validate_links,
)
from app.factual.models import ClaimEvidenceLink, FactualClaim, FactualEvidence
from app.factual_builder import build_evidence_from_sources, build_factual_assessment, infer_strength

FIXTURES = Path(__file__).parent / "fixtures" / "factual"
FILLER = " ".join(["palavra"] * 260)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _blocked(*args, **kwargs):
        raise AssertionError("Teste factual tentou acessar a rede")

    monkeypatch.setattr(socket, "socket", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)


def make_draft(title="Estudio confirma estreia em 29 de julho de 2026", body=None, **kw):
    return EditorialDraft(
        draft_id=kw.pop("draft_id", "draft-1"), article_id=1,
        content=DraftContent(
            title=title,
            body_html=body or f"<p>A serie foi renovada para uma segunda temporada.</p><p>{FILLER}</p>",
            subtitle=kw.pop("subtitle", "Resumo editorial proprio e distinto."),
            meta_description=kw.pop("meta", "Meta description com o fato principal."),
        ),
        provenance=DraftProvenance(input_hash="a" * 64, output_hash="b" * 64,
                                   input_contract_version="rss-prime-event-v1",
                                   prompt_version="p1"),
        event_key="evt-1", revision=1,
        sources=[SourceReference(url="https://variety.example/a", domain="variety.example",
                                 is_primary=True, extraction_status="OK")],
        **kw,
    )


def ev(url, excerpt, **kw):
    return FactualEvidence(source_url=url, excerpt=excerpt, **kw)


def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


# ===========================================================================
# Extracao de claims
# ===========================================================================


def test_segmentation_addresses_every_part_of_the_draft():
    locations = [s.location for s in segment_draft(make_draft())]
    assert "title" in locations
    assert "subtitle" in locations
    assert "meta_description" in locations
    assert "body:p:1" in locations


def test_date_claim_is_extracted():
    claims = extract_deterministic_claims(make_draft())
    dates = [c for c in claims if c.claim_type == S.CLAIM_DATE]
    assert dates and dates[0].normalized_value == "2026-07-29"


def test_number_claim_is_extracted():
    draft = make_draft(body=f"<p>O orcamento chega a R$ 2,5 milhoes.</p><p>{FILLER}</p>")
    numbers = [c for c in extract_deterministic_claims(draft) if c.claim_type == S.CLAIM_NUMBER]
    assert numbers and numbers[0].normalized_value == "BRL 2500000"


def test_percentage_claim_is_extracted():
    draft = make_draft(body=f"<p>A audiencia cresceu 35% na estreia.</p><p>{FILLER}</p>")
    numbers = [c for c in extract_deterministic_claims(draft) if c.claim_type == S.CLAIM_NUMBER]
    assert any(c.normalized_value == "35%" for c in numbers)


def test_currency_claim_keeps_its_currency():
    draft = make_draft(body=f"<p>A producao custou US$ 10 milhoes ao estudio.</p><p>{FILLER}</p>")
    numbers = [c for c in extract_deterministic_claims(draft) if c.claim_type == S.CLAIM_NUMBER]
    assert any(c.normalized_value == "USD 10000000" for c in numbers)


def test_quote_claim_is_extracted():
    draft = make_draft(body=f'<p>O diretor disse: "a producao esta encerrada por agora".</p><p>{FILLER}</p>')
    assert any(c.claim_type == S.CLAIM_QUOTE for c in extract_deterministic_claims(draft))


def test_attribution_claim_is_extracted():
    draft = make_draft(body=f"<p>Segundo Variety Studios, a estreia foi antecipada.</p><p>{FILLER}</p>")
    assert any(c.claim_type == S.CLAIM_ATTRIBUTION for c in extract_deterministic_claims(draft))


@pytest.mark.parametrize(
    "sentence, expected_type",
    [
        ("A serie foi renovada para uma segunda temporada.", S.CLAIM_STATUS),
        ("A producao foi cancelada pelo estudio responsavel.", S.CLAIM_STATUS),
        ("O filme estreia no proximo semestre nos cinemas.", S.CLAIM_RELEASE),
        ("O titulo ja esta disponivel no catalogo do servico.", S.CLAIM_AVAILABILITY),
    ],
)
def test_status_claims_are_extracted(sentence, expected_type):
    draft = make_draft(body=f"<p>{sentence}</p><p>{FILLER}</p>")
    assert any(c.claim_type == expected_type for c in extract_deterministic_claims(draft))


@pytest.mark.parametrize(
    "text",
    [
        "Clique aqui para saber mais sobre o assunto.",
        "Leia tambem a nossa cobertura completa.",
        "Inscreva-se na newsletter para nao perder nada.",
        "Na minha opiniao, este e o melhor filme do ano.",
        "Recomendamos assistir ainda nesta semana.",
    ],
)
def test_navigation_and_opinion_are_not_material(text):
    assert is_material_text(text) is False


def test_editorial_sentence_is_material():
    assert is_material_text("O estudio confirmou a estreia para 29 de julho de 2026.") is True


def test_duplicate_claims_are_merged_and_keep_both_locations():
    """A mesma afirmacao no titulo e no corpo continua sendo uma afirmacao."""
    draft = make_draft(
        title="Estreia confirmada para 29 de julho de 2026",
        body=f"<p>Estreia confirmada para 29 de julho de 2026.</p><p>{FILLER}</p>",
    )
    dates = [c for c in extract_deterministic_claims(draft) if c.claim_type == S.CLAIM_DATE]
    assert len(dates) == 1
    assert set(dates[0].draft_locations) >= {"title", "body:p:1"}


def test_claim_limit_is_respected():
    body = "".join(f"<p>O evento {i} acontece em 0{i%9+1}/07/2026.</p>" for i in range(40))
    claims = extract_deterministic_claims(make_draft(body=body), max_claims=5)
    assert len(claims) <= 5


# ===========================================================================
# Camada de IA validada
# ===========================================================================


def test_ai_claims_are_accepted_when_present_in_the_draft():
    draft = make_draft()
    claims, warnings = parse_claim_response(fixture("claim_extraction_response.json"), draft=draft)
    texts = [c.display_text for c in claims]
    assert "A serie foi renovada para uma segunda temporada" in texts


def test_ai_cannot_invent_a_claim_that_is_not_in_the_draft():
    """A guarda anti-fabricacao: o modelo so pode apontar para texto existente."""
    draft = make_draft()
    claims, warnings = parse_claim_response(fixture("claim_extraction_response.json"), draft=draft)
    assert "Este fato nunca apareceu no rascunho" not in [c.display_text for c in claims]
    assert "CLAIM_NOT_FOUND_IN_DRAFT" in warnings


@pytest.mark.parametrize(
    "raw", ["isto nao e json", "{quebrado", "[]", "12345", None]
)
def test_invalid_model_response_is_rejected(raw):
    """Resposta ininteligivel e descartada, nunca interpretada com tolerancia."""
    with pytest.raises(InvalidModelResponseError):
        parse_claim_response(raw, draft=make_draft())


def test_response_without_claims_field_is_rejected():
    with pytest.raises(InvalidModelResponseError):
        parse_claim_response({"resultados": []}, draft=make_draft())


def test_empty_claim_list_is_valid():
    claims, _ = parse_claim_response({"claims": []}, draft=make_draft())
    assert claims == []


def test_json_in_a_code_fence_is_accepted():
    draft = make_draft()
    fenced = '```json\n{"claims": []}\n```'
    claims, _ = parse_claim_response(fenced, draft=draft)
    assert claims == []


def test_claim_missing_required_fields_is_skipped():
    draft = make_draft()
    payload = {"claims": [{"display_text": "Estudio confirma estreia em 29 de julho de 2026"}]}
    claims, warnings = parse_claim_response(payload, draft=draft)
    assert claims == []
    assert any(w.startswith("CLAIM_MISSING_FIELDS") for w in warnings)


def test_unknown_claim_type_falls_back_to_other():
    draft = make_draft()
    payload = {"claims": [{
        "display_text": "Estudio confirma estreia em 29 de julho de 2026",
        "claim_type": "INVENTADO", "location": "title",
    }]}
    claims, warnings = parse_claim_response(payload, draft=draft)
    assert claims[0].claim_type == S.CLAIM_OTHER
    assert any(w.startswith("CLAIM_TYPE_UNKNOWN") for w in warnings)


def test_out_of_range_confidence_is_discarded():
    draft = make_draft()
    payload = {"claims": [{
        "display_text": "Estudio confirma estreia em 29 de julho de 2026",
        "claim_type": "DATE", "location": "title", "confidence": 5,
    }]}
    claims, warnings = parse_claim_response(payload, draft=draft)
    assert claims[0].confidence is None
    assert "CLAIM_CONFIDENCE_OUT_OF_RANGE" in warnings


def test_merge_prefers_the_deterministic_claim():
    deterministic = FactualClaim(display_text="Estreia em 2026", claim_type=S.CLAIM_DATE,
                                 normalized_value="2026", draft_locations=["title"])
    semantic = FactualClaim(display_text="Estreia em 2026", claim_type=S.CLAIM_DATE,
                            normalized_value="2026", draft_locations=["body:p:1"])
    merged = merge_claims([deterministic], [semantic])
    assert len(merged) == 1
    assert set(merged[0].draft_locations) == {"title", "body:p:1"}


# ===========================================================================
# Evidencias
# ===========================================================================


def test_evidence_is_built_from_received_sources():
    evidence = build_evidence_from_sources(fixture("sources_supported.json"))
    assert evidence
    assert all(e.source_url.startswith("https://") for e in evidence)


def test_evidence_skips_documents_without_a_url():
    assert build_evidence_from_sources([{"content": "texto sem url nenhuma aqui presente"}]) == []


def test_evidence_skips_very_short_passages():
    assert build_evidence_from_sources([{"url": "https://a.example/x", "content": "Curto."}]) == []


def test_primary_strength_requires_an_originating_signal():
    """Reputacao nao torna uma fonte primaria."""
    assert infer_strength("O estudio disse em comunicado oficial que a estreia mudou.") == S.PRIMARY
    assert infer_strength("A Variety informou que a estreia mudou de data neste mes.") == S.SECONDARY


def test_republication_signal_is_weak():
    assert infer_strength("Republicado de outro veiculo, o texto afirma que houve mudanca.") == S.WEAK


def test_evidence_excerpt_respects_the_configured_limit():
    evidence = build_evidence_from_sources(
        [{"url": "https://a.example/x", "content": "palavra " * 300}], max_excerpt_chars=120
    )
    assert all(len(e.excerpt) <= 121 for e in evidence)


# ===========================================================================
# Linking
# ===========================================================================


def test_matching_value_produces_support():
    claim = FactualClaim(display_text="Estreia em 29 de julho de 2026", claim_type=S.CLAIM_DATE,
                         normalized_subject="estudio", normalized_value="2026-07-29")
    item = ev("https://a.example/x", "O estudio confirmou a estreia para 29 de julho de 2026.")
    relation, _, rationale = classify_relation(claim, item)
    assert relation == S.SUPPORTS
    assert rationale


def test_same_subject_without_the_value_is_only_a_mention():
    """Falar do mesmo assunto nao confirma a afirmacao."""
    claim = FactualClaim(display_text="Estreia em 29 de julho de 2026", claim_type=S.CLAIM_DATE,
                         normalized_subject="estudio", normalized_value="2026-07-29")
    item = ev("https://a.example/x", "O estudio comentou o andamento da producao nesta semana.")
    relation, _, _ = classify_relation(claim, item)
    assert relation == S.MENTIONS


def test_divergent_date_produces_a_contradiction():
    claim = FactualClaim(display_text="Estreia em 29 de julho de 2026", claim_type=S.CLAIM_DATE,
                         normalized_subject="estudio", normalized_value="2026-07-29")
    item = ev("https://a.example/x", "O estudio remarcou a estreia para 05 de agosto de 2026.")
    relation, _, _ = classify_relation(claim, item)
    assert relation == S.CONTRADICTS


def test_divergent_number_produces_a_contradiction():
    claim = FactualClaim(display_text="Custa R$ 2,5 milhoes", claim_type=S.CLAIM_NUMBER,
                         normalized_subject="producao", normalized_value="BRL 2500000")
    item = ev("https://a.example/x", "A producao custa R$ 4,1 milhoes de acordo com o estudio.")
    relation, _, _ = classify_relation(claim, item)
    assert relation == S.CONTRADICTS


def test_unrelated_evidence_produces_no_link():
    claim = FactualClaim(display_text="Estreia em 2026", claim_type=S.CLAIM_DATE,
                         normalized_subject="estudio", normalized_value="2026")
    item = ev("https://a.example/x", "Uma receita de bolo de cenoura com cobertura de chocolate.")
    relation, _, _ = classify_relation(claim, item)
    assert relation is None


def test_links_referencing_an_unknown_claim_are_rejected():
    item = ev("https://a.example/x", "O estudio confirmou a estreia para 29 de julho de 2026.")
    link = ClaimEvidenceLink(claim_id="nao-existe", evidence_id=item.evidence_id,
                             relation=S.SUPPORTS, rationale="motivo")
    with pytest.raises(InvalidLinkError):
        validate_links([link], [], [item])


def test_links_referencing_unknown_evidence_are_rejected():
    claim = FactualClaim(display_text="Estreia em 2026", claim_type=S.CLAIM_DATE)
    link = ClaimEvidenceLink(claim_id=claim.claim_id, evidence_id="nao-existe",
                             relation=S.SUPPORTS, rationale="motivo")
    with pytest.raises(InvalidLinkError):
        validate_links([link], [claim], [])


def test_links_without_a_rationale_are_dropped():
    claim = FactualClaim(display_text="Estreia em 2026", claim_type=S.CLAIM_DATE)
    item = ev("https://a.example/x", "O estudio confirmou a estreia para 29 de julho de 2026.")
    link = ClaimEvidenceLink(claim_id=claim.claim_id, evidence_id=item.evidence_id,
                             relation=S.SUPPORTS, rationale=None)
    assert validate_links([link], [claim], [item]) == []


def test_contradictions_survive_the_per_claim_cap():
    """Perder uma contradicao transformaria conflito em concordancia."""
    claim = FactualClaim(display_text="Estreia em 29 de julho de 2026", claim_type=S.CLAIM_DATE,
                         normalized_subject="estudio", normalized_value="2026-07-29")
    evidence = [
        ev(f"https://a{i}.example/x",
           "O estudio confirmou a estreia para 29 de julho de 2026 conforme anunciado.")
        for i in range(6)
    ] + [ev("https://z.example/x", "O estudio remarcou a estreia para 05 de agosto de 2026.")]
    links = link_claims_to_evidence([claim], evidence, max_evidence_per_claim=2)
    assert any(link.relation == S.CONTRADICTS for link in links)


# ===========================================================================
# Status
# ===========================================================================


def make_claim(**kw):
    base = dict(display_text="Estreia em 29 de julho de 2026", claim_type=S.CLAIM_DATE,
                normalized_subject="estudio", normalized_value="2026-07-29")
    base.update(kw)
    return FactualClaim(**base)


def status_for(relations):
    claim = make_claim()
    evidence = [ev(f"https://s{i}.example/x", f"trecho de fonte numero {i} com texto suficiente.")
                for i, _ in enumerate(relations)]
    links = [
        ClaimEvidenceLink(claim_id=claim.claim_id, evidence_id=item.evidence_id,
                          relation=relation, rationale="motivo")
        for item, relation in zip(evidence, relations)
    ]
    return compute_claim_status(claim, links, {e.evidence_id: e for e in evidence})


def test_support_yields_supported():
    assert status_for([S.SUPPORTS]) == S.SUPPORTED


def test_partial_support_yields_partially_supported():
    assert status_for([S.PARTIALLY_SUPPORTS]) == S.PARTIALLY_SUPPORTED


def test_no_links_yields_unsupported():
    assert status_for([]) == S.UNSUPPORTED


def test_contradiction_yields_conflicting():
    assert status_for([S.CONTRADICTS]) == S.CONFLICTING


def test_only_mentions_yields_unverified():
    """As fontes citam o assunto mas nao confirmam nem negam."""
    assert status_for([S.MENTIONS, S.MENTIONS]) == S.UNVERIFIED


def test_conflict_prevails_over_support():
    assert status_for([S.SUPPORTS, S.SUPPORTS, S.CONTRADICTS]) == S.CONFLICTING


def test_apply_statuses_marks_review():
    claim = make_claim()
    result = apply_statuses([claim], [], [])
    assert result[0].status == S.UNSUPPORTED
    assert result[0].requires_review is True


# ===========================================================================
# Conflitos
# ===========================================================================


def two_claims(type_, value_a, value_b, predicate="date"):
    return [
        make_claim(claim_type=type_, normalized_value=value_a, predicate=predicate,
                   display_text=f"Afirmacao com valor {value_a}"),
        make_claim(claim_type=type_, normalized_value=value_b, predicate=predicate,
                   display_text=f"Afirmacao com valor {value_b}"),
    ]


def test_date_conflict_is_detected():
    conflicts = detect_conflicts(two_claims(S.CLAIM_DATE, "2026-07-29", "2026-08-05"), [], [])
    assert any(c.conflict_type == S.DATE_MISMATCH for c in conflicts)


def test_number_conflict_is_detected():
    claims = two_claims(S.CLAIM_NUMBER, "35%", "40%", predicate="amount")
    conflicts = detect_conflicts(claims, [], [])
    assert any(c.conflict_type == S.NUMBER_MISMATCH for c in conflicts)


def test_status_conflict_is_detected():
    claims = two_claims(S.CLAIM_STATUS, "renewed", "cancelled", predicate="state")
    conflicts = detect_conflicts(claims, [], [])
    assert any(c.conflict_type == S.STATUS_MISMATCH for c in conflicts)


def test_attribution_conflict_is_detected():
    claims = two_claims(S.CLAIM_ATTRIBUTION, "pessoa a", "pessoa b", predicate="attributed_to")
    conflicts = detect_conflicts(claims, [], [])
    assert any(c.conflict_type == S.ATTRIBUTION_MISMATCH for c in conflicts)


def test_identity_conflict_is_detected():
    claims = two_claims(S.CLAIM_IDENTITY, "ator a", "ator b", predicate="plays")
    conflicts = detect_conflicts(claims, [], [])
    assert any(c.conflict_type == S.IDENTITY_MISMATCH for c in conflicts)


def test_identical_values_produce_no_conflict():
    assert detect_conflicts(two_claims(S.CLAIM_DATE, "2026-07-29", "2026-07-29"), [], []) == []


def test_different_text_alone_is_not_a_conflict():
    """Reescrever o mesmo fato nao e divergencia."""
    claims = [
        make_claim(display_text="O estudio confirmou a estreia", normalized_value="2026-07-29"),
        make_claim(display_text="A estreia foi confirmada pelo estudio", normalized_value="2026-07-29"),
    ]
    assert detect_conflicts(claims, [], []) == []


def test_partial_and_full_date_do_not_conflict():
    assert detect_conflicts(two_claims(S.CLAIM_DATE, "2026-07", "2026-07-29"), [], []) == []


def test_claims_without_a_subject_do_not_produce_a_conflict():
    """Sem sujeito nao da para distinguir dois fatos de um fato sobre coisas diferentes."""
    claims = [
        make_claim(normalized_subject=None, normalized_value="2026-07-29"),
        make_claim(normalized_subject=None, normalized_value="2026-08-05",
                   display_text="Outra afirmacao"),
    ]
    assert detect_conflicts(claims, [], []) == []


def test_conflicts_are_never_resolved_automatically():
    conflicts = detect_conflicts(two_claims(S.CLAIM_DATE, "2026-07-29", "2026-08-05"), [], [])
    assert all(c.resolution_status == S.UNRESOLVED for c in conflicts)
    assert all(c.requires_review for c in conflicts)


def test_conflict_preserves_every_competing_value():
    conflicts = detect_conflicts(two_claims(S.CLAIM_DATE, "2026-07-29", "2026-08-05"), [], [])
    values = conflicts[0].values
    assert "2026-07-29" in values and "2026-08-05" in values


# ===========================================================================
# Cobertura e independencia editorial
# ===========================================================================


def claims_with(statuses):
    return [
        make_claim(status=status, display_text=f"Afirmacao numero {i}", normalized_value=f"v{i}")
        for i, status in enumerate(statuses)
    ]


def test_full_coverage():
    coverage = compute_coverage(claims_with([S.SUPPORTED] * 4), [])
    assert coverage.coverage_ratio == 1.0


def test_partial_support_counts_as_half():
    coverage = compute_coverage(claims_with([S.SUPPORTED, S.PARTIALLY_SUPPORTED]), [])
    assert coverage.coverage_ratio == 0.75


def test_zero_claims_means_zero_coverage_and_review():
    coverage = compute_coverage([], [])
    assert coverage.coverage_ratio == 0.0
    assert coverage.review_required is True


def test_unsupported_claim_forces_review():
    coverage = compute_coverage(claims_with([S.SUPPORTED, S.UNSUPPORTED]), [])
    assert coverage.review_required is True


def test_coverage_counts_each_status():
    coverage = compute_coverage(
        claims_with([S.SUPPORTED, S.PARTIALLY_SUPPORTED, S.UNSUPPORTED,
                     S.CONFLICTING, S.UNVERIFIED]), []
    )
    assert coverage.total_material_claims == 5
    assert coverage.supported_material_claims == 1
    assert coverage.conflicting_material_claims == 1


def test_non_material_claims_are_excluded():
    claims = claims_with([S.SUPPORTED]) + [make_claim(is_material=False, status=S.UNSUPPORTED,
                                                      display_text="Chamada promocional")]
    assert compute_coverage(claims, []).total_material_claims == 1


def test_coverage_is_deterministic():
    claims = claims_with([S.SUPPORTED, S.UNSUPPORTED])
    assert compute_coverage(claims, []).coverage_ratio == compute_coverage(claims, []).coverage_ratio


def test_duplicate_urls_from_one_outlet_count_as_one_origin():
    """Duas URLs do mesmo veiculo nao sao duas confirmacoes."""
    evidence = [
        ev("https://variety.example/a", "Primeiro trecho da materia com tamanho suficiente."),
        ev("https://variety.example/b", "Segundo trecho da materia com tamanho suficiente."),
    ]
    assert count_editorial_origins(evidence) == 1


def test_distinct_outlets_count_separately():
    evidence = [
        ev("https://variety.example/a", "Primeiro trecho da materia com tamanho suficiente."),
        ev("https://deadline.example/b", "Segundo trecho da materia com tamanho suficiente."),
    ]
    assert count_editorial_origins(evidence) == 2


def test_regional_editions_fold_into_one_origin():
    assert (
        compute_editorial_origin_key(source_url="https://br.variety.example/x")
        == compute_editorial_origin_key(source_url="https://variety.example/y")
    )


def test_declared_republication_folds_into_the_original_origin():
    """Republicacao nao e confirmacao independente."""
    republished = compute_editorial_origin_key(
        source_url="https://portal.example/copia",
        republished_from="https://variety.example/original",
    )
    assert republished == compute_editorial_origin_key(source_url="https://variety.example/x")


def test_unsupported_in_critical_locations_is_identified():
    claims = [
        make_claim(status=S.UNSUPPORTED, draft_locations=["title"]),
        make_claim(status=S.UNSUPPORTED, draft_locations=["body:p:9"],
                   display_text="Afirmacao periferica", normalized_value="x"),
    ]
    offenders = unsupported_claims_in_critical_locations(claims)
    assert len(offenders) == 1
    assert offenders[0].draft_locations == ["title"]


# ===========================================================================
# Assessment ponta a ponta
# ===========================================================================


def test_supported_draft_reaches_high_coverage():
    assessment = build_factual_assessment(
        make_draft(), fixture("sources_supported.json"), mode=S.MODE_DETERMINISTIC
    )
    assert assessment.coverage.coverage_ratio >= 0.5
    assert assessment.conflicts == []


def test_conflicting_sources_produce_conflicts():
    assessment = build_factual_assessment(
        make_draft(), fixture("sources_conflicting.json"), mode=S.MODE_DETERMINISTIC
    )
    assert assessment.conflicts
    assert all(c.resolution_status == S.UNRESOLVED for c in assessment.conflicts)


def test_assessment_without_sources_marks_everything_unsupported():
    assessment = build_factual_assessment(make_draft(), [], mode=S.MODE_DETERMINISTIC)
    assert all(c.status == S.UNSUPPORTED for c in assessment.material_claims)
    assert "NO_EVIDENCE_AVAILABLE" in assessment.warnings


def test_assessment_is_deterministic():
    a = build_factual_assessment(make_draft(), fixture("sources_supported.json"),
                                 mode=S.MODE_DETERMINISTIC)
    b = build_factual_assessment(make_draft(), fixture("sources_supported.json"),
                                 mode=S.MODE_DETERMINISTIC)
    assert a.assessment_hash == b.assessment_hash
    assert a.assessment_id == b.assessment_id


def test_deterministic_mode_never_uses_the_model_response():
    assessment = build_factual_assessment(
        make_draft(), fixture("sources_supported.json"),
        mode=S.MODE_DETERMINISTIC, claim_response=fixture("claim_extraction_response.json"),
    )
    assert "CLAIM_RESPONSE_IGNORED_IN_DETERMINISTIC_MODE" in assessment.warnings
    assert "DETERMINISTIC_MODE_ONLY_PATTERN_CLAIMS" in assessment.warnings


def test_hybrid_mode_uses_the_validated_model_response():
    assessment = build_factual_assessment(
        make_draft(), fixture("sources_supported.json"),
        mode=S.MODE_HYBRID, claim_response=fixture("claim_extraction_response.json"),
    )
    assert "CLAIM_NOT_FOUND_IN_DRAFT" in assessment.warnings


def test_a_rejected_model_response_does_not_break_the_assessment():
    """Resposta inutil e descartada; os claims deterministicos continuam valendo."""
    assessment = build_factual_assessment(
        make_draft(), fixture("sources_supported.json"),
        mode=S.MODE_HYBRID, claim_response="isto nao e json",
    )
    assert assessment.claims
    assert any(w.startswith("CLAIM_RESPONSE_REJECTED") for w in assessment.warnings)


def test_assessment_never_stores_the_whole_article():
    long_source = [{"url": "https://a.example/x", "content": "frase longa da materia. " * 400}]
    assessment = build_factual_assessment(make_draft(), long_source, mode=S.MODE_DETERMINISTIC)
    for item in assessment.evidence:
        assert len(item.excerpt) <= 501
