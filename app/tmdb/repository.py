"""app/tmdb/repository.py — SQLite puro para o banco dedicado tmdb_hub.sqlite"""
from __future__ import annotations
import sqlite3
import json
import logging
import time
from pathlib import Path
from typing import Optional, List, Dict, Any
from app.tmdb.config import TMDB_DB_PATH

logger = logging.getLogger(__name__)

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS tmdb_movies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tmdb_id INTEGER UNIQUE NOT NULL,
    title TEXT NOT NULL,
    slug TEXT UNIQUE NOT NULL,
    overview TEXT,
    release_date TEXT,
    runtime INTEGER DEFAULT 0,
    budget INTEGER DEFAULT 0,
    revenue INTEGER DEFAULT 0,
    rating REAL DEFAULT 0,
    vote_count INTEGER DEFAULT 0,
    popularity REAL DEFAULT 0,
    poster_url TEXT,
    backdrop_url TEXT,
    trailer_url TEXT,
    imdb_id TEXT,
    director TEXT,
    cast_json TEXT,
    genres_json TEXT,
    watch_providers_json TEXT,
    is_trending INTEGER DEFAULT 0,
    is_upcoming INTEGER DEFAULT 0,
    wp_post_id INTEGER,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS tmdb_tv_series (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tmdb_id INTEGER UNIQUE NOT NULL,
    title TEXT NOT NULL,
    slug TEXT UNIQUE NOT NULL,
    overview TEXT,
    first_air_date TEXT,
    last_air_date TEXT,
    status TEXT,
    total_seasons INTEGER DEFAULT 0,
    total_episodes INTEGER DEFAULT 0,
    networks_json TEXT,
    rating REAL DEFAULT 0,
    vote_count INTEGER DEFAULT 0,
    popularity REAL DEFAULT 0,
    poster_url TEXT,
    backdrop_url TEXT,
    trailer_url TEXT,
    imdb_id TEXT,
    creators_json TEXT,
    cast_json TEXT,
    genres_json TEXT,
    watch_providers_json TEXT,
    is_trending INTEGER DEFAULT 0,
    is_ongoing INTEGER DEFAULT 1,
    wp_post_id INTEGER,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS tmdb_genres (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tmdb_id INTEGER UNIQUE NOT NULL,
    name TEXT NOT NULL,
    media_type TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tmdb_watch_providers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider_id INTEGER NOT NULL,
    provider_name TEXT NOT NULL,
    logo_path TEXT,
    region TEXT NOT NULL DEFAULT 'BR',
    UNIQUE(provider_id, region)
);

CREATE TABLE IF NOT EXISTS tmdb_videos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    media_type TEXT NOT NULL,
    tmdb_id INTEGER NOT NULL,
    key TEXT NOT NULL,
    name TEXT,
    type TEXT,
    site TEXT DEFAULT 'YouTube',
    UNIQUE(tmdb_id, media_type, key)
);

