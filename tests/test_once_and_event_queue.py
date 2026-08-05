"""Regression tests for the bounded runner and durable event references."""

from __future__ import annotations

import json

import pytest

from app import pipeline
from app.contracts.rssprime_event_v1 import RssPrimeEventV1
from app.task_queue import ArticleQueue


def _event() -> RssPrimeEventV1:
    return RssPrimeEventV1.from_mapping(
        {
            "contract_version": "rss-prime-event-v1",
            "event_key": "evt-queue-reference",
            "revision": 2,
            "title": "Evento para a fila",
            "topic": "movies",
            "sources": [{"url": "https://example.test/source", "name": "Example"}],
        }
    )


def test_queue_persists_a_stable_event_reference_not_a_python_object(tmp_path):
    queue = ArticleQueue(db_path=str(tmp_path / "app.db"))
    queue.push({"db_id": 41, "title": "x", "_input_event": _event()})

    with queue._get_conn() as conn:
        payload = json.loads(conn.execute("SELECT payload FROM article_queue").fetchone()[0])

    assert "_input_event" not in payload
    assert payload["_input_event_ref"] == {
        "schema": "rssprime-event-reference-v1",
        "event_key": "evt-queue-reference",
        "revision": 2,
    }


def test_queue_rejects_an_unknown_event_reference_version(tmp_path):
    queue = ArticleQueue(db_path=str(tmp_path / "app.db"))
    with pytest.raises(ValueError, match="versao desconhecida"):
        queue.push(
            {
                "db_id": 41,
                "_input_event_ref": {"schema": "future-event-reference", "event_key": "evt", "revision": 1},
            }
        )


def test_once_reports_deadline_without_starting_a_daemon(monkeypatch):
    class Queue:
        def __init__(self):
            self.count = 1

        def __len__(self):
            return self.count

    class DB:
        def recover_stale_processing_claims(self, **_kwargs):
            return {"requeued": 0, "draft_recovered": 0, "failed_permanent": 0, "still_alive": 0}

        def close(self):
            pass

    queue = Queue()
    monkeypatch.setattr(pipeline, "article_queue", queue)
    monkeypatch.setattr(pipeline, "Database", DB)
    moments = iter([100.0, 101.0, 101.0])
    monkeypatch.setattr(pipeline.time, "monotonic", lambda: next(moments, 101.0))
    monkeypatch.setattr(pipeline, "run_pipeline_cycle", lambda **_kwargs: pytest.fail("não deve ingerir"))
    monkeypatch.setattr(pipeline, "ensure_worker_started", lambda: pytest.fail("não deve iniciar worker"))

    result = pipeline.run_pipeline_once(deadline_seconds=1, max_items=1)

    assert result.exit_code == pipeline.EXIT_DEADLINE_EXCEEDED
    assert result.stop_reason == "deadline"
    assert result.remaining == 1
