"""Normalizacao deterministica da slug proposta.

A slug continua sendo **sugestao**. Quem decide colisao, slug canonica,
historico, redirects e preservacao em update e o Payload — e ele tem que decidir,
porque so ele conhece o que ja existe publicado.

O que cabe ao MNScr e entregar uma sugestao que o contrato aceite
(``^[a-z0-9]+(?:-[a-z0-9]+)*$``) e que seja **estavel**: a mesma entrada produz
sempre a mesma slug, sem depender de locale, de versao de biblioteca ou de
ordem de execucao.

Regra que nao e obvia e importa mais que as outras: **a slug de uma materia ja
publicada nao muda porque o titulo mudou**. Trocar a slug troca a URL publica, e
a URL publica ja foi indexada, compartilhada e linkada. Quem decide isso e o
Payload; o MNScr apenas nao propoe a troca.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Final, FrozenSet, List, Optional

from .contract import load_schema

_SLUG_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_COMBINING: Final[re.Pattern[str]] = re.compile(r"[̀-ͯ]")
_INVALID: Final[re.Pattern[str]] = re.compile(r"[^a-z0-9]+")

#: Palavras que nao podem virar slug porque colidem com rotas do site.
#:
#: Nao e uma tentativa de adivinhar o roteador do Screen-App: sao os prefixos
#: que qualquer CMS reserva. Uma slug igual a uma rota nao "quase funciona" —
#: ela sequestra a rota.
RESERVED_SLUGS: Final[FrozenSet[str]] = frozenset(
    {
        "admin",
        "api",
        "assets",
        "auth",
        "busca",
        "categoria",
        "categorias",
        "feed",
        "login",
        "logout",
        "media",
        "noticias",
        "null",
        "pt",
        "rss",
        "search",
        "sitemap",
        "static",
        "tag",
        "tags",
        "undefined",
    }
)

#: Palavras vazias removidas apenas quando a slug precisa encurtar. Remover
#: sempre produziria slugs telegraficas para titulos curtos.
_STOPWORDS: Final[FrozenSet[str]] = frozenset(
    {"a", "as", "e", "o", "os", "da", "das", "de", "do", "dos", "em", "na", "nas", "no", "nos",
     "para", "por", "um", "uma", "com"}
)

DEFAULT_MAX_WORDS: Final[int] = 8


def _schema_max_length() -> int:
    """Teto de caracteres, lido do proprio JSON Schema.

    Redigitar 200 aqui criaria a terceira copia de um numero que ja existe no
    contrato — exatamente o tipo de duplicacao que esta fase veio remover.
    """
    try:
        schema = load_schema()
        seo = schema["properties"]["seo"]["properties"]["slugSuggestion"]
        value = seo.get("maxLength")
        if isinstance(value, int) and value > 0:
            return value
    except (KeyError, TypeError):
        pass
    return 200


def is_valid_slug(value: Optional[str]) -> bool:
    if not value:
        return False
    return bool(_SLUG_PATTERN.match(value)) and len(value) <= _schema_max_length()


def slug_words(value: str) -> List[str]:
    decomposed = unicodedata.normalize("NFD", str(value or ""))
    ascii_only = _COMBINING.sub("", decomposed)
    lowered = ascii_only.lower()
    return [word for word in _INVALID.split(lowered) if word]


def normalize_slug(
    value: object,
    *,
    max_words: int = DEFAULT_MAX_WORDS,
    max_length: Optional[int] = None,
) -> Optional[str]:
    """Slug canonica, ou ``None`` quando nao sobra nada utilizavel.

    ``None`` e um resultado legitimo e importante: uma slug vazia nao pode ser
    "consertada" com um valor inventado, e mandar ``""`` seria pedir ao Payload
    que adivinhasse.
    """
    limit = max_length if max_length is not None else _schema_max_length()
    words = slug_words(value)
    if not words:
        return None

    if len(words) > max_words:
        meaningful = [word for word in words if word not in _STOPWORDS]
        # Se sobrar pouco depois de tirar as vazias, e melhor manter o titulo
        # inteiro truncado do que produzir uma slug irreconhecivel.
        words = meaningful[:max_words] if len(meaningful) >= 3 else words[:max_words]

    slug = "-".join(words)
    if len(slug) > limit:
        truncated: List[str] = []
        for word in words:
            candidate = "-".join([*truncated, word])
            if len(candidate) > limit:
                break
            truncated.append(word)
        slug = "-".join(truncated) if truncated else words[0][:limit]

    slug = slug.strip("-")
    if not slug:
        return None
    if slug in RESERVED_SLUGS:
        return None
    return slug if is_valid_slug(slug) else None


def slug_issue(value: Optional[str]) -> Optional[str]:
    """Motivo pelo qual a slug seria recusada, ou ``None``."""
    if value is None or not str(value).strip():
        return "SLUG_AUSENTE"
    text = str(value)
    if text in RESERVED_SLUGS:
        return f"SLUG_RESERVADA:{text}"
    if len(text) > _schema_max_length():
        return f"SLUG_LONGA_DEMAIS:{len(text)}"
    if not _SLUG_PATTERN.match(text):
        return "SLUG_FORA_DO_FORMATO"
    return None


__all__ = [
    "DEFAULT_MAX_WORDS",
    "RESERVED_SLUGS",
    "is_valid_slug",
    "normalize_slug",
    "slug_issue",
    "slug_words",
]
