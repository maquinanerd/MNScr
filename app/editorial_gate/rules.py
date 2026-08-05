"""The rules the Editorial Gate applies to a draft.

Every rule is a pure function of ``(draft, policy, context)`` returning an
:class:`EditorialRuleResult`. No I/O, no clock, no network — that is what makes
the gate deterministic and exhaustively testable.

Rules reuse the checks that already exist (``validate_draft``, the WordPress
markup patterns, ``TRUSTED_SINGLE_SOURCES``, the editorial validator's depth
metric) instead of restating them. A second copy of a regex is a second thing
that can drift.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, Final, List, Optional
from urllib.parse import urlparse

from app.editorial.models import (
    _WP_BLOCK_PATTERN,
    _WP_IMAGE_CLASS_PATTERN,
    EditorialDraft,
    own_cms_upload_urls,
)
from app.editorial.states import (
    EVIDENCE_CONFLICTING,
    EVIDENCE_UNSUPPORTED,
    EVIDENCE_UNVERIFIED,
    MEDIA_UNVERIFIED,
)

from .models import (
    SEVERITY_BLOCKING,
    SEVERITY_INFO,
    SEVERITY_WARNING,
    EditorialRuleResult,
)
from .policy import EditorialGatePolicy

RULE_VERSION: Final[str] = "1"

LEGACY_CONTRACT_VERSION: Final[str] = "legacy-feed-input-v0"
V1_CONTRACT_VERSION: Final[str] = "rss-prime-event-v1"

#: Extraction statuses that mean "we got usable article text".
SUCCESSFUL_EXTRACTION_STATUSES: Final[frozenset[str]] = frozenset(
    {"OK", "ARTICLE_BODY_OK", "SUCCESS", "EXTRACTED"}
)

#: Statuses that mean the source contributed something, but imperfectly.
PARTIAL_EXTRACTION_STATUSES: Final[frozenset[str]] = frozenset(
    {"PARTIAL", "TOO_SHORT", "PAYWALLED", "BROKEN_ENCODING", "NAVIGATION_NOISE"}
)


# --- Shared helpers --------------------------------------------------------


def _text_of(html: str) -> str:
    """Plain text of a body, reusing the pipeline's own stripper."""
    from app.html_utils import _plain_text_fragment

    return _plain_text_fragment(html or "")


def _word_count(html: str) -> int:
    text = _text_of(html)
    return len([w for w in text.split() if w.strip()])


