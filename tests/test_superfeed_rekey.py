"""O mesmo acontecimento com outra `event_key` nao vira segunda materia.

O RSS Prime cunha `event_key` por topico. Com os superfeeds por portal, o mesmo
acontecimento tem uma chave em `movies` e outra em `cinema_cinerie`. Trocar a
fonte de um topico para o outro trazia de volta, como trabalho novo, tudo o que
ja tinha virado materia na janela do feed: a chave nova chega com revisao 1 e a
identidade versionada a aceita antes de qualquer dedup de URL.

O que fica travado aqui:

* chave nova cuja URL ja e trabalho de outro evento -> gravada como SUPERSEDED,
  nao devolvida ao pipeline;
* as revisoes seguintes dessa chave tambem;
* a reconciliacao de eventos nao recria a tarefa;
* URL liberada (falha, descarte) nao bloqueia;
* chave conhecida segue a regra de revisao;
* fallback continua sendo coberto pelo Superfeed, e nao o contrario.
"""

from __future__ import annotations

from app.feeds import cluster_signature, enrich_feed_item
from app.store import REKEY_SUPERSEDED_PREFIX, Database

VARIETY = "https://variety.com/2026/film/news/practical-magic-2-box-office-1236860432/"
SCREENRANT = "https://screenrant.com/practical-magic-2-box-office-debut/"
THR = "https://www.hollywoodreporter.com/movies/movie-news/practical-magic-2-1236698853/"


def _db(tmp_path):
    db = Database(str(tmp_path / "app.db"))
    db.initialize()
    return db


def _event(event_key: str, urls: list[str], revision: int = 1, **overrides):
    item = {
        "id": f"urn:sf:event:{event_key}",
        "url": urls[0],
        "title": "Practical Magic 2 fizzles with $30 million debut",
        "published": "2026-09-24",
        "origin": "superfeed",
        "topic": "movies",
        "event_key": event_key,
        "event_revision": revision,
        "is_cluster": len(urls) > 1,
        "multi_source": len(urls) > 1,
        "source_count": len(urls),
        "urls": list(urls),
        "cluster_signature": cluster_signature(urls) if len(urls) > 1 else None,
    }
    item.update(overrides)
    return item


def _status(db, db_id):
    row = db._get_cursor().execute(
        "SELECT status, fail_reason FROM seen_articles WHERE id = ?", (db_id,)
    ).fetchone()
    return row["status"], row["fail_reason"]


def _rows_for(db, event_key):
    return db._get_cursor().execute(
        "SELECT id, status, fail_reason FROM seen_articles WHERE event_key = ?", (event_key,)
    ).fetchall()


def test_a_published_event_under_a_new_key_is_not_new_work(tmp_path):
    db = _db(tmp_path)
    try:
        old = db.filter_new_articles("rssprime_movies", [_event("movies-a", [VARIETY, SCREENRANT, THR])])
        assert len(old) == 1
        db.update_article_status(old[0]["db_id"], "PUBLISHED")

        # Mesmo acontecimento no superfeed do Cinerie: so os veiculos do portal.
        again = db.filter_new_articles("rssprime_movies", [_event("cinerie-b", [VARIETY, SCREENRANT])])
        assert again == []

        rows = _rows_for(db, "cinerie-b")
        assert len(rows) == 1, "a linha precisa existir, senao a reconciliacao recria a tarefa"
        assert rows[0]["status"] == "SUPERSEDED"
        assert rows[0]["fail_reason"].startswith(REKEY_SUPERSEDED_PREFIX)
        assert "movies-a" in rows[0]["fail_reason"]
    finally:
        db.close()


def test_a_single_source_item_is_caught_the_same_way(tmp_path):
    db = _db(tmp_path)
    try:
        old = db.filter_new_articles("rssprime_movies", [_event("movies-single", [SCREENRANT])])
        db.update_article_status(old[0]["db_id"], "DRAFT_GENERATED")
        assert db.filter_new_articles("rssprime_movies", [_event("cinerie-single", [SCREENRANT])]) == []
    finally:
        db.close()


def test_work_in_progress_also_holds_the_url(tmp_path):
    db = _db(tmp_path)
    try:
        db.filter_new_articles("rssprime_movies", [_event("movies-a", [VARIETY, THR])])  # NEW
        assert db.filter_new_articles("rssprime_movies", [_event("cinerie-b", [VARIETY])]) == []
    finally:
        db.close()


