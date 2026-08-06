"""A revisao nova de um evento e trabalho novo, e nada pode engoli-la em silencio.

O RSS Prime versiona o que emite: `event_key` diz QUAL assunto, `revision` diz
QUAL versao daquele assunto. Uma revisao nova existe justamente porque a materia
mudou — fonte nova, correcao, desdobramento. Perde-la nao produz erro nenhum: o
item simplesmente nao aparece, e o pipeline segue como se nada tivesse chegado.

`filter_new_articles` aplica quatro dedups em sequencia sobre o mesmo item. O
primeiro entende versao; os outros tres nao. Basta um deles dizer "ja vi" para a
revisao ser descartada depois de o dedup versionado ter dito que ela e nova.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

from app.cinerie_service import CinerieService
from app.cinerie_store import STATUS_BLOCKED, CinerieStore
from app.contracts.rssprime_event_v1 import RssPrimeEventV1
from app.event_store import EventStore
from app.feeds import cluster_signature
from app.ingestion import IngestionService
from app.store import Database

EVENT_KEY = "evt-revisao-nao-pode-sumir"
URLS = ["https://exemplo.test/a", "https://exemplo.test/b"]

# Identica entre revisoes de proposito: `cluster_signature` e hash do conjunto de
# URLs, entao uma revisao que corrige o texto sem mudar as fontes reaproveita a
# assinatura da anterior. E o caso comum, nao o raro.
SIGNATURE = cluster_signature(URLS)


def _revision(n: int) -> dict:
    """Uma revisao do MESMO evento: mesmas fontes, mesmo assunto, versao nova."""
    return {
        "url": URLS[0],
        "urls": list(URLS),
        "title": f"Materia do evento (revisao {n})",
        "event_key": EVENT_KEY,
        "event_revision": n,
        "is_cluster": True,
        "multi_source": True,
        "source_count": len(URLS),
        "cluster_size": len(URLS),
        "cluster_signature": SIGNATURE,
        "origin": "superfeed",
        "topic": "movies",
    }


def _db(tmp_path) -> Database:
    db = Database(str(tmp_path / "app.db"))
    db.initialize()
    return db


def _accept(db: Database, item: dict) -> list:
    # `external_id` estavel entre revisoes: e o mesmo assunto, e e assim que o
    # dedup padrao (source_id + external_id) enxerga o item.
    item = dict(item)
    item["external_id"] = EVENT_KEY
    return db.filter_new_articles("rssprime", [item])


def test_revisao_2_sobrevive_ao_draft_da_revisao_1(tmp_path):
    """Revisao 1 vira draft; revisao 2 chega depois e nao pode ser engolida.

    Este e o caso que perde noticia em producao: a revisao 1 roda ate o fim
    (status DRAFT_GENERATED), a revisao 2 chega com a mesma assinatura de
    cluster e o dedup estrutural a trata como repeticao.
    """
    db = _db(tmp_path)

    primeira = _accept(db, _revision(1))
    assert len(primeira) == 1, "a revisao 1 deveria entrar"

    # A revisao 1 percorre o pipeline e gera draft.
    db.update_article_status(primeira[0]["db_id"], "DRAFT_GENERATED")

    segunda = _accept(db, _revision(2))

    assert len(segunda) == 1, (
        "a revisao 2 e trabalho novo e foi descartada: o dedup versionado "
        "liberou, e o dedup por cluster_signature a bloqueou logo depois "
        "porque a revisao 1 ja estava em DRAFT_GENERATED"
    )


def test_revisao_2_sobrevive_ao_dedup_por_source_id_e_external_id(tmp_path):
    """Sem assinatura de cluster, quem engole a revisao 2 e o dedup padrao.

    Um evento de fonte unica nao tem `cluster_signature`. O dedup versionado nem
    chega a rodar, porque ele exige `is_cluster`. Sobra o dedup padrao, que so
    olha source_id + external_id — ambos estaveis entre revisoes.
    """
    db = _db(tmp_path)

    unica = dict(_revision(1))
    unica["is_cluster"] = False
    unica["multi_source"] = False
    unica["source_count"] = 1
    unica["cluster_signature"] = None
    unica["urls"] = [URLS[0]]

    primeira = _accept(db, unica)
    assert len(primeira) == 1, "a revisao 1 deveria entrar"

    segunda_item = dict(unica)
    segunda_item["event_revision"] = 2
    segunda_item["title"] = "Materia do evento (revisao 2)"
    segunda = _accept(db, segunda_item)

    assert len(segunda) == 1, (
        "a revisao 2 de um evento de fonte unica foi descartada pelo dedup "
        "padrao: o dedup versionado exige is_cluster e nem roda"
    )


def _fixture() -> dict:
    caminho = Path(__file__).parent / "fixtures" / "rssprime" / "event_v1_valid.json"
    return json.loads(caminho.read_text(encoding="utf-8"))


def test_duas_revisoes_aceitas_viram_dois_trabalhos_apos_reinicio(tmp_path):
    """Fronteira de queda: evento e cursor gravados, trabalho ainda inexistente.

    Fechar o EventStore sem nunca ter criado a tarefa e exatamente esse estado;
    abrir o Database depois e o reinicio. A reconciliacao precisa entregar UM
    trabalho por revisao aceita — nem zero (a revisao sumiu) nem dois (a revisao
    duplicou), e reconciliar de novo nao pode criar nada.

    Este teste passa TAMBEM sem a correcao deste commit, e isso e proposital
    registra-lo: a reconciliacao ja era segura porque ela monta o `external_id`
    como `event:<event_key>:<revision>`, que carrega a revisao. Quem perdia
    revisao era o caminho direto do feed, onde o `external_id` sai do hash da
    URL e se repete entre revisoes — o caso dos dois testes acima. Aqui a prova
    e de nao-regressao: o caminho que ja funciona precisa continuar funcionando
    depois de a precedencia do dedup versionado mudar.
    """
    db_path = str(tmp_path / "app.db")
    store = EventStore(db_path=db_path)
    servico = IngestionService(store)

    r1 = _fixture()
    assert servico.ingest(r1, source_id="rssprime_movies").accepted

    # A revisao 2 e recalculada como o produtor faria: mudar o texto sem refazer
    # o `payload_hash` e PAYLOAD_HASH_MISMATCH, e o contrato esta certo em
    # recusar. O teste e sobre perder revisao, nao sobre burlar a validacao.
    r2 = _fixture()
    r2["revision"] = int(r1["revision"]) + 1
    r2["title"] = f"{r1.get('title', 'Materia')} — corrigida"
    r2["payload_hash"] = RssPrimeEventV1.from_mapping(r2).calculate_payload_hash()
    assert servico.ingest(r2, source_id="rssprime_movies").accepted, (
        "a revisao 2 do mesmo evento precisa ser aceita na ingestao"
    )

    store.close()  # queda: evento e cursor gravados, trabalho nenhum criado

    db = Database(db_path)
    db.initialize()
    criados = db.reconcile_rssprime_event_tasks()

    revisoes = sorted(t["event_revision"] for t in criados)
    assert revisoes == [int(r1["revision"]), int(r2["revision"])], (
        f"cada revisao aceita deve virar um trabalho; vieram {revisoes}"
    )

    # Reconciliar de novo nao pode duplicar: o reinicio pode acontecer varias
    # vezes, e trabalho ja criado nao e trabalho faltante.
    assert db.reconcile_rssprime_event_tasks() == []

    for revisao in revisoes:
        linha = db.get_article_by_event_key(r1["event_key"], revisao)
        assert linha is not None, f"revisao {revisao} nao tem linha propria"
    db.close()


def _legacy_database(path: Path) -> None:
    """Banco anterior a ``event_revision``, com uma revisao historica inequívoca."""
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE seen_articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id TEXT NOT NULL,
            external_id TEXT NOT NULL,
            url TEXT,
            status TEXT DEFAULT 'DRAFT_GENERATED',
            event_key TEXT,
            UNIQUE(source_id, external_id)
        );
        CREATE TABLE rssprime_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_key TEXT NOT NULL,
            revision INTEGER NOT NULL,
            validation_status TEXT NOT NULL,
            normalized_payload_json TEXT,
            UNIQUE(event_key, revision)
        );
        INSERT INTO seen_articles
            (source_id, external_id, url, status, event_key)
        VALUES
            ('rssprime_movies', 'legacy-1', 'https://exemplo.test/legacy',
             'DRAFT_GENERATED', 'evt-legado-cinerie');
        INSERT INTO rssprime_events
            (event_key, revision, validation_status, normalized_payload_json)
        VALUES
            ('evt-legado-cinerie', 1, 'VALIDATED', '{}');
        """
    )
    conn.commit()
    conn.close()


