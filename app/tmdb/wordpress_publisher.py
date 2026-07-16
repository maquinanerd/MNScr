"""app/tmdb/wordpress_publisher.py — Publica páginas TMDB no WordPress como rascunho"""
from __future__ import annotations
import logging
from typing import Optional, Dict, Any, List
from app.tmdb.config import TMDB_HUB_DRAFT_MODE, TMDB_AUTO_PUBLISH_HUB, TMDB_WP_CATEGORIES

logger = logging.getLogger(__name__)

# Categorias padrão no WordPress (ajustar IDs conforme seu WP)
_DEFAULT_CATEGORY_IDS: Dict[str, int] = {
    "Filmes": 24,
    "Séries": 21,
    "Em Alta": 0,
    "Lançamentos": 0,
    "Onde Assistir": 0,
}


class TMDbWordPressPublisher:
    """Publica páginas de filmes/séries no WordPress."""

    def __init__(self) -> None:
        from app.wordpress import WordPressClient
        from app.config import WORDPRESS_CONFIG, WORDPRESS_CATEGORIES
        self._wp = WordPressClient(
            config=WORDPRESS_CONFIG,
            categories_map=WORDPRESS_CATEGORIES,
        )
        self._status = "draft" if (TMDB_HUB_DRAFT_MODE or not TMDB_AUTO_PUBLISH_HUB) else "publish"
        logger.info("[TMDB/publisher] Status de publicação: %s", self._status)

    def _get_category_ids(self, names: List[str]) -> List[int]:
        ids = []
        for name in names:
            cid = _DEFAULT_CATEGORY_IDS.get(name, 0)
            if cid:
                ids.append(cid)
        return ids or [_DEFAULT_CATEGORY_IDS.get("Filmes", 24)]

    def _page_already_exists(self, title: str) -> Optional[int]:
        """Verifica se já existe post com esse título. Retorna wp_post_id ou None."""
        try:
            existing = self._wp.search_posts(title, limit=1)
            if existing:
                first = existing[0]
                if first.get("title", {}).get("rendered", "").strip().lower() == title.strip().lower():
                    return first.get("id")
        except Exception:
            pass
        return None

    def publish_movie(self, movie: Dict[str, Any], html_content: str) -> Optional[int]:
        """
        Publica página de filme no WordPress.
        Padrão: rascunho. Retorna wp_post_id ou None.
        """
        title = str(movie.get("title", "Filme Desconhecido"))
        existing_id = self._page_already_exists(title)
        if existing_id:
            logger.info("[TMDB/publisher] Filme já existe WP id=%s: %s", existing_id, title)
            return existing_id

        cat_ids = self._get_category_ids(["Filmes"])
        payload = {
            "title": title,
            "content": html_content,
            "status": self._status,
            "categories": cat_ids,
            "excerpt": str(movie.get("overview", ""))[:160],
            "slug": f"filme-{movie.get('tmdb_id', '')}-{title.lower().replace(' ', '-')[:40]}",
        }
        try:
            post_id = self._wp.create_post(payload)
            if post_id:
                logger.info("[TMDB/publisher] Filme publicado (%s) WP id=%s: %s", self._status, post_id, title)
            return post_id
        except Exception as exc:
            logger.error("[TMDB/publisher] Erro ao publicar filme '%s': %s", title, exc)
            return None

    def publish_tv(self, tv: Dict[str, Any], html_content: str) -> Optional[int]:
        """Publica página de série no WordPress."""
        title = str(tv.get("title", "Série Desconhecida"))
        existing_id = self._page_already_exists(title)
        if existing_id:
            logger.info("[TMDB/publisher] Série já existe WP id=%s: %s", existing_id, title)
            return existing_id

        cat_ids = self._get_category_ids(["Séries"])
        payload = {
            "title": title,
            "content": html_content,
            "status": self._status,
            "categories": cat_ids,
            "excerpt": str(tv.get("overview", ""))[:160],
            "slug": f"serie-{tv.get('tmdb_id', '')}-{title.lower().replace(' ', '-')[:40]}",
        }
        try:
            post_id = self._wp.create_post(payload)
            if post_id:
                logger.info("[TMDB/publisher] Série publicada (%s) WP id=%s: %s", self._status, post_id, title)
            return post_id
        except Exception as exc:
            logger.error("[TMDB/publisher] Erro ao publicar série '%s': %s", title, exc)
            return None
