"""Nenhum corte de texto pode terminar no meio de uma palavra.

A materia 34 ("Ryan Murphy... 13ª temporada", cluster 49ffcdb4e55c6963) foi ao
ar com a citacao de Ryan Murphy exibida como "Foi um confli / de agenda real" —
"conflito" partido. A investigacao mostrou o dado integro em toda a cadeia
(draft, payload, HTML servido); o corte visivel era a hifenizacao do CSS do
Cinerie (`.art-body { hyphens: auto }`). Mas a auditoria dos cortes do MNScr
achou duas brechas REAIS da mesma familia, que so nao dispararam nesta materia:

- ``plain_text``: recuava ate o espaco apenas quando ele estava nos ultimos
  40% do corte; abaixo disso publicava a palavra picada.
- ``content_limiter._truncate_text``: mesma regra com 20% — e esse texto vira
  prompt da IA, que ja reproduziu literalmente o que recebeu.

Estes testes prendem a citacao exata da materia 34 de ponta a ponta na
conversao de blocos e fecham as duas brechas para qualquer limite de corte.
"""

from app.cinerie.blocks import html_to_blocks
from app.cinerie.text import collapse, plain_text
from app.content_limiter import truncate_html_by_visible_chars

# O paragrafo REAL do draft-73b18b194d5f9854... (articleId 34 no Cinerie),
# como saiu da IA e entrou na conversao de blocos.
_AHS_PARAGRAPH = (
    "<p>Um dos pontos mais comentados antes da estreia foi a saída de "
    "<b>Ariana Grande</b> do elenco. <b>Murphy</b> esclareceu que a decisão foi "
    "estritamente motivada por conflitos de agenda, especificamente devido à "
    "turnê <i>Eternal Sunshine</i> da artista. O criador enfatizou que a "
    "relação entre ambos permanece excelente e que a porta está aberta para "
    "futuras colaborações. \"Quem é mais ocupada que <b>Ariana Grande</b>? "
    "Ninguém. Foi um conflito de agenda real. Eu disse que poderíamos adiar "
    "para outra temporada e ela concordou\", explicou.</p>"
)

_QUOTE_SENTENCE = "Foi um conflito de agenda real."


def _ends_at_word_boundary(result: str, source: str) -> bool:
    """O resultado termina em palavra completa do texto de origem?

    Completo = o resultado e o texto inteiro, ou o que vem depois dele na
    origem comeca em fronteira (espaco/pontuacao ja aparada pelo rstrip).
    """
    collapsed = collapse(source)
    if result == collapsed:
        return True
    if not result:
        return True
    index = collapsed.find(result)
    assert index == 0, f"resultado nao e prefixo da origem: {result!r}"
    tail = collapsed[len(result) :]
    # `plain_text` apara pontuacao final; a fronteira pode ser o espaco ou a
    # pontuacao que foi aparada. O que NUNCA pode acontecer e a origem seguir
    # com letra/numero colado no fim do resultado.
    return not tail[:1].isalnum()


class TestMateria34QuoteIntegra:
    def test_citacao_atravessa_conversao_de_blocos_inteira(self):
        conversion = html_to_blocks(_AHS_PARAGRAPH)
        textos = " ".join(block.get("text", "") for block in conversion.blocks)
        assert _QUOTE_SENTENCE in textos
        assert "confli " not in textos
        assert "confli " not in textos

    def test_nenhum_bloco_termina_no_meio_de_palavra(self):
        conversion = html_to_blocks(_AHS_PARAGRAPH)
        for block in conversion.blocks:
            texto = block.get("text", "")
            assert not texto.endswith("confli")
            # Fim de bloco deve ser palavra completa: letra final seguida de
            # nada e aceitavel; o que nao pode e um prefixo do vocabulario do
            # proprio paragrafo (deteccao direta do defeito da materia 34).
            assert "conflito" in texto or "confli" not in texto


class TestPlainTextFronteiraDePalavra:
    def test_corte_nunca_pica_palavra_em_nenhum_limite(self):
        frase = collapse(
            "Quem e mais ocupada que Ariana Grande? Ninguem. Foi um conflito "
            "de agenda real. Eu disse que poderiamos adiar."
        )
        primeira_palavra = frase.split(" ", 1)[0]
        for limite in range(1, len(frase) + 5):
            resultado = plain_text(frase, max_length=limite)
            assert len(resultado) <= limite
            if limite < len(primeira_palavra):
                # Excecao documentada: nao ha fronteira antes da primeira
                # palavra; o corte duro e o unico resultado nao vazio possivel.
                assert resultado == primeira_palavra[:limite]
                continue
            assert _ends_at_word_boundary(resultado, frase), (
                f"limite={limite} produziu corte no meio de palavra: {resultado!r}"
            )

    def test_regressao_espaco_antes_dos_60_por_cento(self):
        # A regra antiga (recuar so quando o espaco estava depois de 60% do
        # corte) picava exatamente este caso: espaco cedo, palavra longa no fim.
        texto = "Um conflito interminavelmente longo"
        resultado = plain_text(texto, max_length=9)
        assert resultado in ("Um", "Um conflito"[:9].rsplit(" ", 1)[0])
        assert resultado == "Um"

    def test_token_unico_maior_que_o_limite_mantem_corte_duro(self):
        # URL/hash sem espacos: nao existe fronteira de palavra para recuar.
        token = "a" * 50
        assert plain_text(token, max_length=10) == "a" * 10

    def test_corte_exatamente_na_fronteira_preserva_a_palavra(self):
        texto = "Foi um conflito de agenda"
        # limite cai exatamente no fim de "conflito": a palavra fica.
        limite = len("Foi um conflito")
        assert plain_text(texto, max_length=limite) == "Foi um conflito"


class TestContentLimiterFronteiraDePalavra:
    def test_limite_no_meio_de_conflito_nao_publica_confli(self):
        html = "<p>Foi um conflito de agenda real e nada mais.</p>"
        for limite in range(1, 46):
            resultado = truncate_html_by_visible_chars(html, limite)
            from bs4 import BeautifulSoup

            visivel = BeautifulSoup(resultado, "html.parser").get_text(" ", strip=True)
            assert "confli " not in visivel + " " or "conflito" in visivel, (
                f"limite={limite} deixou palavra picada: {visivel!r}"
            )
            assert not visivel.endswith("confli"), (
                f"limite={limite} terminou no meio de 'conflito': {visivel!r}"
            )

    def test_corte_em_no_de_texto_interno_respeita_fronteira(self):
        # O limite estoura DENTRO do <b>: o no de texto interno tambem recua
        # ate a fronteira em vez de picar a palavra.
        html = "<p>Motivado por <b>conflitos de agenda</b> reais.</p>"
        resultado = truncate_html_by_visible_chars(html, len("Motivado por confli"))
        from bs4 import BeautifulSoup

        visivel = BeautifulSoup(resultado, "html.parser").get_text(" ", strip=True)
        assert not visivel.endswith("confli")
