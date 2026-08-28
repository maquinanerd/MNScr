"""`marks`: negrito, italico e link como INTERVALO, nunca como HTML.

O contrato do Cinerie nao aceita markup no corpo — o texto viaja limpo e a
enfase viaja ao lado, como `start`/`end` sobre esse texto. Quem desenha
`<strong>` e o site, a partir do dado.

Isso faz do OFFSET o unico ponto de falha que importa, e ele e silencioso: um
intervalo deslocado continua dentro do texto, passa na validacao dos dois lados
e so aparece na pagina, com a palavra errada em negrito. Por isso a maioria dos
casos aqui mede alinhamento, e nao presenca.

Duas armadilhas com nome proprio:

* `collapse` muda o comprimento do texto. Offset calculado antes da limpeza
  aponta para outro lugar depois dela.
* Python conta PONTO DE CODIGO; o contrato (e o JavaScript que renderiza) conta
  UNIDADE UTF-16. Um emoji vale 1 aqui e 2 la.
"""

from app.cinerie.blocks import BLOCK_PARAGRAPH, html_to_blocks


def _paragrafos(html: str, *, links: tuple = ()):
    """Blocos de paragrafo do HTML.

    `links` e a lista FECHADA de destinos que podem virar ancora — as fontes que
    a materia declara. Vazia por padrao de proposito: link que a materia nao
    declarou foi a IA que inventou.
    """
    conversao = html_to_blocks(html, allowed_link_hrefs=links)
    return [b for b in conversao.blocks if b["type"] == BLOCK_PARAGRAPH]


def _trecho(bloco, marca):
    """O que a marcacao pega, lida como o JavaScript leria: em UTF-16."""
    bytes_utf16 = bloco["text"].encode("utf-16-le")
    return bytes_utf16[marca["start"] * 2 : marca["end"] * 2].decode("utf-16-le")


def test_bold_and_italic_land_on_the_words_they_wrap():
    bloco = _paragrafos("<p>O filme <strong>Superman</strong> estreia em <em>julho</em>.</p>")[0]
    assert bloco["text"] == "O filme Superman estreia em julho."
    por_tipo = {marca["type"]: marca for marca in bloco["marks"]}
    assert _trecho(bloco, por_tipo["bold"]) == "Superman"
    assert _trecho(bloco, por_tipo["italic"]) == "julho"


def test_a_link_carries_the_href_and_only_the_anchor_text():
    bloco = _paragrafos(
        '<p>Leia <a href="https://variety.com/x">a reportagem</a> completa.</p>',
        links=("https://variety.com/x",),
    )[0]
    marca = bloco["marks"][0]
    assert marca["type"] == "link"
    assert marca["href"] == "https://variety.com/x"
    assert _trecho(bloco, marca) == "a reportagem"


def test_an_emoji_does_not_shift_the_emphasis_that_follows_it():
    """A regressao mais cara deste arquivo, e a que passa despercebida.

    Em pontos de codigo o intervalo pegaria `"uperman "` — uma casa a esquerda,
    dentro do texto, valida nos dois lados, errada na pagina.
    """
    bloco = _paragrafos("<p>Cena \U0001F3AC com <strong>Superman</strong> no fim.</p>")[0]
    marca = bloco["marks"][0]
    assert _trecho(bloco, marca) == "Superman"
    assert bloco["text"][marca["start"] : marca["end"]] != "Superman"


def test_collapsed_whitespace_does_not_shift_the_emphasis():
    """Quebra de linha e indentacao viram um espaco so; o offset acompanha."""
    bloco = _paragrafos(
        "<p>Um\n  texto    com   espaco   solto e <strong>enfase</strong> no fim.</p>"
    )[0]
    marca = bloco["marks"][0]
    assert _trecho(bloco, marca) == "enfase"


def test_a_relative_link_never_becomes_a_mark():
    """O contrato so aceita http(s). Link relativo viraria pedido recusado."""
    blocos = _paragrafos('<p>Veja <a href="/interno">aqui</a> o resto.</p>')
    assert blocos[0]["text"] == "Veja aqui o resto."
    assert "marks" not in blocos[0]


def test_a_paragraph_without_emphasis_has_no_marks_key():
    """Lista vazia nao e o mesmo que ausencia: `[]` diria que a enfase foi
    apagada, e o CMS nao grava coluna para isso."""
    bloco = _paragrafos("<p>Sem enfase nenhuma aqui.</p>")[0]
    assert "marks" not in bloco


def test_nested_emphasis_inside_a_link_keeps_both_marks():
    bloco = _paragrafos(
        '<p>Veja <a href="https://exemplo.com/a"><strong>o anuncio</strong></a> agora.</p>',
        links=("https://exemplo.com/a",),
    )[0]
    tipos = {marca["type"]: _trecho(bloco, marca) for marca in bloco["marks"]}
    assert tipos["link"] == "o anuncio"
    assert tipos["bold"] == "o anuncio"


def test_a_paragraph_that_carries_several_paragraphs_travels_without_marks():
    """Quando o `<p>` vira varios blocos, a divisao acontece sobre o TEXTO e os
    offsets do DOM deixam de valer. Sai sem enfase — nunca com enfase torta."""
    blocos = _paragrafos("<p>Primeiro com <strong>enfase</strong>.\n\nSegundo pedaco.</p>")
    assert len(blocos) == 2
    assert all("marks" not in bloco for bloco in blocos)


def test_a_link_the_writer_invented_never_becomes_an_anchor():
    """A materia 128 saiu com `https://www.cinerie.com.br/tag/industry` no corpo.

    Dominio errado (o site e `.com`) e caminho de tag do WordPress, que o Cinerie
    nao tem. Foi a IA inventando link interno — e, com `marks` atravessando, a
    invencao virou ancora clicavel numa pagina publicada. Link quebrado e pior
    que texto sem link: o leitor nao tem como saber que ele nao existia.
    """
    html = '<p>A serie <a href="https://www.cinerie.com.br/tag/industry">explora o poder</a> em Londres.</p>'
    blocos = _paragrafos(html)
    assert blocos[0]["text"] == "A serie explora o poder em Londres."
    assert "marks" not in blocos[0]


def test_a_link_to_a_declared_source_survives():
    """A recusa e do INVENTADO, nao do link. Fonte declarada continua clicavel."""
    fonte = "https://collider.com/industry-final-season-5"
    html = f'<p>Segundo o <a href="{fonte}">Collider</a>, as filmagens comecaram.</p>'
    bloco = _paragrafos(html, links=(fonte,))[0]
    marca = bloco["marks"][0]
    assert marca["type"] == "link"
    assert marca["href"] == fonte
    assert _trecho(bloco, marca) == "Collider"
