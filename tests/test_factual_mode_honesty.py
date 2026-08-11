"""O laudo factual precisa dizer o que ele REALMENTE fez, nao o que foi pedido.

Duas coisas se cruzam aqui, e juntas produzem o pior resultado possivel: um
numero que parece veredito editorial e e, na verdade, limite de ferramenta.

1. O MNScr existe para reescrever fonte em INGLES (ScreenRant, Variety,
   Deadline) em PORTUGUES. O matcher deterministico compara texto com texto:
   `similarity = token_overlap(claim, evidencia)`. Entre idiomas diferentes essa
   sobreposicao e ~zero, e so sobrevive claim cujo sujeito e nome proprio grafado
   igual nas duas linguas ("Marvel" sim, "Vingadores"/"Avengers" nao).

2. Quando nada casa, `compute_claim_status` devolve UNSUPPORTED — o mesmo status
   que uma alucinacao recebe. O laudo entao afirma "a fonte nao sustenta", quando
   o que aconteceu foi "o verificador nao consegue ler esta lingua".

O modo `hybrid` existe justamente para isso. Enquanto ele nao roda, o minimo
exigivel e que o laudo nao se anuncie como `hybrid`: quem le um 0.0 precisa saber
se olhou para um veredito ou para um verificador cego.
"""

from __future__ import annotations

from app.editorial import DraftContent, DraftProvenance, EditorialDraft, SourceReference
from app.factual import states as S
from app.factual_builder import build_factual_assessment
from app.factual_store import FactualStore

PT = (
    "A Marvel confirmou que o novo filme dos Vingadores estreia em 1 de maio de 2026. "
    'O diretor afirmou, para a imprensa, que "esta e a maior producao que ja fizemos". '
    "As filmagens comecaram em marco de 2025 e duraram 8 meses. "
    "A serie foi renovada para uma terceira temporada."
)

EN = (
    "Marvel confirmed the new Avengers film premieres on May 1, 2026. "
    'The director said, for the press, that "this is the biggest production we have ever made". '
    "Filming began in March 2025 and lasted 8 months. "
    "The series was renewed for a third season."
)


def _draft() -> EditorialDraft:
    return EditorialDraft(
        draft_id="draft-modo", article_id=1,
        content=DraftContent(
            title="Vingadores estreia em 1 de maio de 2026",
            body_html=f"<p>{PT}</p>",
            subtitle="Estudio confirma data da nova producao.",
            meta_description="Marvel confirma a estreia do novo filme para maio de 2026.",
        ),
        provenance=DraftProvenance(
            input_hash="a" * 64, output_hash="b" * 64,
            input_contract_version="rss-prime-event-v1",
            input_event_key="evt-modo", input_revision=1, prompt_version="p",
        ),
        event_key="evt-modo", revision=1,
        sources=[SourceReference(url="https://variety.example/a", domain="variety.example",
                                 is_primary=True, extraction_status="OK")],
    )


def _material(conteudo: str) -> list[dict]:
    return [{
        "url": "https://variety.example/a",
        "content": conteudo,
        "title": "Avengers",
        "source_name": "Variety",
        "source_domain": "variety.example",
        "is_primary": True,
    }]


def test_hybrid_pedido_sem_resposta_nao_pode_se_declarar_hybrid():
    """Pedir `hybrid` e nao ter resposta semantica nao vira laudo `hybrid`.

    `NO_SEMANTIC_CLAIM_RESPONSE` ja e registrado nos avisos, mas o campo `mode`
    continuava gravado como `hybrid`. Quem le o laudo persistido — a reavaliacao
    do gate, um humano, um relatorio — via `mode: hybrid` e concluia que a camada
    semantica tinha rodado e nao encontrado nada. Ela nao rodou.
    """
    avaliacao = build_factual_assessment(
        _draft(), _material(EN), mode=S.MODE_HYBRID, claim_response=None
    )

    assert "NO_SEMANTIC_CLAIM_RESPONSE" in avaliacao.warnings
    assert avaliacao.effective_mode == S.MODE_DETERMINISTIC, (
        "sem resposta semantica o modo EFETIVO e deterministico, "
        f"e o laudo registrou {avaliacao.effective_mode!r}"
    )
    assert avaliacao.requested_mode == S.MODE_HYBRID, (
        "o modo solicitado precisa ser preservado: e ele que diz que a "
        "camada semantica era esperada e faltou"
    )


def test_deterministico_registra_pedido_e_efetivo_iguais():
    """Sem divergencia nao ha o que explicar: os dois modos coincidem."""
    avaliacao = build_factual_assessment(
        _draft(), _material(PT), mode=S.MODE_DETERMINISTIC
    )

    assert avaliacao.requested_mode == S.MODE_DETERMINISTIC
    assert avaliacao.effective_mode == S.MODE_DETERMINISTIC


def test_a_mesma_materia_desaba_quando_a_fonte_muda_de_idioma():
    """A prova do defeito: so o idioma da fonte muda, e a cobertura desaba.

    Mesmo draft, mesmos fatos, mesma quantidade de evidencia. Se a cobertura cai
    por causa do IDIOMA, ela nao esta medindo sustentacao factual — esta medindo
    se o texto da fonte se parece com o texto do draft.

    Este teste nao exige que o defeito esteja corrigido; ele o PRENDE. Se alguem
    ensinar o matcher a cruzar idiomas, ele falha e obriga a revisar o numero
    aqui, em vez de deixar a melhora passar despercebida.

    NOTA (correcao do sujeito de 1 letra): a frase de atribuicao ganhou "para a
    imprensa"/"for the press" para que ``_language()`` bata >=2 marcadores e
    classifique a frase como PT/EN de verdade. Antes da correcao, o sujeito
    "O" (de "O diretor...") virava "o" e casava por coincidencia como substring
    em qualquer evidencia — o que mascarava esta mesma frase atras do bug de
    sujeito, e nao atras do defeito que este teste prende.
    """
    draft = _draft()
    mesma_lingua = build_factual_assessment(draft, _material(PT), mode=S.MODE_DETERMINISTIC)
    outra_lingua = build_factual_assessment(draft, _material(EN), mode=S.MODE_DETERMINISTIC)

    assert len(mesma_lingua.evidence) == len(outra_lingua.evidence), (
        "o volume de evidencia precisa ser comparavel para o teste isolar o idioma"
    )
    assert mesma_lingua.coverage.coverage_ratio > 0.8
    assert outra_lingua.coverage.coverage_measured is False
    assert outra_lingua.coverage.unmeasured_material_claims > 0
    assert outra_lingua.coverage.unsupported_material_claims == 0
    cegos = [
        c for c in outra_lingua.material_claims
        if "CROSS_LINGUAL_MATCH_UNAVAILABLE" in c.warnings
    ]
    assert cegos
    assert all(c.status == S.UNVERIFIED for c in cegos)


def test_laudo_persistido_preserva_modos_e_cobertura_nao_medida(tmp_path):
    avaliacao = build_factual_assessment(
        _draft(), _material(EN), mode=S.MODE_HYBRID, claim_response=None
    )
    store = FactualStore(db_path=str(tmp_path / "factual.db"))
    try:
        store.save_assessment(avaliacao)
        reloaded = store.get_assessment(avaliacao.draft_id)
    finally:
        store.close()

    assert reloaded is not None
    assert reloaded.requested_mode == S.MODE_HYBRID
    assert reloaded.effective_mode == S.MODE_DETERMINISTIC
    assert reloaded.coverage.coverage_measured is False
    assert (
        reloaded.coverage.unmeasured_material_claims
        == avaliacao.coverage.unmeasured_material_claims
    )