def _domain_of(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    try:
        host = (urlparse(str(url)).hostname or "").lower()
    except ValueError:
        return None
    if not host:
        return None
    return host[4:] if host.startswith("www.") else host


def _distinct_domains(draft: EditorialDraft) -> List[str]:
    domains = []
    for source in draft.sources:
        domain = source.domain or _domain_of(source.url)
        if domain and domain not in domains:
            domains.append(domain)
    return sorted(domains)


def _result(
    rule_code: str,
    severity: str,
    triggered: bool,
    message: str = "",
    *,
    evidence: Optional[List[Any]] = None,
    affected_fields: Optional[List[str]] = None,
    remediation: Optional[str] = None,
    metrics: Optional[Dict[str, Any]] = None,
) -> EditorialRuleResult:
    return EditorialRuleResult(
        rule_code=rule_code,
        rule_version=RULE_VERSION,
        severity=severity,
        triggered=triggered,
        message=message,
        evidence=evidence or [],
        affected_fields=affected_fields or [],
        remediation=remediation,
        metrics=metrics or {},
    )


# ===========================================================================
# BLOCKING RULES
# ===========================================================================


def rule_missing_title(draft, policy, context) -> EditorialRuleResult:
    title = (draft.content.title or "").strip()
    return _result(
        "GATE_MISSING_TITLE", SEVERITY_BLOCKING, not title,
        "Draft sem titulo utilizavel." if not title else "Titulo presente.",
        evidence=[] if title else ["content.title vazio ou somente espacos"],
        affected_fields=["content.title"],
        remediation="Regerar o titulo editorial antes de qualquer submissao.",
        metrics={"title_length": len(title)},
    )


def rule_missing_body(draft, policy, context) -> EditorialRuleResult:
    words = _word_count(draft.content.body_html)
    empty = words == 0
    return _result(
        "GATE_MISSING_BODY", SEVERITY_BLOCKING, empty,
        "Corpo sem texto editorial util apos sanitizacao." if empty else "Corpo presente.",
        evidence=[] if not empty else ["content.body_html sem texto apos remocao de markup"],
        affected_fields=["content.body_html"],
        remediation="Reprocessar a redacao: o corpo nao tem conteudo aproveitavel.",
        metrics={"body_word_count": words},
    )


def rule_missing_sources(draft, policy, context) -> EditorialRuleResult:
    valid = [s for s in draft.sources if (s.url or "").strip()]
    return _result(
        "GATE_MISSING_SOURCES", SEVERITY_BLOCKING, not valid,
        "Draft sem nenhuma fonte valida." if not valid else f"{len(valid)} fonte(s) registrada(s).",
        evidence=[] if valid else ["sources vazio"],
        affected_fields=["sources"],
        remediation="Um draft sem fonte nao e verificavel; descartar ou reingerir o acontecimento.",
        metrics={"source_count": len(valid)},
    )


def rule_no_successful_extraction(draft, policy, context) -> EditorialRuleResult:
    """Every source failed extraction.

    Only fires when there *are* sources — a draft with no sources at all is
    already reported by GATE_MISSING_SOURCES, and two blocking rules for one
    cause makes the verdict harder to read, not safer.
    """
    if not draft.sources:
        return _result(
            "GATE_NO_SUCCESSFUL_EXTRACTION", SEVERITY_BLOCKING, False,
            "Sem fontes para avaliar extracao.", metrics={"successful_sources": 0},
        )
    successful = [
        s for s in draft.sources
        if str(s.extraction_status or "").upper() in SUCCESSFUL_EXTRACTION_STATUSES
    ]
    # An absent status is treated as usable: the legacy path does not always
    # record one, and inventing a failure would block valid drafts.
    unknown = [s for s in draft.sources if not (s.extraction_status or "").strip()]
    usable = successful or unknown
    triggered = not usable
    return _result(
        "GATE_NO_SUCCESSFUL_EXTRACTION", SEVERITY_BLOCKING, triggered,
        "Nenhuma fonte teve extracao bem-sucedida." if triggered else
        f"{len(usable)} fonte(s) com conteudo aproveitavel.",
        evidence=[
            f"{_domain_of(s.url) or s.url}: {s.extraction_status}" for s in draft.sources
        ] if triggered else [],
        affected_fields=["sources"],
        remediation="Reprocessar a extracao das fontes antes de submeter.",
        metrics={"successful_sources": len(usable), "total_sources": len(draft.sources)},
    )


_REQUIRED_PROVENANCE: Final[tuple[str, ...]] = (
    "input_contract_version",
    "input_hash",
    "output_hash",
    "pipeline_version",
)


def rule_missing_provenance(draft, policy, context) -> EditorialRuleResult:
    missing = [
        name for name in _REQUIRED_PROVENANCE
        if not str(getattr(draft.provenance, name, "") or "").strip()
    ]
    return _result(
        "GATE_MISSING_PROVENANCE", SEVERITY_BLOCKING, bool(missing),
        f"Proveniencia incompleta: {', '.join(missing)}." if missing else "Proveniencia completa.",
        evidence=[f"provenance.{name} ausente" for name in missing],
        affected_fields=[f"provenance.{name}" for name in missing],
        remediation="Sem proveniencia o draft nao e auditavel; reprocessar.",
        metrics={"missing_provenance_fields": len(missing)},
    )


def rule_missing_prompt_version(draft, policy, context) -> EditorialRuleResult:
    """``prompt_version`` is a warning, not a blocker.

    A draft can legitimately be produced without an AI step (replay of a stored
    payload, deterministic paths), and blocking those would be wrong. It is
    still worth flagging: without it, a bad prompt cannot be traced to the
    drafts it produced.
    """
    value = str(getattr(draft.provenance, "prompt_version", "") or "").strip()
    return _result(
        "GATE_MISSING_PROMPT_VERSION", SEVERITY_WARNING, not value,
        "prompt_version ausente: o draft nao pode ser rastreado ate a versao do prompt."
        if not value else "prompt_version registrada.",
        evidence=[] if value else ["provenance.prompt_version vazio"],
        affected_fields=["provenance.prompt_version"],
        remediation="Registrar PROMPT_VERSION no caminho que gerou este draft.",
        metrics={"has_prompt_version": bool(value)},
    )


#: Chaves de CMS de terceiro recusadas em QUALQUER profundidade do draft.
#:
#: A comparacao ignora underscore, hifen e caixa: `post_status`, `postStatus` e
#: `POST-STATUS` sao a mesma tentativa.
_FORBIDDEN_CMS_KEYS: Final[frozenset[str]] = frozenset(
    {
        "yoastmeta", "yoast", "yoastwpseotitle", "yoastwpseometadesc",
        "yoastwpseofocuskw", "yoastwpseocanonical", "yoastnewskeywords",
        "yoastwpseoopengraphtitle", "yoastwpseoopengraphdescription",
        "yoastwpseotwittertitle", "yoastwpseotwitterdescription",
        "poststatus", "wppost", "wppostid", "wordpress", "featuredmedia",
        "rankmath", "aioseo", "canonical", "canonicalurl", "robots", "noindex",
        "indexstatus", "jsonld", "schemajson", "publisher", "hreflang", "sitemap",
    }
)

_KEY_SEPARATORS: Final[re.Pattern[str]] = re.compile(r"[_-]")


def _find_cms_keys(payload: Any, depth: int = 0) -> List[str]:
    """Chaves de CMS de terceiro, em qualquer nivel.

    Varre CHAVES, e nao o `str()` do dicionario inteiro. A diferenca importa: a
    varredura anterior achava "yoast" tambem dentro de um VALOR, e uma materia
    que citasse o plugin no proprio texto era bloqueada por falar sobre ele.
    """
    if depth > 12 or payload is None:
        return []
    found: List[str] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            if _KEY_SEPARATORS.sub("", str(key).lower()) in _FORBIDDEN_CMS_KEYS:
                found.append(str(key))
            found.extend(_find_cms_keys(value, depth + 1))
    elif isinstance(payload, (list, tuple)):
        for item in payload:
            found.extend(_find_cms_keys(item, depth + 1))
    return found


def rule_forbidden_wordpress_markup(draft, policy, context) -> EditorialRuleResult:
    body = draft.content.body_html or ""
    found: List[str] = []
    # Só a URL de upload do NOSSO CMS: a mesma URL vinda da fonte (que também
    # roda WordPress) é dado de terceiro, não vazamento nosso.
    for url in own_cms_upload_urls(body):
        found.append(f"URL /wp-content/uploads/ de dominio proprio: {url}")
    if _WP_IMAGE_CLASS_PATTERN.search(body):
        found.append("classe wp-image-* no corpo")
    if _WP_BLOCK_PATTERN.search(body):
        found.append("comentario de bloco Gutenberg <!-- wp: -->")
    for key in dict.fromkeys(_find_cms_keys(draft.to_dict())):
        found.append(f"campo de CMS de terceiro no draft: '{key}'")
    return _result(
        "GATE_FORBIDDEN_WORDPRESS_MARKUP", SEVERITY_BLOCKING, bool(found),
        "Markup ou identificador WordPress presente no draft." if found else
        "Draft neutro em relacao a CMS.",
        evidence=found,
        affected_fields=["content.body_html"],
        remediation="O draft precisa ser neutro de CMS; limpar o markup herdado da fonte.",
        metrics={"wordpress_signals": len(found)},
    )


_UNSAFE_PATTERNS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    ("tag <script>", re.compile(r"<\s*script\b", re.IGNORECASE)),
    ("tag <form>", re.compile(r"<\s*form\b", re.IGNORECASE)),
    ("tag <object>", re.compile(r"<\s*object\b", re.IGNORECASE)),
    ("tag <embed>", re.compile(r"<\s*embed\b", re.IGNORECASE)),
    ("handler on*=", re.compile(r"<[^>]+\son[a-z]+\s*=", re.IGNORECASE)),
    ("URL javascript:", re.compile(r"javascript\s*:", re.IGNORECASE)),
    ("tag <base>", re.compile(r"<\s*base\b", re.IGNORECASE)),
)

