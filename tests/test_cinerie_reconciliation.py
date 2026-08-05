"""Resultado ambiguo: o pedido pode ter sido aplicado, e ninguem sabe.

Um timeout de LEITURA acontece depois de o pedido ter saido. O servidor pode ter
criado o artigo e a resposta ter se perdido no caminho. Duas reacoes ingenuas
estragam coisas diferentes:

- declarar falha esconde uma publicacao que existe;
- reenviar as cegas gasta cota, e cota esgotada so volta a meia-noite do fuso da
  redacao.

A saida e a chave idempotente. O reenvio IDENTICO nao e uma segunda publicacao:
e a pergunta "isto chegou?", e o proprio contrato responde sem reaplicar e sem
consumir teto. Por isso a reconciliacao precisa reusar a identidade GRAVADA, e
nunca inventar uma nova.

Sem rede externa: tudo contra `FakeCinerieServer`, em loopback.
"""

from __future__ import annotations

import pytest

from app.cinerie_store import (
    STATUS_COMPLETED,
    STATUS_NEEDS_RECONCILIATION,
    CinerieStore,
)
from tests.fake_cinerie_server import FakeCinerieServer, ScriptedResponse
from tests.test_cinerie_delivery import (
    assert_loopback_only,
    make_draft,
    make_service,
    published_body,
)


@pytest.fixture
def server():
    instance = FakeCinerieServer().start()
    assert_loopback_only(instance.base_url)
    yield instance
    instance.stop()


@pytest.fixture
def store(tmp_path):
    instance = CinerieStore(db_path=str(tmp_path / "app.db"))
    yield instance
    instance.close()


def test_timeout_de_leitura_vira_needs_reconciliation_e_nao_falha(server, store):
    """O caso que perde ou duplica materia, dependendo de como se erra.

    O servidor demora mais que o timeout do cliente. Do lado de ca nao da para
    saber se o artigo foi criado. O estado tem de dizer exatamente isso — nem
    "falhou", nem "publicou".
    """
    server.script = [ScriptedResponse(status=201, body=published_body(), delay_seconds=2.0)]
    servico = make_service(server, store, client=_cliente_impaciente(server))

    resultado = servico.publish_draft(make_draft())

    assert resultado.status == STATUS_NEEDS_RECONCILIATION, (
        f"timeout de leitura precisa virar NEEDS_RECONCILIATION, veio {resultado.status}"
    )
    # A ambiguidade nao pode ser apagada pelas tentativas seguintes: o retry
    # aqui recebe 500 na 2a e na 3a, e mesmo assim o pedido segue podendo ter
    # sido aplicado na 1a. O motivo registrado precisa dizer isso, senao quem
    # ler "SERVER_ERROR" conclui que basta reenviar.
    assert "RESULTADO_AMBIGUO" in (resultado.reason_codes or []), (
        f"o estado precisa registrar a ambiguidade; veio {resultado.reason_codes}"
    )


def test_reconciliacao_reusa_a_identidade_gravada_e_nao_inventa_outra(server, store):
    """A propriedade que impede duplicata e queima de cota.

    Se a reconciliacao recalculasse `requestId`/`idempotencyKey` a partir de um
    corpo levemente diferente, o Cinerie veria um pedido NOVO — criaria um
    segundo artigo e consumiria mais uma fatia do teto. O reenvio so e seguro
    porque e IDENTICO.
    """
    server.script = [ScriptedResponse(status=201, body=published_body(), delay_seconds=2.0)]
    servico = make_service(server, store, client=_cliente_impaciente(server))
    draft = make_draft()

    primeiro = servico.publish_draft(draft)
    assert primeiro.status == STATUS_NEEDS_RECONCILIATION
    identidade_gravada = store.get_by_request_id(primeiro.request_id)

    # O destino responde a pergunta: ja tenho isto, e nao apliquei de novo.
    server.script = [
        ScriptedResponse(
            status=200,
            body=published_body(article_id="article-8801", idempotent=True),
        )
    ]
    reconciliado = servico.reconcile_ambiguous(primeiro.request_id, draft=draft)

    enviado = server.last_request.body
    assert enviado["requestId"] == identidade_gravada.request_id, (
        "a reconciliacao mandou um requestId diferente do gravado: o Cinerie "
        "veria um pedido novo, criaria outro artigo e queimaria cota"
    )
    assert enviado["idempotencyKey"] == identidade_gravada.idempotency_key

    assert reconciliado.delivery_status == STATUS_COMPLETED
    assert reconciliado.payload_article_id == "article-8801"


def test_reconciliacao_encontra_o_artigo_que_ja_existia(server, store):
    """O desfecho que o timeout escondia: o artigo tinha sido criado."""
    server.script = [ScriptedResponse(status=201, body=published_body(article_id="article-9"), delay_seconds=2.0)]
    servico = make_service(server, store, client=_cliente_impaciente(server))
    draft = make_draft()
    pendente = servico.publish_draft(draft)

    server.script = [
        ScriptedResponse(status=200, body=published_body(article_id="article-9", idempotent=True))
    ]
    reconciliado = servico.reconcile_ambiguous(pendente.request_id, draft=draft)

    assert reconciliado.payload_article_id == "article-9"
    assert reconciliado.delivery_status == STATUS_COMPLETED
    assert reconciliado.idempotent_replay is True, (
        "o registro precisa dizer que este desfecho veio de replay idempotente, "
        "e nao de uma publicacao nova"
    )


def test_item_ja_concluido_nao_e_reenviado_pela_reconciliacao(server, store):
    """Reconciliar o que ja terminou seria gastar cota para reconfirmar."""
    server.script = [ScriptedResponse(status=201, body=published_body())]
    servico = make_service(server, store)
    draft = make_draft()
    concluido = servico.publish_draft(draft)
    assert concluido.status == STATUS_COMPLETED

    chamadas_antes = server.publication_count
    servico.reconcile_ambiguous(concluido.request_id, draft=draft)

    assert server.publication_count == chamadas_antes, (
        "a reconciliacao reenviou um item ja concluido"
    )


def _cliente_impaciente(server):
    """Cliente com timeout menor que o atraso do servidor."""
    from tests.test_cinerie_delivery import make_client

    return make_client(server, timeout=0.4)
