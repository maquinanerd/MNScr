"""End-to-end pipeline test with everything external mocked.

Feed, extraction and the model are all local fakes. The test asserts that one
article produces exactly one draft artifact and touches nothing else.
"""

from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest

from app import pipeline
from app.store import Database
from app.submitters import LocalDraftSubmitter

FEED_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Fake Superfeed</title>
    <item>
      <title>Estudio confirma nova temporada da serie</title>
      <link>https://deadline.example/nova-temporada</link>
      <guid>evt-nova-temporada</guid>
      <pubDate>Mon, 05 May 2026 12:00:00 +0000</pubDate>
    </item>
  </channel>
</rss>
"""

ARTICLE_HTML = """
<html><head>
  <meta property="og:title" content="Estudio confirma nova temporada da serie">
  <meta name="description" content="O estudio confirmou a nova temporada.">
  <meta property="og:image" content="https://cdn.example/poster.jpg">
</head><body>
  <article>
    <p>O estudio confirmou nesta segunda-feira a producao da nova temporada da serie,
       com estreia prevista para 2027 e retorno do elenco principal.</p>
    <h2>O que muda na nova fase</h2>
    <p>A producao sera retomada em Vancouver com o mesmo showrunner, segundo o comunicado
       oficial divulgado pela distribuidora.</p>
    <p>A distribuidora nao informou o numero de episodios nem o orcamento da temporada.</p>
  </article>
