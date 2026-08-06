"""Queda em cada fronteira, e reinicio depois de cada uma.

O criterio de aceite da A1: para cada `(event_key, revision)` aceita existe,
depois de reinicio e retry, EXATAMENTE UM resultado — pendente, processando,
concluido ou falhou-recuperavel. Nenhuma revisao some, nenhum draft duplica.

As fronteiras, na ordem em que o trabalho atravessa o sistema:

1. antes do commit do evento;
2. depois do evento e antes do trabalho existir;
3. entre o trabalho e o cursor;
4. depois do cursor e antes do consumo;
5. durante o processamento;
6. depois do draft persistido e antes do ack.

A fronteira 2 ja tem cobertura em `test_event_revision_identity.py`; aqui ela
aparece de novo so como elo da cadeia. A fronteira 3 merece uma nota, e ela esta
no proprio teste.

"Queda" aqui e fechar o handle e reabrir: e o que um processo morto deixa para
tras, sem desligamento gracioso.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.event_store import EventStore
from app.ingestion import IngestionService
from app.store import Database

FIXTURE = Path(__file__).parent / "fixtures" / "rssprime" / "event_v1_valid.json"


def _payload() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _reabrir(caminho: str) -> Database:
    """O reinicio: processo novo, banco que sobreviveu."""
    db = Database(caminho)
    db.initialize()
    return db


def test_1_queda_antes_do_commit_do_evento_nao_deixa_rastro(tmp_path):
    """Nada aceito, nada perdido: o feed reentrega na proxima rodada.

    A propriedade aqui e negativa e vale a pena prende-la: um evento que NAO
    chegou a ser aceito nao pode deixar trabalho meio-criado para tras, senao a
    reconciliacao produziria materia a partir de um evento que ninguem validou.
    """
    caminho = str(tmp_path / "app.db")
    store = EventStore(db_path=caminho)
    store.close()  # queda antes de qualquer ingestao

    db = _reabrir(caminho)
    assert db.reconcile_rssprime_event_tasks() == []
    db.close()


def test_2_queda_entre_evento_e_trabalho_e_recuperada(tmp_path):
    """Cursor ja andou, trabalho nunca existiu. O reinicio precisa cria-lo."""
    caminho = str(tmp_path / "app.db")
    store = EventStore(db_path=caminho)
    assert IngestionService(store).ingest(_payload(), source_id="rssprime_movies").accepted
    store.close()

    db = _reabrir(caminho)
    criados = db.reconcile_rssprime_event_tasks()
    assert len(criados) == 1
    db.close()


def test_3_o_cursor_nunca_anda_sem_o_evento_gravado(tmp_path):
    """A fronteira "entre trabalho e cursor" nao existe nesta arquitetura.

    Vale registrar por que, senao alguem procura o teste e conclui que falta
    cobertura: evento e cursor sao gravados na MESMA transacao
    (`persist_event_and_advance_cursor`), e o trabalho operacional e derivado
    DEPOIS, pela reconciliacao. Entao a ordem de risco e a inversa da que o
    enunciado supoe — nao ha janela em que o cursor ande a frente do evento.

    O que se prova aqui e a invariante que substitui aquela fronteira: um evento
    recusado nao move o cursor.
    """
    caminho = str(tmp_path / "app.db")
    store = EventStore(db_path=caminho)

    invalido = _payload()
    invalido["revision"] = -1  # revisao invalida: o contrato recusa

    resultado = IngestionService(store).ingest(invalido, source_id="rssprime_movies")
    assert not resultado.accepted
    assert not resultado.cursor_advanced, (
        "um evento recusado moveu o cursor: o proximo ciclo pularia material "
        "que nunca foi processado"
    )
    store.close()

    db = _reabrir(caminho)
    assert db.reconcile_rssprime_event_tasks() == [], (
        "evento recusado nao pode virar trabalho"
    )
    db.close()


def test_4_queda_depois_do_cursor_e_antes_do_consumo_nao_duplica(tmp_path):
    """O trabalho ja existe; o reinicio nao pode cria-lo de novo.

    Este e o lado espelhado da fronteira 2: la o risco e sumir, aqui e duplicar.
    Reconciliar e uma operacao que roda a CADA ciclo, entao ela precisa ser
    idempotente — senao cada rodada acrescenta uma copia da mesma materia.
    """
    caminho = str(tmp_path / "app.db")
    store = EventStore(db_path=caminho)
    payload = _payload()
    assert IngestionService(store).ingest(payload, source_id="rssprime_movies").accepted
    store.close()

    db = _reabrir(caminho)
    assert len(db.reconcile_rssprime_event_tasks()) == 1
    db.close()

    # Reinicios sucessivos, cada um chamando a reconciliacao de novo.
    for _ in range(3):
        db = _reabrir(caminho)
        assert db.reconcile_rssprime_event_tasks() == []
        db.close()

    db = _reabrir(caminho)
    linhas = db.conn.execute(
        "SELECT COUNT(*) AS total FROM seen_articles WHERE event_key = ?",
        (payload["event_key"],),
    ).fetchone()["total"]
    db.close()
    assert linhas == 1, f"a mesma revisao virou {linhas} linhas"


def test_5_queda_durante_o_processamento_devolve_o_trabalho(tmp_path):
    """Worker morto segurando um item nao pode congelar a materia para sempre.

    `PROCESSING` com posse de um processo que nao existe mais e o rastro exato de
    uma queda no meio do trabalho. Sem recuperacao, o item fica preso ate alguem
    olhar o banco na mao.
    """
    caminho = str(tmp_path / "app.db")
    db = _reabrir(caminho)
    db.conn.execute(
        """
        INSERT INTO seen_articles (source_id, external_id, url, status, claimed_by, claimed_at, fail_count)
        VALUES ('rssprime', 'evt#rev1', 'https://exemplo.test/a', 'PROCESSING',
                '999999:worker:morto', '2000-01-01T00:00:00+00:00', 0)
        """
    )
    db.conn.commit()
    db.close()

    db = _reabrir(caminho)
    resultado = db.recover_stale_processing_claims(stale_seconds=60, max_recoveries=10)
    linha = db.conn.execute(
        "SELECT status, claimed_by FROM seen_articles WHERE external_id = 'evt#rev1'"
    ).fetchone()
    db.close()

    assert sum(resultado.values()) >= 1, f"nada foi recuperado: {resultado}"
    assert linha["status"] != "PROCESSING", (
        "o item continua PROCESSING com dono morto: preso para sempre"
    )
    assert linha["claimed_by"] is None, "a posse do processo morto nao foi liberada"


def test_6_draft_persistido_antes_do_ack_nao_e_refeito(tmp_path):
    """Draft ja gerado e trabalho CONCLUIDO, mesmo sem o ack ter acontecido.

    Se a recuperacao tratasse `DRAFT_GENERATED` como pendente, o reinicio geraria
    a materia de novo — e o custo aqui nao e so processamento: e outra chamada de
    IA e outra fatia do teto de publicacao.
    """
    caminho = str(tmp_path / "app.db")
    db = _reabrir(caminho)
    db.conn.execute(
        """
        INSERT INTO seen_articles (source_id, external_id, url, status, draft_status, draft_id, fail_count)
        VALUES ('rssprime', 'evt#rev9', 'https://exemplo.test/b', 'DRAFT_GENERATED',
                'DRAFT_GENERATED', 'draft-abc', 0)
        """
    )
    db.conn.commit()
    db.close()

    db = _reabrir(caminho)
    db.recover_stale_processing_claims(stale_seconds=0, max_recoveries=10)
    db.reconcile_rssprime_event_tasks()
    linha = db.conn.execute(
        "SELECT status, draft_id FROM seen_articles WHERE external_id = 'evt#rev9'"
    ).fetchone()
    total = db.conn.execute(
        "SELECT COUNT(*) AS t FROM seen_articles WHERE external_id = 'evt#rev9'"
    ).fetchone()["t"]
    db.close()

    assert linha["status"] == "DRAFT_GENERATED", "o trabalho concluido foi reaberto"
    assert linha["draft_id"] == "draft-abc"
    assert total == 1, "o reinicio duplicou a linha de um trabalho ja concluido"