def test_later_revisions_of_the_new_key_stay_out(tmp_path):
    db = _db(tmp_path)
    try:
        old = db.filter_new_articles("rssprime_movies", [_event("movies-a", [VARIETY, THR])])
        db.update_article_status(old[0]["db_id"], "PUBLISHED")
        assert db.filter_new_articles("rssprime_movies", [_event("cinerie-b", [VARIETY])]) == []

        # Revisao 2 da chave nova, agora com uma URL que ninguem cobriu.
        revision_2 = _event("cinerie-b", [VARIETY, SCREENRANT], revision=2)
        assert db.filter_new_articles("rssprime_movies", [revision_2]) == []
        assert {row["status"] for row in _rows_for(db, "cinerie-b")} == {"SUPERSEDED"}
    finally:
        db.close()


def test_reconciliation_does_not_bring_it_back(tmp_path):
    db = _db(tmp_path)
    try:
        old = db.filter_new_articles("rssprime_movies", [_event("movies-a", [VARIETY, THR])])
        db.update_article_status(old[0]["db_id"], "PUBLISHED")
        item = _event("cinerie-b", [VARIETY], id="event:cinerie-b:1")
        assert db.filter_new_articles("rssprime_movies", [item]) == []
        # O mesmo item que a reconciliacao montaria para o evento aceito.
        assert db.filter_new_articles("rssprime_movies", [dict(item)]) == []
        assert len(_rows_for(db, "cinerie-b")) == 1
    finally:
        db.close()


def test_a_released_url_does_not_block(tmp_path):
    db = _db(tmp_path)
    try:
        old = db.filter_new_articles("rssprime_movies", [_event("movies-a", [VARIETY])])
        db.update_article_status(old[0]["db_id"], "FAILED", reason="extracao falhou")
        fresh = db.filter_new_articles("rssprime_movies", [_event("cinerie-b", [VARIETY])])
        assert len(fresh) == 1
    finally:
        db.close()


def test_a_covered_url_blocks_a_new_key(tmp_path):
    db = _db(tmp_path)
    try:
        db.register_covered_urls("movies-a", [VARIETY, THR], seen_article_id=None)
        assert db.filter_new_articles("rssprime_movies", [_event("cinerie-b", [THR])]) == []
    finally:
        db.close()


def test_a_known_key_keeps_its_revision_rule(tmp_path):
    db = _db(tmp_path)
    try:
        first = db.filter_new_articles("rssprime_movies", [_event("movies-a", [VARIETY])])
        db.update_article_status(first[0]["db_id"], "PUBLISHED")
        revision_2 = db.filter_new_articles(
            "rssprime_movies", [_event("movies-a", [VARIETY, THR], revision=2)]
        )
        assert len(revision_2) == 1, "revisao nova de chave conhecida continua sendo trabalho"
    finally:
        db.close()


def test_an_unrelated_new_event_passes(tmp_path):
    db = _db(tmp_path)
    try:
        old = db.filter_new_articles("rssprime_movies", [_event("movies-a", [VARIETY])])
        db.update_article_status(old[0]["db_id"], "PUBLISHED")
        other = db.filter_new_articles("rssprime_movies", [_event("cinerie-c", [THR])])
        assert len(other) == 1
    finally:
        db.close()


def test_a_fallback_written_first_does_not_block_the_superfeed(tmp_path):
    """Regra de sempre: o Superfeed cobre o fallback, nao o contrario."""
    db = _db(tmp_path)
    try:
        fallback = {
            "id": "fb-1", "url": VARIETY, "title": "x", "published": "2026-09-24",
            "origin": "fallback", "topic": "movies", "is_cluster": False,
            "multi_source": False, "source_count": 1,
        }
        written = db.filter_new_articles("variety_fallback", [fallback])
        db.update_article_status(written[0]["db_id"], "PUBLISHED")
        superfeed = db.filter_new_articles("rssprime_movies", [_event("movies-a", [VARIETY, THR])])
        assert len(superfeed) == 1
    finally:
        db.close()


# ── topico da fonte vence o sf:topic ─────────────────────────────────────────

def test_the_source_topic_wins_over_the_portal_superfeed_slug():
    item = {"url": VARIETY, "topic": "cinema_cinerie", "urls": [VARIETY]}
    enriched = enrich_feed_item(
        item, {"origin": "superfeed", "topic": "movies", "category": "Filmes"}, "rssprime_movies"
    )
    assert enriched["topic"] == "movies"


def test_without_a_source_topic_the_item_topic_is_kept():
    item = {"url": VARIETY, "topic": "games", "urls": [VARIETY]}
    enriched = enrich_feed_item(item, {"origin": "superfeed"}, "rssprime_games")
    assert enriched["topic"] == "games"