#: Embeds the current pipeline already allows (YouTube et al. survive
#: ``strip_credits_and_normalize_youtube``). Anything else is unsafe.
_ALLOWED_IFRAME_HOSTS: Final[tuple[str, ...]] = (
    "youtube.com", "youtube-nocookie.com", "youtu.be",
    "player.vimeo.com", "open.spotify.com",
)
_IFRAME_RE: Final[re.Pattern[str]] = re.compile(
    r"<\s*iframe\b[^>]*\bsrc\s*=\s*[\"']([^\"']+)[\"']", re.IGNORECASE
)
_BARE_IFRAME_RE: Final[re.Pattern[str]] = re.compile(r"<\s*iframe\b", re.IGNORECASE)


def rule_unsafe_html(draft, policy, context) -> EditorialRuleResult:
    body = draft.content.body_html or ""
    found: List[str] = []
    for label, pattern in _UNSAFE_PATTERNS:
        if pattern.search(body):
            found.append(label)

    iframe_srcs = _IFRAME_RE.findall(body)
    for src in iframe_srcs:
        host = _domain_of(src) or ""
        if not any(allowed in host for allowed in _ALLOWED_IFRAME_HOSTS):
            found.append(f"iframe nao permitido: {host or 'origem desconhecida'}")
    if _BARE_IFRAME_RE.search(body) and not iframe_srcs:
        found.append("iframe sem src identificavel")

    return _result(
        "GATE_UNSAFE_HTML", SEVERITY_BLOCKING, bool(found),
        "HTML inseguro sobreviveu a sanitizacao." if found else "HTML sanitizado.",
        evidence=found,
        affected_fields=["content.body_html"],
        remediation="Remover o elemento inseguro; embeds so sao permitidos na allowlist atual.",
        metrics={"unsafe_signals": len(found)},
    )


def rule_unsupported_topic(draft, policy, context) -> EditorialRuleResult:
    from app.config import BLOCKED_CATEGORY_NAMES, BLOCKED_TOPICS

    blocked = {t.lower() for t in BLOCKED_TOPICS} | {c.lower() for c in BLOCKED_CATEGORY_NAMES}
    candidates: List[str] = []
    event = context.get("input_event")
    if event is not None and getattr(event, "topic", None):
        candidates.append(str(event.topic))
    candidates.extend(str(c) for c in (draft.content.category_suggestions or []))

    hits = sorted({c for c in candidates if c.strip().lower() in blocked})
    return _result(
        "GATE_UNSUPPORTED_TOPIC", SEVERITY_BLOCKING, bool(hits),
        f"Assunto bloqueado editorialmente: {', '.join(hits)}." if hits else
        "Assunto aceito pela politica editorial.",
        evidence=[f"topico bloqueado: {h}" for h in hits],
        affected_fields=["content.category_suggestions"],
        remediation="Games e demais assuntos bloqueados nao entram no Cinerie.",
        metrics={"blocked_topic_hits": len(hits)},
    )


def rule_blocking_errors_present(draft, policy, context) -> EditorialRuleResult:
    errors = list(draft.blocking_errors or [])
    return _result(
        "GATE_BLOCKING_ERRORS_PRESENT", SEVERITY_BLOCKING, bool(errors),
        f"O draft carrega {len(errors)} erro(s) bloqueante(s) da validacao tecnica."
        if errors else "Nenhum erro bloqueante herdado.",
        evidence=errors,
        affected_fields=["blocking_errors"],
        remediation="Resolver os codigos originais listados na evidencia.",
        metrics={"blocking_error_count": len(errors)},
    )


def rule_invalid_revision_context(draft, policy, context) -> EditorialRuleResult:
    """The draft's revision identity must agree with its provenance.

    The gate never repairs this: an inconsistency means the draft and the event
    it claims to come from disagree, and picking one silently would destroy the
    audit trail.
    """
    provenance = draft.provenance
    problems: List[str] = []

    prov_revision = getattr(provenance, "input_revision", None)
    if prov_revision is not None and int(prov_revision) != int(draft.revision):
        problems.append(
            f"revision do draft ({draft.revision}) != input_revision ({prov_revision})"
        )

    prov_event = getattr(provenance, "input_event_key", None)
    if prov_event and draft.event_key and str(prov_event) != str(draft.event_key):
        problems.append(f"event_key do draft difere de input_event_key ({prov_event})")

    if int(draft.revision) < 1:
        problems.append(f"revision invalida: {draft.revision}")

    stored = context.get("stored_event")
    if stored is not None:
        if getattr(stored, "revision", None) is not None and int(stored.revision) != int(draft.revision):
            problems.append(
                f"revision do evento persistido ({stored.revision}) != revision do draft"
            )
        if getattr(stored, "event_key", None) and draft.event_key and \
                str(stored.event_key) != str(draft.event_key):
            problems.append("event_key do evento persistido difere do draft")

    return _result(
        "GATE_INVALID_REVISION_CONTEXT", SEVERITY_BLOCKING, bool(problems),
        "Contexto de revisao inconsistente." if problems else "Contexto de revisao coerente.",
        evidence=problems,
        affected_fields=["revision", "provenance.input_revision", "event_key"],
        remediation="Nao ajustar o evento para a regra passar; investigar a origem da divergencia.",
        metrics={"revision": int(draft.revision), "inconsistencies": len(problems)},
    )


