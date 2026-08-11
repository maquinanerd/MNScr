"""O objeto ``seo`` neutro do contrato, montado deterministicamente.

Duas coisas acontecem aqui, e vale separa-las.

**Migracao.** O MNScr produzia ``yoast_meta`` — o objeto de um plugin de
WordPress. Os campos nao sao jogados fora: ``_yoast_wpseo_opengraph-title`` vira
``openGraphTitleSuggestion``, e assim por diante. O que se perde e a *semantica*
do outro CMS (controle de canonical, de robots, de index), que nunca deveria ter
saido daqui. Descartar os valores junto com o formato jogaria fora trabalho
editorial util; manter o formato importaria o modelo de um CMS que nao e o nosso.

**Reconstrucao determinista.** Mesmo quando a IA devolve o objeto pronto, nada
dela e usado cru: cada campo e normalizado, deduplicado, limitado e reconstruido.
A IA propoe; quem monta o que sai e este modulo. "A IA disse" nao e uma garantia
de formato, e um pedido invalido custa uma materia inteira.

O nome interno pode ser apropriado ao projeto; a **serializacao** usa exatamente
os nomes do JSON Schema do Cinerie.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Final, FrozenSet, List, Mapping, Optional, Sequence

from .fallbacks import (
    body_texts,
    derive_focus_keyphrase,
    derive_meta_description,
    derive_seo_title,
    derive_slug,
)
from .policy import AUTO_PUBLISH_INELIGIBLE, BLOCKING, WARNING, seo_policy
from .slug import slug_issue
from .text import (
    collapse,
    contains_keyphrase,
    count_occurrences,
    dedupe_keyphrases,
    find_forbidden_markup,
    normalize_keyphrase,
    plain_text,
    singular_form,
    word_count,
)

logger = logging.getLogger(__name__)

#: Campos do Yoast e seus equivalentes neutros. O valor sobrevive; o formato nao.
LEGACY_SEO_MIGRATION: Final[Dict[str, str]] = {
    "_yoast_wpseo_title": "title",
    "_yoast_wpseo_metadesc": "metaDescription",
    "_yoast_wpseo_focuskw": "focusKeyphrase",
    "_yoast_news_keywords": "editorialKeywords",
    "_yoast_wpseo_opengraph-title": "openGraphTitleSuggestion",
    "_yoast_wpseo_opengraph-description": "openGraphDescriptionSuggestion",
    "_yoast_wpseo_twitter-title": "twitterTitleSuggestion",
    "_yoast_wpseo_twitter-description": "twitterDescriptionSuggestion",
}

#: Termos genericos demais para funcionarem como frase-chave sozinhos.
#:
#: A lista e curta de proposito: ela recusa a palavra ISOLADA, nao a palavra
#: dentro de uma frase. "serie" nao distingue nada; "nova serie da Netflix" sim.
_GENERIC_KEYPHRASES: Final[FrozenSet[str]] = frozenset(
    {"filme", "filmes", "serie", "series", "noticia", "noticias", "cinema", "tv",
     "streaming", "trailer", "estreia", "elenco", "temporada", "novidade", "novidades"}
)


@dataclass(frozen=True)
class SeoFinding:
    """Um achado de SEO, com severidade e campo."""

    severity: str
    code: str
    field: str
    detail: str = ""

    def __str__(self) -> str:  # pragma: no cover - trivial
        suffix = f" ({self.detail})" if self.detail else ""
        return f"{self.severity}:{self.code}:{self.field}{suffix}"


@dataclass
class SeoProposal:
    """A proposta pronta, mais o que se descobriu ao monta-la."""

    title: str
    meta_description: str
    slug_suggestion: Optional[str]
    focus_keyphrase: str
    schema_type_recommendation: str
    related_keyphrases: List[str] = field(default_factory=list)
    editorial_keywords: List[str] = field(default_factory=list)
    article_section_suggestion: Optional[str] = None
    open_graph_title: Optional[str] = None
    open_graph_description: Optional[str] = None
    twitter_title: Optional[str] = None
    twitter_description: Optional[str] = None
    image_alt_suggestions: List[Dict[str, str]] = field(default_factory=list)
    internal_link_suggestions: List[Dict[str, Any]] = field(default_factory=list)
    findings: List[SeoFinding] = field(default_factory=list)

    # -- severidades -------------------------------------------------------

    @property
    def blocking(self) -> List[SeoFinding]:
        return [item for item in self.findings if item.severity == BLOCKING]

    @property
    def auto_publish_blockers(self) -> List[SeoFinding]:
        return [item for item in self.findings if item.severity == AUTO_PUBLISH_INELIGIBLE]

    @property
    def warnings(self) -> List[SeoFinding]:
        return [item for item in self.findings if item.severity == WARNING]

    @property
    def auto_publishable(self) -> bool:
        """Elegivel para sair sem humano? Transportavel e outra pergunta."""
        return not self.blocking and not self.auto_publish_blockers

    def to_contract(self) -> Dict[str, Any]:
        """Serializacao com os nomes EXATOS do JSON Schema do Cinerie."""
        payload: Dict[str, Any] = {
            "version": seo_policy().version,
            "title": self.title,
            "metaDescription": self.meta_description,
            "slugSuggestion": self.slug_suggestion,
            "focusKeyphrase": self.focus_keyphrase,
            "relatedKeyphrases": list(self.related_keyphrases),
            "editorialKeywords": list(self.editorial_keywords),
            "schemaTypeRecommendation": self.schema_type_recommendation,
            "imageAltSuggestions": [dict(item) for item in self.image_alt_suggestions],
            "internalLinkSuggestions": [dict(item) for item in self.internal_link_suggestions],
        }
        optional = {
            "articleSectionSuggestion": self.article_section_suggestion,
            "openGraphTitleSuggestion": self.open_graph_title,
            "openGraphDescriptionSuggestion": self.open_graph_description,
            "twitterTitleSuggestion": self.twitter_title,
            "twitterDescriptionSuggestion": self.twitter_description,
        }
        # Campo opcional AUSENTE e diferente de campo opcional nulo: o contrato e
        # `.strict()` e nao aceita `null` onde espera string.
        payload.update({key: value for key, value in optional.items() if value})
        return payload


# --- Migracao do legado ----------------------------------------------------


def migrate_legacy_seo(source: Mapping[str, Any]) -> Dict[str, Any]:
    """Le um dicionario da IA (novo ou legado) numa forma neutra unica.

    Os campos neutros vencem quando os dois existem: se o prompt novo ja produziu
    ``openGraphTitleSuggestion``, um ``_yoast_wpseo_opengraph-title`` remanescente
    e residuo de uma execucao antiga.
    """
    neutral: Dict[str, Any] = {}

    legacy = source.get("yoast_meta")
    if isinstance(legacy, Mapping):
        for old, new in LEGACY_SEO_MIGRATION.items():
            value = legacy.get(old)
            if value:
                neutral[new] = value

    for key in (
        "title",
        "metaDescription",
        "focusKeyphrase",
        "openGraphTitleSuggestion",
        "openGraphDescriptionSuggestion",
        "twitterTitleSuggestion",
        "twitterDescriptionSuggestion",
        "articleSectionSuggestion",
    ):
        if source.get(key):
            neutral[key] = source[key]

    return neutral


# --- Matriz de Schema.org --------------------------------------------------


def resolve_schema_type(
    content_type: str,
    *,
    requested: Optional[str],
    has_steps: bool,
    has_list: bool,
    has_rating: bool,
) -> tuple[str, List[SeoFinding]]:
    """Recomendacao de Schema.org coerente com o tipo E com o corpo.

    Duas recusas diferentes:

    * fora da matriz -> **ambiguidade de classificacao**. Cai para ``Article``,
      que nao promete estrutura nenhuma e por isso nunca produz structured data
      falso, e registra um aviso.
    * dentro da matriz mas **sem a estrutura prometida** -> ``Review`` sem
      avaliacao, ``HowTo`` sem passos, ``ItemList`` sem itens. Isso e structured
      data falso, e cai para ``Article`` com achado de nao-elegibilidade.

    O MNScr **nunca presume o JSON-LD final**. Esta e uma recomendacao; quem
    renderiza — e quem pode recusa-la — e o Screen-App.
    """
    policy = seo_policy()
    findings: List[SeoFinding] = []
    allowed = policy.schema_types_for(content_type)
    fallback = "Article" if "Article" in allowed else (allowed[0] if allowed else "Article")

    # Sem recomendacao explicita, o padrao e o primeiro tipo permitido que NAO
    # promete estrutura. Para `news` isso e `NewsArticle`, que descreve o que a
    # materia e; para `guide` seria `HowTo`, que exige passos que o corpo nao
    # comprova — e nao pedir HowTo nao deve gerar achado nenhum.
    structural = {"HowTo", "ItemList", "Review"}
    default = next((item for item in allowed if item not in structural), fallback)

    candidate = collapse(requested) or default
    if candidate not in policy.schema_types:
        findings.append(
            SeoFinding(WARNING, "SCHEMA_TYPE_DESCONHECIDO", "seo.schemaTypeRecommendation", candidate)
        )
        return fallback, findings

    if candidate not in allowed:
        findings.append(
            SeoFinding(
                WARNING,
                "SCHEMA_TYPE_FORA_DA_MATRIZ",
                "seo.schemaTypeRecommendation",
                f"{candidate} nao consta em {content_type}",
            )
        )
        return fallback, findings

    required = {"HowTo": has_steps, "ItemList": has_list, "Review": has_rating}
    if candidate in required and not required[candidate]:
        findings.append(
            SeoFinding(
                AUTO_PUBLISH_INELIGIBLE,
                "SCHEMA_TYPE_SEM_ESTRUTURA",
                "seo.schemaTypeRecommendation",
                f"{candidate} exige estrutura que o corpo nao comprova",
            )
        )
        return fallback, findings

    return candidate, findings


# --- Construcao ------------------------------------------------------------


def _check_focus_keyphrase(
    focus: str,
    *,
    title: str,
    meta: str,
    lead: str,
    related: Sequence[str],
) -> List[SeoFinding]:
    """Validacao deterministica da frase-chave.

    Nao depende da resposta do modelo: a IA propoe a frase, mas quem verifica se
    ela aparece no lead, no titulo e na meta e este codigo. Um modelo que
    "garante" que colocou a keyphrase no lead nao e uma verificacao.
    """
    policy = seo_policy()
    findings: List[SeoFinding] = []
    field_name = "seo.focusKeyphrase"

    if not focus:
        return [SeoFinding(BLOCKING, "FOCUS_KEYPHRASE_AUSENTE", field_name)]

    length = len(focus)
    if length < policy.focus_keyphrase.min or length > policy.focus_keyphrase.max:
        return [SeoFinding(BLOCKING, "FOCUS_KEYPHRASE_FORA_DO_TAMANHO", field_name, str(length))]

    if find_forbidden_markup(focus) is not None:
        return [SeoFinding(BLOCKING, "FOCUS_KEYPHRASE_COM_MARKUP", field_name)]

    normalized = normalize_keyphrase(focus)
    if normalized in _GENERIC_KEYPHRASES:
        findings.append(
            SeoFinding(AUTO_PUBLISH_INELIGIBLE, "FOCUS_KEYPHRASE_GENERICA", field_name, focus)
        )

    if not contains_keyphrase(title, focus):
        findings.append(SeoFinding(WARNING, "FOCUS_KEYPHRASE_AUSENTE_NO_TITULO", "seo.title"))
    if not contains_keyphrase(meta, focus):
        findings.append(
            SeoFinding(WARNING, "FOCUS_KEYPHRASE_AUSENTE_NA_META", "seo.metaDescription")
        )
    if lead and not contains_keyphrase(lead, focus):
        # Presenca no lead e sinal editorial, nao regra de forma: o texto pode
        # dizer a mesma coisa com outras palavras e continuar bom.
        findings.append(SeoFinding(WARNING, "FOCUS_KEYPHRASE_AUSENTE_NO_LEAD", "blocks[0]"))

    # Uma related que repete a focus ja foi REMOVIDA pela deduplicacao — este
    # laco existe para o caso de a lista chegar pronta de outro caminho. Recusar
    # o pedido inteiro por causa disso seria desproporcional: o conteudo esta
    # certo, a lista e que veio redundante.
    for item in related:
        if singular_form(item) == singular_form(focus):
            findings.append(
                SeoFinding(WARNING, "RELATED_REPETE_A_FOCUS", "seo.relatedKeyphrases", item)
            )
            break

    return findings


#: Sugestoes sociais, na ordem em que sao descartadas quando a frase-chave
#: repete demais — da menos informativa para a mais — com o campo obrigatorio
#: que o CMS usa quando ela nao vem.
#:
#: Todas sao OPCIONAIS no contrato, e essa e a razao de o descarte ser barato:
#: sem ``openGraphTitleSuggestion`` o Cinerie usa ``seo.title``, que continua no
#: pedido. Nada que o leitor veria se perde.
_SOCIAL_SUGGESTIONS: Final[tuple[tuple[str, str, str], ...]] = (
    ("twitter_title", "seo.twitterTitleSuggestion", "title"),
    ("open_graph_title", "seo.openGraphTitleSuggestion", "title"),
    ("twitter_description", "seo.twitterDescriptionSuggestion", "meta_description"),
    ("open_graph_description", "seo.openGraphDescriptionSuggestion", "meta_description"),
)


def _keyphrase_occurrences(proposal: "SeoProposal") -> int:
    return count_occurrences(
        proposal.focus_keyphrase,
        [
            proposal.title,
            proposal.meta_description,
            proposal.open_graph_title,
            proposal.open_graph_description,
            proposal.twitter_title,
            proposal.twitter_description,
            *[item.get("alt") for item in proposal.image_alt_suggestions],
        ],
    )


def _relieve_stuffing(proposal: "SeoProposal") -> List[SeoFinding]:
    """Desfaz a repeticao excedente descartando sugestao social redundante.

    Detectar nao bastava, e a execucao real mostrou o preco: o MNScr media 4
    repeticoes contra o maximo de 3, registrava o achado como
    ``AUTO_PUBLISH_INELIGIBLE`` — um aviso — e mandava assim mesmo. O Cinerie
    respondeu ``422 BLOCKED / SEO_KEYWORD_STUFFING`` e a materia morreu. Saber do
    defeito e enviar mesmo assim e pior do que nao saber: gasta a tentativa e
    ainda registra uma recusa na auditoria do outro lado.

    A repeticao vinha inteira de duplicata: ``openGraphTitleSuggestion`` e
    ``twitterTitleSuggestion`` eram o titulo copiado letra por letra. Descartar
    uma copia identica de um campo que continua no pedido nao perde informacao
    nenhuma — o CMS ja cai no titulo quando a sugestao nao vem.

    Duas passadas, e a ordem e o argumento: primeiro saem as sugestoes
    REDUNDANTES (texto igual ao do campo-base), que nao custam nada; so se ainda
    faltar e que sai sugestao com texto proprio, e essa perda aparece no aviso
    com o campo nomeado. O texto editorial nunca e reescrito: campo opcional e
    removido por inteiro ou fica como veio.
    """
    policy = seo_policy()
    limit = policy.max_keyphrase_repetition
    findings: List[SeoFinding] = []

    if _keyphrase_occurrences(proposal) <= limit:
        return []

    for only_redundant in (True, False):
        for attribute, field_name, base_attribute in _SOCIAL_SUGGESTIONS:
            if _keyphrase_occurrences(proposal) <= limit:
                break
            value = getattr(proposal, attribute)
            if not value:
                continue
            base = getattr(proposal, base_attribute)
            redundant = normalize_keyphrase(value) == normalize_keyphrase(base)
            if only_redundant and not redundant:
                continue
            setattr(proposal, attribute, None)
            findings.append(
                SeoFinding(
                    WARNING,
                    "SUGESTAO_SOCIAL_DESCARTADA",
                    field_name,
                    (
                        f"copia identica de {base_attribute}; removida para respeitar o "
                        f"maximo de {limit} repeticoes da frase-chave"
                        if redundant
                        else f"sugestao propria removida: {limit} repeticoes da frase-chave "
                        "e o teto do Cinerie, e as copias redundantes ja tinham saido"
                    ),
                )
            )

    remaining = _keyphrase_occurrences(proposal)
    if remaining > limit:
        # Sobraram so campos obrigatorios repetindo a frase-chave. Aqui nao ha o
        # que descartar sem reescrever texto editorial, e reescrever nao e
        # trabalho desta camada.
        findings.append(
            SeoFinding(
                AUTO_PUBLISH_INELIGIBLE,
                "KEYWORD_STUFFING",
                "seo",
                f"{remaining} repeticoes da focus keyphrase mesmo apos descartar as "
                f"sugestoes opcionais (maximo {limit})",
            )
        )
    return findings


def build_seo_proposal(
    *,
    title: str,
    meta_description: str,
    focus_keyphrase: str,
    slug_suggestion: Optional[str],
    related_keyphrases: Sequence[str] = (),
    editorial_keywords: Sequence[str] = (),
    content_type: str = "news",
    schema_type: Optional[str] = None,
    article_section: Optional[str] = None,
    lead_text: str = "",
    body_blocks: Sequence[Mapping[str, Any]] = (),
    subtitle: str = "",
    summary: str = "",
    cluster_id: str = "",
    extra: Optional[Mapping[str, Any]] = None,
    media_ids: Sequence[str] = (),
    alt_texts: Optional[Mapping[str, str]] = None,
    internal_links: Sequence[Mapping[str, Any]] = (),
    has_steps: bool = False,
    has_list: bool = False,
    has_rating: bool = False,
) -> SeoProposal:
    """Monta a proposta e classifica tudo o que estiver fora de politica."""
    policy = seo_policy()
    neutral = migrate_legacy_seo(extra or {})
    findings: List[SeoFinding] = []

    paragraphs = body_texts(body_blocks)
    lead = lead_text or (paragraphs[0] if paragraphs else "")

    # Nenhum destes tres campos pode sair vazio ou fora da faixa de transporte:
    # os tres sao obrigatorios no contrato e dependem da saida da IA. Quando ela
    # omite, o valor e DERIVADO do proprio material da materia — e a derivacao
    # vira aviso, nunca silencio.
    title_derived = derive_seo_title(
        title=neutral.get("title") or title,
        subtitle=subtitle,
        summary=summary,
        lead=lead,
        minimum=policy.title.min,
        maximum=policy.title.max,
    )
    meta_derived = derive_meta_description(
        meta=neutral.get("metaDescription") or meta_description,
        summary=summary,
        lead=lead,
        body=paragraphs,
        minimum=policy.meta_description.min,
        maximum=policy.meta_description.max,
    )
    seo_title = title_derived.value
    seo_meta = meta_derived.value

    focus_derived = derive_focus_keyphrase(
        focus=neutral.get("focusKeyphrase") or focus_keyphrase,
        title=seo_title,
        slug=slug_suggestion,
        lead=lead,
        minimum=policy.focus_keyphrase.min,
        maximum=policy.focus_keyphrase.max,
    )
    focus = focus_derived.value

    for field_name, derived in (
        ("seo.title", title_derived),
        ("seo.metaDescription", meta_derived),
        ("seo.focusKeyphrase", focus_derived),
    ):
        if derived.is_fallback:
            findings.append(
                SeoFinding(
                    WARNING,
                    "CAMPO_DERIVADO_POR_FALLBACK",
                    field_name,
                    f"a IA nao entregou um valor utilizavel; derivado de {derived.source}",
                )
            )

    for value, length_policy, field_name in (
        (seo_title, policy.title, "seo.title"),
        (seo_meta, policy.meta_description, "seo.metaDescription"),
    ):
        severity = length_policy.classify(len(value))
        if severity is not None:
            findings.append(
                SeoFinding(
                    severity,
                    f"{field_name.split('.')[-1].upper()}_FORA_DA_FAIXA",
                    field_name,
                    f"{len(value)} caracteres; {length_policy.describe()}",
                )
            )

    related_input = [item for item in (collapse(value) for value in related_keyphrases or []) if item]
    related = dedupe_keyphrases(
        related_input,
        exclude=[focus],
        limit=policy.related_keyphrases_max,
        item_max_length=policy.related_keyphrase_item_max,
    )
    dropped = len(related_input) - len(related)
    if dropped > 0 and len(related_input) <= policy.related_keyphrases_max:
        # A lista foi consertada; o achado registra que ela veio redundante.
        # Sem isso, o defeito na geracao seria invisivel para sempre.
        findings.append(
            SeoFinding(
                WARNING,
                "RELATED_DUPLICADA_REMOVIDA",
                "seo.relatedKeyphrases",
                f"{dropped} keyphrase(s) redundante(s) removida(s)",
            )
        )
    keywords = dedupe_keyphrases(
        [*editorial_keywords, *_split_keywords(neutral.get("editorialKeywords"))],
        limit=policy.editorial_keywords_max,
        item_max_length=policy.editorial_keyword_item_max,
    )

    slug_derived = derive_slug(
        slug=slug_suggestion or seo_title,
        title=seo_title,
        focus=focus,
        cluster_id=cluster_id,
    )
    slug = slug_derived.value or None
    if slug_derived.is_fallback:
        findings.append(
            SeoFinding(
                WARNING,
                "CAMPO_DERIVADO_POR_FALLBACK",
                "seo.slugSuggestion",
                f"slug proposta inutilizavel; derivada de {slug_derived.source}",
            )
        )
    issue = slug_issue(slug)
    if issue is not None:
        findings.append(SeoFinding(BLOCKING, issue.split(":")[0], "seo.slugSuggestion", issue))

    schema_recommendation, schema_findings = resolve_schema_type(
        content_type,
        requested=schema_type,
        has_steps=has_steps,
        has_list=has_list,
        has_rating=has_rating,
    )
    findings.extend(schema_findings)

    findings.extend(
        _check_focus_keyphrase(focus, title=seo_title, meta=seo_meta, lead=lead, related=related)
    )

    proposal = SeoProposal(
        title=seo_title,
        meta_description=seo_meta,
        slug_suggestion=slug,
        focus_keyphrase=focus,
        schema_type_recommendation=schema_recommendation,
        related_keyphrases=related,
        editorial_keywords=keywords,
        article_section_suggestion=plain_text(
            neutral.get("articleSectionSuggestion") or article_section or "", max_length=500
        )
        or None,
        open_graph_title=plain_text(
            neutral.get("openGraphTitleSuggestion") or "", max_length=policy.social_title_max
        )
        or None,
        open_graph_description=plain_text(
            neutral.get("openGraphDescriptionSuggestion") or "",
            max_length=policy.social_description_max,
        )
        or None,
        twitter_title=plain_text(
            neutral.get("twitterTitleSuggestion") or "", max_length=policy.social_title_max
        )
        or None,
        twitter_description=plain_text(
            neutral.get("twitterDescriptionSuggestion") or "",
            max_length=policy.social_description_max,
        )
        or None,
        image_alt_suggestions=build_alt_suggestions(alt_texts, media_ids=media_ids),
        internal_link_suggestions=build_internal_links(internal_links),
        findings=findings,
    )

    proposal.findings.extend(_relieve_stuffing(proposal))
    if alt_texts and not proposal.image_alt_suggestions:
        proposal.findings.append(
            SeoFinding(
                WARNING,
                "ALT_SEM_MIDIA_CORRESPONDENTE",
                "seo.imageAltSuggestions",
                "o pedido nao referencia midia do CMS; as sugestoes de alt nao tem a que se ligar",
            )
        )
    return proposal


def _split_keywords(value: Any) -> List[str]:
    """``"kw1, kw2, kw3"`` do Yoast vira lista. Lista continua lista."""
    if isinstance(value, str):
        return [part for part in (item.strip() for item in value.split(",")) if part]
    if isinstance(value, (list, tuple)):
        return [collapse(item) for item in value if collapse(item)]
    return []


def build_alt_suggestions(
    alt_texts: Optional[Mapping[str, str]],
    *,
    media_ids: Sequence[str],
) -> List[Dict[str, str]]:
    """Alts sugeridos, **somente** para midia presente no pedido.

    Um alt sem midia correspondente e texto solto que nunca chega a imagem
    nenhuma — e o contrato o recusa. Enquanto o MNScr nao conhece ids de midia do
    Cinerie, esta lista sai vazia: e o resultado honesto, nao um esquecimento.
    """
    if not alt_texts:
        return []
    policy = seo_policy()
    known = list(dict.fromkeys(str(item) for item in media_ids if item))
    result: List[Dict[str, str]] = []
    used: set[str] = set()
    for key, value in alt_texts.items():
        media_ref = str(key)
        if media_ref not in known or media_ref in used:
            continue
        alt = plain_text(value, max_length=policy.image_alt_max)
        if len(alt) < policy.image_alt_min or find_forbidden_markup(alt) is not None:
            continue
        used.add(media_ref)
        result.append({"mediaRef": media_ref, "alt": alt})
        if len(result) >= policy.image_alt_max_items:
            break
    return result


def build_internal_links(links: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Links internos estruturados. Nada de URL inventada, nada de id cru.

    ``targetPath`` e caminho de site; uma URL absoluta ali apontaria para fora do
    Cinerie com cara de link interno. Sem ``targetId`` nem ``targetPath`` a
    sugestao nao diz para onde vai, e e descartada em vez de enviada quebrada.
    """
    policy = seo_policy()
    allowed = set(policy.internal_link_target_types)
    result: List[Dict[str, Any]] = []
    seen: set[tuple] = set()

    for link in links or []:
        if not isinstance(link, Mapping):
            continue
        target_type = collapse(link.get("targetType"))
        if target_type not in allowed:
            continue

        target_id = collapse(link.get("targetId")) or None
        target_path = collapse(link.get("targetPath")) or None
        if target_path is not None and not target_path.startswith("/"):
            # URL absoluta ou caminho relativo: os dois seriam link externo
            # disfarcado de interno.
            target_path = None
        if target_id is None and target_path is None:
            continue

        anchor = plain_text(link.get("anchorText"), max_length=500)
        if not anchor or find_forbidden_markup(anchor) is not None:
            continue

        key = (target_type, target_id, target_path, normalize_keyphrase(anchor))
        if key in seen:
            continue
        seen.add(key)

        entry: Dict[str, Any] = {"targetType": target_type, "anchorText": anchor}
        if target_id is not None:
            entry["targetId"] = target_id
        if target_path is not None:
            entry["targetPath"] = target_path
        reason = plain_text(link.get("reason"), max_length=500)
        if reason:
            entry["reason"] = reason
        confidence = link.get("confidence")
        if isinstance(confidence, (int, float)) and 0.0 <= float(confidence) <= 1.0:
            entry["confidence"] = float(confidence)
        context = plain_text(link.get("sourceContext"), max_length=500)
        if context:
            entry["sourceContext"] = context

        result.append(entry)
        if len(result) >= policy.internal_links_max:
            break

    return result


def lead_text_of(blocks: Sequence[Mapping[str, Any]]) -> str:
    """Texto do primeiro paragrafo — o "lead" contra o qual a focus e conferida."""
    for block in blocks or []:
        if isinstance(block, Mapping) and block.get("type") == "paragraph":
            return str(block.get("text") or "")
    return ""


def summarize_findings(findings: Sequence[SeoFinding]) -> Dict[str, int]:
    counts = {BLOCKING: 0, AUTO_PUBLISH_INELIGIBLE: 0, WARNING: 0}
    for item in findings:
        counts[item.severity] = counts.get(item.severity, 0) + 1
    return counts


__all__ = [
    "LEGACY_SEO_MIGRATION",
    "SeoFinding",
    "SeoProposal",
    "build_alt_suggestions",
    "build_internal_links",
    "build_seo_proposal",
    "lead_text_of",
    "migrate_legacy_seo",
    "resolve_schema_type",
    "summarize_findings",
    "word_count",
]
