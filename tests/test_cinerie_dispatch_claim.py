"""Dois processos nao podem possuir a mesma entrega.

O MNScr grava a intencao de publicar antes de publicar, e depois alguem precisa
varrer o que ficou pendente. Enquanto essa varredura for uma LEITURA, duas
instancias — um reinicio que se sobrepoe ao anterior, um `--once` rodando junto
do daemon, dois containers — leem a mesma linha e enviam o mesmo pedido.

Do lado do Cinerie a dupla entrega e barrada pela chave idempotente, entao o
estrago nao e artigo duplicado: e cota. O teto diario e consumido POR CHAVE, e
duas tentativas simultaneas gastam duas fatias de um teto que, esgotado, so
volta a meia-noite do fuso da redacao.

Por isso a posse tem de ser tomada com escrita condicional, e nao com leitura.
"""

from __future__ import annotations

from app.cinerie_store import (
    STATUS_COMPLETED,
    STATUS_DEFERRED,
    STATUS_FAILED_RETRYABLE,
    STATUS_PENDING,
    CinerieStore,
)

CONTRATO = {
    "delivery_mode": "AUTO_PUBLISH",
    "contract_name": "editorial-publication-request-v1",
    "contract_version": "1.0.0",
    "schema_hash": "sha256:930243294465",
    "public_author_id": "1",
    "publication_intent": "PUBLISH",
}


def _store(tmp_path) -> CinerieStore:
    return CinerieStore(db_path=str(tmp_path / "app.db"))


def _publicacao(store: CinerieStore, n: int, *, status: str = STATUS_PENDING):
    return store.create_publication(
        request_id=f"req-{n}",
        idempotency_key=f"mnscr:cluster-{n}:rev-1",
        draft_id=f"draft-{n}",
        source_cluster_id=f"cluster-{n}",
        source_revision=1,
        delivery_status=status,
        **CONTRATO,
    )


def test_dois_workers_nao_recebem_a_mesma_entrega(tmp_path):
    """A prova central: quem chega depois nao pode levar o mesmo trabalho."""
    store = _store(tmp_path)
    for n in range(3):
        _publicacao(store, n)

    primeiro = store.claim_pending(worker_id="worker-A", limit=10, lease_seconds=300)
    segundo = store.claim_pending(worker_id="worker-B", limit=10, lease_seconds=300)

    ids_a = {r.request_id for r in primeiro}
    ids_b = {r.request_id for r in segundo}

    assert len(ids_a) == 3, "o primeiro worker deveria tomar as tres pendentes"
    assert ids_a & ids_b == set(), (
        f"os dois workers possuem o mesmo trabalho: {sorted(ids_a & ids_b)}"
    )
    store.close()


def test_lease_expirado_e_reassumido_por_outro_worker(tmp_path):
    """Worker que morreu segurando trabalho nao pode travar a fila para sempre.

    A posse tem prazo. Passado o prazo sem conclusao, o trabalho volta a ser
    reivindicavel — senao um processo morto congela a entrega ate alguem
    perceber na mao.
    """
    store = _store(tmp_path)
    _publicacao(store, 0)

    tomado = store.claim_pending(worker_id="worker-morto", limit=10, lease_seconds=0)
    assert len(tomado) == 1

    # Nada foi concluido; o lease de 0s ja venceu.
    retomado = store.claim_pending(worker_id="worker-vivo", limit=10, lease_seconds=300)
    assert [r.request_id for r in retomado] == ["req-0"], (
        "o trabalho com lease vencido precisa voltar a ser reivindicavel"
    )
    store.close()


def test_trabalho_concluido_nunca_e_reivindicado(tmp_path):
    """COMPLETED e terminal. Reenviar o que ja entrou consome cota a toa."""
    store = _store(tmp_path)
    _publicacao(store, 0, status=STATUS_COMPLETED)

    assert store.claim_pending(worker_id="w", limit=10, lease_seconds=300) == []
    store.close()


