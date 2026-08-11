"""As regras do contrato que o JSON Schema **nao consegue expressar**.

Este e o modulo mais importante da validacao local, e a razao merece ser dita
sem rodeio: ``z.toJSONSchema(..., { unrepresentable: 'any' })`` **descarta todo
``superRefine``**. O que sobra no arquivo ``.schema.json`` sao tipos, tamanhos
declarados por ``.min()/.max()`` e enums. Some dali, entre outras coisas:

* ``seo.title`` precisar de 15 caracteres e ``seo.metaDescription`` de 70 — os
  dois minimos vivem em ``superRefine``, e o schema so registra ``minLength: 1``;
* ``publicationIntent: 'update'`` exigir ``targetArticleId``;
* publicacao automatica exigir ao menos uma fonte externa;
* ``qa.passed = false`` exigir um erro bloqueante declarado;
* alt sugerido apontar para midia que esta no pedido;
* ids de bloco unicos;
* ausencia de markup em qualquer texto editorial.

Confiar so no JSON Schema deixaria todas essas passarem daqui e morrerem no
servidor — com a materia ja escrita, o teto de publicacao possivelmente
consumido e um ``BLOCKED`` que o pipeline nao saberia prevenir. Por isso as
regras sao reimplementadas aqui, e por isso o teste de compatibilidade roda as
**fixtures invalidas do proprio Cinerie** contra esta implementacao: metade
delas passaria batido por um validador que fosse so JSON Schema.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Final, List, Mapping, Sequence
from urllib.parse import urlparse

from .policy import seo_policy
from .text import find_forbidden_markup, normalize_keyphrase

_TIMEZONE_SUFFIX: Final[re.Pattern[str]] = re.compile(r"(?:Z|[+-]\d{2}:?\d{2})$")


#: Quantos caracteres de um valor recusado aparecem no log.
#:
#: Curto de proposito. O que um operador precisa para consertar um campo
#: obrigatorio e distinguir ``''`` de ``None`` de ``'   '`` de um texto grande
#: demais — nao ler a materia. Trinta caracteres respondem essa pergunta e nao
#: transportam conteudo inedito.
VALUE_EXCERPT_MAX: Final[int] = 30


def describe_value(value: Any) -> str:
    """Rendericao SEGURA do valor recusado: forma e tamanho, nunca o conteudo inteiro.

    A decisao anterior era nao mostrar valor nenhum, e ela custava caro na
    pratica: ``seo.focusKeyphrase: minLength: 1`` nao diz se o campo chegou
    vazio, nulo ou com espacos, e as tres causas se consertam em lugares
    diferentes. O meio-termo e mostrar a FORMA sempre e um trecho curto quando o
    valor e texto — o suficiente para identificar o defeito, longe do suficiente
    para vazar a materia.
    """
    if value is None:
        return "None"
    if isinstance(value, bool) or isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        if not value:
            return "'' (string vazia)"
        if not value.strip():
            return f"'   ' (so espacos, {len(value)} caracteres)"
        excerpt = value[:VALUE_EXCERPT_MAX]
        suffix = "..." if len(value) > VALUE_EXCERPT_MAX else ""
        return f"{excerpt!r}{suffix} ({len(value)} caracteres)"
    if isinstance(value, (list, tuple)):
        return f"<lista de {len(value)} item(ns)>"
    if isinstance(value, Mapping):
        return f"<objeto com {len(value)} chave(s)>"
    return f"<{type(value).__name__}>"


@dataclass(frozen=True)
class ContractIssue:
    """Caminho + mensagem, e a FORMA do valor recebido.

    O valor nunca aparece inteiro: ``received`` e o resultado de
    :func:`describe_value`, que mostra tipo, tamanho e no maximo um trecho
    curto. Um erro de validacao que ecoasse o payload viraria vetor de
    vazamento — o valor de um campo aqui e trecho de materia inedita.
    """

    path: str
    message: str
    received: str = ""

    def __str__(self) -> str:  # pragma: no cover - trivial
        suffix = f" [recebido: {self.received}]" if self.received else ""
        return f"{self.path}: {self.message}{suffix}"


def _issue(path: str, message: str, received: str = "") -> ContractIssue:
    return ContractIssue(path=path or "(raiz)", message=message, received=received)


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


# --- Primitivas ------------------------------------------------------------


def check_iso_datetime(value: Any, path: str) -> List[ContractIssue]:
    text = _text(value)
    if not text:
        return [_issue(path, "instante ISO-8601 ausente")]
    if not _TIMEZONE_SUFFIX.search(text):
        return [_issue(path, "instante ISO-8601 exige timezone explicito")]
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return [_issue(path, "instante ISO-8601 invalido")]
    return []


def check_http_url(value: Any, path: str) -> List[ContractIssue]:
    text = _text(value)
    try:
        parsed = urlparse(text)
    except ValueError:
        return [_issue(path, "URL invalida")]
    if not parsed.scheme or not parsed.netloc:
        return [_issue(path, "URL invalida")]
    if parsed.scheme not in ("http", "https"):
        return [_issue(path, "somente http(s) e aceito")]
    return []


def check_markup_everywhere(payload: Any, path: str = "", depth: int = 0) -> List[ContractIssue]:
    """Nenhuma string do pedido pode conter markup.

    A varredura e geral, e nao campo a campo, porque **nenhum** campo string
    deste contrato admite markup legitimamente: os textos editoriais sao
    ``plainText``, e o resto e id, hash, slug, enum ou URL. Uma varredura geral
    e mais simples de manter que uma lista de campos que envelhece a cada versao
    do contrato — e erra para o lado seguro.
    """
    if depth > 16:
        return []
    if isinstance(payload, str):
        found = find_forbidden_markup(payload)
        return [_issue(path, f"conteudo proibido no texto: {found}")] if found else []
    if isinstance(payload, list):
        issues: List[ContractIssue] = []
        for index, item in enumerate(payload):
            issues.extend(check_markup_everywhere(item, f"{path}[{index}]", depth + 1))
        return issues
    if isinstance(payload, dict):
        issues = []
        for key, item in payload.items():
            child = f"{path}.{key}" if path else str(key)
            issues.extend(check_markup_everywhere(item, child, depth + 1))
        return issues
    return []


# --- Corpo -----------------------------------------------------------------


def check_blocks(blocks: Any) -> List[ContractIssue]:
    if not isinstance(blocks, list) or not blocks:
        return [_issue("blocks", "corpo vazio")]
    issues: List[ContractIssue] = []
    seen: set[str] = set()
    for index, block in enumerate(blocks):
        if not isinstance(block, Mapping):
            issues.append(_issue(f"blocks[{index}]", "bloco nao e um objeto"))
            continue
        block_id = _text(block.get("id"))
        if block_id in seen:
            issues.append(_issue(f"blocks[{index}].id", f"id de bloco duplicado: {block_id}"))
        seen.add(block_id)
    return issues


# --- SEO -------------------------------------------------------------------


def check_seo(seo: Any, *, media_ids: Sequence[str]) -> List[ContractIssue]:
    policy = seo_policy()
    if not isinstance(seo, Mapping):
        return [_issue("seo", "objeto seo ausente")]

    issues: List[ContractIssue] = []

    title = _text(seo.get("title")).strip()
    if len(title) < policy.title.min:
        issues.append(
            _issue(
                "seo.title",
                f"title precisa de ao menos {policy.title.min} caracteres",
                describe_value(seo.get("title")),
            )
        )

    meta = _text(seo.get("metaDescription")).strip()
    if len(meta) < policy.meta_description.min:
        issues.append(
            _issue(
                "seo.metaDescription",
                f"metaDescription precisa de ao menos {policy.meta_description.min} caracteres",
                describe_value(seo.get("metaDescription")),
            )
        )

    related_raw = seo.get("relatedKeyphrases")
    related = [normalize_keyphrase(item) for item in related_raw] if isinstance(related_raw, list) else []
    if len(set(related)) != len(related):
        issues.append(
            _issue("seo.relatedKeyphrases", "relatedKeyphrases contem duplicata (normalizada)")
        )
    if normalize_keyphrase(seo.get("focusKeyphrase")) in related:
        issues.append(
            _issue("seo.relatedKeyphrases", "relatedKeyphrases nao pode repetir a focusKeyphrase")
        )

    links = seo.get("internalLinkSuggestions")
    if isinstance(links, list):
        for index, link in enumerate(links):
            if not isinstance(link, Mapping):
                continue
            if link.get("targetId") is None and link.get("targetPath") is None:
                issues.append(
                    _issue(
                        f"seo.internalLinkSuggestions[{index}]",
                        "link interno exige targetId ou targetPath",
                    )
                )

    alts = seo.get("imageAltSuggestions")
    if isinstance(alts, list):
        refs = [_text(item.get("mediaRef")) for item in alts if isinstance(item, Mapping)]
        if len(set(refs)) != len(refs):
            issues.append(
                _issue("seo.imageAltSuggestions", "duas sugestoes de alt para a mesma midia")
            )
        known = set(media_ids)
        for index, item in enumerate(alts):
            if not isinstance(item, Mapping):
                continue
            if _text(item.get("mediaRef")) not in known:
                issues.append(
                    _issue(
                        f"seo.imageAltSuggestions[{index}].mediaRef",
                        "alt sugerido referencia midia que nao esta no pedido",
                    )
                )
            alt = _text(item.get("alt")).strip()
            if len(alt) < policy.image_alt_min:
                issues.append(
                    _issue(
                        f"seo.imageAltSuggestions[{index}].alt",
                        f"alt precisa de ao menos {policy.image_alt_min} caracteres",
                    )
                )

    return issues


# --- Pedido completo -------------------------------------------------------


def check_request(payload: Any) -> List[ContractIssue]:
    """Todos os refinamentos, na ordem em que o Cinerie os aplica."""
    if not isinstance(payload, Mapping):
        return [_issue("(raiz)", "pedido nao e um objeto")]

    issues: List[ContractIssue] = []
    intent = _text(payload.get("publicationIntent"))
    target = payload.get("targetArticleId")

    if intent == "update" and target is None:
        issues.append(_issue("targetArticleId", 'publicationIntent "update" exige targetArticleId'))
    if intent == "publish" and target is not None:
        issues.append(
            _issue("targetArticleId", 'publicationIntent "publish" nao pode declarar targetArticleId')
        )

    qa = payload.get("qa")
    if isinstance(qa, Mapping):
        blocking = qa.get("blockingErrors")
        blocking_count = len(blocking) if isinstance(blocking, list) else 0
        if qa.get("passed") is False and blocking_count == 0:
            issues.append(
                _issue("qa.blockingErrors", "qa.passed=false exige ao menos um blockingError declarado")
            )

    sources = payload.get("externalSources")
    if not isinstance(sources, list) or not sources:
        issues.append(
            _issue("externalSources", "publicacao automatica exige ao menos uma fonte externa")
        )
    else:
        for index, source in enumerate(sources):
            if isinstance(source, Mapping):
                issues.extend(check_http_url(source.get("url"), f"externalSources[{index}].url"))

    media = payload.get("media")
    media_ids = (
        [_text(item.get("mediaId")) for item in media if isinstance(item, Mapping)]
        if isinstance(media, list)
        else []
    )

    issues.extend(check_blocks(payload.get("blocks")))
    issues.extend(check_seo(payload.get("seo"), media_ids=media_ids))
    issues.extend(check_iso_datetime(payload.get("generatedAt"), "generatedAt"))
    issues.extend(check_markup_everywhere(payload))

    return issues


def issue_paths(issues: Sequence[ContractIssue]) -> List[str]:
    return [issue.path for issue in issues]


def issues_as_dicts(issues: Sequence[ContractIssue]) -> List[Dict[str, str]]:
    return [{"path": issue.path, "message": issue.message} for issue in issues]


__all__ = [
    "ContractIssue",
    "check_blocks",
    "check_http_url",
    "check_iso_datetime",
    "check_markup_everywhere",
    "check_request",
    "check_seo",
    "issue_paths",
    "issues_as_dicts",
]