def test_migracao_marca_legado_e_backfill_so_quando_revisao_e_inequivoca(tmp_path):
    db_path = tmp_path / "legacy.db"
    _legacy_database(db_path)

    db = Database(str(db_path))
    row = db.conn.execute(
        """
        SELECT event_revision, legacy_cinerie_identity_unmapped
        FROM seen_articles WHERE event_key = 'evt-legado-cinerie'
        """
    ).fetchone()

    assert row["event_revision"] == 1
    assert row["legacy_cinerie_identity_unmapped"] == 1
    db.close()


class _MustNotBeCalled:
    def __getattr__(self, name):
        raise AssertionError(f"Cinerie tentou usar {name} apesar da guarda legada")


def test_reemissao_rev2_legada_sem_mapeamento_nao_publica_segunda_materia(tmp_path):
    db_path = tmp_path / "legacy.db"
    _legacy_database(db_path)

    # O comando de despacho pode abrir CinerieStore sem antes abrir Database.
    # A guarda precisa falhar fechada mesmo nessa ordem de inicializacao.
    store = CinerieStore(str(db_path))
    service = CinerieService(
        store=store,
        client=_MustNotBeCalled(),
        preflight=_MustNotBeCalled(),
        public_author_id="12",
        attribution_mode="newsroom",
    )
    draft_rev2 = SimpleNamespace(
        draft_id="draft-legado-rev2",
        event_key="evt-legado-cinerie",
        revision=2,
    )

    result = service.publish_draft(draft_rev2)

    assert result.status == STATUS_BLOCKED
    assert result.error_code == "LEGACY_CINERIE_IDENTITY_UNMAPPED"
    assert store.conn.execute("SELECT COUNT(*) FROM cinerie_publications").fetchone()[0] == 0
    store.close()


def test_startup_refaz_marcacao_se_schema_foi_persistido_antes_do_backfill(tmp_path):
    db_path = tmp_path / "legacy-interrompido.db"
    _legacy_database(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute("ALTER TABLE seen_articles ADD COLUMN event_revision INTEGER")
    conn.execute(
        "ALTER TABLE seen_articles ADD COLUMN "
        "legacy_cinerie_identity_unmapped INTEGER NOT NULL DEFAULT 0"
    )
    conn.commit()
    conn.close()

    db = Database(str(db_path))
    row = db.conn.execute(
        "SELECT event_revision, legacy_cinerie_identity_unmapped "
        "FROM seen_articles WHERE event_key = ?",
        ("evt-legado-cinerie",),
    ).fetchone()

    assert row["event_revision"] == 1
    assert row["legacy_cinerie_identity_unmapped"] == 1
    db.close()