def test_deferred_so_volta_depois_de_next_eligible_at(tmp_path):
    """`DEFERRED` e agendamento, nao falha: antes da hora ele nao e trabalho.

    Reivindicar um `DEFERRED` antes de `next_eligible_at` produz um 429 novo,
    que nao adianta nada e ainda gasta uma tentativa.
    """
    store = _store(tmp_path)
    _publicacao(store, 0, status=STATUS_DEFERRED)
    store.update_result(
        request_id="req-0",
        delivery_status=STATUS_DEFERRED,
        next_eligible_at="2999-01-01T00:00:00+00:00",
    )

    assert store.claim_pending(worker_id="w", limit=10, lease_seconds=300) == [], (
        "DEFERRED com next_eligible_at no futuro nao pode ser reivindicado"
    )

    store.update_result(
        request_id="req-0",
        delivery_status=STATUS_DEFERRED,
        next_eligible_at="2000-01-01T00:00:00+00:00",
    )
    assert len(store.claim_pending(worker_id="w", limit=10, lease_seconds=300)) == 1, (
        "passada a hora, DEFERRED volta a ser trabalho"
    )
    store.close()


def test_reivindicar_deferred_nao_queima_tentativa(tmp_path):
    """O teto so cai a meia-noite; contar tentativa ate la esgota o orcamento.

    Se `DEFERRED` incrementasse `retry_count` como uma falha comum, um pedido
    perfeitamente valido seria aposentado por excesso de tentativas antes mesmo
    de a cota virar.
    """
    store = _store(tmp_path)
    _publicacao(store, 0, status=STATUS_DEFERRED)
    store.update_result(
        request_id="req-0",
        delivery_status=STATUS_DEFERRED,
        next_eligible_at="2000-01-01T00:00:00+00:00",
    )
    antes = store.get_by_request_id("req-0").retry_count

    store.claim_pending(worker_id="w", limit=10, lease_seconds=300)

    depois = store.get_by_request_id("req-0").retry_count
    assert depois == antes, (
        f"reivindicar um DEFERRED gastou tentativa ({antes} -> {depois})"
    )
    store.close()


def test_failed_retryable_e_reivindicavel(tmp_path):
    """Falha retentavel e exatamente o que a varredura existe para reprocessar."""
    store = _store(tmp_path)
    _publicacao(store, 0, status=STATUS_FAILED_RETRYABLE)

    tomado = store.claim_pending(worker_id="w", limit=10, lease_seconds=300)
    assert [r.request_id for r in tomado] == ["req-0"]
    store.close()


def test_claim_concorrente_de_verdade_nao_entrega_a_mesma_linha_duas_vezes(tmp_path):
    """A prova sob concorrencia real: threads, conexoes separadas, mesmo arquivo.

    O teste sequencial acima prova a regra; este prova que ela sobrevive a
    corrida, que e a unica forma em que o defeito aparece em producao.
    """
    import threading

    caminho = str(tmp_path / "app.db")
    semeador = CinerieStore(db_path=caminho)
    for n in range(20):
        _publicacao(semeador, n)
    semeador.close()

    largada = threading.Barrier(4)
    tomados: list[list[str]] = []
    erros: list[BaseException] = []
    trava = threading.Lock()

    def trabalhador(nome: str) -> None:
        try:
            store = CinerieStore(db_path=caminho)
            largada.wait()
            registros = store.claim_pending(worker_id=nome, limit=20, lease_seconds=300)
            with trava:
                tomados.append([r.request_id for r in registros])
            store.close()
        except BaseException as exc:  # noqa: BLE001 - o teste precisa ver a falha
            with trava:
                erros.append(exc)
            try:
                largada.abort()
            except Exception:
                pass

    threads = [threading.Thread(target=trabalhador, args=(f"w{i}",)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not erros, f"worker falhou durante a corrida: {erros[0]!r}"

    todos = [rid for lote in tomados for rid in lote]
    assert len(todos) == len(set(todos)), (
        "a mesma entrega foi tomada por mais de um worker sob concorrencia: "
        f"{sorted({r for r in todos if todos.count(r) > 1})}"
    )
    assert len(todos) == 20, f"alguma entrega ficou sem dono: {len(todos)} de 20"
