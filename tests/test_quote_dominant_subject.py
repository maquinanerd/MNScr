"""Citacao com verbo de fala sem nome, quando a materia tem UM so falante.

A materia 34 ("Ryan Murphy confirma elenco estelar para 13ª temporada de AHS")
tem quatro citacoes diretas e nenhuma virou bloco ``quote``: todas usam
"..., afirmou." / "..., explicou." sem nome colado as aspas, porque a materia
inteira tem um unico sujeito. A regra que exigia nome adjacente esta certa para
``"...", afirmou o executivo`` — executivo quem? — mas apertada demais aqui.

Estes testes usam os textos REAIS da materia 34 e prendem os dois lados:
promover quando o sujeito dominante e inequivoco, continuar rejeitando quando
ha ambiguidade (dois falantes) ou cargo generico.
"""

from app.cinerie.blocks import (
    BLOCK_QUOTE,
    dominant_speaker,
    html_to_blocks,
    split_promotable_quote,
)

_TITLE = "Ryan Murphy confirma elenco estelar para 13ª temporada de AHS"

# Paragrafos reais do draft da materia 34 (draft-73b18b194d..., articleId 34).
_PARA_COLON = (
    "Em entrevista recente, Ryan Murphy revelou que o processo de reunir o "
    "elenco original foi surpreendentemente simples. Murphy admitiu que a "
    "participação de Lange foi fundamental para a viabilidade do projeto: "
    "\"Se ela tivesse dito não, eu provavelmente não teria feito\"."
)
_PARA_AFIRMOU = (
    "A nova temporada, composta por 13 episódios, foi descrita por Murphy como "
    "uma experiência comparável aos filmes da franquia Avengers. O criador "
    "explicou que o esforço de escrita durou meses e que o resultado final é "
    "uma carta de amor aos admiradores da obra. \"É como reunir a banda "
    "novamente. Assim que eles chegaram ao set, foi como se nunca tivéssemos "
    "parado de produzir\", afirmou."
)
_PARA_EXPLICOU = (
    "Um dos pontos mais comentados antes da estreia foi a saída de Ariana "
    "Grande do elenco. Murphy esclareceu que a decisão foi estritamente "
    "motivada por conflitos de agenda. O criador enfatizou que a relação entre "
    "ambos permanece excelente. \"Quem é mais ocupada que Ariana Grande? "
    "Ninguém. Foi um conflito de agenda real. Eu disse que poderíamos adiar "
    "para outra temporada e ela concordou\", explicou."
)

_BODY_34 = (
    f"<p>{_PARA_COLON}</p>"
    f"<h2>Uma homenagem aos fãs</h2>"
    f"<p>{_PARA_AFIRMOU}</p>"
    f"<h2>O motivo da ausência de Ariana Grande</h2>"
    f"<p>{_PARA_EXPLICOU}</p>"
)


class TestDominantSpeaker:
    def test_um_falante_com_duas_grafias_e_um_so(self):
        corpo = (
            "Ryan Murphy revelou que tudo mudou. Murphy admitiu que foi "
            "dificil. Murphy garantiu que continua."
        )
        assert dominant_speaker(corpo, _TITLE) == "Ryan Murphy"

    def test_titulo_completa_a_grafia_do_corpo(self):
        corpo = "Murphy admitiu que foi dificil."
        assert dominant_speaker(corpo, _TITLE) == "Ryan Murphy"

    def test_dois_falantes_nao_ha_dominante(self):
        corpo = (
            "Ryan Murphy revelou que tudo mudou. John Landgraf afirmou que o "
            "canal apoia o projeto."
        )
        assert dominant_speaker(corpo, "") is None

    def test_sem_verbo_de_fala_nao_ha_dominante(self):
        corpo = "Ryan Murphy dirigiu o piloto. John Landgraf lidera o FX."
        assert dominant_speaker(corpo, _TITLE) is None

    def test_pronome_nao_vira_falante(self):
        corpo = "Ele disse que tudo mudou. Ele afirmou que continua."
        assert dominant_speaker(corpo, "") is None


class TestBareVerbPromotion:
    def test_afirmou_sem_nome_promove_com_dominante(self):
        split = split_promotable_quote(_PARA_AFIRMOU, dominant="Ryan Murphy")
        assert split is not None
        assert split.text.startswith("É como reunir a banda novamente.")
        assert split.attribution == "Ryan Murphy"
        assert split.before.endswith("admiradores da obra.")

    def test_explicou_sem_nome_promove_com_dominante(self):
        split = split_promotable_quote(_PARA_EXPLICOU, dominant="Ryan Murphy")
        assert split is not None
        assert split.text.endswith("e ela concordou")
        assert split.attribution == "Ryan Murphy"

    def test_dois_pontos_com_verbo_na_oracao_promove(self):
        split = split_promotable_quote(_PARA_COLON, dominant="Ryan Murphy")
        assert split is not None
        assert split.text == (
            "Se ela tivesse dito não, eu provavelmente não teria feito"
        )
        # A oracao nomeia "Murphy", subconjunto do dominante -> grafia completa.
        assert split.attribution == "Ryan Murphy"
        # A oracao introdutoria carrega informacao e FICA no paragrafo.
        assert "participação de Lange foi fundamental" in split.before
        assert split.before.endswith(":")

    def test_sem_dominante_nada_promove(self):
        assert split_promotable_quote(_PARA_AFIRMOU) is None
        assert split_promotable_quote(_PARA_EXPLICOU) is None
        assert split_promotable_quote(_PARA_COLON) is None

    def test_cargo_generico_continua_rejeitado(self):
        paragrafo = (
            "O estudio confirmou a mudanca de planos para a proxima fase. "
            "\"Vamos reavaliar todos os projetos em andamento antes de "
            "qualquer anuncio\", afirmou o executivo."
        )
        assert split_promotable_quote(paragrafo, dominant="Ryan Murphy") is None

    def test_dois_pontos_sem_verbo_de_fala_nao_promove(self):
        paragrafo = (
            "A produção preparou uma surpresa para os fãs da franquia inteira: "
            "\"uma temporada que celebra tudo o que veio antes e aponta para o "
            "futuro\"."
        )
        assert split_promotable_quote(paragrafo, dominant="Ryan Murphy") is None


class TestMateria34EndToEnd:
    def test_materia_34_agora_emite_blocos_quote(self):
        conversion = html_to_blocks(_BODY_34, article_title=_TITLE)
        quotes = [b for b in conversion.blocks if b["type"] == BLOCK_QUOTE]
        # Tres candidatas, teto de 2 por materia — e as duas atribuidas ao
        # sujeito dominante com a grafia completa do titulo.
        assert len(quotes) == 2
        assert all(q["attribution"] == "Ryan Murphy" for q in quotes)

    def test_terceira_citacao_fica_no_paragrafo(self):
        conversion = html_to_blocks(_BODY_34, article_title=_TITLE)
        paragrafos = " ".join(
            b["text"] for b in conversion.blocks if b["type"] == "paragraph"
        )
        # A citacao que nao coube no teto continua INTEIRA no corpo.
        assert "Quem é mais ocupada que Ariana Grande?" in paragrafos

    def test_materia_com_dois_falantes_nao_promove_sem_nome(self):
        body = (
            "<p>Ryan Murphy revelou os planos. John Landgraf afirmou que o "
            "canal apoia. \"É como reunir a banda novamente, com todos os "
            "nomes que marcaram a franquia\", afirmou.</p>"
        )
        conversion = html_to_blocks(body, article_title="Nova temporada")
        assert all(b["type"] != BLOCK_QUOTE for b in conversion.blocks)
