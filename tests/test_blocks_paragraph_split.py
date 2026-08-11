"""Um `<p>` gigante nao pode virar um bloco gigante.

A materia publicada em 05/08 as 19:42 saiu do MNScr como UM unico bloco de
~2.755 caracteres, com os intertitulos como texto solto e quebra dupla dentro
dele. O `universal_prompt.txt` pede `<h2>` e paragrafos separados — e o
conversor de blocos SABE emitir `heading`. Naquela geracao o modelo devolveu
tudo dentro de um `<p>` so, e o conversor embarcou fielmente o que recebeu.

Fidelidade ao que chega nao e virtude quando o que chega esta quebrado: um bloco
unico de 2.755 caracteres nao tem estrutura para o leitor nem para o buscador.
"""

from __future__ import annotations

from app.cinerie.blocks import html_to_blocks


def test_paragrafos_colados_num_p_unico_viram_blocos_separados():
    corpo = (
        "<p>A Marvel confirmou a data de estreia do novo filme.\n\n"
        "O que se sabe ate agora\n\n"
        "O estudio ainda nao divulgou o elenco completo.</p>"
    )
    blocos = html_to_blocks(corpo).blocks

    assert [b["text"] for b in blocos] == [
        "A Marvel confirmou a data de estreia do novo filme.",
        "O que se sabe ate agora",
        "O estudio ainda nao divulgou o elenco completo.",
    ]
    assert all(b["type"] == "paragraph" for b in blocos)


def test_ids_seguem_sequenciais_apos_a_divisao():
    """O id ancora comentario e correcao no CMS: nao pode repetir nem pular."""
    corpo = "<p>Um.\n\nDois.</p><p>Tres.</p>"
    blocos = html_to_blocks(corpo).blocks
    assert [b["id"] for b in blocos] == ["b1", "b2", "b3"]


def test_corpo_bem_formado_nao_muda():
    """A correcao nao pode reescrever o que ja estava certo."""
    corpo = "<h2>O que muda</h2><p>Paragrafo unico e limpo.</p>"
    blocos = html_to_blocks(corpo).blocks
    assert [(b["type"], b["text"]) for b in blocos] == [
        ("heading", "O que muda"),
        ("paragraph", "Paragrafo unico e limpo."),
    ]


def test_quebra_simples_nao_divide():
    """So linha EM BRANCO separa paragrafo.

    Uma quebra simples dentro da frase e formatacao do gerador, nao fronteira de
    paragrafo — dividir ali picaria a frase no meio.
    """
    blocos = html_to_blocks("<p>Primeira linha\nsegunda linha da mesma frase.</p>").blocks
    assert len(blocos) == 1
    assert blocos[0]["text"] == "Primeira linha segunda linha da mesma frase."


def test_nenhum_bloco_gigante_sobrevive_quando_ha_fronteira_declarada():
    """A propriedade que importa, medida como ela apareceu em producao."""
    trecho = "Frase de tamanho realista para uma materia de entretenimento. " * 12
    corpo = f"<p>{trecho}\n\n{trecho}\n\n{trecho}</p>"
    blocos = html_to_blocks(corpo).blocks

    assert len(blocos) == 3
    assert max(len(b["text"]) for b in blocos) < 1000, (
        "um bloco unico de milhares de caracteres foi ao ar em 05/08; "
        "com fronteira declarada no texto, isso nao pode se repetir"
    )
