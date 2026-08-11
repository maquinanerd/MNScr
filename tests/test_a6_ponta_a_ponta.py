"""A6 — prova ponta a ponta contra o servidor falso e SQLite descartavel.

Oito propriedades. Cada uma corresponde a uma forma de o sistema estragar
noticia em producao, e nenhuma delas produz erro visivel quando quebra:

1. revisao 1 cria um pedido aceito;
2. reentrega identica nao duplica e nao consome teto;
3. revisao 2 vira atualizacao distinta;
4. timeout depois do commit e reconciliado, nao reenviado;
5. dois workers nao duplicam;
6. reinicio recupera inbox e outbox;
7. pedido bloqueado nao gera conteudo;
8. `429` respeita `Retry-After` e nao queima tentativas.

Nada aqui toca rede externa: o servidor e local, em loopback, e o banco morre
com o teste.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.cinerie import outcomes as O
from app.cinerie_store import (
    STATUS_COMPLETED,
    STATUS_DEFERRED,
    STATUS_NEEDS_RECONCILIATION,
    CinerieStore,
)
from app.event_store import EventStore
from app.ingestion import IngestionService
from app.store import Database
from tests.fake_cinerie_server import FakeCinerieServer, ScriptedResponse
from tests.test_cinerie_delivery import (
    assert_loopback_only,
    make_client,
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
    instance = CinerieStore(db_path=str(tmp_path / "cinerie.db"))
    yield instance
    instance.close()


# ---------------------------------------------------------------------------
# 1 e 3 — a revisao e a unidade de trabalho
# ---------------------------------------------------------------------------


def test_1_revisao_1_cria_um_pedido_aceito(server, store):
    server.script = [ScriptedResponse(status=201, body=published_body())]
    resultado = make_service(server, store).publish_draft(make_draft(revision=1))

    assert resultado.status == STATUS_COMPLETED
    assert resultado.outcome == O.PUBLISHED
    assert server.publication_count == 1


def test_3_revisao_2_vira_um_trabalho_distinto(server, store):
    """Revisao nova nao e repeticao: ela tem identidade propria e sai de novo."""
    servico = make_service(server, store)

    server.script = [ScriptedResponse(status=201, body=published_body())]
    r1 = servico.publish_draft(make_draft(revision=1))

    server.script = [ScriptedResponse(status=201, body=published_body(article_id="article-2"))]
    r2 = servico.publish_draft(make_draft(revision=2))

    assert r1.idempotency_key != r2.idempotency_key, (
        "revisoes diferentes precisam de chaves idempotentes diferentes, senao a "
        "revisao 2 seria engolida como replay da revisao 1"
    )
    assert server.publication_count == 2
    assert {r.source_revision for r in store.list_for_cluster("cluster-4711")} == {1, 2}


# ---------------------------------------------------------------------------
# 2 — reentrega identica
# ---------------------------------------------------------------------------


def test_2_reentrega_identica_nao_duplica_nem_consome_teto(server, store):
    """O mesmo trabalho, mandado duas vezes, nao pode virar duas publicacoes.

    A prova mais forte disponivel: a segunda chamada nao gera requisicao NENHUMA.
    O desfecho ja esta gravado, entao nao ha o que perguntar — e o teto diario,
    que e consumido por chave, fica intocado.
    """
    servico = make_service(server, store)
    draft = make_draft(revision=1)

    server.script = [ScriptedResponse(status=201, body=published_body())]
    primeiro = servico.publish_draft(draft)
    chamadas = server.publication_count

    segundo = servico.publish_draft(draft)

    assert server.publication_count == chamadas, (
        "a reentrega identica saiu na rede de novo e consumiu teto"
    )
    assert segundo.status == primeiro.status
    assert segundo.article_id == primeiro.article_id
    assert len(store.list_for_cluster("cluster-4711")) == 1


# ---------------------------------------------------------------------------
# 4 — resultado ambiguo
# ---------------------------------------------------------------------------


def test_4_timeout_depois_do_envio_e_reconciliado_e_nao_reenviado(server, store):
    """Timeout de leitura acontece DEPOIS de os bytes sairem.

    O artigo pode existir. Reenviar as cegas duplicaria e queimaria cota;
    declarar falha esconderia uma publicacao real. A saida e perguntar com a
    identidade GRAVADA, e a resposta idempotente resolve.
    """
    servico = make_service(server, store, client=make_client(server, timeout=0.4))
    draft = make_draft(revision=1)

    server.script = [ScriptedResponse(status=201, body=published_body(), delay_seconds=2.0)]
    ambiguo = servico.publish_draft(draft)

    assert ambiguo.status == STATUS_NEEDS_RECONCILIATION
    assert "RESULTADO_AMBIGUO" in (ambiguo.reason_codes or [])

    server.script = [
        ScriptedResponse(status=200, body=published_body(article_id="article-8801", idempotent=True))
    ]
    reconciliado = servico.reconcile_ambiguous(ambiguo.request_id, draft=draft)

    assert reconciliado.delivery_status == STATUS_COMPLETED
    assert reconciliado.payload_article_id == "article-8801"
    # A identidade nao pode ter mudado: e ela que faz do reenvio uma pergunta.
    enviado = server.last_request.body
    assert enviado["requestId"] == ambiguo.request_id
    assert enviado["idempotencyKey"] == ambiguo.idempotency_key


# ---------------------------------------------------------------------------
# 5 — concorrencia
# ---------------------------------------------------------------------------


def test_5_dois_workers_nao_pegam_a_mesma_entrega(server, store):
    """Posse e exclusiva. O prejuizo da dupla entrega e cota, nao duplicata."""
    servico = make_service(server, store, client=make_client(server, timeout=0.4))
    server.script = [ScriptedResponse(status=201, body=published_body(), delay_seconds=2.0)]
    pendente = servico.publish_draft(make_draft(revision=1))
    assert pendente.status == STATUS_NEEDS_RECONCILIATION

    a = store.claim_pending(worker_id="A", limit=10, lease_seconds=300)
    b = store.claim_pending(worker_id="B", limit=10, lease_seconds=300)

    assert [r.request_id for r in a] == [pendente.request_id]
    assert b == [], "o segundo worker levou trabalho que ja tinha dono"


# ---------------------------------------------------------------------------
# 6 — reinicio
# ---------------------------------------------------------------------------


def test_6_reinicio_recupera_inbox_e_outbox(server, tmp_path):
    """As duas metades da durabilidade, no mesmo teste.

    INBOX: evento aceito e cursor movido, mas o trabalho operacional nunca criado
    (queda entre as duas coisas). O reinicio precisa produzir o trabalho.

    OUTBOX: entrega em estado nao-terminal atravessa o fechamento do processo e
    continua reivindicavel depois.
    """
    caminho = str(tmp_path / "app.db")

    # --- INBOX -------------------------------------------------------------
    event_store = EventStore(db_path=caminho)
    servico_ingestao = IngestionService(event_store)
    payload = json.loads(
        (Path(__file__).parent / "fixtures" / "rssprime" / "event_v1_valid.json").read_text(
            encoding="utf-8"
        )
    )
    assert servico_ingestao.ingest(payload, source_id="rssprime_movies").accepted
    event_store.close()  # queda

    db = Database(caminho)
    db.initialize()
    criados = db.reconcile_rssprime_event_tasks()
    assert [t["event_revision"] for t in criados] == [int(payload["revision"])], (
        "o reinicio precisa criar o trabalho que a queda deixou faltando"
    )
    assert db.reconcile_rssprime_event_tasks() == [], "reconciliar de novo duplicou"
    db.close()

    # --- OUTBOX ------------------------------------------------------------
    caminho_cinerie = str(tmp_path / "cinerie.db")
    store = CinerieStore(db_path=caminho_cinerie)
    servico = make_service(server, store, client=make_client(server, timeout=0.4))
    server.script = [ScriptedResponse(status=201, body=published_body(), delay_seconds=2.0)]
    pendente = servico.publish_draft(make_draft(revision=1))
    assert pendente.status == STATUS_NEEDS_RECONCILIATION
    store.close()  # queda com entrega em aberto

    reaberto = CinerieStore(db_path=caminho_cinerie)
    retomado = reaberto.claim_pending(worker_id="apos-reinicio", limit=10, lease_seconds=300)
    assert [r.request_id for r in retomado] == [pendente.request_id], (
        "a entrega em aberto nao sobreviveu ao reinicio"
    )
    assert reaberto.get_by_request_id(pendente.request_id).delivery_status == (
        STATUS_NEEDS_RECONCILIATION
    ), "o motivo pelo qual a entrega precisa de atencao foi perdido no reinicio"
    reaberto.close()


# ---------------------------------------------------------------------------
# 7 — bloqueio
# ---------------------------------------------------------------------------


def test_7_pedido_bloqueado_nao_gera_conteudo(server, store):
    """`422 BLOCKED` e desfecho, nao falha: nada e criado e nada e reenviado."""
    server.script = [
        ScriptedResponse(
            status=422,
            body=published_body(
                outcome=O.BLOCKED,
                articleId=None,
                reasons=[{"code": "SCHEMA_TYPE_NOT_ALLOWED", "detail": "HowTo bloqueia sempre"}],
            ),
        )
    ]
    resultado = make_service(server, store).publish_draft(make_draft(revision=1))

    assert resultado.article_id is None, "bloqueado nao pode ter criado artigo"
    assert resultado.outcome == O.BLOCKED

    # O estado local e FAILED_PERMANENT, nao STATUS_BLOCKED. As duas coisas sao
    # terminais e nao-retentaveis, entao o COMPORTAMENTO esta certo; o que se
    # perde e a distincao no relatorio entre "o Cinerie recusou por politica" e
    # "a entrega falhou de vez". Fica registrado, nao corrigido aqui: mudar o
    # mapeamento e decisao de quem le esses relatorios.
    assert resultado.status not in (STATUS_COMPLETED, STATUS_DEFERRED, STATUS_NEEDS_RECONCILIATION)

    # E nao volta para a fila: insistir num pedido recusado por politica so
    # gastaria tentativa e teto.
    assert store.claim_pending(worker_id="w", limit=10, lease_seconds=300) == []


# ---------------------------------------------------------------------------
# 8 — teto diario
# ---------------------------------------------------------------------------


def test_8_deferred_respeita_retry_after_e_nao_queima_tentativa(server, store):
    """`429 DEFERRED` e agendamento. O teto so cai a meia-noite do fuso.

    Duas coisas nao podem acontecer: reenviar antes da hora (produz outro 429 e
    nada mais) e contar isso como tentativa (aposentaria por excesso de
    tentativas um pedido que esta correto).
    """
    # O `nextEligibleAt` precisa estar no FUTURO para o teste medir o que ele diz
    # medir: o helper canonico traz uma data fixa que ja passou, e um teto que ja
    # virou e teto liberado.
    server.script = [
        ScriptedResponse(
            status=429,
            body=published_body(
                outcome=O.DEFERRED,
                articleId=None,
                reasons=[{"code": "AUTO_PUBLISH_GLOBAL_DAILY_LIMIT_REACHED", "detail": "global"}],
                nextEligibleAt="2999-01-01T03:00:00.000Z",
                retryable=True,
            ),
            headers={"Retry-After": "3600"},
        )
    ]
    resultado = make_service(server, store).publish_draft(make_draft(revision=1))

    assert resultado.status == STATUS_DEFERRED
    assert resultado.outcome == O.DEFERRED
    assert resultado.article_id is None, "DEFERRED nao cria linha do outro lado"
    assert resultado.next_eligible_at, "sem next_eligible_at nao ha quando reenviar"

    registro = store.get_by_request_id(resultado.request_id)
    tentativas_antes = registro.retry_count

    # Antes da hora, nao e trabalho.
    assert store.claim_pending(worker_id="w", limit=10, lease_seconds=300) == [], (
        "um DEFERRED com next_eligible_at no futuro voltou para a fila"
    )
    assert store.get_by_request_id(resultado.request_id).retry_count == tentativas_antes, (
        "reivindicar/adiar consumiu tentativa de um pedido que esta correto"
    )
