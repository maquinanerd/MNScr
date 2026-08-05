"""Protocolo de categorias em tres niveis.

    nivel 1  Noticias | Lista | Review | ...   ->  `contentType` do contrato
    nivel 2  Cinema | Series                   ->  `seo.articleSectionSuggestion`
    nivel 3  nome do filme, serie, ator, ...   ->  `entityLinks[]`

**Os tres sao PROPOSTA.** O contrato `editorial-publication-request-v1` do
Cinerie tem uma secao chamada "O que o produtor NAO decide", e a service account
do MNScr so pode ter os escopos `draft_ingest`, `publication_projection` e
`editorial_auto_publish` — nenhum deles cria taxonomia. Este modulo existe para
que o MNScr proponha de forma disciplinada, nao para que ele decida.

Tres consequencias praticas, e nenhuma delas e negociavel deste lado:

1. **Nivel 1 e enum fechado.** `CONTENT_TYPES` espelha o contrato. Um tipo fora
   da lista nao e "categoria nova": e pedido recusado com `422 BLOCKED`.
2. **Nivel 2 e sugestao.** O CMS pode ignorar. Enviar como se fosse decisao
   criaria duas fontes discordando sobre a secao de uma materia.
3. **Nivel 3 exige `entityId` que ja existe no CMS.** Um nome sem id resolvido
   NUNCA vira `entityLink` — vira proposta registrada para um humano. Inventar
   id aqui produziria link para a entidade errada, que e pior que link nenhum.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, Final, FrozenSet, List, Optional, Sequence, Tuple

# ===========================================================================
# Nivel 1 — contentType (espelho do enum do contrato)
# ===========================================================================

#: Espelha `PUBLICATION_CONTENT_TYPES` de `@screena/editorial-contracts`.
#: Divergir daqui e enviar um pedido que o CMS recusa antes de olhar o conteudo.
CONTENT_TYPES: Final[Tuple[str, ...]] = (
    "news",
    "feature",
    "review",
    "guide",
    "list",
    "interview",
    "evergreen",
)
CONTENT_TYPE_SET: Final[FrozenSet[str]] = frozenset(CONTENT_TYPES)

#: Rotulo editorial em portugues -> tipo do contrato. So para leitura humana:
#: o que viaja no pedido e sempre o valor do contrato.
CONTENT_TYPE_LABELS: Final[Dict[str, str]] = {
    "news": "Notícias",
    "list": "Lista",
    "review": "Review",
    "feature": "Reportagem",
    "guide": "Guia",
    "interview": "Entrevista",
    "evergreen": "Atemporal",
}

#: `classify_article_type` do policy_engine devolve quatro valores proprios, e
#: `analysis` nao existe no contrato. Mapeado para `feature`, que e o tipo do
#: contrato para texto autoral que nao e resenha.
_ARTICLE_TYPE_TO_CONTENT_TYPE: Final[Dict[str, str]] = {
    "news": "news",
    "list": "list",
    "guide": "guide",
    "analysis": "feature",
    "review": "review",
}

#: Sinais de resenha PROPRIA — veredito sobre a obra, nao noticia sobre ela.
#: Deliberadamente mais estreito que `_ANALYSIS_SIGNALS` do policy_engine, que
#: junta resenha, opiniao e coluna no mesmo balde `analysis`.
_REVIEW_SIGNALS: Final[re.Pattern[str]] = re.compile(
    r"\b(cr[ií]tica\s+d[eoa]|resenha\s+d[eoa]|review\s+d[eoa]|"
    r"nossa\s+(cr[ií]tica|resenha|an[aá]lise)|"
    r"vale\s+a\s+pena\s+(assistir|ver)|nota\s+\d(?:[.,]\d)?\s*/\s*\d{1,2})\b",
    re.IGNORECASE,
)

# ===========================================================================
# Nivel 2 — secao
# ===========================================================================

SECTION_CINEMA: Final[str] = "Cinema"
SECTION_SERIES: Final[str] = "Séries"
SECTIONS: Final[Tuple[str, ...]] = (SECTION_CINEMA, SECTION_SERIES)

# ===========================================================================
# Nivel 3 — entidades (espelho de ENTITY_KINDS do contrato)
# ===========================================================================

ENTITY_KINDS: Final[Tuple[str, ...]] = (
    "movie",
    "tv",
    "season",
    "episode",
    "person",
    "character",
    "franchise",
)
ENTITY_KIND_SET: Final[FrozenSet[str]] = frozenset(ENTITY_KINDS)

#: Espelha `publicationEntityLink.relation` do contrato.
ENTITY_RELATIONS: Final[Tuple[str, ...]] = (
    "primary_subject",
    "secondary_subject",
    "mentioned",
    "reviewed",
    "recommended",
    "compared",
)
ENTITY_RELATION_SET: Final[FrozenSet[str]] = frozenset(ENTITY_RELATIONS)

#: A secao (nivel 2) e DERIVADA do tipo da entidade principal (nivel 3), nunca
#: adivinhada do texto. `person`, `character` e `franchise` nao decidem secao:
#: um ator faz filme e serie, e uma franquia costuma ser as duas coisas.
_KIND_TO_SECTION: Final[Dict[str, Optional[str]]] = {
    "movie": SECTION_CINEMA,
    "tv": SECTION_SERIES,
    "season": SECTION_SERIES,
    "episode": SECTION_SERIES,
    "person": None,
    "character": None,
    "franchise": None,
}


@dataclass(frozen=True)
class EntityProposal:
    """Uma entidade que o rascunho trata — nivel 3 do protocolo.

    ``entity_id`` so e preenchido quando resolvido contra o catalogo do CMS.
    Enquanto for ``None``, esta proposta e informacao editorial para um humano,
    e nao pode virar `entityLink` no pedido de publicacao.
    """

    name: str
    kind: str
    relation: str = "mentioned"
    confidence: float = 0.5
    entity_id: Optional[str] = None

    @property
    def resolved(self) -> bool:
        return bool(self.entity_id)


@dataclass(frozen=True)
class TaxonomyProposal:
    """Os tres niveis propostos para um rascunho."""

    content_type: str
    section: Optional[str] = None
    entities: Tuple[EntityProposal, ...] = ()
    warnings: Tuple[str, ...] = field(default=())

    @property
    def resolved_entities(self) -> Tuple[EntityProposal, ...]:
        """Somente as que podem virar `entityLinks` no pedido."""
        return tuple(e for e in self.entities if e.resolved)

    @property
    def unresolved_entities(self) -> Tuple[EntityProposal, ...]:
        return tuple(e for e in self.entities if not e.resolved)

    @property
    def label(self) -> str:
        """Caminho legivel: ``Notícias / Cinema / Superman``."""
        parts: List[str] = [CONTENT_TYPE_LABELS.get(self.content_type, self.content_type)]
        if self.section:
            parts.append(self.section)
        primary = self.primary_entity()
        if primary is not None:
            parts.append(primary.name)
        return " / ".join(parts)

    def primary_entity(self) -> Optional[EntityProposal]:
        for entity in self.entities:
            if entity.relation in ("primary_subject", "reviewed"):
                return entity
        return self.entities[0] if self.entities else None


def map_content_type(
    article_type: str,
    *,
    title: str = "",
    content_snippet: str = "",
) -> Tuple[str, List[str]]:
    """Nivel 1: tipo do MNScr -> `contentType` do contrato.

    A promocao para ``review`` acontece aqui, e nao no ``policy_engine``, porque
    ela carrega uma consequencia editorial que o classificador de tamanho de
    texto nao tem como pesar: ``contentType: review`` significa que a Cinerie
    assina uma resenha PROPRIA. Como o MNScr reescreve materia de terceiros, o
    aviso viaja junto — o proprio contrato recusa `schemaTypeRecommendation:
    Review` pelo mesmo motivo ("schema falso sem review propria").
    """
    warnings: List[str] = []
    normalized = (article_type or "").strip().lower()

    content_type = _ARTICLE_TYPE_TO_CONTENT_TYPE.get(normalized)
    if content_type is None:
        warnings.append(f"TAXONOMY_UNKNOWN_ARTICLE_TYPE:{normalized or '-'}")
        content_type = "news"

    if content_type in ("feature", "news") and _REVIEW_SIGNALS.search(f"{title} {content_snippet[:1500]}"):
        content_type = "review"
        warnings.append("TAXONOMY_REVIEW_FROM_THIRD_PARTY_SOURCE")

    return content_type, warnings


def infer_section(entities: Sequence[EntityProposal]) -> Tuple[Optional[str], List[str]]:
    """Nivel 2: derivado do tipo da entidade principal, com desempate por maioria.

    Devolve ``None`` quando o material nao decide. ``None`` e um resultado
    legitimo: o CMS tem catalogo e decide melhor que um chute daqui, e uma secao
    errada e mais cara que secao nenhuma.
    """
    warnings: List[str] = []
    if not entities:
        return None, ["TAXONOMY_SECTION_UNDECIDED:sem_entidades"]

    for entity in entities:
        if entity.relation in ("primary_subject", "reviewed"):
            section = _KIND_TO_SECTION.get(entity.kind)
            if section is not None:
                return section, warnings

    counts: Dict[str, int] = {}
    for entity in entities:
        section = _KIND_TO_SECTION.get(entity.kind)
        if section is not None:
            counts[section] = counts.get(section, 0) + 1

    if not counts:
        return None, ["TAXONOMY_SECTION_UNDECIDED:sem_obra_identificada"]

    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
        return None, ["TAXONOMY_SECTION_UNDECIDED:empate"]
    return ranked[0][0], warnings


def build_taxonomy_proposal(
    *,
    article_type: str,
    title: str = "",
    content_snippet: str = "",
    entities: Sequence[EntityProposal] = (),
) -> TaxonomyProposal:
    """Monta os tres niveis e registra tudo que ficou indeciso."""
    content_type, warnings = map_content_type(
        article_type, title=title, content_snippet=content_snippet
    )
    valid_entities = tuple(e for e in entities if e.kind in ENTITY_KIND_SET)
    if len(valid_entities) != len(entities):
        warnings.append(f"TAXONOMY_ENTITY_KIND_INVALID:{len(entities) - len(valid_entities)}")

    section, section_warnings = infer_section(valid_entities)
    warnings.extend(section_warnings)

    unresolved = [e for e in valid_entities if not e.resolved]
    if unresolved:
        # Nao e erro: e o estado normal enquanto nao existe resolucao contra o
        # catalogo do CMS. Registrado para que a ausencia de `entityLinks` no
        # pedido tenha explicacao, em vez de parecer que o texto nao falava de
        # nada.
        warnings.append(f"TAXONOMY_ENTITIES_UNRESOLVED:{len(unresolved)}")

    return TaxonomyProposal(
        content_type=content_type,
        section=section,
        entities=valid_entities,
        warnings=tuple(warnings),
    )


def validate_proposal(proposal: TaxonomyProposal) -> List[str]:
    """Problemas que fariam o CMS recusar o pedido (`422 BLOCKED`)."""
    issues: List[str] = []

    if proposal.content_type not in CONTENT_TYPE_SET:
        issues.append(
            f"contentType '{proposal.content_type}' nao existe no contrato. "
            f"Aceitos: {', '.join(CONTENT_TYPES)}"
        )
    if proposal.section is not None and proposal.section not in SECTIONS:
        issues.append(
            f"secao '{proposal.section}' fora do protocolo. Aceitas: {', '.join(SECTIONS)}"
        )

    for index, entity in enumerate(proposal.entities):
        if entity.kind not in ENTITY_KIND_SET:
            issues.append(f"entities[{index}].kind '{entity.kind}' nao existe no contrato")
        if entity.relation not in ENTITY_RELATION_SET:
            issues.append(f"entities[{index}].relation '{entity.relation}' nao existe no contrato")
        if not (0.0 <= entity.confidence <= 1.0):
            issues.append(f"entities[{index}].confidence fora de [0,1]: {entity.confidence}")
        if not entity.name.strip():
            issues.append(f"entities[{index}].name vazio")

    return issues


def to_entity_links(proposal: TaxonomyProposal) -> List[Dict[str, object]]:
    """`entityLinks` do pedido de publicacao — SO as entidades resolvidas.

    Uma proposta sem `entity_id` fica de fora por construcao. O contrato exige
    `entityId` valido, e um id inventado apontaria para outra obra: o leitor
    veria a materia ligada ao filme errado, e a ligacao erraria em silencio.
    """
    return [
        {
            "entityKind": entity.kind,
            "entityId": entity.entity_id,
            "relation": entity.relation,
            "confidence": round(float(entity.confidence), 4),
        }
        for entity in proposal.resolved_entities
    ]


__all__ = [
    "CONTENT_TYPES",
    "CONTENT_TYPE_LABELS",
    "CONTENT_TYPE_SET",
    "ENTITY_KINDS",
    "ENTITY_KIND_SET",
    "ENTITY_RELATIONS",
    "EntityProposal",
    "SECTIONS",
    "SECTION_CINEMA",
    "SECTION_SERIES",
    "TaxonomyProposal",
    "build_taxonomy_proposal",
    "infer_section",
    "map_content_type",
    "to_entity_links",
    "validate_proposal",
]
