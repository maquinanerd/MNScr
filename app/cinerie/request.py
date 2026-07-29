"""Montagem do ``editorial-publication-request-v1`` a partir de um draft.

Esta e a fronteira: entra o modelo interno do MNScr (``EditorialDraft``, HTML,
avaliacao factual, veredito do gate), sai exatamente o corpo que o contrato
descreve — nem um campo a mais, nem um campo com nome nosso.

O que o MNScr declara aqui: redacao, consolidacao factual, SEO editorial,
fontes, evidencias, proveniencia, sugestoes de midia e de links internos, QA.

O que ele **nao** declara, e o contrato recusa se tentar: canonical, robots, URL
publica, JSON-LD, publisher, sitemap, datePublished, dateModified, indexacao,
estado publico e identidade tecnica. Nao e uma lista de boas maneiras — cada um
desses e uma decisao que pertence ao outro lado, e declara-la aqui criaria duas
fontes discordando sobre a mesma coisa.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Final, List, Mapping, Optional, Sequence

from .blocks import BlockConversion, has_rating, has_structured_list, has_structured_steps, html_to_blocks
from .contract import ContractIdentity, local_identity
from .errors import RequestBuildError
from .identity import RequestIdentity, build_identity, build_source_id, is_stable_id
from .policy import AUTO_PUBLISH_INELIGIBLE, BLOCKING, seo_policy
from .seo import SeoFinding, SeoProposal, build_seo_proposal, lead_text_of
from .text import collapse, plain_text

logger = logging.getLogger(__name__)

INTENT_PUBLISH: Final[str] = "publish"
INTENT_UPDATE: Final[str] = "update"

DEFAULT_LANGUAGE: Final[str] = "pt-BR"
DEFAULT_CONTENT_TYPE: Final[str] = "news"

#: `role` de uma fonte externa, conforme o contrato.
ROLE_PRIMARY: Final[str] = "primary"
ROLE_SECONDARY: Final[str] = "secondary"


@dataclass
class BuiltRequest:
    """O pedido pronto, mais o que se aprendeu ao monta-lo."""

    payload: Dict[str, Any]
    identity: RequestIdentity
    contract: ContractIdentity
    seo: SeoProposal
    blocks: BlockConversion
    warnings: List[str] = field(default_factory=list)

    @property
    def intent(self) -> str:
        return str(self.payload.get("publicationIntent") or "")

    @property
    def auto_publishable(self) -> bool:
        """O MNScr acha que isto sai sem humano?

        Palpite informado, nunca decisao: quem decide e o Cinerie, que revalida
        autor, QA, fontes, midia, SEO, tetos e kill switch no servidor. Serve
        para o log dizer se um ``ROUTED_TO_REVIEW`` era esperado.
        """
        return self.seo.auto_publishable and bool(self.payload.get("qa", {}).get("passed"))

    def safe_log_fields(self) -> Dict[str, Any]:
        fields = self.identity.safe_log_fields()
        fields.update(
            {
                "contract_name": self.contract.contract_name,
                "contract_version": self.contract.contract_version,
                "schema_hash": self.contract.truncated_hash(),
                "publication_intent": self.intent,
                "block_count": len(self.payload.get("blocks", [])),
            }
        )
        return fields


def _sources_from_draft(draft: Any) -> List[Dict[str, Any]]:
    """Fontes externas com id estavel, nome e papel.

    O papel nao e inventado: ``primary`` sai de ``is_primary``, que a fase de
    clusterizacao ja decidiu. Promover uma fonte a primaria aqui seria criar um
    fato editorial na camada de transporte.
    """
    policy_max = 50
    sources: List[Dict[str, Any]] = []
    seen: set[str] = set()

    for index, source in enumerate(getattr(draft, "sources", []) or []):
        url = collapse(getattr(source, "url", ""))
        if not url or url in seen:
            continue
        seen.add(url)
        source_id = collapse(getattr(source, "source_id", "")) or build_source_id(url, index=index)
        if not is_stable_id(source_id):
            source_id = build_source_id(url, index=index)
        name = plain_text(
            getattr(source, "name", None) or getattr(source, "domain", None) or url,
            max_length=500,
        )
        entry: Dict[str, Any] = {
            "id": source_id,
            "name": name,
            "url": url,
            "role": ROLE_PRIMARY if getattr(source, "is_primary", False) else ROLE_SECONDARY,
        }
        published_at = collapse(getattr(source, "published_at", ""))
        if published_at:
            # So entra se ja estiver no formato que o contrato exige. Corrigir
            # uma data aqui seria inventar quando a fonte publicou.
            from .refinements import check_iso_datetime

            if not check_iso_datetime(published_at, "x"):
                entry["publishedAt"] = published_at
        sources.append(entry)
        if len(sources) >= policy_max:
            break

    return sources


def _qa_from_draft(draft: Any, *, seo: SeoProposal, extra_warnings: Sequence[str]) -> Dict[str, Any]:
    """O QA do pipeline, consolidado.

    ``passed`` so e verdadeiro quando **nada** bloqueia: nem o gate editorial,
    nem a validacao tecnica, nem o SEO. ``passed=false`` nao recusa o pedido — o
    Cinerie o encaminha para revisao humana —, mas precisa vir acompanhado do
    motivo, e o contrato recusa um QA que reprova sem dizer o que quebrou.
    """
    blocking: List[str] = [str(item) for item in (getattr(draft, "blocking_errors", None) or [])]
    warnings: List[str] = [str(item) for item in (getattr(draft, "warnings", None) or [])]

    gate = getattr(draft, "editorial_gate", None)
    gate_outcome = collapse(getattr(gate, "outcome", "")) if gate is not None else ""
    if gate is None:
        blocking.append("EDITORIAL_GATE_AUSENTE")
    elif gate_outcome != "GATE_CLEAR":
        blocking.append(f"EDITORIAL_GATE:{gate_outcome}")

    assessment = getattr(draft, "factual_assessment", None)
    if assessment is None:
        blocking.append("AVALIACAO_FACTUAL_AUSENTE")
    else:
        critical = list(getattr(assessment, "critical_conflicts", None) or [])
        if critical:
            blocking.append(f"CONFLITO_FACTUAL_CRITICO:{len(critical)}")

    blocking.extend(str(item) for item in seo.blocking)
    warnings.extend(str(item) for item in seo.warnings)
    warnings.extend(str(item) for item in seo.auto_publish_blockers)
    warnings.extend(str(item) for item in extra_warnings)

    version = collapse(getattr(getattr(draft, "provenance", None), "prompt_version", "")) or "qa@1"

    return {
        "version": plain_text(version, max_length=500),
        "passed": not blocking,
        "warnings": [plain_text(item, max_length=500) for item in dict.fromkeys(warnings)][:100],
        "blockingErrors": [plain_text(item, max_length=500) for item in dict.fromkeys(blocking)][:100],
    }


def _provenance_from_draft(draft: Any) -> Dict[str, Any]:
    provenance = getattr(draft, "provenance", None)
    if provenance is None:
        raise RequestBuildError("draft sem proveniencia")
    input_hash = collapse(getattr(provenance, "input_hash", ""))
    if not input_hash:
        raise RequestBuildError("draft sem input_hash: o pedido exige lastro de proveniencia")

    entry: Dict[str, Any] = {
        "pipeline": plain_text(getattr(provenance, "pipeline_version", "") or "mnscr", max_length=500),
        "inputHash": input_hash,
    }
    model = collapse(getattr(provenance, "model_name", ""))
    if model:
        entry["model"] = plain_text(model, max_length=500)
    prompt_version = collapse(getattr(provenance, "prompt_version", ""))
    if prompt_version:
        entry["promptVersion"] = plain_text(prompt_version, max_length=500)
    return entry


def resolve_content_type(value: Optional[str]) -> str:
    """Tipo editorial dentro do vocabulario do contrato.

    Desconhecido cai para ``news`` em vez de ser enviado: um ``contentType`` fora
    do enum reprovaria o pedido inteiro por causa de uma etiqueta.
    """
    candidate = collapse(value).lower()
    return candidate if candidate in seo_policy().content_types else DEFAULT_CONTENT_TYPE


def build_publication_request(
    draft: Any,
    *,
    public_author_id: str,
    attribution_mode: str,
    intent: str = INTENT_PUBLISH,
    target_article_id: Optional[str] = None,
    content_type: Optional[str] = None,
    language: str = DEFAULT_LANGUAGE,
    pipeline_version: Optional[str] = None,
    seo_source: Optional[Mapping[str, Any]] = None,
    internal_links: Sequence[Mapping[str, Any]] = (),
    media: Sequence[Mapping[str, Any]] = (),
    entity_links: Sequence[Mapping[str, Any]] = (),
    contract: Optional[ContractIdentity] = None,
) -> BuiltRequest:
    """Monta o pedido. Nao valida e nao envia — ver ``validation`` e ``client``."""
    policy = seo_policy()
    contract_identity = contract or local_identity()

    if not is_stable_id(public_author_id):
        raise RequestBuildError(
            "publicAuthorId ausente ou fora do formato; a materia sai assinada por uma "
            "entidade Author previamente autorizada, nunca por um nome livre"
        )
    if attribution_mode not in policy.attribution_modes:
        raise RequestBuildError(
            f"attributionMode invalido: {attribution_mode!r}; aceitos {list(policy.attribution_modes)}"
        )
    if intent not in policy.publication_intents:
        raise RequestBuildError(f"publicationIntent invalido: {intent!r}")
    if intent == INTENT_UPDATE and not is_stable_id(target_article_id or ""):
        raise RequestBuildError('publicationIntent "update" exige targetArticleId valido')
    if intent == INTENT_PUBLISH and target_article_id:
        raise RequestBuildError('publicationIntent "publish" nao pode declarar targetArticleId')

    content = getattr(draft, "content", None)
    if content is None:
        raise RequestBuildError("draft sem conteudo")

    provenance = getattr(draft, "provenance", None)
    payload_hash = collapse(getattr(provenance, "output_hash", ""))
    if not payload_hash:
        raise RequestBuildError("draft sem output_hash: nao ha como identificar a revisao")

    identity = build_identity(
        event_key=getattr(draft, "event_key", None),
        revision=getattr(draft, "revision", 1),
        payload_hash=payload_hash,
    )

    conversion = html_to_blocks(getattr(content, "body_html", ""))
    if conversion.is_empty:
        raise RequestBuildError("corpo do draft nao produziu nenhum bloco editorial")
    if conversion.truncated:
        raise RequestBuildError(
            "corpo acima do limite de blocos do contrato; truncar publicaria a materia pela metade"
        )

    resolved_content_type = resolve_content_type(
        content_type or (getattr(content, "category_suggestions", None) or [None])[0]
    )

    media_entries = _normalize_media(media)
    seo = build_seo_proposal(
        title=getattr(content, "title", ""),
        meta_description=getattr(content, "meta_description", "") or "",
        focus_keyphrase=getattr(content, "focus_keyphrase", "") or "",
        slug_suggestion=getattr(content, "slug_suggestion", None),
        related_keyphrases=getattr(content, "related_keyphrases", None) or [],
        editorial_keywords=getattr(content, "tag_suggestions", None) or [],
        content_type=resolved_content_type,
        schema_type=(seo_source or {}).get("schemaTypeRecommendation"),
        article_section=(getattr(content, "category_suggestions", None) or [None])[0],
        lead_text=lead_text_of(conversion.blocks),
        extra=seo_source,
        media_ids=[entry["mediaId"] for entry in media_entries],
        alt_texts=(seo_source or {}).get("image_alt_texts"),
        internal_links=internal_links,
        has_steps=has_structured_steps(conversion.blocks),
        has_list=has_structured_list(conversion.blocks),
        has_rating=has_rating(conversion.blocks),
    )

    summary = plain_text(
        getattr(content, "summary", None)
        or getattr(content, "subtitle", None)
        or seo.meta_description,
        max_length=2000,
    )
    if not summary:
        raise RequestBuildError("draft sem resumo: `summary` e obrigatorio no contrato")

    payload: Dict[str, Any] = {
        **contract_identity.as_request_fields(),
        **identity.as_fields(),
        "publicationIntent": intent,
        "publicAuthorId": public_author_id,
        "attributionMode": attribution_mode,
        "contentType": resolved_content_type,
        "language": language,
        "title": plain_text(getattr(content, "title", ""), max_length=300),
        "summary": summary,
        "blocks": conversion.blocks,
        "externalSources": _sources_from_draft(draft),
        "entityLinks": _normalize_entity_links(entity_links),
        "media": media_entries,
        "seo": seo.to_contract(),
        "provenance": _provenance_from_draft(draft),
        "qa": _qa_from_draft(draft, seo=seo, extra_warnings=conversion.warnings),
        "generatedAt": _generated_at(draft),
        "pipelineVersion": plain_text(
            pipeline_version or getattr(provenance, "pipeline_version", "") or "mnscr",
            max_length=500,
        ),
    }

    subtitle = plain_text(getattr(content, "subtitle", None) or "", max_length=400)
    if subtitle:
        payload["subtitle"] = subtitle
    if intent == INTENT_UPDATE:
        payload["targetArticleId"] = target_article_id

    return BuiltRequest(
        payload=payload,
        identity=identity,
        contract=contract_identity,
        seo=seo,
        blocks=conversion,
        warnings=list(conversion.warnings),
    )


def _generated_at(draft: Any) -> str:
    """``generatedAt`` e o instante em que o PIPELINE gerou a materia.

    Nao e o horario de recebimento nem "agora": o horario de auditoria e o do
    servidor do Cinerie, que o carimba sozinho. Mandar `now()` daqui faria os
    dois competirem pelo mesmo papel.
    """
    provenance = getattr(draft, "provenance", None)
    created = collapse(getattr(provenance, "created_at", ""))
    if created:
        return created
    raise RequestBuildError("draft sem created_at na proveniencia")


def _normalize_media(media: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Referencias a midia **ja aprovada no CMS**.

    O pedido REFERENCIA, nunca envia bytes — e o MNScr nao inventa ``mediaId``:
    hoje ele conhece a URL que a fonte usou, nao o id do item aprovado no
    Cinerie. Por isso, na pratica, esta lista sai vazia. Preenche-la com um id
    fabricado faria o Cinerie publicar apontando para nada.
    """
    entries: List[Dict[str, Any]] = []
    allowed_uses = {"hero", "inline", "gallery", "social"}
    for item in media or []:
        if not isinstance(item, Mapping):
            continue
        media_id = collapse(item.get("mediaId"))
        use = collapse(item.get("intendedUse"))
        if not is_stable_id(media_id) or use not in allowed_uses:
            continue
        entry: Dict[str, Any] = {"mediaId": media_id, "intendedUse": use}
        alt = plain_text(item.get("altSuggestion"), max_length=500)
        if alt:
            entry["altSuggestion"] = alt
        entries.append(entry)
    return entries


