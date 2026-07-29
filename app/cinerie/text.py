"""Higiene de texto do contrato, reimplementada em Python.

O Zod do Cinerie recusa markup em QUALQUER texto editorial, e essa regra vive
num ``superRefine`` — que **nao e representavel em JSON Schema**. Validar so
pelo schema deixaria passar um paragrafo com ``<script>`` dentro e o pedido
morreria no servidor, com a materia ja escrita.

Os padroes abaixo sao a traducao literal de ``FORBIDDEN_MARKUP`` em
``packages/editorial-contracts/src/common.ts``. Traduzir e o custo de existirem
duas linguagens; o teste de compatibilidade contra as fixtures do proprio
Cinerie e o que impede a traducao de envelhecer em silencio.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Final, Iterable, List, Optional, Sequence, Tuple

#: Espelha `FORBIDDEN_MARKUP` do Cinerie, na mesma ordem — a ordem decide qual
#: rotulo aparece quando um texto casa com mais de um padrao.
_FORBIDDEN_MARKUP: Final[Tuple[Tuple[re.Pattern[str], str], ...]] = (
    (re.compile(r"<\s*script\b", re.IGNORECASE), "tag <script>"),
    (re.compile(r"<\s*iframe\b", re.IGNORECASE), "tag <iframe>"),
    (re.compile(r"<\s*object\b", re.IGNORECASE), "tag <object>"),
    (re.compile(r"<\s*embed\b", re.IGNORECASE), "tag <embed>"),
    (re.compile(r"<\s*style\b", re.IGNORECASE), "tag <style>"),
    (re.compile(r"<\s*/?[a-z][a-z0-9-]*\s*[^>]*>", re.IGNORECASE), "markup HTML"),
    (re.compile(r"javascript\s*:", re.IGNORECASE), "esquema javascript:"),
    (re.compile(r"data\s*:\s*text/html", re.IGNORECASE), "data: URI de HTML"),
    (re.compile(r"\bon[a-z]+\s*=\s*[\"']", re.IGNORECASE), "handler inline (onX=)"),
)

_WHITESPACE: Final[re.Pattern[str]] = re.compile(r"\s+")
_COMBINING: Final[re.Pattern[str]] = re.compile(r"[̀-ͯ]")
_NON_ALNUM: Final[re.Pattern[str]] = re.compile(r"[^a-z0-9\s]")


def find_forbidden_markup(value: str) -> Optional[str]:
    """Primeiro padrao proibido encontrado, ou ``None`` quando o texto e limpo."""
    for pattern, label in _FORBIDDEN_MARKUP:
        if pattern.search(value):
            return label
    return None


def collapse(value: object) -> str:
    """Colapsa espacos em branco. Nao remove nada alem disso."""
    return _WHITESPACE.sub(" ", str(value or "")).strip()


def plain_text(value: object, *, max_length: Optional[int] = None) -> str:
    """Texto editorial simples: espacos colapsados e, opcionalmente, truncado.

    O truncamento corta em fronteira de palavra quando da, porque cortar no meio
    de uma palavra e visivel para o leitor na SERP.
    """
    text = collapse(value)
    if max_length is None or len(text) <= max_length:
        return text
    cut = text[:max_length]
    space = cut.rfind(" ")
    if space >= max_length * 0.6:
        cut = cut[:space]
    return cut.rstrip(" ,;:-—–")


def normalize_keyphrase(value: object) -> str:
    """Minuscula, sem acento, sem pontuacao — a forma de COMPARACAO.

    Espelha ``normalizeKeyphrase`` do Cinerie. Usada para deduplicar e para
    contar repeticao; nunca para gravar: o texto entregue mantem os acentos.
    """
    decomposed = unicodedata.normalize("NFD", str(value or ""))
    without_accents = _COMBINING.sub("", decomposed)
    lowered = without_accents.lower()
    spaced = _NON_ALNUM.sub(" ", lowered)
    return _WHITESPACE.sub(" ", spaced).strip()


def singular_form(value: object) -> str:
    """Forma de comparacao insensivel a plural simples.

    ``nova serie`` e ``novas series`` sao a mesma keyphrase escrita duas vezes.
    O plural e removido **por palavra**, e nao no fim da frase: cortar so o
    ultimo ``s`` deixaria ``novas serie`` != ``nova serie``.

    E deliberadamente ingenuo — nao e um lematizador. Serve para pegar a
    variacao trivial que a geracao automatica produz, nao para analisar
    morfologia do portugues.
    """
    return " ".join(word.rstrip("s") for word in normalize_keyphrase(value).split())


def dedupe_keyphrases(
    values: Iterable[object],
    *,
    exclude: Sequence[object] = (),
    limit: Optional[int] = None,
    item_max_length: Optional[int] = None,
) -> List[str]:
    """Normaliza, deduplica e limita — preservando a ordem e o texto original.

    A comparacao e feita na forma normalizada (sem acento, sem caixa, sem
    pontuacao, sem plural trivial) porque "estreia", "Estreia", "estréia" e
    "estreias" sao a mesma keyphrase; o que fica gravado, porem, e a primeira
    grafia recebida — corrigir a grafia do redator nao e trabalho desta camada.
    """
    excluded = {singular_form(item) for item in exclude if normalize_keyphrase(item)}
    seen: set[str] = set()
    result: List[str] = []
    for value in values or []:
        text = collapse(value)
        if not text:
            continue
        if item_max_length is not None and len(text) > item_max_length:
            continue
        key = singular_form(text)
        if not key or key in seen or key in excluded:
            continue
        if find_forbidden_markup(text) is not None:
            continue
        seen.add(key)
        result.append(text)
        if limit is not None and len(result) >= limit:
            break
    return result


def count_occurrences(needle: str, haystacks: Iterable[Optional[str]]) -> int:
    """Quantas vezes a keyphrase aparece no conjunto de campos.

    Conta no conjunto, e nao por campo: a mesma frase em titulo, meta, og,
    twitter e alt e o padrao classico de texto escrito para robo, e cada campo
    isolado pareceria inocente.
    """
    target = normalize_keyphrase(needle)
    if not target:
        return 0
    total = 0
    for field in haystacks:
        if not isinstance(field, str):
            continue
        normalized = normalize_keyphrase(field)
        if not normalized:
            continue
        index = normalized.find(target)
        while index != -1:
            total += 1
            index = normalized.find(target, index + len(target))
    return total


def contains_keyphrase(haystack: Optional[str], needle: str) -> bool:
    return count_occurrences(needle, [haystack]) > 0


def word_count(value: object) -> int:
    text = collapse(value)
    return len(text.split()) if text else 0


__all__ = [
    "collapse",
    "contains_keyphrase",
    "count_occurrences",
    "dedupe_keyphrases",
    "find_forbidden_markup",
    "normalize_keyphrase",
    "plain_text",
    "singular_form",
    "word_count",
]
