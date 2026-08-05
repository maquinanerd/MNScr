"""Validacao de midia editorial — o que da para afirmar SEM baixar nada.

O contrato `editorial-publication-request-v1` e explicito: *"o pedido
REFERENCIA, nunca envia bytes"*. `media` e uma lista de `{mediaId, intendedUse,
altSuggestion}`, e `mediaId` e um documento que JA existe no CMS, com licenca ja
decidida la. O CMS e o unico que conhece a licenca; o MNScr nao vira autoridade
sobre isso por ter achado a imagem primeiro.

Entao "validar imagem" aqui NAO e conferir se a URL abre. E responder duas
perguntas que sao do MNScr:

1. **Este candidato pode virar referencia de midia?** Forma da URL, formato do
   arquivo, alt utilizavel, ausencia de pixel de rastreio e de placeholder.
2. **Existe exatamente uma imagem de destaque?** `intendedUse: 'hero'` e
   singular por natureza. Zero significa materia sem capa; duas significam que
   ninguem escolheu, e o CMS escolheria por conta propria.

O que este modulo NAO faz, de proposito:

- **nao baixa bytes.** Seguir uma URL vinda de pagina de terceiro com a
  credencial do processo no bolso e SSRF. O proprio CMS criou
  `publication-media.ts` justamente para que ninguem precise fazer isso.
- **nao adivinha licenca nem credito.** Desconhecido continua desconhecido —
  a MS-0 encontrou o pipeline antigo deduzindo detentor de direitos por palavra
  chave, e isso nao e reproduzido aqui.
- **nao resolve `mediaId`.** Sem catalogo do CMS nao ha resolucao, e um id
  inventado publicaria a imagem errada.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Final, FrozenSet, List, Optional, Sequence, Tuple
from urllib.parse import urlparse

# ===========================================================================
# Regras que espelham o CMS
# ===========================================================================

#: Espelha `DELIVERABLE_MIME_TYPES` de `apps/cms/src/endpoints/publication-media.ts`.
DELIVERABLE_EXTENSIONS: Final[FrozenSet[str]] = frozenset({"jpg", "jpeg", "png", "webp", "avif"})

#: Espelha `publicationMediaRef.intendedUse` do contrato.
INTENDED_USES: Final[Tuple[str, ...]] = ("hero", "inline", "gallery", "social")
INTENDED_USE_SET: Final[FrozenSet[str]] = frozenset(INTENDED_USES)

#: Espelha `SEO_POLICY.imageAlt` de `packages/editorial-contracts/src/seo-proposal.ts`.
ALT_MIN_CHARS: Final[int] = 4
ALT_MAX_CHARS: Final[int] = 200
MAX_ALT_SUGGESTIONS: Final[int] = 20

#: Espelha `LIMITS.mediaCandidates`. Ver `validate_media_set`.
MAX_MEDIA_PER_REQUEST: Final[int] = 20

#: Sinais de imagem que nao e conteudo editorial. Pixel de rastreio publicado
#: como imagem de destaque produz uma materia com capa de 1x1 transparente.
_NON_EDITORIAL_URL_SIGNALS: Final[Tuple[re.Pattern[str], ...]] = (
    re.compile(r"/(pixel|tracking|beacon|spacer|blank|1x1|placeholder)\b", re.IGNORECASE),
    re.compile(r"\b(doubleclick|googlesyndication|scorecardresearch|quantserve)\b", re.IGNORECASE),
    re.compile(r"\b(avatar|gravatar|profile[-_]?pic|author[-_]?photo)\b", re.IGNORECASE),
    re.compile(r"\b(logo|favicon|sprite|watermark|badge)\b", re.IGNORECASE),
    re.compile(r"\b(share|social)[-_]?(icon|button)\b", re.IGNORECASE),
)

#: Dimensoes embutidas na URL, quando o site as expoe. Nao substitui medir a
#: imagem — apenas descarta o que ja se declara pequeno demais para ser capa.
_DIMENSION_IN_URL: Final[re.Pattern[str]] = re.compile(r"[-_/](\d{2,4})x(\d{2,4})[-_./]")

#: Menor lado aceitavel para imagem de destaque. Abaixo disso a capa aparece
#: borrada no card social, que e onde a maioria dos leitores a ve primeiro.
MIN_HERO_EDGE_PX: Final[int] = 600


@dataclass(frozen=True)
class MediaIssue:
    """Um problema em um candidato. `blocking` decide se ele pode ser usado."""

    code: str
    detail: str
    blocking: bool = True

    def __str__(self) -> str:  # pragma: no cover - conveniencia de log
        return f"{self.code}: {self.detail}"


@dataclass(frozen=True)
class MediaValidation:
    """Resultado da validacao de um candidato."""

    source_url: str
    intended_use: str
    issues: Tuple[MediaIssue, ...] = ()
    alt_suggestion: Optional[str] = None

    @property
    def usable(self) -> bool:
        return not any(issue.blocking for issue in self.issues)

    @property
    def blocking_codes(self) -> Tuple[str, ...]:
        return tuple(issue.code for issue in self.issues if issue.blocking)

    @property
    def warning_codes(self) -> Tuple[str, ...]:
        return tuple(issue.code for issue in self.issues if not issue.blocking)


def _extension_of(url: str) -> str:
    path = urlparse(url).path
    _, _, ext = path.rpartition(".")
    return ext.lower().strip() if "." in path else ""


def normalize_alt(
    *candidates: Optional[str],
) -> Optional[str]:
    """Primeiro alt utilizavel entre os candidatos, truncado ao limite do CMS.

    Nao INVENTA alt. Um alt fabricado descreveria uma imagem que este processo
    nunca viu, e o CMS decide o alt definitivo de qualquer forma — a sugestao so
    tem valor se vier do material recebido.
    """
    for candidate in candidates:
        text = (candidate or "").strip()
        if len(text) < ALT_MIN_CHARS:
            continue
        return text[:ALT_MAX_CHARS].strip()
    return None


def validate_candidate(
    source_url: str,
    *,
    intended_use: str = "inline",
    alt: Optional[str] = None,
    caption: Optional[str] = None,
) -> MediaValidation:
    """Valida um candidato isolado, sem rede."""
    issues: List[MediaIssue] = []
    url = (source_url or "").strip()

    if intended_use not in INTENDED_USE_SET:
        issues.append(
            MediaIssue(
                "MEDIA_INTENDED_USE_INVALID",
                f"'{intended_use}' nao existe no contrato. Aceitos: {', '.join(INTENDED_USES)}",
            )
        )

    if not url:
        issues.append(MediaIssue("MEDIA_URL_EMPTY", "candidato sem URL"))
        return MediaValidation(url, intended_use, tuple(issues), None)

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        # `data:` inclusive: bytes embutidos sao exatamente o que o contrato
        # recusa ao exigir referencia a midia ja aprovada.
        issues.append(
            MediaIssue("MEDIA_URL_SCHEME_INVALID", f"esquema '{parsed.scheme or '-'}' nao aceito")
        )
    if not parsed.netloc:
        issues.append(MediaIssue("MEDIA_URL_NOT_ABSOLUTE", "URL relativa nao identifica a origem"))

    extension = _extension_of(url)
    if extension and extension not in DELIVERABLE_EXTENSIONS:
        issues.append(
            MediaIssue(
                "MEDIA_FORMAT_NOT_DELIVERABLE",
                f"'.{extension}' fora de {sorted(DELIVERABLE_EXTENSIONS)}",
            )
        )
    elif not extension:
        # URL sem extensao e comum em CDN com transformacao; nao e defeito, mas
        # impede afirmar o formato antes do CMS resolver o `mediaId`.
        issues.append(
            MediaIssue("MEDIA_FORMAT_UNKNOWN", "URL sem extensao: formato indeterminado", blocking=False)
        )

    for pattern in _NON_EDITORIAL_URL_SIGNALS:
        if pattern.search(url):
            issues.append(
                MediaIssue("MEDIA_NOT_EDITORIAL", f"URL sugere imagem nao editorial ({pattern.pattern})")
            )
            break

    dimensions = _DIMENSION_IN_URL.search(url)
    if dimensions is not None and intended_use == "hero":
        width, height = int(dimensions.group(1)), int(dimensions.group(2))
        if min(width, height) < MIN_HERO_EDGE_PX:
            issues.append(
                MediaIssue(
                    "MEDIA_HERO_TOO_SMALL",
                    f"{width}x{height} declarado na URL; minimo {MIN_HERO_EDGE_PX}px no menor lado",
                )
            )

    alt_suggestion = normalize_alt(alt, caption)
    if alt_suggestion is None:
        # Sem alt o CMS ainda publica: ele decide o alt definitivo. Por isso e
        # aviso, e nao bloqueio — mas fica registrado, porque imagem sem alt e
        # falha de acessibilidade que alguem precisa resolver.
        issues.append(
            MediaIssue("MEDIA_ALT_MISSING", "nenhum alt ou legenda aproveitavel na fonte", blocking=False)
        )

    return MediaValidation(url, intended_use, tuple(issues), alt_suggestion)


def validate_media_set(
    candidates: Sequence[MediaValidation],
    *,
    require_hero: bool = True,
) -> List[MediaIssue]:
    """Regras que so existem no CONJUNTO — a principal delas e a capa.

    `intendedUse: 'hero'` e singular. Duas capas nao e redundancia util: e
    ausencia de escolha, e o CMS escolheria uma por conta propria, sem que
    ninguem tenha decidido qual.
    """
    issues: List[MediaIssue] = []
    usable = [c for c in candidates if c.usable]

    heroes = [c for c in usable if c.intended_use == "hero"]
    if require_hero and not heroes:
        issues.append(
            MediaIssue("MEDIA_HERO_MISSING", "nenhum candidato utilizavel marcado como 'hero'")
        )
    if len(heroes) > 1:
        issues.append(
            MediaIssue("MEDIA_HERO_AMBIGUOUS", f"{len(heroes)} candidatos marcados como 'hero'")
        )

    if len(usable) > MAX_MEDIA_PER_REQUEST:
        issues.append(
            MediaIssue(
                "MEDIA_TOO_MANY",
                f"{len(usable)} candidatos; o contrato aceita {MAX_MEDIA_PER_REQUEST}",
            )
        )

    seen: Dict[str, int] = {}
    for candidate in usable:
        seen[candidate.source_url] = seen.get(candidate.source_url, 0) + 1
    duplicated = [url for url, count in seen.items() if count > 1]
    if duplicated:
        issues.append(
            MediaIssue("MEDIA_DUPLICATED", f"{len(duplicated)} URL(s) repetida(s)", blocking=False)
        )

    alt_count = sum(1 for c in usable if c.alt_suggestion)
    if alt_count > MAX_ALT_SUGGESTIONS:
        issues.append(
            MediaIssue(
                "MEDIA_ALT_SUGGESTIONS_TOO_MANY",
                f"{alt_count} sugestoes de alt; o contrato aceita {MAX_ALT_SUGGESTIONS}",
            )
        )

    return issues


def validate_draft_media(
    media_candidates: Sequence[object],
    *,
    featured_url: Optional[str] = None,
    require_hero: bool = True,
) -> Tuple[List[MediaValidation], List[MediaIssue]]:
    """Valida os candidatos de um draft e aponta qual e a capa.

    ``featured_url`` marca o `hero`. Quando ela nao esta entre os candidatos, o
    primeiro candidato utilizavel NAO e promovido a capa por conveniencia: uma
    capa escolhida por posicao na lista e uma decisao editorial tomada por
    acidente de ordenacao.
    """
    validations: List[MediaValidation] = []
    featured = (featured_url or "").strip()

    for candidate in media_candidates:
        url = str(getattr(candidate, "source_url", "") or "").strip()
        use = "hero" if featured and url == featured else "inline"
        validations.append(
            validate_candidate(
                url,
                intended_use=use,
                alt=getattr(candidate, "original_alt", None),
                caption=getattr(candidate, "original_caption", None),
            )
        )

    if featured and not any(v.intended_use == "hero" for v in validations):
        validations.append(
            validate_candidate(featured, intended_use="hero", alt=None, caption=None)
        )

    return validations, validate_media_set(validations, require_hero=require_hero)


__all__ = [
    "ALT_MAX_CHARS",
    "ALT_MIN_CHARS",
    "DELIVERABLE_EXTENSIONS",
    "INTENDED_USES",
    "MIN_HERO_EDGE_PX",
    "MediaIssue",
    "MediaValidation",
    "normalize_alt",
    "validate_candidate",
    "validate_draft_media",
    "validate_media_set",
]
