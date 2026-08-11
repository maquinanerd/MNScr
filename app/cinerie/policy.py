"""Politica canonica de SEO — UMA fonte de verdade dentro do MNScr.

Antes desta fase o projeto tinha tres numeros diferentes para a mesma coisa: o
prompt pedia titulo de no maximo 65, o otimizador considerava ideal ate 70 e o
sanitizador aceitava 90. Nenhum deles estava errado isoladamente; o defeito era
nao existir uma politica que explicasse a diferenca — e o efeito pratico era um
titulo que passava numa etapa e era recusado na seguinte, sem que nada dissesse
qual regra valia.

A distincao que resolve isso vem do proprio Cinerie e tem duas faixas:

``TRANSPORTE``
    o que o JSON Schema aceita. Faixa larga de proposito: o CMS precisa
    RECEBER material imperfeito destinado a revisao humana.

``AUTOPUBLICACAO``
    o que sai sem humano. Confundir os dois seria tomar "cabe no campo" por
    "esta bom o suficiente para ir ao ar sozinho".

Fora da faixa de autopublicacao e dentro da de transporte, o pedido segue e o
Cinerie o encaminha para revisao. Fora da de transporte, ele nem sai daqui.

Os numeros NAO sao redigitados: vem de ``contracts/cinerie/seo-policy.json``,
exportado do mesmo ``SEO_POLICY`` que o Screen-App usa para validar. Redigita-los
recriaria exatamente o problema que esta fase fecha.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Dict, Final, List, Mapping, Optional, Tuple

from .contract import load_seo_policy
from .errors import ContractArtifactError

#: Severidades de um achado de SEO.
#:
#: A do meio e a que faltava: um campo pode ser perfeitamente valido para
#: transporte e ainda assim nao ser elegivel para publicar sozinho.
BLOCKING: Final[str] = "BLOCKING"
WARNING: Final[str] = "WARNING"
AUTO_PUBLISH_INELIGIBLE: Final[str] = "AUTO_PUBLISH_INELIGIBLE"

SEVERITIES: Final[Tuple[str, ...]] = (BLOCKING, AUTO_PUBLISH_INELIGIBLE, WARNING)


@dataclass(frozen=True)
class LengthPolicy:
    """Faixas de um campo de texto.

    ``min``/``max`` sao transporte; ``auto_min``/``auto_max`` sao
    autopublicacao; ``editorial_min``/``editorial_max`` sao a preferencia da
    redacao — documentada, nunca bloqueante.
    """

    name: str
    min: int
    max: int
    auto_min: Optional[int] = None
    auto_max: Optional[int] = None
    ideal_max: Optional[int] = None
    editorial_min: Optional[int] = None
    editorial_max: Optional[int] = None

    def transportable(self, length: int) -> bool:
        return self.min <= length <= self.max

    def auto_publishable(self, length: int) -> bool:
        low = self.min if self.auto_min is None else self.auto_min
        high = self.max if self.auto_max is None else self.auto_max
        return low <= length <= high

    def classify(self, length: int) -> Optional[str]:
        """``None`` quando o tamanho e bom em todas as faixas."""
        if not self.transportable(length):
            return BLOCKING
        if not self.auto_publishable(length):
            return AUTO_PUBLISH_INELIGIBLE
        if (
            self.editorial_min is not None
            and self.editorial_max is not None
            and not (self.editorial_min <= length <= self.editorial_max)
        ):
            return WARNING
        return None

    def describe(self) -> str:
        auto = (
            ""
            if self.auto_min is None or self.auto_max is None
            else f", autopublica {self.auto_min}-{self.auto_max}"
        )
        return f"{self.name}: transporte {self.min}-{self.max}{auto}"


def _int(mapping: Mapping[str, Any], key: str) -> Optional[int]:
    value = mapping.get(key)
    return int(value) if isinstance(value, (int, float)) else None


def _require_int(mapping: Mapping[str, Any], key: str, *, field: str) -> int:
    value = _int(mapping, key)
    if value is None:
        raise ContractArtifactError(
            f"politica de SEO do Cinerie sem `{field}.{key}`; o pacote contratual esta incompleto"
        )
    return value


def _length_policy(raw: Mapping[str, Any], field: str) -> LengthPolicy:
    section = raw.get(field)
    if not isinstance(section, Mapping):
        raise ContractArtifactError(f"politica de SEO do Cinerie sem a secao `{field}`")
    return LengthPolicy(
        name=field,
        min=_require_int(section, "min", field=field),
        max=_require_int(section, "max", field=field),
        auto_min=_int(section, "autoMin"),
        auto_max=_int(section, "autoMax"),
        ideal_max=_int(section, "idealMax"),
        editorial_min=_int(section, "editorialMin"),
        editorial_max=_int(section, "editorialMax"),
    )


@dataclass(frozen=True)
class SeoPolicy:
    """A politica inteira, lida do artefato do Cinerie."""

    version: str
    title: LengthPolicy
    meta_description: LengthPolicy
    focus_keyphrase: LengthPolicy
    related_keyphrases_max: int
    related_keyphrase_item_max: int
    editorial_keywords_max: int
    editorial_keyword_item_max: int
    social_title_max: int
    social_description_max: int
    image_alt_min: int
    image_alt_max: int
    image_alt_max_items: int
    internal_links_max: int
    max_keyphrase_repetition: int
    schema_types: Tuple[str, ...]
    schema_by_content_type: Mapping[str, Tuple[str, ...]]
    content_types: Tuple[str, ...]
    publication_intents: Tuple[str, ...]
    attribution_modes: Tuple[str, ...]
    internal_link_target_types: Tuple[str, ...]
    forbidden_seo_keys: Tuple[str, ...]
    forbidden_publication_keys: Tuple[str, ...]
    forbidden_top_level_seo_keys: Tuple[str, ...]

    def schema_types_for(self, content_type: str) -> Tuple[str, ...]:
        return tuple(self.schema_by_content_type.get(content_type, ()))

    def schema_matches_content_type(self, content_type: str, schema_type: str) -> bool:
        return schema_type in self.schema_types_for(content_type)


def _tuple(raw: Mapping[str, Any], key: str) -> Tuple[str, ...]:
    value = raw.get(key)
    if not isinstance(value, list):
        raise ContractArtifactError(f"politica de SEO do Cinerie sem a lista `{key}`")
    return tuple(str(item) for item in value)


@lru_cache(maxsize=1)
def seo_policy() -> SeoPolicy:
    """A politica canonica. Uma unica leitura, memorizada."""
    document = load_seo_policy()
    raw = document.get("policy")
    if not isinstance(raw, Mapping):
        raise ContractArtifactError("politica de SEO do Cinerie sem a chave `policy`")

    def section(name: str) -> Mapping[str, Any]:
        value = raw.get(name)
        if not isinstance(value, Mapping):
            raise ContractArtifactError(f"politica de SEO do Cinerie sem a secao `{name}`")
        return value

    by_content_type_raw = document.get("schemaByContentType")
    if not isinstance(by_content_type_raw, Mapping):
        raise ContractArtifactError("politica de SEO do Cinerie sem `schemaByContentType`")
    by_content_type: Dict[str, Tuple[str, ...]] = {
        str(key): tuple(str(item) for item in value)
        for key, value in by_content_type_raw.items()
        if isinstance(value, list)
    }

    image_alt = section("imageAlt")
    max_repetition = _int(raw, "maxKeyphraseRepetition")
    if max_repetition is None:
        raise ContractArtifactError("politica de SEO do Cinerie sem `maxKeyphraseRepetition`")

    return SeoPolicy(
        version=str(document.get("seoProposalVersion") or ""),
        title=_length_policy(raw, "title"),
        meta_description=_length_policy(raw, "metaDescription"),
        focus_keyphrase=_length_policy(raw, "focusKeyphrase"),
        related_keyphrases_max=_require_int(
            section("relatedKeyphrases"), "max", field="relatedKeyphrases"
        ),
        related_keyphrase_item_max=_require_int(
            section("relatedKeyphrases"), "itemMax", field="relatedKeyphrases"
        ),
        editorial_keywords_max=_require_int(
            section("editorialKeywords"), "max", field="editorialKeywords"
        ),
        editorial_keyword_item_max=_require_int(
            section("editorialKeywords"), "itemMax", field="editorialKeywords"
        ),
        social_title_max=_require_int(section("socialTitle"), "max", field="socialTitle"),
        social_description_max=_require_int(
            section("socialDescription"), "max", field="socialDescription"
        ),
        image_alt_min=_require_int(image_alt, "min", field="imageAlt"),
        image_alt_max=_require_int(image_alt, "max", field="imageAlt"),
        image_alt_max_items=_require_int(image_alt, "max_items", field="imageAlt"),
        internal_links_max=_require_int(section("internalLinks"), "max", field="internalLinks"),
        max_keyphrase_repetition=max_repetition,
        schema_types=_tuple(document, "schemaTypeRecommendations"),
        schema_by_content_type=by_content_type,
        content_types=_tuple(document, "publicationContentTypes"),
        publication_intents=_tuple(document, "publicationIntents"),
        attribution_modes=_tuple(document, "attributionModes"),
        internal_link_target_types=_tuple(document, "internalLinkTargetTypes"),
        forbidden_seo_keys=_tuple(document, "forbiddenSeoKeys"),
        forbidden_publication_keys=_tuple(document, "forbiddenPublicationKeys"),
        forbidden_top_level_seo_keys=_tuple(document, "forbiddenTopLevelSeoKeys"),
    )


def seo_proposal_version() -> str:
    return seo_policy().version


def policy_summary() -> List[str]:
    """Resumo legivel, usado na documentacao e no CLI."""
    policy = seo_policy()
    return [
        policy.title.describe(),
        policy.meta_description.describe(),
        f"focusKeyphrase: {policy.focus_keyphrase.min}-{policy.focus_keyphrase.max}",
        f"relatedKeyphrases: ate {policy.related_keyphrases_max}",
        f"editorialKeywords: ate {policy.editorial_keywords_max}",
        f"repeticao maxima da focus keyphrase: {policy.max_keyphrase_repetition}",
    ]


__all__ = [
    "AUTO_PUBLISH_INELIGIBLE",
    "BLOCKING",
    "LengthPolicy",
    "SEVERITIES",
    "SeoPolicy",
    "WARNING",
    "policy_summary",
    "seo_policy",
    "seo_proposal_version",
]
