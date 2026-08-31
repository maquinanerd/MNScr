"""Duas fontes num cacho precisam falar do MESMO acontecimento.

O RSS Prime agrupa por similaridade e erra. Em 28/08/2026 ele juntou
"Kit Harington ... Industry" (Collider, 1.368 palavras) com
"Dan Stevens ... RoboCop" (ComicBook, 503) — dois assuntos sem nada em comum.

O MNScr aceitou: mesclou 1.871 palavras num texto so, publicou as duas na lista
de fontes da materia, e o Editorial Gate ainda registrou `GATE_MULTI_SOURCE`
como sinal de QUALIDADE. O leitor terminou a materia sobre `Industry` com um
link para uma entrevista sobre `RoboCop` no rodape, como se fosse fonte dela.

O caso e traicoeiro porque as duas manchetes COMPARTILHAM meia frase — as duas
sao "Breaks Silence on". Por isso a checagem ignora palavra de manchete e olha
so o que identifica assunto.
"""

from app.multi_source_builder import _subject_tokens, _talks_about_the_same_thing

COLLIDER = {
    "title": "Kit Harington Officially Breaks Silence on 'Industry's' Fantastic Final Season",
    "content": "Kit Harington talks about the final season of Industry on HBO.",
}


def test_the_robocop_article_is_refused_even_sharing_half_the_headline():
    comicbook = {
        "title": "Dan Stevens Breaks Silence on RoboCop Casting And What He Wants to Preserve",
        "content": "Dan Stevens discusses the RoboCop remake and the original movie.",
    }
    assert _talks_about_the_same_thing(COLLIDER, comicbook) is False


def test_legitimate_coverage_of_the_same_event_is_accepted():
    """A checagem existe para recusar assunto diferente, nao angulo diferente."""
    variety = {
        "title": "'Industry' Sets Fifth And Final Season At HBO",
        "content": "The drama returns for a last run, with Kit Harington back as Henry Muck.",
    }
    assert _talks_about_the_same_thing(COLLIDER, variety) is True


def test_a_secondary_that_only_repeats_the_subject_in_the_BODY_is_accepted():
    """Manchete com outro angulo e comum; o corpo quase sempre repete o nome.

    O corpo aqui nomeia as DUAS ancoras da primaria (a pessoa e a obra). Ate
    30/08/2026 ele nomeava so a pessoa e o teste passava, porque bastava um
    token compartilhado — e era essa folga que deixava passar materia sobre a
    mesma pessoa em obra diferente. Ver o teste logo abaixo.
    """
    outra = {
        "title": "HBO renova drama financeiro para desfecho",
        "content": (
            "A emissora confirmou o retorno de Kit Harington para o "
            "encerramento de Industry."
        ),
    }
    assert _talks_about_the_same_thing(COLLIDER, outra) is True


def test_the_same_person_in_a_DIFFERENT_work_is_refused():
    """O buraco que a contagem de tokens deixava aberto.

    "Kit Harington" sozinho nao identifica acontecimento: ele aparece tanto na
    materia sobre `Industry` quanto numa sobre um filme da Marvel. Compartilhar
    UM token distintivo bastava, e as duas eram mescladas num texto so.
    """
    outro_trabalho = {
        "title": "Kit Harington Joins Marvel's Next Blockbuster",
        "content": "The actor was cast in a Marvel feature, his first since leaving HBO.",
    }
    assert _talks_about_the_same_thing(COLLIDER, outro_trabalho) is False


def test_the_same_franchise_in_a_DIFFERENT_event_is_refused():
    """A colisao de franquia, medida contra o texto real de 31/08/2026.

    "Halloween Horror Nights 'Stranger Things' House" e "Stephen King meets
    Stranger Things in Flanagan's Carrie" sao acontecimentos diferentes que
    dividem o nome inteiro da franquia. Com o corpo real da secundaria em maos,
    o criterio de contagem aceitava 5 dos 6 cachos podres do dia.
    """
    casa_do_horror = {
        "title": "Inside the Halloween Horror Nights Stranger Things House",
        "content": (
            "The Halloween Horror Nights house recreates the Stranger Things "
            "world with an epic effort from the Duffer Brothers."
        ),
    }
    carrie = {
        "title": "Stephen King Meets Stranger Things in Mike Flanagan's Carrie",
        "content": (
            "Mike Flanagan adapts Stephen King's Carrie for television, and the "
            "series shares a cast member with Stranger Things."
        ),
    }
    assert _talks_about_the_same_thing(casa_do_horror, carrie) is False