def _normalize_entity_links(links: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Entidades do catalogo do Cinerie. O MNScr **nao cria entidade**.

    Sem um id interno verificavel a sugestao e descartada: apontar para uma
    entidade inexistente e pior do que nao apontar.
    """
    allowed_kinds = {"movie", "tv", "season", "episode", "person", "character", "franchise"}
    allowed_relations = {
        "primary_subject",
        "secondary_subject",
        "mentioned",
        "reviewed",
        "recommended",
        "compared",
    }
    entries: List[Dict[str, Any]] = []
    for item in links or []:
        if not isinstance(item, Mapping):
            continue
        kind = collapse(item.get("entityKind"))
        entity_id = collapse(item.get("entityId"))
        relation = collapse(item.get("relation"))
        confidence = item.get("confidence")
        if kind not in allowed_kinds or relation not in allowed_relations:
            continue
        if not is_stable_id(entity_id):
            continue
        if not isinstance(confidence, (int, float)) or not 0.0 <= float(confidence) <= 1.0:
            continue
        entries.append(
            {
                "entityKind": kind,
                "entityId": entity_id,
                "relation": relation,
                "confidence": float(confidence),
            }
        )
    return entries


def blocking_findings(built: BuiltRequest) -> List[SeoFinding]:
    return [item for item in built.seo.findings if item.severity in (BLOCKING, AUTO_PUBLISH_INELIGIBLE)]


__all__ = [
    "BuiltRequest",
    "DEFAULT_CONTENT_TYPE",
    "DEFAULT_LANGUAGE",
    "INTENT_PUBLISH",
    "INTENT_UPDATE",
    "ROLE_PRIMARY",
    "ROLE_SECONDARY",
    "blocking_findings",
    "build_publication_request",
    "resolve_content_type",
]