# ===========================================================================
# WARNING RULES
# ===========================================================================


def rule_legacy_input_contract(draft, policy, context) -> EditorialRuleResult:
    version = str(getattr(draft.provenance, "input_contract_version", "") or "")
    legacy = version == LEGACY_CONTRACT_VERSION
    return _result(
        "GATE_LEGACY_INPUT_CONTRACT", SEVERITY_WARNING, legacy,
        "Draft originado do feed legado: hash local, revisao 1 e sem cursor."
        if legacy else "Draft originado de contrato versionado.",
        evidence=[f"input_contract_version={version}"] if legacy else [],
        affected_fields=["provenance.input_contract_version"],
        remediation="Nao e defeito: some quando o RSS Prime emitir rss-prime-event-v1.",
        metrics={"input_contract_version": version},
    )


def rule_single_untrusted_source(draft, policy, context) -> EditorialRuleResult:
    sources = [s for s in draft.sources if (s.url or "").strip()]
    if len(sources) != 1:
        return _result(
            "GATE_SINGLE_UNTRUSTED_SOURCE", SEVERITY_WARNING, False,
            "Nao e caso de fonte unica.", metrics={"source_count": len(sources)},
        )
    domain = sources[0].domain or _domain_of(sources[0].url)
    trusted = policy.is_trusted_domain(domain)
    return _result(
        "GATE_SINGLE_UNTRUSTED_SOURCE", SEVERITY_WARNING, not trusted,
        f"Fonte unica fora da allowlist: {domain}." if not trusted else
        f"Fonte unica confiavel: {domain}.",
        evidence=[f"dominio unico: {domain}"] if not trusted else [],
        affected_fields=["sources"],
        remediation="Decisao editorial humana: publicar com fonte unica nao-allowlisted.",
        metrics={"source_count": 1, "domain": domain or "", "trusted": trusted},
    )


def rule_source_diversity_low(draft, policy, context) -> EditorialRuleResult:
    sources = [s for s in draft.sources if (s.url or "").strip()]
    domains = _distinct_domains(draft)
    minimum = int(policy.threshold("min_distinct_source_domains", 2))
    # Only meaningful when the draft claims more than one source: a genuine
    # single-source draft is GATE_SINGLE_UNTRUSTED_SOURCE's business.
    triggered = len(sources) >= 2 and len(domains) < minimum
    return _result(
        "GATE_SOURCE_DIVERSITY_LOW", SEVERITY_WARNING, triggered,
        f"{len(sources)} fontes, mas apenas {len(domains)} dominio(s) distinto(s)."
        if triggered else f"{len(domains)} dominio(s) distinto(s).",
        evidence=[f"dominios: {', '.join(domains)}"] if triggered else [],
        affected_fields=["sources"],
        remediation="Fontes do mesmo veiculo nao constituem confirmacao independente.",
        metrics={"source_count": len(sources), "distinct_domains": len(domains)},
    )


def rule_extraction_warnings(draft, policy, context) -> EditorialRuleResult:
    partial = [
        s for s in draft.sources
        if str(s.extraction_status or "").upper() in PARTIAL_EXTRACTION_STATUSES
    ]
    usable = [
        s for s in draft.sources
        if str(s.extraction_status or "").upper() in SUCCESSFUL_EXTRACTION_STATUSES
        or not (s.extraction_status or "").strip()
    ]
    triggered = bool(partial) and bool(usable)
    return _result(
        "GATE_EXTRACTION_WARNINGS", SEVERITY_WARNING, triggered,
        f"{len(partial)} fonte(s) com extracao parcial." if triggered else
        "Extracao sem avisos.",
        evidence=[
            f"{_domain_of(s.url) or s.url}: {s.extraction_status}" for s in partial
        ] if triggered else [],
        affected_fields=["sources"],
        remediation="Conferir se o trecho faltante muda o sentido da materia.",
        metrics={"partial_sources": len(partial)},
    )


def _assessment_of(draft, context):
    """The factual assessment for this draft, from the draft or the context."""
    return getattr(draft, "factual_assessment", None) or context.get("factual_assessment")


def rule_evidence_missing(draft, policy, context) -> EditorialRuleResult:
    """Material claims exist but nothing sustains them.

    Since MS-4 this reads the FactualAssessment. Without one it falls back to
    the older EvidenceRecord list, so a draft produced before the factual layer
    is still evaluated instead of silently passing.
    """
    assessment = _assessment_of(draft, context)
    if assessment is None:
        missing = not draft.evidence
        return _result(
            "GATE_EVIDENCE_MISSING", SEVERITY_WARNING, missing,
            "Nenhuma evidencia associada ao draft." if missing else
            f"{len(draft.evidence)} registro(s) de evidencia.",
            evidence=["evidence vazio; avaliacao factual ausente"] if missing else [],
            affected_fields=["evidence"],
            remediation="Verificar por que a avaliacao factual nao foi produzida.",
            metrics={"evidence_count": len(draft.evidence), "has_assessment": False},
        )

    material = assessment.coverage.total_material_claims
    linked = len([link for link in assessment.links if link.is_supporting])
    triggered = material > 0 and linked == 0
    return _result(
        "GATE_EVIDENCE_MISSING", SEVERITY_WARNING, triggered,
        f"{material} afirmacao(oes) material(is) sem nenhuma evidencia associada."
        if triggered else f"{linked} vinculo(s) de suporte para {material} afirmacao(oes).",
        evidence=[f"material_claims={material} evidencias_de_suporte=0"] if triggered else [],
        affected_fields=["factual_assessment"],
        remediation="Nao fabricar evidencia: verificar a extracao das fontes recebidas.",
        metrics={"material_claims": material, "supporting_links": linked, "has_assessment": True},
    )


_UNCERTAIN_EVIDENCE: Final[frozenset[str]] = frozenset(
    {EVIDENCE_CONFLICTING, EVIDENCE_UNSUPPORTED, EVIDENCE_UNVERIFIED}
)


