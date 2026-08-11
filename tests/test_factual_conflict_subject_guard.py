"""Afirmacao sem sujeito nao vira conflito — por NENHUM dos dois caminhos.

`detect_conflicts` tem duas fontes independentes de conflito, e ate agora so uma
delas descartava afirmacao sem sujeito:

  * `_conflicts_between_claims` — duas afirmacoes do draft discordando entre si;
    ja tinha o guard.
  * `_conflicts_from_contradicting_evidence` — uma afirmacao que uma fonte
    recebida contradiz; NAO tinha.

O efeito visivel era o log `[FACTUAL_CONFLICT_DETECTED] subject=-`: o `-` nao era
um sujeito estranho, era `conflict.subject or '-'` imprimindo `None`. Um conflito
sem sujeito nao diz ao humano SOBRE O QUE as fontes discordam, que e a unica
informacao que faz o aviso valer alguma coisa.

Estes testes prendem os DOIS caminhos ao mesmo criterio. Testar so o caminho
corrigido deixaria o irmao livre para regredir sem ninguem notar.
"""

from __future__ import annotations

from app.factual import states as S
from app.factual.conflicts import detect_conflicts
from app.factual.models import ClaimEvidenceLink, FactualClaim, FactualEvidence


def _claim(display_text: str, *, subject, value: str, claim_id: str) -> FactualClaim:
    claim = FactualClaim(
        display_text=display_text,
        claim_type=S.CLAIM_DATE,
        normalized_subject=subject,
        predicate="estreia",
        normalized_value=value,
        is_material=True,
    )
    claim.claim_id = claim_id
    return claim


def _evidence(excerpt: str, *, evidence_id: str) -> FactualEvidence:
    evidence = FactualEvidence(
        source_url="https://variety.example/materia",
        excerpt=excerpt,
        source_domain="variety.example",
    )
    evidence.evidence_id = evidence_id
    return evidence


# -- caminho 1: duas afirmacoes do draft ---------------------------------------


def test_claims_without_subject_do_not_conflict_with_each_other():
    """Mesmo predicado, valores incompativeis, mas nenhum sujeito: nada a afirmar."""
    claims = [
        _claim("Estreia em 1 de maio de 2026.", subject=None, value="2026-05-01", claim_id="c1"),
        _claim("Estreia em 3 de junho de 2026.", subject=None, value="2026-06-03", claim_id="c2"),
    ]
    assert detect_conflicts(claims, [], []) == []


def test_claims_with_subject_still_conflict_with_each_other():
    """O guard descarta sujeito ausente, NAO o conflito legitimo."""
    claims = [
        _claim("Duna 3 estreia em 1 de maio de 2026.", subject="duna 3",
               value="2026-05-01", claim_id="c1"),
        _claim("Duna 3 estreia em 3 de junho de 2026.", subject="duna 3",
               value="2026-06-03", claim_id="c2"),
    ]
    conflicts = detect_conflicts(claims, [], [])
    assert len(conflicts) == 1
    assert conflicts[0].subject == "duna 3"


# -- caminho 2: fonte contradiz a afirmacao ------------------------------------


def test_contradicted_claim_without_subject_produces_no_conflict():
    """O caminho que FALTAVA o guard: evidencia contradizente + sujeito nulo."""
    claim = _claim("Estreia em 1 de maio de 2026.", subject=None,
                   value="2026-05-01", claim_id="c1")
    evidence = _evidence("A estreia foi adiada para junho de 2026.", evidence_id="e1")
    links = [ClaimEvidenceLink(claim_id="c1", evidence_id="e1", relation=S.CONTRADICTS)]

    assert detect_conflicts([claim], links, [evidence]) == []


def test_contradicted_claim_with_subject_still_produces_a_conflict():
    """Mesma montagem, com sujeito: o conflito continua sendo detectado e NOMEADO."""
    claim = _claim("Duna 3 estreia em 1 de maio de 2026.", subject="duna 3",
                   value="2026-05-01", claim_id="c1")
    evidence = _evidence("A estreia de Duna 3 foi adiada para junho de 2026.", evidence_id="e1")
    links = [ClaimEvidenceLink(claim_id="c1", evidence_id="e1", relation=S.CONTRADICTS)]

    conflicts = detect_conflicts([claim], links, [evidence])
    assert len(conflicts) == 1
    assert conflicts[0].subject == "duna 3"


def test_no_detected_conflict_ever_carries_a_null_subject():
    """A afirmacao geral, sobre a mistura dos dois caminhos de uma vez.

    Se um terceiro caminho de conflito for acrescentado sem o guard, este teste
    e o que falha — os dois acima so cobrem os caminhos que existem hoje.
    """
    claims = [
        _claim("Estreia em 1 de maio de 2026.", subject=None, value="2026-05-01", claim_id="c1"),
        _claim("Estreia em 3 de junho de 2026.", subject=None, value="2026-06-03", claim_id="c2"),
        _claim("Duna 3 estreia em 1 de maio de 2026.", subject="duna 3",
               value="2026-05-01", claim_id="c3"),
        _claim("Duna 3 estreia em 3 de junho de 2026.", subject="duna 3",
               value="2026-06-03", claim_id="c4"),
    ]
    evidence = _evidence("A estreia foi adiada.", evidence_id="e1")
    links = [ClaimEvidenceLink(claim_id="c1", evidence_id="e1", relation=S.CONTRADICTS)]

    conflicts = detect_conflicts(claims, links, [evidence])
    assert conflicts, "o cenario precisa produzir ao menos um conflito, senao nao prova nada"
    assert all(c.subject for c in conflicts), (
        f"conflito com sujeito nulo: {[(c.conflict_type, c.subject) for c in conflicts]}"
    )
