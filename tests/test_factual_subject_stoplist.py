"""Preposicao nao e sujeito.

O laudo factual saia com sujeitos "do" e "com". O filtro que deveria pegar isso
exigia dois caracteres ALFABETICOS — e "do" tem dois. O resultado eram avisos em
``qa.warnings`` dizendo que as fontes discordavam sobre "do", que nao informa
nada e ainda gasta a atencao de quem revisa.

O criterio novo nao e de tamanho: e de CLASSE. Uma sequencia formada so por
palavras funcionais (artigos, preposicoes, contracoes, conjuncoes) nao e um
sujeito; e a metade da frente de um sintagma cujo nucleo ficou de fora.
"""

from __future__ import annotations

import pytest

from app.factual.extraction import _subject_of
from app.stopwords import is_function_word, trim_function_words


@pytest.mark.parametrize(
    "sentence",
    [
        "Do anuncio da Netflix veio a confirmacao de 12 episodios.",
        "Com a estreia marcada para 3 de julho, o estudio comemorou.",
        "Da temporada nova, o estudio so confirmou 8 episodios.",
        "Na coletiva, a producao confirmou 2 filmes.",
        "Of the announcement, only 3 details were confirmed.",
        "The of a and 5 episodios.",
    ],
)
def test_function_word_run_is_not_a_subject(sentence):
    assert _subject_of(sentence) is None


@pytest.mark.parametrize(
    "sentence,expected",
    [
        ("Stranger Things ganha data de estreia em 3 de julho.", "Stranger Things"),
        ("The Batman arrecadou US$ 100 milhoes.", "Batman"),
        ("Netflix confirmou 8 episodios para a nova temporada.", "Netflix"),
    ],
)
def test_real_subjects_survive(sentence, expected):
    assert _subject_of(sentence) == expected


def test_function_words_in_the_middle_are_preserved():
    """"Nova temporada de Stranger Things" precisa do "de" para continuar portugues.

    O corte e so nas PONTAS: comecar ou terminar numa preposicao significa que a
    captura pegou meio sintagma. No meio, ela liga duas partes reais.
    """
    assert trim_function_words("Casa de Papel".split()) == ["Casa", "de", "Papel"]
    assert trim_function_words("de Stranger Things de".split()) == ["Stranger", "Things"]


@pytest.mark.parametrize("word", ["do", "da", "com", "de", "no", "pela", "the", "of", "and", "À"])
def test_known_function_words_are_recognised(word):
    assert is_function_word(word)


@pytest.mark.parametrize("word", ["Netflix", "estreia", "Batman", "temporada", "episodios"])
def test_content_words_are_not_function_words(word):
    assert not is_function_word(word)
