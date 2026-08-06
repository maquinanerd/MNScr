"""O modo `hybrid`, exercitado com fake determinístico — sem rede, sem cota.

O `hybrid` existe porque o matcher determinístico compara TEXTO com TEXTO, e o
MNScr reescreve fonte em inglês para português: a sobreposição de tokens entre as
duas línguas é ~zero (ver `test_factual_mode_honesty.py`). A camada semântica é a
saída prevista para isso.

Aqui ela é exercitada com uma resposta FIXA. O motivo de não chamar o modelo de
verdade não é só velocidade: a coleta do pytest já disparou chamada real ao
Gemini uma vez neste repositório e gastou cota (corrigido em `fe7399e`). Um teste
que depende de rede não testa o código — testa o dia.

Os casos são os que o mandato A2 lista: resposta boa, resposta semanticamente
inválida, `hybrid` sem adapter, e a garantia de que nada disso derruba o draft.
"""

from __future__ import annotations

import json

import pytest

from app.editorial import DraftContent, DraftProvenance, EditorialDraft, SourceReference
from app.factual import states as S
from app.factual_builder import build_factual_assessment

CORPO = (
    "A Marvel confirmou que o filme estreia em 1 de maio de 2026. "
    "O estudio investiu 250 milhoes de dolares na producao."
)


def _draft() -> EditorialDraft:
    return EditorialDraft(
        draft_id="draft-hybrid", article_id=1,
        content=DraftContent(
            title="Filme da Marvel estreia em 1 de maio de 2026",
            body_html=f"<p>{CORPO}</p>",
            subtitle="Estudio confirma data e orcamento da producao.",
            meta_description="Marvel confirma a estreia do novo filme para maio de 2026.",
        ),
        provenance=DraftProvenance(
            input_hash="a" * 64, output_hash="b" * 64,
            input_contract_version="rss-prime-event-v1",
            input_event_key="evt-h", input_revision=1, prompt_version="p",
        ),
        event_key="evt-h", revision=1,
        sources=[SourceReference(url="https://variety.example/a", domain="variety.example",
                                 is_primary=True, extraction_status="OK")],
    )


def _material() -> list[dict]:
    return [{
        "url": "https://variety.example/a",
        "content": CORPO,
        "title": "Marvel",
        "source_name": "Variety",
        "source_domain": "variety.example",
        "is_primary": True,
    }]


def _resposta_fixa(*afirmacoes: str) -> str:
    """A resposta do modelo, escrita a mao e sempre igual.

    Determinística de propósito: um fake que varia transforma falha de teste em
    adivinhação sobre qual execução produziu qual resultado.
    """
    return json.dumps({
        "claims": [
            {
                "display_text": texto,
                "claim_type": "OTHER",
                "confidence": 0.9,
                "location": ["body_1"],
            }
            for texto in afirmacoes
        ]
    })


def test_hybrid_com_resposta_valida_soma_claims_e_declara_o_modo():
    """O caminho feliz: a camada semântica rodou e o laudo diz isso."""
    avaliacao = build_factual_assessment(
        _draft(), _material(),
        mode=S.MODE_HYBRID,
        claim_response=_resposta_fixa("A Marvel confirmou que o filme estreia em 1 de maio de 2026."),
    )

    assert avaliacao.effective_mode == S.MODE_HYBRID
    assert avaliacao.requested_mode == S.MODE_HYBRID
    assert "NO_SEMANTIC_CLAIM_RESPONSE" not in avaliacao.warnings


def test_hybrid_sem_adapter_cai_para_deterministico_sem_mentir():
    """`hybrid` pedido e nenhuma resposta: o laudo NAO pode se dizer hybrid.

    Este e o estado de produção hoje — o adapter existe mas não está ligado. O
    perigo não é a queda de qualidade; é o registro dizer que a camada semântica
    rodou e não achou nada, quando ela não rodou.
    """
    avaliacao = build_factual_assessment(
        _draft(), _material(), mode=S.MODE_HYBRID, claim_response=None
    )

    assert avaliacao.effective_mode == S.MODE_DETERMINISTIC
    assert avaliacao.requested_mode == S.MODE_HYBRID
    assert "NO_SEMANTIC_CLAIM_RESPONSE" in avaliacao.warnings


@pytest.mark.parametrize(
    "resposta, rotulo",
    [
        ("isto nao e json", "texto solto"),
        ("[]", "topo e lista, nao objeto"),
        ('{"sem_claims": []}', "campo `claims` ausente"),
        ('{"claims": "nao e lista"}', "`claims` nao e lista"),
    ],
)
def test_resposta_semanticamente_invalida_e_recusada_sem_derrubar_o_draft(resposta, rotulo):
    """Resposta que não dá para interpretar é descartada, nunca adivinhada.

    E o draft segue: os claims determinísticos continuam de pé. Uma avaliação
    factual mais pobre é um problema; perder a matéria por causa de uma resposta
    malformada do modelo seria outro, maior.
    """
    avaliacao = build_factual_assessment(
        _draft(), _material(), mode=S.MODE_HYBRID, claim_response=resposta
    )

    assert any(w.startswith("CLAIM_RESPONSE_REJECTED") for w in avaliacao.warnings), (
        f"{rotulo}: a recusa precisa ficar registrada; veio {avaliacao.warnings}"
    )
    assert avaliacao.effective_mode == S.MODE_DETERMINISTIC, (
        f"{rotulo}: resposta rejeitada nao pode contar como camada semantica executada"
    )
    assert avaliacao.claims, f"{rotulo}: os claims deterministicos deveriam continuar de pe"


def test_resposta_valida_e_vazia_nao_se_confunde_com_ausencia():
    """"Rodou e não achou nada" e "não rodou" pedem providências diferentes.

    A primeira é sinal editorial sobre a matéria; a segunda é falha de ligação
    do adapter. Um aviso só para as duas obrigaria a adivinhar qual delas foi.
    """
    avaliacao = build_factual_assessment(
        _draft(), _material(), mode=S.MODE_HYBRID, claim_response=_resposta_fixa(),
    )

    assert "SEMANTIC_CLAIM_RESPONSE_EMPTY" in avaliacao.warnings
    assert "NO_SEMANTIC_CLAIM_RESPONSE" not in avaliacao.warnings
    assert avaliacao.effective_mode == S.MODE_DETERMINISTIC


def test_claim_inventado_pelo_modelo_nao_entra_no_laudo():
    """A defesa contra alucinação: a afirmação precisa existir no rascunho.

    Sem esta conferência o modo `hybrid` seria um caminho para o modelo inserir
    fato que o texto não afirma — e o laudo factual passaria a sustentar
    exatamente aquilo que ele existe para vigiar.
    """
    avaliacao = build_factual_assessment(
        _draft(), _material(),
        mode=S.MODE_HYBRID,
        claim_response=_resposta_fixa("O filme foi cancelado pela Disney em 2027."),
    )

    textos = " ".join(c.display_text for c in avaliacao.claims).lower()
    assert "cancelado" not in textos, (
        "uma afirmacao que nao esta no rascunho entrou no laudo"
    )
    assert "CLAIM_NOT_FOUND_IN_DRAFT" in avaliacao.warnings, (
        "o descarte precisa nomear o motivo, senao some do relatorio"
    )
    # Consequencia que vale prender: se o UNICO claim semantico foi descartado,
    # a camada semantica nao produziu nada aproveitavel — e o modo efetivo diz
    # isso. Um modelo que so alucina nao recebe credito por ter rodado.
    assert avaliacao.effective_mode == S.MODE_DETERMINISTIC
