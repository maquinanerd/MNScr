"""Varredura de campos recusados pelo contrato — antes do JSON Schema.

O Cinerie roda tres varreduras ANTES de validar o schema, e a ordem importa.
``additionalProperties: false`` ja recusaria os mesmos campos, mas com um
"propriedade adicional nao permitida" que nao ensina nada: o pipeline corrigiria
adivinhando. Nomear o campo e dizer POR QUE ele nao entra e a diferenca entre
consertar o produtor e tentar de novo.

As tres varreduras tem profundidades diferentes, e isso e deliberado:

``publicacao``
    profunda. Identidade tecnica escondida dentro de ``provenance`` ainda e
    identidade tecnica declarada pelo cliente.

``SEO duplicado``
    **so no topo**. ``metaDescription``, ``slugSuggestion`` e ``focusKeyphrase``
    sao os campos REAIS dentro de ``seo``; varre-los fundo recusaria o proprio
    objeto valido.

``SEO de terceiro``
    profunda. ``_yoast_wpseo_title`` aninhado carrega a semantica do outro CMS
    do mesmo jeito.

As listas nao sao redigitadas: vem de ``contracts/cinerie/seo-policy.json``,
exportado dos mesmos arrays do Screen-App.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Final, FrozenSet, Optional, Tuple

from .policy import seo_policy

MAX_DEPTH: Final[int] = 12

_SEPARATORS: Final[re.Pattern[str]] = re.compile(r"[_-]")

#: Grupos de recusa. A mensagem por grupo diz o que o produtor tentou fazer.
_IDENTITY_KEYS: Final[FrozenSet[str]] = frozenset(
    {
        "technicalactor",
        "technicalactorid",
        "serviceaccount",
        "serviceaccountid",
        "serviceaccountlabel",
        "actor",
        "actorid",
        "publishedby",
        "createdby",
        "updatedby",
        "scopes",
        "scope",
        "authenticatedas",
    }
)


def normalize_key(key: str) -> str:
    """``post_status``, ``postStatus`` e ``POST-STATUS`` sao a mesma tentativa."""
    return _SEPARATORS.sub("", str(key).lower())


@lru_cache(maxsize=1)
def _publication_keys() -> FrozenSet[str]:
    return frozenset(normalize_key(key) for key in seo_policy().forbidden_publication_keys)


@lru_cache(maxsize=1)
def _top_level_seo_keys() -> FrozenSet[str]:
    return frozenset(normalize_key(key) for key in seo_policy().forbidden_top_level_seo_keys)


@lru_cache(maxsize=1)
def _seo_keys() -> FrozenSet[str]:
    return frozenset(normalize_key(key) for key in seo_policy().forbidden_seo_keys)


@dataclass(frozen=True)
class ForbiddenKeyFinding:
    """O campo recusado, onde ele estava e por que nao entra."""

    key: str
    path: str
    reason: str
    group: str


def _scan(value: Any, keys: FrozenSet[str], path: str, depth: int) -> Optional[Tuple[str, str]]:
    if depth > MAX_DEPTH or value is None:
        return None
    if isinstance(value, list):
        for index, item in enumerate(value):
            found = _scan(item, keys, f"{path}[{index}]" if path else f"[{index}]", depth + 1)
            if found is not None:
                return found
        return None
    if not isinstance(value, dict):
        return None
    for key, item in value.items():
        if normalize_key(key) in keys:
            return key, f"{path}.{key}" if path else str(key)
        found = _scan(item, keys, f"{path}.{key}" if path else str(key), depth + 1)
        if found is not None:
            return found
    return None


def explain_publication_key(key: str) -> Tuple[str, str]:
    """Mensagem e grupo de um campo de publicacao recusado."""
    if normalize_key(key) in _IDENTITY_KEYS:
        return (
            "identidade tecnica e derivada da credencial autenticada, nunca declarada no corpo",
            "IDENTIDADE_TECNICA",
        )
    return (
        "campo de estado publico nao pode vir do produtor "
        "(o Cinerie decide publicacao, datas e indexacao)",
        "ESTADO_PUBLICO",
    )


def find_forbidden_publication_key(payload: Any) -> Optional[ForbiddenKeyFinding]:
    found = _scan(payload, _publication_keys(), "", 0)
    if found is None:
        return None
    key, path = found
    reason, group = explain_publication_key(key)
    return ForbiddenKeyFinding(key=key, path=path, reason=reason, group=group)


def find_duplicate_seo_key(payload: Any) -> Optional[ForbiddenKeyFinding]:
    """SEO solto no nivel superior. NAO recursivo — ver o docstring do modulo."""
    if not isinstance(payload, dict):
        return None
    for key in payload:
        if normalize_key(key) in _top_level_seo_keys():
            return ForbiddenKeyFinding(
                key=key,
                path=str(key),
                reason=(
                    "SEO tem UMA fonte de verdade neste contrato: o objeto `seo`. "
                    "Campo solto no nivel superior nao e aceito"
                ),
                group="SEO_DUPLICADO",
            )
    return None


def find_forbidden_seo_key(payload: Any) -> Optional[ForbiddenKeyFinding]:
    found = _scan(payload, _seo_keys(), "", 0)
    if found is None:
        return None
    key, path = found
    return ForbiddenKeyFinding(
        key=key,
        path=path,
        reason=(
            "campo de CMS de terceiro (WordPress/Yoast) ou de SEO tecnico "
            "nao e aceito neste contrato"
        ),
        group="CMS_DE_TERCEIRO",
    )


def find_forbidden_key(payload: Any) -> Optional[ForbiddenKeyFinding]:
    """As tres varreduras, na MESMA ordem do Cinerie.

    A ordem decide qual mensagem o produtor ve quando o pedido tem mais de um
    defeito — e a primeira mensagem e a que ele vai ler.
    """
    return (
        find_forbidden_publication_key(payload)
        or find_duplicate_seo_key(payload)
        or find_forbidden_seo_key(payload)
    )


__all__ = [
    "ForbiddenKeyFinding",
    "MAX_DEPTH",
    "explain_publication_key",
    "find_duplicate_seo_key",
    "find_forbidden_key",
    "find_forbidden_publication_key",
    "find_forbidden_seo_key",
    "normalize_key",
]