def rule_evidence_conflict(draft, policy, context) -> EditorialRuleResult:
    """Sources disagree. Recorded here, resolved by a person."""
    assessment = _assessment_of(draft, context)
    if assessment is None:
        uncertain = [e for e in draft.evidence if str(e.status or "") in _UNCERTAIN_EVIDENCE]
        return _result(
            "GATE_EVIDENCE_CONFLICT", SEVERITY_WARNING, bool(uncertain),
            f"{len(uncertain)} evidencia(s) em conflito ou nao verificadas." if uncertain else
            "Evidencias sem conflito registrado.",
            evidence=[f"{e.evidence_id}: {e.status}" for e in uncertain],
            affected_fields=["evidence"],
            remediation="O gate nao escolhe versao: a divergencia e decisao editorial humana.",
            metrics={"uncertain_evidence": len(uncertain), "has_assessment": False},
        )

    conflicting_claims = assessment.claims_with_status("CONFLICTING")
    conflicts = list(assessment.conflicts)
    triggered = bool(conflicting_claims or conflicts)
    return _result(
        "GATE_EVIDENCE_CONFLICT", SEVERITY_WARNING, triggered,
        f"{len(conflicts)} conflito(s) factual(is) e {len(conflicting_claims)} "
        f"afirmacao(oes) contraditada(s)." if triggered else "Sem divergencia entre fontes.",
        evidence=[
            f"{c.conflict_type}: {c.subject or '-'} -> {' | '.join(c.values[:2])}"
            for c in conflicts[:5]
        ],
        affected_fields=["factual_assessment"],
        remediation="O gate nao escolhe versao: a divergencia e decisao editorial humana.",
        metrics={
            "conflict_count": len(conflicts),
            "conflicting_claims": len(conflicting_claims),
            "has_assessment": True,
        },
    )


# --- MS-4: regras factuais -------------------------------------------------


def rule_material_claim_unsupported(draft, policy, context) -> EditorialRuleResult:
    """A material assertion in a loud place that no received source sustains.

    Only critical locations block — headline, subtitle, meta, lead. An
    unsupported aside in the tenth paragraph is a warning elsewhere, not a
    reason to stop the draft.

    UNSUPPORTED means "no source we received sustains this", never "this is
    false".
    """
    if not bool(policy.threshold("blockUnsupportedMaterialClaimsInCriticalLocations", True)):
        return _result(
            "GATE_MATERIAL_CLAIM_UNSUPPORTED", SEVERITY_BLOCKING, False,
            "Bloqueio de afirmacao sem suporte desabilitado pela politica.",
        )
    assessment = _assessment_of(draft, context)
    if assessment is None:
        return _result(
            "GATE_MATERIAL_CLAIM_UNSUPPORTED", SEVERITY_BLOCKING, False,
            "Sem avaliacao factual para inspecionar.",
            metrics={"has_assessment": False},
        )

    offenders = assessment.unsupported_in_critical_locations
    return _result(
        "GATE_MATERIAL_CLAIM_UNSUPPORTED", SEVERITY_BLOCKING, bool(offenders),
        f"{len(offenders)} afirmacao(oes) material(is) sem suporte em posicao critica."
        if offenders else "Nenhuma afirmacao critica sem suporte.",
        evidence=[
            f"{', '.join(c.draft_locations)}: {c.display_text}" for c in offenders[:5]
        ],
        affected_fields=["content.title", "content.subtitle", "content.meta_description"],
        remediation=(
            "Sem suporte nao significa falso: ou a fonte nao foi recebida, ou a "
            "afirmacao precisa sair da manchete."
        ),
        metrics={"unsupported_critical_claims": len(offenders)},
    )


def rule_critical_fact_conflict(draft, policy, context) -> EditorialRuleResult:
    """Sources contradict each other on something the reader would act on."""
    if not bool(policy.threshold("blockCriticalFactConflicts", True)):
        return _result(
            "GATE_CRITICAL_FACT_CONFLICT", SEVERITY_BLOCKING, False,
            "Bloqueio de conflito critico desabilitado pela politica.",
        )
    assessment = _assessment_of(draft, context)
    if assessment is None:
        return _result(
            "GATE_CRITICAL_FACT_CONFLICT", SEVERITY_BLOCKING, False,
            "Sem avaliacao factual para inspecionar.",
            metrics={"has_assessment": False},
        )

    critical = assessment.critical_conflicts
    return _result(
        "GATE_CRITICAL_FACT_CONFLICT", SEVERITY_BLOCKING, bool(critical),
        f"{len(critical)} conflito(s) critico(s) entre fontes." if critical else
        "Nenhum conflito critico.",
        evidence=[
            f"{c.conflict_type} {c.subject or '-'}: {' vs '.join(c.values[:2])}"
            for c in critical[:5]
        ],
        affected_fields=["factual_assessment"],
        remediation="Nao resolver automaticamente: um humano decide qual versao vale.",
        metrics={
            "critical_conflicts": len(critical),
            "total_conflicts": len(assessment.conflicts),
        },
    )


def rule_factual_coverage_low(draft, policy, context) -> EditorialRuleResult:
    assessment = _assessment_of(draft, context)
    if assessment is None:
        return _result("GATE_FACTUAL_COVERAGE_LOW", SEVERITY_WARNING, False,
                       "Sem avaliacao factual.", metrics={"has_assessment": False})
    minimum = float(policy.threshold("minimumFactualCoverageRatio", 0.75))
    ratio = assessment.coverage.coverage_ratio
    triggered = ratio < minimum
    return _result(
        "GATE_FACTUAL_COVERAGE_LOW", SEVERITY_WARNING, triggered,
        f"Cobertura factual de {ratio} abaixo do minimo {minimum}." if triggered else
        f"Cobertura factual de {ratio}.",
        evidence=[
            f"suportados={assessment.coverage.supported_material_claims} "
            f"parciais={assessment.coverage.partially_supported_material_claims} "
            f"total={assessment.coverage.total_material_claims}"
        ] if triggered else [],
        affected_fields=["factual_assessment"],
        remediation="Cobertura baixa e sinal de revisao, nao prova de erro.",
        metrics={"coverage_ratio": ratio, "minimum": minimum},
    )


