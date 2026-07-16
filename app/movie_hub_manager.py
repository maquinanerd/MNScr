"""
Hub Manager - Orquestrador do sistema completo
Sincroniza TMDb, banco de dados e geração de páginas
"""

import logging
from typing import List, Optional, Dict, Tuple
from datetime import datetime, timedelta
import time

from app.tmdb_extended import TMDbExtendedClient, get_tmdb_extended_client
from app.movie_repository import MovieRepository, TvRepository, GenreRepository
from app.page_generator import MoviePageGenerator, TvPageGenerator
from app.models import Movie, TvSeries, init_db

logger = logging.getLogger(__name__)


class MovieHubManager:
    """Gerenciador central do Hub de Filmes & Séries"""
    
    def __init__(self, tmdb_client: Optional[TMDbExtendedClient] = None):
        """
        Inicializa o hub manager
        
        Args:
            tmdb_client: Cliente TMDb (cria novo se None)
        """
        self.tmdb = tmdb_client or get_tmdb_extended_client()
        self.movie_repo = MovieRepository()
        self.tv_repo = TvRepository()
        self.genre_repo = GenreRepository()
        
        if not self.tmdb:
            logger.warning("[HubManager] TMDb não disponível")
    
    # ===== SINCRONIZAÇÃO COM TMDB =====
    
    def sync_trending_movies(self, limit: int = 10) -> List[Dict]:
        """
        Sincroniza filmes em tendência da TMDb
        
        Args:
            limit: Número máximo de filmes
            
        Returns:
            Lista de filmes sincronizados
        """
        if not self.tmdb:
            return []
        
        try:
            logger.info(f"[HubManager] Sincronizando {limit} filmes em tendência...")
            
            trending = self.tmdb.get_trending('movie', 'week')
            synced = []
            
            for i, movie_data in enumerate(trending[:limit]):
                # Pega detalhes completos
                details = self.tmdb.get_movie_details(movie_data['id'])
                if details:
                    formatted = self.tmdb.format_movie_data(details)
                    
                    # Adiciona ao banco
                    movie = self.movie_repo.add_movie(formatted)
                    if movie:
                        self.movie_repo.update_movie(movie.id, {'is_trending': True})
                        synced.append(formatted)
                    
                    # Rate limiting
                    time.sleep(0.5)
            
            logger.info(f"[HubManager] {len(synced)} filmes sincronizados como trending")
            return synced
            
        except Exception as e:
            logger.error(f"[HubManager] Erro ao sincronizar trending: {e}")
            return []
    
    def sync_trending_tv(self, limit: int = 10) -> List[Dict]:
        """Sincroniza séries em tendência"""
        if not self.tmdb:
            return []
        
        try:
            logger.info(f"[HubManager] Sincronizando {limit} séries em tendência...")
            
            trending = self.tmdb.get_trending('tv', 'week')
            synced = []
            
            for tv_data in trending[:limit]:
                details = self.tmdb.get_tv_details(tv_data['id'])
                if details:
                    formatted = self.tmdb.format_tv_data(details)
                    
                    tv = self.tv_repo.add_tv(formatted)
                    if tv:
                        self.tv_repo.update_tv(tv.id, {'is_trending': True})
                        synced.append(formatted)
                    
                    time.sleep(0.5)
            
            logger.info(f"[HubManager] {len(synced)} séries sincronizadas como trending")
            return synced
            
        except Exception as e:
            logger.error(f"[HubManager] Erro ao sincronizar séries: {e}")
            return []
    
    def sync_upcoming_movies(self, limit: int = 10) -> List[Dict]:
        """Sincroniza filmes em breve"""
        if not self.tmdb:
            return []
        
        try:
            logger.info(f"[HubManager] Sincronizando {limit} filmes em breve...")
            
            upcoming = self.tmdb.get_upcoming_movies()
            synced = []
            
            for movie_data in upcoming[:limit]:
                details = self.tmdb.get_movie_details(movie_data['id'])
                if details:
                    formatted = self.tmdb.format_movie_data(details)
                    
                    movie = self.movie_repo.add_movie(formatted)
                    if movie:
                        synced.append(formatted)
                    
                    time.sleep(0.5)
            
            logger.info(f"[HubManager] {len(synced)} filmes 'em breve' sincronizados")
            return synced
            
        except Exception as e:
            logger.error(f"[HubManager] Erro ao sincronizar upcoming: {e}")
            return []
    
    def sync_all_genres(self) -> Tuple[int, int]:
        """
        Sincroniza todos os gêneros de filmes e séries
        
        Returns:
            Tupla (filmes_genres_adicionados, tv_genres_adicionados)
        """
        if not self.tmdb:
            return (0, 0)
        
        try:
            logger.info("[HubManager] Sincronizando gêneros...")
            
            # Gêneros de filmes
            movie_genres = self.tmdb.get_movie_genres()
            movie_count = 0
            for genre_id, genre_name in movie_genres.items():
                self.genre_repo.add_genre(genre_name)
                movie_count += 1
            
            # Gêneros de séries
            tv_genres = self.tmdb.get_tv_genres()
            tv_count = 0
            for genre_id, genre_name in tv_genres.items():
                self.genre_repo.add_genre(genre_name)
                tv_count += 1
            
            logger.info(f"[HubManager] {movie_count + tv_count} gêneros sincronizados")
            return (movie_count, tv_count)
            
        except Exception as e:
            logger.error(f"[HubManager] Erro ao sincronizar gêneros: {e}")
            return (0, 0)
    
    def search_and_add_movie(self, title: str, year: Optional[int] = None) -> Optional[Dict]:
        """
        Busca um filme na TMDb e o adiciona ao banco
        
        Args:
            title: Título do filme
            year: Ano opcional
            
        Returns:
            Dados do filme ou None
        """
        if not self.tmdb:
            return None
        
        try:
            results = self.tmdb.search_movie(title, year)
            
            if not results:
                logger.warning(f"[HubManager] Filme '{title}' não encontrado na TMDb")
                return None
            
            # Pega o primeiro resultado (mais relevante)
            movie_data = results[0]
            details = self.tmdb.get_movie_details(movie_data['id'])
            
            if details:
                formatted = self.tmdb.format_movie_data(details)
                movie = self.movie_repo.add_movie(formatted)
                
                if movie:
                    logger.info(f"[HubManager] Filme adicionado: {formatted['title']}")
                    return formatted
            
        except Exception as e:
            logger.error(f"[HubManager] Erro ao buscar filme: {e}")
        
        return None
    
    def search_and_add_tv(self, title: str) -> Optional[Dict]:
        """Busca série na TMDb e a adiciona"""
        if not self.tmdb:
            return None
        
        try:
            results = self.tmdb.search_tv(title)
            
            if not results:
                logger.warning(f"[HubManager] Série '{title}' não encontrada")
                return None
            
            tv_data = results[0]
            details = self.tmdb.get_tv_details(tv_data['id'])
            
            if details:
                formatted = self.tmdb.format_tv_data(details)
                tv = self.tv_repo.add_tv(formatted)
                
                if tv:
                    logger.info(f"[HubManager] Série adicionada: {formatted['title']}")
                    return formatted
            
        except Exception as e:
            logger.error(f"[HubManager] Erro ao buscar série: {e}")
        
        return None
    
    # ===== GERAÇÃO DE PÁGINAS =====
    
    def generate_movie_page(self, movie_id: int) -> Optional[str]:
        """
        Gera página HTML completa para um filme
        
        Args:
            movie_id: ID do filme no banco
            
        Returns:
            HTML da página ou None
        """
        try:
            movie = self.movie_repo.get_movie(movie_id)
            
            if not movie:
                logger.warning(f"[HubManager] Filme {movie_id} não encontrado")
                return None
            
            # Converte ORM para dict
            movie_dict = {
                'id': movie.id,
                'title': movie.title,
                'overview': movie.overview,
                'rating': movie.rating,
                'vote_count': movie.vote_count,
                'release_date': movie.release_date,
                'genres': [],  # TODO: carregar gêneros relacionados
                'runtime': movie.runtime,
                'budget': movie.budget,
                'revenue': movie.revenue,
                'poster_url': movie.poster_url,
                'backdrop_url': movie.backdrop_url,
                'director': movie.director,
                'cast': movie.actors,
                'watch_providers': movie.watch_providers,
                'imdb_id': movie.imdb_id,
            }
            
            html = MoviePageGenerator.generate_movie_page(movie_dict)
            logger.info(f"[HubManager] Página do filme gerada: {movie.title}")
            return html
            
        except Exception as e:
            logger.error(f"[HubManager] Erro ao gerar página: {e}")
            return None
    
    def generate_tv_page(self, tv_id: int) -> Optional[str]:
        """Gera página HTML para uma série"""
        try:
            tv = self.tv_repo.get_tv(tv_id)
            
            if not tv:
                logger.warning(f"[HubManager] Série {tv_id} não encontrada")
                return None
            
            # Converte ORM para dict
            tv_dict = {
                'id': tv.id,
                'title': tv.title,
                'overview': tv.overview,
                'rating': tv.rating,
                'vote_count': tv.vote_count,
                'first_air_date': tv.first_air_date,
                'last_air_date': tv.last_air_date,
                'status': tv.status,
                'total_seasons': tv.total_seasons,
                'total_episodes': tv.total_episodes,
                'networks': tv.networks or [],
                'genres': [],
                'poster_url': tv.poster_url,
                'backdrop_url': tv.backdrop_url,
                'cast': tv.actors,
                'watch_providers': tv.watch_providers,
                'popularity': tv.popularity,
            }
            
            html = TvPageGenerator.generate_tv_page(tv_dict)
            logger.info(f"[HubManager] Página da série gerada: {tv.title}")
            return html
            
        except Exception as e:
            logger.error(f"[HubManager] Erro ao gerar página: {e}")
            return None
    
    # ===== LISTAGENS =====
    
    def get_trending_movies_page(self) -> str:
        """Gera página com filmes em tendência"""
        try:
            movies = self.movie_repo.get_trending_movies(limit=20)
            
            html = """
<div class="trending-movies" style="max-width: 1200px; margin: 0 auto; padding: 40px 20px;">
    <h1 style="border-bottom: 3px solid #667eea; padding-bottom: 10px;">
        🔥 Filmes em Tendência
    </h1>
    
    <div class="movies-grid" style="display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 20px; margin-top: 30px;">
"""
            
            for movie in movies:
                html += f"""
        <div class="movie-card" style="
            border-radius: 8px;
            overflow: hidden;
            box-shadow: 0 4px 12px rgba(0,0,0,0.15);
            transition: transform 0.3s, box-shadow 0.3s;
            cursor: pointer;
        " onmouseover="this.style.transform='scale(1.05)'; this.style.boxShadow='0 8px 20px rgba(0,0,0,0.25)'" 
           onmouseout="this.style.transform='scale(1)'; this.style.boxShadow='0 4px 12px rgba(0,0,0,0.15)'">
            <img src="{movie.poster_url}" alt="{movie.title}" style="width: 100%; height: 270px; object-fit: cover;">
            <div style="padding: 15px;">
                <h3 style="margin: 0 0 5px 0; font-size: 0.95em; height: 2.2em; overflow: hidden;">{movie.title}</h3>
                <p style="margin: 5px 0; color: #667eea; font-weight: bold;">⭐ {movie.rating}/10</p>
                <p style="margin: 5px 0 0 0; color: #999; font-size: 0.85em;">{movie.release_date}</p>
            </div>
        </div>
"""
            
            html += """
    </div>
</div>
"""
            
            return html
            
        except Exception as e:
            logger.error(f"[HubManager] Erro ao gerar página de tendências: {e}")
            return "<p>Erro ao carregar filmes em tendência</p>"
    
    def get_trending_tv_page(self) -> str:
        """Gera página com séries em tendência"""
        try:
            tv_series = self.tv_repo.get_trending_tv(limit=20)
            
            html = """
<div class="trending-tv" style="max-width: 1200px; margin: 0 auto; padding: 40px 20px;">
    <h1 style="border-bottom: 3px solid #4169E1; padding-bottom: 10px;">
        🔥 Séries em Tendência
    </h1>
    
    <div class="tv-grid" style="display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 20px; margin-top: 30px;">
"""
            
            for tv in tv_series:
                html += f"""
        <div class="tv-card" style="
            border-radius: 8px;
            overflow: hidden;
            box-shadow: 0 4px 12px rgba(0,0,0,0.15);
            transition: transform 0.3s;
        ">
            <img src="{tv.poster_url}" alt="{tv.title}" style="width: 100%; height: 270px; object-fit: cover;">
            <div style="padding: 15px;">
                <h3 style="margin: 0 0 5px 0; font-size: 0.95em;">{tv.title}</h3>
                <p style="margin: 5px 0; color: #4169E1; font-weight: bold;">⭐ {tv.rating}/10</p>
                <p style="margin: 5px 0 0 0; color: #999; font-size: 0.85em;">{tv.status}</p>
            </div>
        </div>
"""
            
            html += """
    </div>
</div>
"""
            
            return html
            
        except Exception as e:
            logger.error(f"[HubManager] Erro ao gerar página de tendências: {e}")
            return "<p>Erro ao carregar séries em tendência</p>"


def init_movie_hub(db_path: str = None) -> MovieHubManager:
    """Inicializa todo o hub de filmes"""
    logger.info("[HubManager] Inicializando Movie Hub...")
    
    # Inicializa BD
    init_db(db_path)
    
    # Cria manager
    manager = MovieHubManager()
    
    logger.info("[HubManager] Movie Hub pronto!")
    return manager