CREATE TABLE IF NOT EXISTS tmdb_trending (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    media_type TEXT NOT NULL,
    tmdb_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    popularity REAL DEFAULT 0,
    snapshot_date TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS tmdb_upcoming (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tmdb_id INTEGER UNIQUE NOT NULL,
    title TEXT NOT NULL,
    release_date TEXT,
    overview TEXT,
    poster_url TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS tmdb_article_matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    article_db_id INTEGER,
    wp_post_id INTEGER,
    tmdb_id INTEGER NOT NULL,
    media_type TEXT NOT NULL,
    title TEXT NOT NULL,
    confidence REAL NOT NULL,
    match_reason TEXT,
    enriched_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS tmdb_sync_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    operation TEXT NOT NULL,
    status TEXT NOT NULL,
    items_synced INTEGER DEFAULT 0,
    duration_s REAL DEFAULT 0,
    error_msg TEXT,
    executed_at TEXT DEFAULT (datetime('now'))
);
"""


def _slugify(text: str) -> str:
    import re, unicodedata
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^\w\s-]", "", text).strip().lower()
    return re.sub(r"[-\s]+", "-", text)


class TMDbRepository:
    """Repositório SQLite puro para o banco dedicado TMDB."""

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self.db_path = db_path or TMDB_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_schema(self) -> None:
        with self._conn() as conn:
            conn.executescript(SCHEMA)
        logger.info("[TMDB/repo] Banco inicializado: %s", self.db_path)

    # ------------------------------------------------------------------
    # Movies
    # ------------------------------------------------------------------

    def upsert_movie(self, data: Dict[str, Any]) -> bool:
        slug = _slugify(data.get("title", "")) or f"movie-{data['tmdb_id']}"
        try:
            with self._conn() as conn:
                conn.execute("""
                    INSERT INTO tmdb_movies
                        (tmdb_id, title, slug, overview, release_date, runtime,
                         budget, revenue, rating, vote_count, popularity,
                         poster_url, backdrop_url, trailer_url, imdb_id,
                         director, cast_json, genres_json, watch_providers_json,
                         is_trending, updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))
                    ON CONFLICT(tmdb_id) DO UPDATE SET
                        title=excluded.title, overview=excluded.overview,
                        rating=excluded.rating, vote_count=excluded.vote_count,
                        popularity=excluded.popularity, poster_url=excluded.poster_url,
                        backdrop_url=excluded.backdrop_url, trailer_url=excluded.trailer_url,
                        cast_json=excluded.cast_json, genres_json=excluded.genres_json,
                        watch_providers_json=excluded.watch_providers_json,
                        is_trending=excluded.is_trending, updated_at=datetime('now')
                """, (
                    data["tmdb_id"], data.get("title"), slug, data.get("overview"),
                    data.get("release_date"), data.get("runtime", 0),
                    data.get("budget", 0), data.get("revenue", 0),
                    data.get("rating", 0), data.get("vote_count", 0),
                    data.get("popularity", 0), data.get("poster_url"),
                    data.get("backdrop_url"), data.get("trailer_url"),
                    data.get("imdb_id"), data.get("director"),
                    json.dumps(data.get("cast", []), ensure_ascii=False),
                    json.dumps(data.get("genres", []), ensure_ascii=False),
                    json.dumps(data.get("watch_providers", {}), ensure_ascii=False),
                    1 if data.get("is_trending") else 0,
                ))
            return True
        except Exception as exc:
            logger.error("[TMDB/repo] upsert_movie erro: %s", exc)
            return False

    def get_movie_by_tmdb_id(self, tmdb_id: int) -> Optional[Dict]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM tmdb_movies WHERE tmdb_id=?", (tmdb_id,)).fetchone()
        return dict(row) if row else None

    def get_trending_movies(self, limit: int = 20) -> List[Dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM tmdb_movies WHERE is_trending=1 ORDER BY popularity DESC LIMIT ?",
                (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def get_all_movies(self, limit: int = 100, offset: int = 0) -> List[Dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM tmdb_movies ORDER BY popularity DESC LIMIT ? OFFSET ?",
                (limit, offset)
            ).fetchall()
        return [dict(r) for r in rows]

    def count_movies(self) -> int:
        with self._conn() as conn:
            return conn.execute("SELECT COUNT(*) FROM tmdb_movies").fetchone()[0]

    def set_movie_wp_post_id(self, tmdb_id: int, wp_post_id: int) -> None:
        with self._conn() as conn:
            conn.execute("UPDATE tmdb_movies SET wp_post_id=? WHERE tmdb_id=?", (wp_post_id, tmdb_id))

    # ------------------------------------------------------------------
    # TV Series
    # ------------------------------------------------------------------

    def upsert_tv(self, data: Dict[str, Any]) -> bool:
        slug = _slugify(data.get("title", "")) or f"tv-{data['tmdb_id']}"
        try:
            with self._conn() as conn:
                conn.execute("""
                    INSERT INTO tmdb_tv_series
                        (tmdb_id, title, slug, overview, first_air_date, last_air_date,
                         status, total_seasons, total_episodes, networks_json,
                         rating, vote_count, popularity, poster_url, backdrop_url,
                         trailer_url, imdb_id, creators_json, cast_json, genres_json,
                         watch_providers_json, is_trending, is_ongoing, updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))
                    ON CONFLICT(tmdb_id) DO UPDATE SET
                        title=excluded.title, overview=excluded.overview,
                        status=excluded.status, total_seasons=excluded.total_seasons,
                        total_episodes=excluded.total_episodes, rating=excluded.rating,
                        vote_count=excluded.vote_count, popularity=excluded.popularity,
                        poster_url=excluded.poster_url, backdrop_url=excluded.backdrop_url,
                        trailer_url=excluded.trailer_url, cast_json=excluded.cast_json,
                        genres_json=excluded.genres_json, watch_providers_json=excluded.watch_providers_json,
                        is_trending=excluded.is_trending, updated_at=datetime('now')
                """, (
                    data["tmdb_id"], data.get("title"), slug, data.get("overview"),
                    data.get("first_air_date"), data.get("last_air_date"),
                    data.get("status"), data.get("total_seasons", 0),
                    data.get("total_episodes", 0),
                    json.dumps(data.get("networks", []), ensure_ascii=False),
                    data.get("rating", 0), data.get("vote_count", 0),
                    data.get("popularity", 0), data.get("poster_url"),
                    data.get("backdrop_url"), data.get("trailer_url"),
                    data.get("imdb_id"),
                    json.dumps(data.get("creators", []), ensure_ascii=False),
                    json.dumps(data.get("cast", []), ensure_ascii=False),
                    json.dumps(data.get("genres", []), ensure_ascii=False),
                    json.dumps(data.get("watch_providers", {}), ensure_ascii=False),
                    1 if data.get("is_trending") else 0,
                    1 if data.get("status") == "Returning Series" else 0,
                ))
            return True
        except Exception as exc:
            logger.error("[TMDB/repo] upsert_tv erro: %s", exc)
            return False

    def get_tv_by_tmdb_id(self, tmdb_id: int) -> Optional[Dict]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM tmdb_tv_series WHERE tmdb_id=?", (tmdb_id,)).fetchone()
        return dict(row) if row else None

    def get_trending_tv(self, limit: int = 20) -> List[Dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM tmdb_tv_series WHERE is_trending=1 ORDER BY popularity DESC LIMIT ?",
                (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def get_all_tv(self, limit: int = 100, offset: int = 0) -> List[Dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM tmdb_tv_series ORDER BY popularity DESC LIMIT ? OFFSET ?",
                (limit, offset)
            ).fetchall()
        return [dict(r) for r in rows]

    def count_tv(self) -> int:
        with self._conn() as conn:
            return conn.execute("SELECT COUNT(*) FROM tmdb_tv_series").fetchone()[0]

    def set_tv_wp_post_id(self, tmdb_id: int, wp_post_id: int) -> None:
        with self._conn() as conn:
            conn.execute("UPDATE tmdb_tv_series SET wp_post_id=? WHERE tmdb_id=?", (wp_post_id, tmdb_id))

    # ------------------------------------------------------------------
    # Genres
    # ------------------------------------------------------------------

    def upsert_genre(self, tmdb_id: int, name: str, media_type: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO tmdb_genres (tmdb_id, name, media_type) VALUES (?,?,?)",
                (tmdb_id, name, media_type)
            )

    def get_all_genres(self) -> List[Dict]:
        with self._conn() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM tmdb_genres").fetchall()]

    # ------------------------------------------------------------------
    # Sync log
    # ------------------------------------------------------------------

    def log_sync(self, operation: str, status: str, items: int = 0,
                 duration_s: float = 0.0, error: str = "") -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO tmdb_sync_log (operation, status, items_synced, duration_s, error_msg) VALUES (?,?,?,?,?)",
                (operation, status, items, round(duration_s, 2), error or None)
            )

    def get_last_sync(self, operation: str = None) -> Optional[Dict]:
        with self._conn() as conn:
            if operation:
                row = conn.execute(
                    "SELECT * FROM tmdb_sync_log WHERE operation=? ORDER BY executed_at DESC LIMIT 1",
                    (operation,)
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM tmdb_sync_log ORDER BY executed_at DESC LIMIT 1"
                ).fetchone()
        return dict(row) if row else None

    # ------------------------------------------------------------------
    # Article matches
    # ------------------------------------------------------------------

    def log_article_match(self, article_db_id: int, tmdb_id: int, media_type: str,
                          title: str, confidence: float, match_reason: str = "",
                          wp_post_id: int = None) -> None:
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO tmdb_article_matches
                    (article_db_id, wp_post_id, tmdb_id, media_type, title, confidence, match_reason)
                VALUES (?,?,?,?,?,?,?)
            """, (article_db_id, wp_post_id, tmdb_id, media_type, title, confidence, match_reason))

    # ------------------------------------------------------------------
    # Upcoming
    # ------------------------------------------------------------------

    def upsert_upcoming(self, data: Dict) -> None:
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO tmdb_upcoming (tmdb_id, title, release_date, overview, poster_url)
                VALUES (?,?,?,?,?)
                ON CONFLICT(tmdb_id) DO UPDATE SET
                    title=excluded.title, release_date=excluded.release_date
            """, (data["tmdb_id"], data.get("title"), data.get("release_date"),
                  data.get("overview"), data.get("poster_url")))

    def get_upcoming(self, limit: int = 20) -> List[Dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM tmdb_upcoming ORDER BY release_date ASC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]


# Singleton
_repo: Optional[TMDbRepository] = None

def get_repository() -> TMDbRepository:
    global _repo
    if _repo is None:
        _repo = TMDbRepository()
    return _repo
