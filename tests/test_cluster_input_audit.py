import json
import logging

from app.pipeline import _write_cluster_prompt_input_audit


def test_write_cluster_prompt_input_audit_records_prompt_sources(tmp_path, monkeypatch, caplog):
    monkeypatch.chdir(tmp_path)
    ai_payload = {
        "cluster_mode": True,
        "event_key": "event-123",
        "title": "Cluster Teste",
        "documents": [
            {
                "source": "source-a",
                "url": "https://a.example/story",
                "title": "A",
                "content": "<p>alpha beta gamma</p>",
            },
            {
                "source": "source-b",
                "url": "https://b.example/story",
                "title": "B",
                "content": "<p>delta epsilon zeta eta</p>",
            },
        ],
    }
    art_data = {"db_id": 123, "cluster_item": {"source_count": 3}}

    with caplog.at_level(logging.INFO):
        _write_cluster_prompt_input_audit(ai_payload, art_data)

    files = list((tmp_path / "debug").glob("prompt_input_cluster-teste_*.json"))
    assert len(files) == 1
    payload = json.loads(files[0].read_text(encoding="utf-8"))

    assert payload["cluster_event_key"] == "event-123"
    assert payload["source_count"] == 3
    assert payload["prompt_sources_count"] == 2
    assert payload["total_source_words_sent_to_writer"] == 7
    assert payload["estimated_source_tokens_sent_to_writer"] == 9
    assert payload["veredito"] == "MULTI_FONTE_OK"
    assert payload["sources"][0]["first_200_chars"] == "alpha beta gamma"
    assert "[CLUSTER_INPUT_AUDIT]" in caplog.text