def test_headline_noise_alone_never_counts_as_subject():
    """`Breaks`, `Silence`, `Exclusive` e nomes de plataforma nao identificam nada."""
    assert _subject_tokens("Breaks Silence: Exclusive First Look on Netflix") == set()


def test_without_a_title_the_check_abstains():
    """Recusar por falta de dado NOSSO descartaria fonte boa por defeito de
    extracao. Na duvida, a fonte entra e as outras regras decidem."""
    sem_titulo = {"title": "", "content": "qualquer coisa"}
    assert _talks_about_the_same_thing(sem_titulo, {"title": "Outra", "content": "x"}) is True


def test_the_word_policy_no_longer_throws_away_two_thirds_of_a_long_source():
    """O teto de 550/1000 anulava a regra proporcional em toda fonte grande.

    Medido na materia 128: fonte de 1.879 palavras, minimo proporcional de
    1.409, minimo APLICADO de 550. O escritor entregou 562 e a expansao foi
    dispensada — 30% da fonte, com o veiculo original visivelmente mais completo
    que a materia publicada.
    """
    from app.policy_engine import calculate_dynamic_word_policy

    politica = calculate_dynamic_word_policy(1879, "news", "superfeed")
    assert politica["min_acceptable_words"] == 1200
    assert politica["target_words"] > 1600


def test_the_ceiling_still_protects_against_an_anomalous_source():
    """O teto vira SEGURANCA, nao politica: fonte de 12 mil palavras (extracao
    que concatenou o site inteiro) nao pode virar pedido de materia impossivel
    nem conta de token inesperada."""
    from app.policy_engine import calculate_dynamic_word_policy

    politica = calculate_dynamic_word_policy(12000, "news", "superfeed")
    assert politica["max_recommended_words"] == 1800


def test_a_short_source_is_untouched_by_the_new_ceiling():
    """Fonte pequena nunca chegou perto do teto; a mudanca nao pode move-la."""
    from app.policy_engine import calculate_dynamic_word_policy

    politica = calculate_dynamic_word_policy(380, "news", "superfeed")
    assert politica["min_acceptable_words"] == 285
    assert politica["target_words"] == 380


def test_the_dead_generation_config_is_gone():
    """`AI_GENERATION_CONFIG` declarava `temperature: 0.7` e ninguem o lia.

    A configuracao real do escritor e 0.2. Uma constante morta com nome bom
    responde a pergunta errada numa auditoria — foi o que quase aconteceu.
    """
    import app.config as config

    assert not hasattr(config, "AI_GENERATION_CONFIG")


def test_headline_decoration_never_becomes_an_anchor():
    """A ancora e o que a manchete E o lead repetem.

    "Fantastic" enfeita a manchete e some no lead; "Industry" e "Harington"
    ficam. Sem esse cruzamento, a decoracao entra na conta e faz o denominador
    crescer com palavras que nao identificam nada.
    """
    from app.multi_source_builder import _subject_anchors

    assert _subject_anchors(COLLIDER) == {"harington", "industry"}


def test_without_a_lead_the_check_abstains():
    """Sem corpo nao ha ancora, e sem ancora a checagem se cala.

    Nao e alcancavel em producao — so documento com `ARTICLE_BODY_OK` chega
    aqui — mas a escolha e a mesma de sempre: na duvida por defeito de dado
    NOSSO, a fonte entra e as outras regras decidem.
    """
    from app.multi_source_builder import subject_coverage

    so_titulo = {"title": "Industry Sets Final Season", "content": ""}
    assert subject_coverage(so_titulo, {"title": "x", "content": "y"}) is None
    assert _talks_about_the_same_thing(so_titulo, {"title": "x", "content": "y"}) is True


def test_the_threshold_sits_in_the_middle_of_the_measured_band():
    """0,65 nao e chute: e o meio da faixa que acerta os 16 pares reais.

    Medido em 31/08/2026 com o corpo baixado dos dois veiculos de cada par:
    contaminado nunca passou de 0,50, legitimo nunca ficou abaixo de 0,80.
    Se este numero for afrouxado sem uma nova medicao, a colisao de franquia
    volta — foi assim que ela entrou.
    """
    from app.multi_source_builder import _MIN_COBERTURA_DO_ASSUNTO

    assert 0.55 <= _MIN_COBERTURA_DO_ASSUNTO <= 0.70