def rule_partial_support_present(draft, policy, context) -> EditorialRuleResult:
    assessment = _assessment_of(draft, context)
    if assessment is None:
        return _result("GATE_PARTIAL_SUPPORT_PRESENT", SEVERITY_WARNING, False,
                       "Sem avaliacao factual.", metrics={"has_assessment": False})
    partial = assessment.claims_with_status("PARTIALLY_SUPPORTED")
    return _result(
        "GATE_PARTIAL_SUPPORT_PRESENT", SEVERITY_WARNING, bool(partial),
        f"{len(partial)} afirmacao(oes) com suporte apenas parcial." if partial else
        "Nenhum suporte parcial.",
        evidence=[c.display_text for c in partial[:5]],
        affected_fields=["factual_assessment"],
        remediation="Conferir se a parte nao confirmada muda o sentido.",
        metrics={"partially_supported_claims": len(partial)},
    )


def rule_unverified_claims_present(draft, policy, context) -> EditorialRuleResult:
    assessment = _assessment_of(draft, context)
    if assessment is None:
        return _result("GATE_UNVERIFIED_CLAIMS_PRESENT", SEVERITY_WARNING, False,
                       "Sem avaliacao factual.", metrics={"has_assessment": False})
    unverified = assessment.claims_with_status("UNVERIFIED")
    return _result(
        "GATE_UNVERIFIED_CLAIMS_PRESENT", SEVERITY_WARNING, bool(unverified),
        f"{len(unverified)} afirmacao(oes) sem dados suficientes para concluir."
        if unverified else "Nenhuma afirmacao indeterminada.",
        evidence=[c.display_text for c in unverified[:5]],
        affected_fields=["factual_assessment"],
        remediation="As fontes citam o assunto mas nao confirmam a afirmacao.",
        metrics={"unverified_claims": len(unverified)},
    )


def rule_source_origin_diversity_low(draft, policy, context) -> EditorialRuleResult:
    """Several URLs, few actual outlets.

    Republications of one wire report are one origin, not three confirmations.
    """
    assessment = _assessment_of(draft, context)
    if assessment is None:
        return _result("GATE_SOURCE_ORIGIN_DIVERSITY_LOW", SEVERITY_WARNING, False,
                       "Sem avaliacao factual.", metrics={"has_assessment": False})
    minimum = int(policy.threshold("minimumIndependentSourceOrigins", 2))
    origins = assessment.coverage.source_diversity
    url_count = len({e.source_url for e in assessment.evidence})
    triggered = url_count >= 2 and origins < minimum
    return _result(
        "GATE_SOURCE_ORIGIN_DIVERSITY_LOW", SEVERITY_WARNING, triggered,
        f"{url_count} URLs mas apenas {origins} origem(ns) editorial(is) distinta(s)."
        if triggered else f"{origins} origem(ns) editorial(is) distinta(s).",
        evidence=[f"urls={url_count} origens={origins} minimo={minimum}"] if triggered else [],
        affected_fields=["factual_assessment"],
        remediation="Republicacoes da mesma origem nao sao confirmacao independente.",
        metrics={"editorial_origins": origins, "source_urls": url_count, "minimum": minimum},
    )


def rule_content_too_thin(draft, policy, context) -> EditorialRuleResult:
    """Depth check, delegated to the editorial validator's own metric.

    The word count and the per-type minimum both come from
    ``app.editorial_validator``; the gate only decides that thinness is a
    warning. Two competing depth metrics would be worse than none.
    """
    from app.editorial_validator import MIN_WORDS, detect_content_type

    body = draft.content.body_html or ""
    words = _word_count(body)
    content_type = detect_content_type(draft.content.title or "", _text_of(body), content_html=body)
    floor = int(policy.threshold("min_body_words", 0)) or MIN_WORDS.get(
        content_type, MIN_WORDS["unknown"]
    )
    triggered = words < floor
    return _result(
        "GATE_CONTENT_TOO_THIN", SEVERITY_WARNING, triggered,
        f"Corpo com {words} palavras, abaixo de {floor} para tipo '{content_type}'."
        if triggered else f"Profundidade adequada ({words} palavras).",
        evidence=[f"tipo={content_type} palavras={words} minimo={floor}"] if triggered else [],
        affected_fields=["content.body_html"],
        remediation="Profundidade insuficiente e sinal editorial, nao erro tecnico.",
        metrics={"word_count": words, "min_words": floor, "content_type": content_type},
    )


def rule_missing_meta_description(draft, policy, context) -> EditorialRuleResult:
    meta = (draft.content.meta_description or "").strip()
    return _result(
        "GATE_MISSING_META_DESCRIPTION", SEVERITY_WARNING, not meta,
        "Meta description ausente." if not meta else "Meta description presente.",
        evidence=[] if meta else ["content.meta_description vazio"],
        affected_fields=["content.meta_description"],
        remediation="O gate nao inventa meta description; regerar no pipeline.",
        metrics={"meta_description_length": len(meta)},
    )


_HTML_IN_TEXT: Final[re.Pattern[str]] = re.compile(r"<[a-z/][^>]*>", re.IGNORECASE)


def rule_subtitle_issue(draft, policy, context) -> EditorialRuleResult:
    content = draft.content
    subtitle = (content.subtitle or "").strip()
    title = (content.title or "").strip()
    meta = (content.meta_description or "").strip()

    problems: List[str] = []
    if not subtitle:
        problems.append("subtitle vazio apos normalizacao")
    else:
        if subtitle.casefold() == title.casefold():
            problems.append("subtitle identico ao titulo")
        if meta and subtitle.casefold() == meta.casefold():
            problems.append("subtitle identico a meta description")
        if _HTML_IN_TEXT.search(subtitle):
            problems.append("subtitle contem HTML")

    return _result(
        "GATE_SUBTITLE_ISSUE", SEVERITY_WARNING, bool(problems),
        "; ".join(problems) if problems else "Subtitle distinto e limpo.",
        evidence=problems,
        affected_fields=["content.subtitle"],
        remediation="O gate nao inventa subtitulo; corrigir na normalizacao.",
        metrics={"subtitle_length": len(subtitle)},
    )


