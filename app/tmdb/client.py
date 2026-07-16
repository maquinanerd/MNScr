"""
app/tmdb/client.py
Adapter sobre TMDbExtendedClient existente.
Centraliza a criação do cliente TMDB com as configurações do pacote dedicado.
Não duplica lógica — reutiliza app/tmdb_extended.py diretamente.
"""

from __future__ import annotations

import logging
import time
from typing import Optional, List, Dict, Any

from app.tmdb.config import (
    TMDB_API_KEY,
    TMDB_ACCESS_TOKEN,
    TMDB_LANGUAGE,
    TMDB_REGION,
    TMDB_REQUEST_TIMEOUT,
    TMDB_RATE_LIMIT_SLEEP,
    HAS_API_KEY,
    masked_key,
)

logger = logging.getLogger(__name__)

# Re-exporta o cliente original (sem duplicar código)
from app.tmdb_extended import TMDbExtendedClient  # noqa: F401


class TMDbHubClient:
    """
    Wrapper fino sobre TMDbExtendedClient com:
    - Configuração via app/tmdb/config.py
    - Rate limiting automático
    - Logging prefixado [TMDB]
    - Tratamento de timeout e resposta vazia
    """

    def __init__(self) -> None:
        if not HAS_API_KEY:
            raise ValueError("[TMDB] TMDB_API_KEY não configurada")

        self._client = TMDbExtendedClient(
            api_key=TMDB_API_KEY,
            access_token=TMDB_ACCESS_TOKEN or None,
        )
        # Sobrescreve timeout para o valor configurado
        self._client.session.request = self._timed_request  # type: ignore[method-assign]
        self._timeout = TMDB_REQUEST_TIMEOUT
        self._sleep = TMDB_RATE_LIMIT_SLEEP
        logger.info("[TMDB/client] Iniciado | key=%s | lang=%s | region=%s",
                    masked_key(TMDB_API_KEY), TMDB_LANGUAGE, TMDB_REGION)

    def _timed_request(self, method, url, **kwargs):
        """Injeta timeout em todas as requisições."""
        kwargs.setdefault("timeout", self._timeout)
        return type(self._client.session).request(self._client.session, method, url, **kwargs)

    def _sleep_rate_limit(self) -> None:
        if self._sleep > 0:
            time.sleep(self._sleep)

    # -----------------------------------------------------------------------
    # Delegação para o cliente subjacente
    # -----------------------------------------------------------------------

    def search_movie(self, query: str, year: Optional[int] = None) -> List[Dict]:
        self._sleep_rate_limit()
        return self._client.search_movie(query, year)

    def search_tv(self, query: str) -> List[Dict]:
        self._sleep_rate_limit()
        return self._client.search_tv(query)

    def get_movie_details(self, movie_id: int) -> Optional[Dict]:
        self._sleep_rate_limit()
        return self._client.get_movie_details(movie_id)

    def get_tv_details(self, tv_id: int) -> Optional[Dict]:
        self._sleep_rate_limit()
        return self._client.get_tv_details(tv_id)

    def get_trending(self, media_type: str = "movie", time_window: str = "week") -> List[Dict]:
        self._sleep_rate_limit()
        return self._client.get_trending(media_type, time_window)

    def get_upcoming_movies(self) -> List[Dict]:
        self._sleep_rate_limit()
        return self._client.get_upcoming_movies()

    def get_popular_movies(self) -> List[Dict]:
        self._sleep_rate_limit()
        return self._client.get_popular_movies()

    def get_popular_tv(self) -> List[Dict]:
        self._sleep_rate_limit()
        return self._client.get_popular_tv()

    def get_top_rated_movies(self) -> List[Dict]:
        self._sleep_rate_limit()
        return self._client.get_top_rated_movies()

    def get_top_rated_tv(self) -> List[Dict]:
        self._sleep_rate_limit()
        return self._client.get_top_rated_tv()

    def get_movie_genres(self) -> Dict[int, str]:
        self._sleep_rate_limit()
        return self._client.get_movie_genres()

    def get_tv_genres(self) -> Dict[int, str]:
        self._sleep_rate_limit()
        return self._client.get_tv_genres()

    def get_movies_by_genre(self, genre_id: int) -> List[Dict]:
        self._sleep_rate_limit()
        return self._client.get_movies_by_genre(genre_id)

    def format_movie_data(self, movie: Dict) -> Dict:
        return self._client.format_movie_data(movie)

    def format_tv_data(self, tv: Dict) -> Dict:
        return self._client.format_tv_data(tv)

    def get_image_url(self, path: str, size: str = "w500") -> str:
        return self._client.get_image_url(path, size)

    def get_movie_watch_providers(self, movie_id: int, region: str = None) -> Dict:
        return self._client.get_movie_watch_providers(movie_id, region or TMDB_REGION)

    def get_tv_watch_providers(self, tv_id: int, region: str = None) -> Dict:
        return self._client.get_tv_watch_providers(tv_id, region or TMDB_REGION)


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_client_instance: Optional[TMDbHubClient] = None


def get_client() -> Optional[TMDbHubClient]:
    """Retorna singleton do cliente TMDB ou None se indisponível."""
    global _client_instance
    if _client_instance is None:
        if not HAS_API_KEY:
            logger.warning("[TMDB/client] API key ausente — cliente indisponível")
            return None
        try:
            _client_instance = TMDbHubClient()
        except Exception as exc:
            logger.warning("[TMDB/client] Falha ao criar cliente: %s", exc)
            return None
    return _client_instance
