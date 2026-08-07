"""Palavras funcionais de portugues e ingles — artigos, preposicoes, contracoes.

Uma lista so, e a razao e concreta. O laudo factual saia com sujeitos "do" e
"com": o extrator exigia dois caracteres alfabeticos, e uma preposicao tem dois.
"Do anuncio da Netflix" nao e um sujeito; e o comeco de um sintagma cujo nucleo
ficou de fora. O mesmo problema aparece do outro lado do pipeline, quando uma
frase-chave precisa ser derivada de um titulo: comecar em "de" ou "the" produz
uma keyphrase que nao distingue nada.

Os dois consumidores precisam da MESMA nocao de palavra vazia, e por isso ela
mora aqui, sem depender de nenhum dos dois. Uma segunda copia da lista seria uma
segunda coisa capaz de divergir.

A comparacao ignora acento e caixa: ``À``, ``a`` e ``À`` sao a mesma palavra
funcional. O que a lista **nao** faz e julgar conteudo — ela so reconhece a
classe fechada de palavras que nunca carregam o assunto sozinhas.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Final, FrozenSet, Iterable, List

_COMBINING: Final[re.Pattern[str]] = re.compile(r"[̀-ͯ]")

#: Artigos definidos e indefinidos do portugues.
PT_ARTICLES: Final[FrozenSet[str]] = frozenset(
    {"o", "a", "os", "as", "um", "uma", "uns", "umas"}
)

#: Preposicoes simples do portugues.
PT_PREPOSITIONS: Final[FrozenSet[str]] = frozenset(
    {
        "a", "ante", "apos", "ate", "com", "contra", "de", "desde", "em", "entre",
        "para", "perante", "por", "sem", "sob", "sobre", "tras",
    }
)

#: Contracoes de preposicao com artigo/pronome. Sao a forma que mais aparece em
#: manchete ("estreia DA serie", "trailer DO filme") e a que passava batido.
PT_CONTRACTIONS: Final[FrozenSet[str]] = frozenset(
    {
        "ao", "aos", "a", "as", "aquele", "aquela", "aqueles", "aquelas", "aquilo",
        "da", "das", "do", "dos", "dum", "duns", "duma", "dumas",
        "dela", "delas", "dele", "deles", "desta", "destas", "deste", "destes",
        "dessa", "dessas", "desse", "desses", "disto", "disso", "daquele",
        "daquela", "daqueles", "daquelas", "daquilo", "daqui", "dai", "dali",
        "na", "nas", "no", "nos", "num", "nuns", "numa", "numas",
        "nela", "nelas", "nele", "neles", "nesta", "nestas", "neste", "nestes",
        "nessa", "nessas", "nesse", "nesses", "nisto", "nisso",
        "pela", "pelas", "pelo", "pelos",
    }
)

#: Conjuncoes coordenativas frequentes. Entram pelo mesmo motivo: "E a Netflix
#: confirmou" nao tem "E" como sujeito.
PT_CONJUNCTIONS: Final[FrozenSet[str]] = frozenset(
    {"e", "mas", "ou", "nem", "porem", "que", "se", "como", "quando", "pois"}
)

#: Artigos e preposicoes do ingles — as fontes do MNScr sao majoritariamente
#: anglofonas, e o titulo original sobrevive em citacao e em nome de obra.
EN_FUNCTION_WORDS: Final[FrozenSet[str]] = frozenset(
    {
        "a", "an", "the",
        "about", "above", "across", "after", "against", "along", "among", "around",
        "as", "at", "before", "behind", "below", "beneath", "beside", "between",
        "beyond", "by", "down", "during", "except", "for", "from", "in", "inside",
        "into", "like", "near", "of", "off", "on", "onto", "out", "outside", "over",
        "past", "since", "than", "through", "to", "toward", "towards", "under",
        "until", "up", "upon", "with", "within", "without",
        "and", "but", "or", "nor", "so", "yet", "if", "that", "when", "while",
    }
)

#: A uniao — ja normalizada (minuscula, sem acento).
FUNCTION_WORDS: Final[FrozenSet[str]] = frozenset(
    {
        *PT_ARTICLES,
        *PT_PREPOSITIONS,
        *PT_CONTRACTIONS,
        *PT_CONJUNCTIONS,
        *EN_FUNCTION_WORDS,
    }
)


def fold(word: object) -> str:
    """Minuscula, sem acento e sem pontuacao de borda — a forma de COMPARACAO."""
    decomposed = unicodedata.normalize("NFD", str(word or ""))
    without_accents = _COMBINING.sub("", decomposed)
    return without_accents.lower().strip(" \t\r\n.,;:!?\"'’“”()[]{}-–—")


def is_function_word(word: object) -> bool:
    """A palavra pertence a classe fechada que nunca carrega o assunto sozinha?"""
    folded = fold(word)
    return bool(folded) and folded in FUNCTION_WORDS


def strip_leading_function_words(words: Iterable[object]) -> List[str]:
    """Remove as palavras funcionais do INICIO, preservando o resto intacto.

    Do inicio, e nao de toda a sequencia, de proposito: "Nova temporada de
    Stranger Things" precisa do "de" no meio para continuar sendo portugues. O
    que nao pode e a sequencia COMECAR numa preposicao — ali ela nao liga nada,
    so sobrou de um sintagma cortado.
    """
    result = [str(word) for word in words]
    while result and is_function_word(result[0]):
        result.pop(0)
    return result


def strip_trailing_function_words(words: Iterable[object]) -> List[str]:
    """Idem, pelo fim: um trecho que termina em "de" foi cortado no meio."""
    result = [str(word) for word in words]
    while result and is_function_word(result[-1]):
        result.pop()
    return result


def trim_function_words(words: Iterable[object]) -> List[str]:
    return strip_trailing_function_words(strip_leading_function_words(words))


def has_content_word(text: object) -> bool:
    """Sobra alguma palavra de conteudo depois de tirar as funcionais?"""
    return any(not is_function_word(part) and fold(part) for part in str(text or "").split())


__all__ = [
    "EN_FUNCTION_WORDS",
    "FUNCTION_WORDS",
    "PT_ARTICLES",
    "PT_CONJUNCTIONS",
    "PT_CONTRACTIONS",
    "PT_PREPOSITIONS",
    "fold",
    "has_content_word",
    "is_function_word",
    "strip_leading_function_words",
    "strip_trailing_function_words",
    "trim_function_words",
]