def rule_unverified_media(draft, policy, context) -> EditorialRuleResult:
    unverified = [
        m for m in draft.media_candidates if str(getattr(m, "status", "")) == MEDIA_UNVERIFIED
    ]
    return _result(
        "GATE_UNVERIFIED_MEDIA", SEVERITY_WARNING, bool(unverified),
        f"{len(unverified)} candidato(s) de midia sem licenca verificada." if unverified else
        "Nenhuma midia pendente de verificacao.",
        evidence=[f"{_domain_of(m.source_url) or m.source_url}: status=unverified" for m in unverified],
        affected_fields=["media_candidates"],
        remediation="Direito de uso e decisao humana; o MNScr nao baixa nem atribui credito.",
        metrics={"unverified_media": len(unverified)},
    )


#: Conservative injection signals. Each is a *phrase*, not a single word, so an
#: article that merely mentions "prompt" or "instructions" is not flagged.
_INJECTION_PATTERNS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    ("ignore previous instructions", re.compile(
        r"\bignore\s+(?:all\s+|any\s+)?(?:previous|prior|above)\s+instructions?\b", re.IGNORECASE)),
    ("disregard instructions", re.compile(
        r"\bdisregard\s+(?:all\s+|the\s+)?(?:previous|prior|above)\s+\w+", re.IGNORECASE)),
    ("ignore as instrucoes anteriores", re.compile(
        r"\bignore\s+(?:as\s+|todas\s+as\s+)?instru[cç][oõ]es\s+(?:anteriores|acima)\b", re.IGNORECASE)),
    ("declara-se system prompt", re.compile(
        r"\b(?:you\s+are\s+now|voc[eê]\s+agora\s+[eé])\s+(?:a|an|um|uma)?\s*\w*\s*(?:system|assistant)\b",
        re.IGNORECASE)),
    ("marcador de papel de sistema", re.compile(
        r"(?:^|\s)(?:system|assistant)\s*:\s*(?:you|voc[eê])\b", re.IGNORECASE)),
    ("delimitador de prompt forjado", re.compile(
        r"<<<\s*(?:INICIO|FIM)_CONTEUDO_FONTE\s*>>>", re.IGNORECASE)),
    ("pedido de revelar instrucoes", re.compile(
        r"\b(?:reveal|show|print)\s+(?:your\s+)?(?:system\s+)?(?:prompt|instructions?)\b",
        re.IGNORECASE)),
)


def rule_prompt_injection_signal(draft, policy, context) -> EditorialRuleResult:
    """Detect text aimed at the model rather than the reader.

    Deliberately conservative and *never acted upon*: the gate records that an
    instruction-shaped string exists and hands it to a human. It does not obey
    it, does not rewrite the body, and does not block on its own — a quoted
    article about prompt injection is legitimate journalism.
    """
    # The raw HTML is scanned alongside the extracted text: a forged delimiter
    # such as ``<<<INICIO_CONTEUDO_FONTE>>>`` looks like a malformed tag to the
    # HTML parser and disappears from the plain text — which is exactly what an
    # attacker would rely on, since the prompt is built from the markup.
    haystacks = {
        "content.body_html": _text_of(draft.content.body_html),
        "content.body_html:raw": draft.content.body_html or "",
        "content.title": draft.content.title or "",
        "content.subtitle": draft.content.subtitle or "",
    }
    hits: List[str] = []
    fields: List[str] = []
    seen: set[tuple[str, str]] = set()
    for field_name, text in haystacks.items():
        if not text:
            continue
        base_field = field_name.split(":", 1)[0]
        for label, pattern in _INJECTION_PATTERNS:
            match = pattern.search(text)
            if not match:
                continue
            excerpt = " ".join(match.group(0).split())
            # The same signal found in both the text and the raw markup is one
            # finding, not two.
            key = (label, excerpt.casefold())
            if key in seen:
                continue
            seen.add(key)
            hits.append(f"{base_field}: {label} -> {excerpt}")
            if base_field not in fields:
                fields.append(base_field)

    minimum = int(policy.threshold("prompt_injection_min_signals", 1))
    triggered = len(hits) >= minimum
    return _result(
        "GATE_PROMPT_INJECTION_SIGNAL", SEVERITY_WARNING, triggered,
        f"{len(hits)} sinal(is) de instrucao dirigida ao modelo no conteudo." if triggered else
        "Nenhum sinal de prompt injection.",
        evidence=hits,
        affected_fields=fields,
        remediation="Nao obedecer a instrucao encontrada; avaliar se a fonte foi comprometida.",
        metrics={"injection_signals": len(hits)},
    )


def rule_revision_gap(draft, policy, context) -> EditorialRuleResult:
    warnings = [str(w) for w in (draft.warnings or [])]
    ingestion = [str(w) for w in (context.get("ingestion_warnings") or [])]
    hits = [w for w in warnings + ingestion if "REVISION_GAP" in w]
    return _result(
        "GATE_REVISION_GAP", SEVERITY_WARNING, bool(hits),
        "Revisoes intermediarias nao foram recebidas." if hits else "Sem salto de revisao.",
        evidence=hits,
        affected_fields=["revision"],
        remediation="As revisoes ausentes ficam registradas; o MNScr nao as inventa.",
        metrics={"revision_gap_signals": len(hits)},
    )


# ===========================================================================
# INFO RULES
# ===========================================================================