</body></html>
"""

AI_JSON = {
    "titulo_final": "Estudio confirma nova temporada da serie para 2027",
    "conteudo_final": (
        "<p>O estudio confirmou a producao da nova temporada, com estreia prevista para 2027.</p>"
        "<h2>O que muda na nova fase</h2>"
        "<p>A producao sera retomada em Vancouver com o mesmo showrunner.</p>"
    ),
    "meta_description": "O estudio confirmou a nova temporada da serie com estreia prevista para 2027 e retorno do elenco.",
    "subtitle": "A producao sera retomada em Vancouver com o mesmo showrunner e o elenco principal confirmado para 2027.",
    "focus_keyphrase": "nova temporada",
    "related_keyphrases": ["estreia 2027", "retorno do elenco"],
    "slug": "nova-temporada-2027",
    "categorias": [{"nome": "Séries"}],
    "tags_sugeridas": ["nova temporada", "estreia"],
    "yoast_meta": {},
    "_input_tokens": 1200,
    "_output_tokens": 800,
    "_total_tokens": 2000,
}


class _FakeAIClient:
    def get_last_used_model(self):
        return "gemini-3.1-flash-lite"

    def get_last_used_key(self):
        return "****1234"

    def generate_text(self, *args, **kwargs):
        raise AssertionError("Nenhuma chamada extra de IA deveria ocorrer neste teste")


class _FakeProcessor:
    def __init__(self):
        self._ai_client = _FakeAIClient()

    def rewrite_batch(self, batch_data):
        return [(dict(AI_JSON), None) for _ in batch_data]


class _Recorder:
    """Collects any attempt to reach the outside world."""

    def __init__(self):
        self.network_calls: list[str] = []


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    recorder = _Recorder()

    def _blocked(*args, **kwargs):
        recorder.network_calls.append("socket")
        raise AssertionError("Pipeline tentou acessar a rede")

    monkeypatch.setattr(socket, "socket", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)

    # local state
    db_path = tmp_path / "app.db"
    drafts_dir = tmp_path / "drafts"
    monkeypatch.chdir(tmp_path)

    monkeypatch.setattr(pipeline, "article_queue", pipeline.ArticleQueue(db_path=str(db_path)))
    monkeypatch.setattr(pipeline, "Database", lambda *a, **k: Database(str(db_path)))
    monkeypatch.setattr(pipeline, "get_ai_processor", lambda: _FakeProcessor())
    monkeypatch.setattr(pipeline, "ensure_worker_started", lambda: None)
    monkeypatch.setattr(pipeline, "build_submitter", lambda *a, **k: LocalDraftSubmitter(output_dir=drafts_dir))
    monkeypatch.setattr(pipeline.time, "sleep", lambda *_: None)
    monkeypatch.setattr(pipeline, "ls_save_article", lambda **kwargs: None)
    monkeypatch.setattr(pipeline, "ls_get_link_map", lambda: {"posts": []})

    # feed + extraction are local fakes
    monkeypatch.setattr(
        pipeline.FeedReader, "_fetch_content", lambda self, url: FEED_XML.encode("utf-8")
    )
    monkeypatch.setattr(pipeline.ContentExtractor, "_fetch_html", lambda self, url: ARTICLE_HTML)

    monkeypatch.setattr(
        pipeline, "PIPELINE_ORDER", ["screenrant_movie_news"], raising=False
    )

    return {"db_path": db_path, "drafts_dir": drafts_dir, "recorder": recorder}


def _run_one_article(sandbox):
    db = Database(str(sandbox["db_path"]))
    db.initialize()
    items = pipeline.FeedReader(user_agent="test").read_feeds(
        pipeline.RSS_FEEDS["screenrant_movie_news"], "screenrant_movie_news"
    )
    new_articles = db.filter_new_articles("screenrant_movie_news", items, limit=1)
    assert len(new_articles) == 1
    article = new_articles[0]
    article["db_id"] = article["db_id"]
    article["source_id"] = "screenrant_movie_news"
    db.close()

    pipeline.article_queue.push_many([article])
    claimed = pipeline.article_queue.pop_claimed("test-worker")
    assert claimed is not None

    pipeline.process_batch([claimed], link_map={"posts": []})
    return claimed


class TestPipelineProducesDrafts:
    def test_exactly_one_draft_artifact_is_written(self, sandbox):
        _run_one_article(sandbox)
        artifacts = list(Path(sandbox["drafts_dir"]).glob("*.json"))
        assert len(artifacts) == 1

    def test_draft_state_is_draft_generated(self, sandbox):
        claimed = _run_one_article(sandbox)
        db = Database(str(sandbox["db_path"]))
        row = db._get_cursor().execute(
            "SELECT status, draft_status, draft_id, draft_artifact_path, submission_destination,"
            " submission_status, draft_output_hash FROM seen_articles WHERE id = ?",
            (claimed["db_id"],),
        ).fetchone()
        db.close()
        assert row["status"] == "DRAFT_GENERATED"
        assert row["draft_status"] == "DRAFT_GENERATED"
        assert row["draft_id"]
        assert row["draft_output_hash"]
        assert row["submission_destination"] == "local"
        assert row["submission_status"] == "WRITTEN"

    def test_artifact_contains_sources_and_media_candidates(self, sandbox):
        _run_one_article(sandbox)
        artifact = next(Path(sandbox["drafts_dir"]).glob("*.json"))
        payload = json.loads(artifact.read_text(encoding="utf-8"))

        assert payload["status"] == "DRAFT_GENERATED"
        assert payload["sources"], "o draft precisa registrar as fontes"
        assert payload["sources"][0]["url"].startswith("https://deadline.example")
        assert payload["evidence"], "o draft precisa registrar evidencias"
        assert payload["provenance"]["input_hash"]
        assert payload["provenance"]["output_hash"]
        assert payload["provenance"]["output_contract_version"] == "mnscr-editorial-draft-v0"

    def test_media_candidates_are_unverified_and_not_downloaded(self, sandbox):
        _run_one_article(sandbox)
        payload = json.loads(next(Path(sandbox["drafts_dir"]).glob("*.json")).read_text(encoding="utf-8"))
        for candidate in payload["media_candidates"]:
            assert candidate["status"] == "unverified"
            assert candidate["license"] is None
            assert candidate["credit"] is None

    def test_categories_and_tags_are_plain_suggestions(self, sandbox):
        _run_one_article(sandbox)
        payload = json.loads(next(Path(sandbox["drafts_dir"]).glob("*.json")).read_text(encoding="utf-8"))
        assert all(isinstance(name, str) for name in payload["content"]["category_suggestions"])
        assert all(isinstance(name, str) for name in payload["content"]["tag_suggestions"])
        # ordem: sugestao deterministica da fonte, depois sugestao da IA
        assert payload["content"]["category_suggestions"] == ["Filmes", "Séries"]

    def test_no_cms_identifier_leaks_into_the_artifact(self, sandbox):
        _run_one_article(sandbox)
        raw = next(Path(sandbox["drafts_dir"]).glob("*.json")).read_text(encoding="utf-8")
        for forbidden in ("wp_post_id", "featured_media", "wp-content/uploads", "wp:paragraph", "yoast"):
            assert forbidden not in raw, f"artefato vazou identificador de CMS: {forbidden}"

    def test_no_network_was_used(self, sandbox):
        _run_one_article(sandbox)
        assert sandbox["recorder"].network_calls == []

    def test_no_posts_row_is_created(self, sandbox):
        _run_one_article(sandbox)
        db = Database(str(sandbox["db_path"]))
        count = db._get_cursor().execute("SELECT COUNT(*) AS n FROM posts").fetchone()["n"]
        db.close()
        assert count == 0, "MNScr nao pode registrar publicacoes"
