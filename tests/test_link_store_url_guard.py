"""O campo `url` do link_store guarda endereço público, e só isso.

O ciclo de 05/08 gravou ali `artifacts\\local-drafts\\draft-....json` — o
caminho do artefato em disco. Ele voltou pelo link_map e o linking interno o
injetou no corpo da matéria seguinte como <a href>. A barreira fica no
armazenamento para não depender de cada chamador lembrar disso.
"""

from __future__ import annotations

import pytest

from app import link_store

FILE_PATH = "artifacts\\local-drafts\\draft-983733e65486f043f9d59f594fb88218.json"
REAL_URL = "https://cinerie.example/star-wars-personagens-inteligentes/"


@pytest.fixture(autouse=True)
def store_in_tmp(tmp_path, monkeypatch):
    """Um banco por teste; o link_store guarda o caminho num global de módulo."""
    monkeypatch.setattr(link_store, "_DB", str(tmp_path / "app.db"))
    monkeypatch.setattr(link_store, "_LEGACY_DB", str(tmp_path / "app.db"))
    monkeypatch.setattr(link_store, "_INITIALIZED", False)


def _rows() -> list[tuple[str, str]]:
    with link_store._conn() as c:
        return [(r["title"], r["url"]) for r in c.execute("SELECT title, url FROM link_store")]


class TestWriteRefusesWhatIsNotAnAddress:
    @pytest.mark.parametrize(
        "value",
        [
            FILE_PATH,
            "artifacts/local-drafts/draft-abc.json",
            "draft-983733e65486f043f9d59f594fb88218",
            "/wp-content/uploads/a.jpg",
            "",
            "   ",
        ],
        ids=["caminho-windows", "caminho-posix", "draft-id", "relativa", "vazia", "espacos"],
    )
    def test_non_url_is_not_stored(self, value):
        link_store.save_article(title="Star Wars", url=value, category="movies")
        assert _rows() == []

    def test_a_real_url_is_stored(self):
        link_store.save_article(title="Star Wars", url=REAL_URL, category="movies")
        assert _rows() == [("Star Wars", REAL_URL)]


class TestReadIgnoresRowsWrittenBeforeTheGuard:
    """O banco em produção já tem a linha envenenada; ela não volta ao prompt."""

    def _poison_directly(self):
        with link_store._conn() as c:
            c.execute(
                "INSERT INTO link_store (title, url, category, entity) VALUES (?, ?, ?, ?)",
                ("Star Wars: 14 personagens", FILE_PATH, "movies", "star wars"),
            )

    def test_get_link_map_skips_it(self):
        self._poison_directly()
        assert link_store.get_link_map() == {"posts": []}

    def test_get_related_skips_it(self):
        self._poison_directly()
        assert link_store.get_related(entity="star wars", category="movies") == []

    def test_a_clean_row_beside_it_still_comes_back(self):
        self._poison_directly()
        link_store.save_article(title="Marvel", url=REAL_URL, category="movies", entity="marvel")
        links = [p["link"] for p in link_store.get_link_map()["posts"]]
        assert links == [REAL_URL]