def rule_trusted_single_source(draft, policy, context) -> EditorialRuleResult:
    sources = [s for s in draft.sources if (s.url or "").strip()]
    if len(sources) != 1:
        return _result("GATE_TRUSTED_SINGLE_SOURCE", SEVERITY_INFO, False, "Nao e fonte unica.")
    domain = sources[0].domain or _domain_of(sources[0].url)
    trusted = policy.is_trusted_domain(domain)
    return _result(
        "GATE_TRUSTED_SINGLE_SOURCE", SEVERITY_INFO, trusted,
        f"Fonte unica na allowlist: {domain}. Isso nao equivale a aprovacao editorial."
        if trusted else f"Fonte unica fora da allowlist: {domain}.",
        evidence=[f"dominio confiavel: {domain}"] if trusted else [],
        affected_fields=["sources"],
        metrics={"domain": domain or "", "trusted": trusted},
    )


def rule_multi_source(draft, policy, context) -> EditorialRuleResult:
    domains = _distinct_domains(draft)
    triggered = len(domains) >= 2
    return _result(
        "GATE_MULTI_SOURCE", SEVERITY_INFO, triggered,
        f"{len(domains)} dominios distintos sustentam o draft." if triggered else
        "Menos de dois dominios distintos.",
        evidence=[f"dominios: {', '.join(domains)}"] if triggered else [],
        affected_fields=["sources"],
        metrics={"distinct_domains": len(domains)},
    )


def rule_v1_input_contract(draft, policy, context) -> EditorialRuleResult:
    version = str(getattr(draft.provenance, "input_contract_version", "") or "")
    triggered = version == V1_CONTRACT_VERSION
    return _result(
        "GATE_V1_INPUT_CONTRACT", SEVERITY_INFO, triggered,
        "Entrada validada pelo contrato versionado do RSS Prime." if triggered else
        f"Entrada em contrato '{version}'.",
        evidence=[f"input_contract_version={version}"] if triggered else [],
        affected_fields=["provenance.input_contract_version"],
        metrics={"input_contract_version": version},
    )


def rule_local_output_only(draft, policy, context) -> EditorialRuleResult:
    """Always-on reminder recorded in the artifact itself."""
    destination = str(context.get("output_mode") or "local")
    return _result(
        "GATE_LOCAL_OUTPUT_ONLY", SEVERITY_INFO, True,
        f"Destino operacional atual: {destination}. Nenhuma publicacao ocorre nesta fase.",
        evidence=[f"output_mode={destination}"],
        affected_fields=[],
        metrics={"output_mode": destination},
    )


# --- Registry --------------------------------------------------------------

RuleFn = Callable[[Any, EditorialGatePolicy, Dict[str, Any]], EditorialRuleResult]

BLOCKING_RULES: Final[Dict[str, RuleFn]] = {
    "GATE_MISSING_TITLE": rule_missing_title,
    "GATE_MISSING_BODY": rule_missing_body,
    "GATE_MISSING_SOURCES": rule_missing_sources,
    "GATE_NO_SUCCESSFUL_EXTRACTION": rule_no_successful_extraction,
    "GATE_MISSING_PROVENANCE": rule_missing_provenance,
    "GATE_FORBIDDEN_WORDPRESS_MARKUP": rule_forbidden_wordpress_markup,
    "GATE_UNSAFE_HTML": rule_unsafe_html,
    "GATE_UNSUPPORTED_TOPIC": rule_unsupported_topic,
    "GATE_BLOCKING_ERRORS_PRESENT": rule_blocking_errors_present,
    "GATE_INVALID_REVISION_CONTEXT": rule_invalid_revision_context,
    "GATE_MATERIAL_CLAIM_UNSUPPORTED": rule_material_claim_unsupported,
    "GATE_CRITICAL_FACT_CONFLICT": rule_critical_fact_conflict,
}

WARNING_RULES: Final[Dict[str, RuleFn]] = {
    "GATE_LEGACY_INPUT_CONTRACT": rule_legacy_input_contract,
    "GATE_MISSING_PROMPT_VERSION": rule_missing_prompt_version,
    "GATE_SINGLE_UNTRUSTED_SOURCE": rule_single_untrusted_source,
    "GATE_SOURCE_DIVERSITY_LOW": rule_source_diversity_low,
    "GATE_EXTRACTION_WARNINGS": rule_extraction_warnings,
    "GATE_EVIDENCE_MISSING": rule_evidence_missing,
    "GATE_EVIDENCE_CONFLICT": rule_evidence_conflict,
    "GATE_CONTENT_TOO_THIN": rule_content_too_thin,
    "GATE_MISSING_META_DESCRIPTION": rule_missing_meta_description,
    "GATE_SUBTITLE_ISSUE": rule_subtitle_issue,
    "GATE_UNVERIFIED_MEDIA": rule_unverified_media,
    "GATE_PROMPT_INJECTION_SIGNAL": rule_prompt_injection_signal,
    "GATE_REVISION_GAP": rule_revision_gap,
    "GATE_FACTUAL_COVERAGE_LOW": rule_factual_coverage_low,
    "GATE_PARTIAL_SUPPORT_PRESENT": rule_partial_support_present,
    "GATE_UNVERIFIED_CLAIMS_PRESENT": rule_unverified_claims_present,
    "GATE_SOURCE_ORIGIN_DIVERSITY_LOW": rule_source_origin_diversity_low,
}

INFO_RULES: Final[Dict[str, RuleFn]] = {
    "GATE_TRUSTED_SINGLE_SOURCE": rule_trusted_single_source,
    "GATE_MULTI_SOURCE": rule_multi_source,
    "GATE_V1_INPUT_CONTRACT": rule_v1_input_contract,
    "GATE_LOCAL_OUTPUT_ONLY": rule_local_output_only,
}

ALL_RULES: Final[Dict[str, RuleFn]] = {**BLOCKING_RULES, **WARNING_RULES, **INFO_RULES}


__all__ = [
    "ALL_RULES",
    "BLOCKING_RULES",
    "INFO_RULES",
    "PARTIAL_EXTRACTION_STATUSES",
    "RULE_VERSION",
    "SUCCESSFUL_EXTRACTION_STATUSES",
    "WARNING_RULES",
]
