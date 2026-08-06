"""O runtime precisa acionar a fila Cinerie sem sobrepor dispatchers."""

from __future__ import annotations

import logging
import threading

from app import pipeline
from app.cinerie import outcomes as O
from app.cinerie_service import PublicationAttemptResult
from app.cinerie_store import CinerieStore


def test_run_pipeline_cycle_dispara_dispatcher_sem_esperar(monkeypatch):
    chamadas: list[str] = []

    class _DB:
        def recover_stale_processing_claims(self, **_kwargs):
            return {}

        def get_recent_superfeed_items(self, _topic):
            return []

        def get_consecutive_failures(self, _source_id):
            return 0

        def get_pending_fallback_articles(self, _topic):
            return []

        def close(self):
            return None

    monkeypatch.setattr(pipeline, "initialize_runtime", lambda: None)
    monkeypatch.setattr(pipeline, "reconcile_event_tasks", lambda: 0)
    monkeypatch.setattr(pipeline, "ensure_worker_started", lambda: None)
    monkeypatch.setattr(pipeline, "pipeline_has_pending_work", lambda: False)
    monkeypatch.setattr(pipeline, "Database", _DB)
    monkeypatch.setattr(pipeline, "PIPELINE_ORDER", [])
    monkeypatch.setattr(
        pipeline,
        "schedule_cinerie_dispatch",
        lambda: chamadas.append("dispatch") or True,
        raising=False,
    )

    pipeline.run_pipeline_cycle()

    assert chamadas == ["dispatch"]


def test_lock_de_processo_permite_um_unico_dispatcher(tmp_path):
    caminho = str(tmp_path / "app.db")
    largada = threading.Barrier(2)
    resultados: list[bool] = []
    trava = threading.Lock()

    def disputar(owner: str) -> None:
        store = CinerieStore(caminho)
        try:
            largada.wait()
            acquired = store.acquire_dispatch_lock(owner=owner, lease_seconds=60)
            with trava:
                resultados.append(acquired)
        finally:
            store.close()

    threads = [threading.Thread(target=disputar, args=(f"worker-{n}",)) for n in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert sorted(resultados) == [False, True]


def test_observabilidade_conta_adiamentos_por_dimensao(caplog):
    resultados = [
        PublicationAttemptResult(
            status="DEFERRED",
            outcome=O.DEFERRED,
            reason_codes=["AUTO_PUBLISH_AUTHOR_DAILY_LIMIT_REACHED"],
        ),
        PublicationAttemptResult(
            status="DEFERRED",
            outcome=O.DEFERRED,
            reason_codes=[
                "AUTO_PUBLISH_AUTHOR_DAILY_LIMIT_REACHED",
                "AUTO_PUBLISH_SECTION_DAILY_LIMIT_REACHED",
            ],
        ),
    ]

    with caplog.at_level(logging.INFO):
        pipeline.log_cinerie_quota_deferrals(resultados)

    assert "deferred_total=2" in caplog.text
    assert "AUTO_PUBLISH_AUTHOR_DAILY_LIMIT_REACHED=2" in caplog.text
    assert "AUTO_PUBLISH_SECTION_DAILY_LIMIT_REACHED=1" in caplog.text
