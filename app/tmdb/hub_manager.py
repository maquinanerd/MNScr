"""app/tmdb/hub_manager.py — Orquestrador do TMDB Hub dedicado"""
from __future__ import annotations
import logging
import time
from typing import Optional, List, Dict, Tuple
from app.tmdb.config import TMDB_RATE_LIMIT_SLEEP, TMDB_LANGUAGE, TMDB_REGION, TMDB_SYNC_WATCH_PROVIDERS

logger = logging.getLogger(__name__)


class TMDbHubManager:
    """Orquestrador principal do Movie Hub. Usa cliente e repositório dedicados."""

    def __init__(self) -> None:
        from app.tmdb.client import get_client
        from app.tmdb.repository import get_repository
        self.client = get_client()
        self.repo = get_repository()

    # ------------------------------------------------------------------
    # Gêneros
    # ------------------------------------------------------------------

    def sync_genres(self) -> Tuple[int, int]:
        if not self.client:
            return 0, 0
        t0 = time.time()
        movie_count = tv_count = 0
        for tmdb_id, name in self.client.get_movie_genres().items():
            self.repo.upsert_genre(tmdb_id, name, "movie")
            movie_count += 1
        for tmdb_id, name in self.client.get_tv_genres().items():
            self.repo.upsert_genre(tmdb_id, name, "tv")
            tv_count += 1
        total = movie_count + tv_count
        self.repo.log_sync("genres", "ok", total, time.time() - t0)
        logger.info("[TMDB/hub] %d gêneros sincronizados", total)
        return movie_count, tv_count

    # ------------------------------------------------------------------
    # Trending
    # ------------------------------------------------------------------

    def sync_trending_movies(self, limit: int = 20) -> List[Dict]:
        if not self.client:
            return []
        t0 = time.time()
        synced = []
        try:
            items = self.client.get_trending("movie", "week")
            for item in items[:limit]:
                details = self.client.get_movie_details(item["id"])
                if not details:
                    continue
                data = self.client.format_movie_data(details)
                data["is_trending"] = True
                if self.repo.upsert_movie(data):
                    synced.append(data)
                time.sleep(TMDB_RATE_LIMIT_SLEEP)
            self.repo.log_sync("trending_movies", "ok", len(synced), time.time() - t0)
            logger.info("[TMDB/hub] %d filmes trending sincronizados", len(synced))
        except Exception as exc:
            self.repo.log_sync("trending_movies", "error", 0, time.time() - t0, str(exc))
            logger.error("[TMDB/hub] Erro trending movies: %s", exc)
        return synced

    def sync_trending_tv(self, limit: int = 20) -> List[Dict]:
        if not self.client:
            return []
        t0 = time.time()
        synced = []
        try:
            items = self.client.get_trending("tv", "week")
            for item in items[:limit]:
                details = self.client.get_tv_details(item["id"])
                if not details:
                    continue
                data = self.client.format_tv_data(details)
                data["is_trending"] = True
                if self.repo.upsert_tv(data):
                    synced.append(data)
                time.sleep(TMDB_RATE_LIMIT_SLEEP)
            self.repo.log_sync("trending_tv", "ok", len(synced), time.time() - t0)
            logger.info("[TMDB/hub] %d séries trending sincronizadas", len(synced))
        except Exception as exc:
            self.repo.log_sync("trending_tv", "error", 0, time.time() - t0, str(exc))
            logger.error("[TMDB/hub] Erro trending tv: %s", exc)
        return synced

    # ------------------------------------------------------------------
    # Upcoming
    # ------------------------------------------------------------------

    def sync_upcoming_movies(self, limit: int = 20) -> List[Dict]:
        if not self.client:
            return []
        t0 = time.time()
        synced = []
        try:
            items = self.client.get_upcoming_movies()
            for item in items[:limit]:
                data = {
                    "tmdb_id": item["id"],
                    "title": item.get("title", ""),
                    "release_date": item.get("release_date"),
                    "overview": item.get("overview"),
                    "poster_url": self.client.get_image_url(item.get("poster_path", "")),
                }
                self.repo.upsert_upcoming(data)
                # Também persiste como filme completo
                details = self.client.get_movie_details(item["id"])
                if details:
                    formatted = self.client.format_movie_data(details)
                    formatted["is_upcoming"] = True
                    self.repo.upsert_movie(formatted)
                synced.append(data)
                time.sleep(TMDB_RATE_LIMIT_SLEEP)
            self.repo.log_sync("upcoming_movies", "ok", len(synced), time.time() - t0)
            logger.info("[TMDB/hub] %d filmes upcoming sincronizados", len(synced))
        except Exception as exc:
            self.repo.log_sync("upcoming_movies", "error", 0, time.time() - t0, str(exc))
            logger.error("[TMDB/hub] Erro upcoming: %s", exc)
        return synced

    # ------------------------------------------------------------------
    # Popular
    # ------------------------------------------------------------------

    def sync_popular_movies(self, limit: int = 20) -> int:
        if not self.client:
            return 0
        count = 0
        for item in self.client.get_popular_movies()[:limit]:
            details = self.client.get_movie_details(item["id"])
            if details and self.repo.upsert_movie(self.client.format_movie_data(details)):
                count += 1
            time.sleep(TMDB_RATE_LIMIT_SLEEP)
        return count

    def sync_popular_tv(self, limit: int = 20) -> int:
        if not self.client:
            return 0
        count = 0
        for item in self.client.get_popular_tv()[:limit]:
            details = self.client.get_tv_details(item["id"])
            if details and self.repo.upsert_tv(self.client.format_tv_data(details)):
                count += 1
            time.sleep(TMDB_RATE_LIMIT_SLEEP)
        return count

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def get_stats(self) -> Dict:
        return {
            "movies": self.repo.count_movies(),
            "tv": self.repo.count_tv(),
            "last_sync": self.repo.get_last_sync(),
        }

    # ------------------------------------------------------------------
    # Lookup por título (para enricher)
    # ------------------------------------------------------------------

    def find_movie_by_title(self, title: str, year: Optional[int] = None) -> Optional[Dict]:
        if not self.client:
            return None
        results = self.client.search_movie(title, year)
        if not results:
            return None
        details = self.client.get_movie_details(results[0]["id"])
        return self.client.format_movie_data(details) if details else None

    def find_tv_by_title(self, title: str) -> Optional[Dict]:
        if not self.client:
            return None
        results = self.client.search_tv(title)
        if not results:
            return None
        details = self.client.get_tv_details(results[0]["id"])
        return self.client.format_tv_data(details) if details else None


# Singleton
_hub: Optional[TMDbHubManager] = None

def get_hub() -> Optional[TMDbHubManager]:
    global _hub
    if _hub is None:
        try:
            _hub = TMDbHubManager()
        except Exception as exc:
            logger.warning("[TMDB/hub] Não foi possível inicializar HubManager: %s", exc)
    return _hub
