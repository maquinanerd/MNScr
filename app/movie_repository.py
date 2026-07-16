"""
Repositório de Filmes & Séries
CRUD completo para operações no banco de dados
"""

import logging
from typing import List, Optional, Dict, Any
from datetime import datetime
from slugify import slugify
from sqlalchemy.exc import IntegrityError

from app.models import (
    Movie, TvSeries, Genre, Actor, WatchProvider, 
    MovieReview, TvReview, List as ListModel, get_db
)
from app.tmdb_extended import TMDbExtendedClient

logger = logging.getLogger(__name__)


class MovieRepository:
    """Repositório para operações com filmes"""
    
    def __init__(self):
        self.db = get_db()
    
    def add_movie(self, movie_data: Dict) -> Optional[Movie]:
        """
        Adiciona um novo filme ao banco de dados
        
        Args:
            movie_data: Dados do filme (formato TMDb expandido)
            
        Returns:
            Objeto Movie criado ou None se erro
        """
        session = self.db.get_session()
        
        try:
            # Verifica se já existe
            existing = session.query(Movie).filter_by(tmdb_id=movie_data['tmdb_id']).first()
            if existing:
                logger.debug(f"[MovieRepo] Filme já existe: {movie_data['title']}")
                return existing
            
            # Cria novo filme
            movie = Movie(
                tmdb_id=movie_data['tmdb_id'],
                title=movie_data['title'],
                slug=slugify(movie_data['title']),
                overview=movie_data.get('overview'),
                release_date=movie_data.get('release_date'),
                runtime=movie_data.get('runtime'),
                budget=movie_data.get('budget'),
                revenue=movie_data.get('revenue'),
                rating=movie_data.get('rating', 0),
                vote_count=movie_data.get('vote_count', 0),
                popularity=movie_data.get('popularity', 0),
                poster_url=movie_data.get('poster_url'),
                backdrop_url=movie_data.get('backdrop_url'),
                trailer_url=movie_data.get('trailer_url'),
                imdb_id=movie_data.get('imdb_id'),
                director=movie_data.get('director'),
                watch_providers=movie_data.get('watch_providers'),
            )
            
            session.add(movie)
            session.commit()
            
            logger.info(f"[MovieRepo] Filme adicionado: {movie.title}")
            return movie
            
        except IntegrityError as e:
            session.rollback()
            logger.error(f"[MovieRepo] Erro de integridade: {e}")
            return None
        except Exception as e:
            session.rollback()
            logger.error(f"[MovieRepo] Erro ao adicionar filme: {e}")
            return None
        finally:
            session.close()
    
    def get_movie(self, movie_id: int) -> Optional[Movie]:
        """Obtém filme por ID"""
        session = self.db.get_session()
        movie = session.query(Movie).filter_by(id=movie_id).first()
        session.close()
        return movie
    
    def get_movie_by_slug(self, slug: str) -> Optional[Movie]:
        """Obtém filme por slug (para URLs amigáveis)"""
        session = self.db.get_session()
        movie = session.query(Movie).filter_by(slug=slug).first()
        session.close()
        return movie
    
    def get_movie_by_tmdb_id(self, tmdb_id: int) -> Optional[Movie]:
        """Obtém filme pelo ID da TMDb"""
        session = self.db.get_session()
        movie = session.query(Movie).filter_by(tmdb_id=tmdb_id).first()
        session.close()
        return movie
    
    def get_all_movies(self, limit: int = 100, offset: int = 0) -> List[Movie]:
        """Obtém todos os filmes com paginação"""
        session = self.db.get_session()
        movies = session.query(Movie).limit(limit).offset(offset).all()
        session.close()
        return movies
    
    def get_trending_movies(self, limit: int = 10) -> List[Movie]:
        """Obtém filmes marcados como trending"""
        session = self.db.get_session()
        movies = session.query(Movie).filter_by(is_trending=True).limit(limit).all()
        session.close()
        return movies
    
    def get_featured_movies(self, limit: int = 5) -> List[Movie]:
        """Obtém filmes destacados"""
        session = self.db.get_session()
        movies = session.query(Movie).filter_by(is_featured=True).limit(limit).all()
        session.close()
        return movies
    
    def get_by_genre(self, genre_id: int, limit: int = 20) -> List[Movie]:
        """Obtém filmes de um gênero"""
        session = self.db.get_session()
        genre = session.query(Genre).filter_by(id=genre_id).first()
        
        if not genre:
            session.close()
            return []
        
        movies = genre.movies[:limit]
        session.close()
        return movies
    
    def update_movie(self, movie_id: int, data: Dict) -> Optional[Movie]:
        """Atualiza dados de um filme"""
        session = self.db.get_session()
        
        try:
            movie = session.query(Movie).filter_by(id=movie_id).first()
            
            if not movie:
                return None
            
            for key, value in data.items():
                if hasattr(movie, key):
                    setattr(movie, key, value)
            
            movie.updated_at = datetime.utcnow()
            session.commit()
            
            logger.info(f"[MovieRepo] Filme atualizado: {movie.title}")
            return movie
            
        except Exception as e:
            session.rollback()
            logger.error(f"[MovieRepo] Erro ao atualizar: {e}")
            return None
        finally:
            session.close()
    
    def delete_movie(self, movie_id: int) -> bool:
        """Deleta um filme"""
        session = self.db.get_session()
        
        try:
            movie = session.query(Movie).filter_by(id=movie_id).first()
            if movie:
                session.delete(movie)
                session.commit()
                logger.info(f"[MovieRepo] Filme deletado: {movie.title}")
                return True
            return False
        except Exception as e:
            session.rollback()
            logger.error(f"[MovieRepo] Erro ao deletar: {e}")
            return False
        finally:
            session.close()
    
    def search_movies(self, query: str, limit: int = 20) -> List[Movie]:
        """Busca filmes por título ou overview"""
        session = self.db.get_session()
        results = session.query(Movie).filter(
            (Movie.title.ilike(f"%{query}%")) | 
            (Movie.overview.ilike(f"%{query}%"))
        ).limit(limit).all()
        session.close()
        return results


class TvRepository:
    """Repositório para operações com séries"""
    
    def __init__(self):
        self.db = get_db()
    
    def add_tv(self, tv_data: Dict) -> Optional[TvSeries]:
        """Adiciona uma nova série ao banco de dados"""
        session = self.db.get_session()
        
        try:
            # Verifica se já existe
            existing = session.query(TvSeries).filter_by(tmdb_id=tv_data['tmdb_id']).first()
            if existing:
                logger.debug(f"[TvRepo] Série já existe: {tv_data['title']}")
                return existing
            
            # Cria nova série
            tv = TvSeries(
                tmdb_id=tv_data['tmdb_id'],
                title=tv_data['title'],
                slug=slugify(tv_data['title']),
                overview=tv_data.get('overview'),
                first_air_date=tv_data.get('first_air_date'),
                last_air_date=tv_data.get('last_air_date'),
                status=tv_data.get('status'),
                total_seasons=tv_data.get('total_seasons', 0),
                total_episodes=tv_data.get('total_episodes', 0),
                networks=tv_data.get('networks', []),
                rating=tv_data.get('rating', 0),
                vote_count=tv_data.get('vote_count', 0),
                popularity=tv_data.get('popularity', 0),
                poster_url=tv_data.get('poster_url'),
                backdrop_url=tv_data.get('backdrop_url'),
                trailer_url=tv_data.get('trailer_url'),
                imdb_id=tv_data.get('imdb_id'),
                creators=tv_data.get('creators', []),
                watch_providers=tv_data.get('watch_providers'),
                is_ongoing=tv_data.get('status') == 'Returning Series',
            )
            
            session.add(tv)
            session.commit()
            
            logger.info(f"[TvRepo] Série adicionada: {tv.title}")
            return tv
            
        except IntegrityError as e:
            session.rollback()
            logger.error(f"[TvRepo] Erro de integridade: {e}")
            return None
        except Exception as e:
            session.rollback()
            logger.error(f"[TvRepo] Erro ao adicionar série: {e}")
            return None
        finally:
            session.close()
    
    def get_tv(self, tv_id: int) -> Optional[TvSeries]:
        """Obtém série por ID"""
        session = self.db.get_session()
        tv = session.query(TvSeries).filter_by(id=tv_id).first()
        session.close()
        return tv
    
    def get_tv_by_slug(self, slug: str) -> Optional[TvSeries]:
        """Obtém série por slug"""
        session = self.db.get_session()
        tv = session.query(TvSeries).filter_by(slug=slug).first()
        session.close()
        return tv
    
    def get_tv_by_tmdb_id(self, tmdb_id: int) -> Optional[TvSeries]:
        """Obtém série pelo ID da TMDb"""
        session = self.db.get_session()
        tv = session.query(TvSeries).filter_by(tmdb_id=tmdb_id).first()
        session.close()
        return tv
    
    def get_all_tv(self, limit: int = 100, offset: int = 0) -> List[TvSeries]:
        """Obtém todas as séries"""
        session = self.db.get_session()
        tv_series = session.query(TvSeries).limit(limit).offset(offset).all()
        session.close()
        return tv_series
    
    def get_trending_tv(self, limit: int = 10) -> List[TvSeries]:
        """Obtém séries em tendência"""
        session = self.db.get_session()
        tv_series = session.query(TvSeries).filter_by(is_trending=True).limit(limit).all()
        session.close()
        return tv_series
    
    def get_ongoing_tv(self, limit: int = 10) -> List[TvSeries]:
        """Obtém séries em exibição"""
        session = self.db.get_session()
        tv_series = session.query(TvSeries).filter_by(is_ongoing=True).limit(limit).all()
        session.close()
        return tv_series
    
    def search_tv(self, query: str, limit: int = 20) -> List[TvSeries]:
        """Busca séries por título"""
        session = self.db.get_session()
        results = session.query(TvSeries).filter(
            (TvSeries.title.ilike(f"%{query}%")) | 
            (TvSeries.overview.ilike(f"%{query}%"))
        ).limit(limit).all()
        session.close()
        return results
    
    def update_tv(self, tv_id: int, data: Dict) -> Optional[TvSeries]:
        """Atualiza dados de uma série"""
        session = self.db.get_session()
        
        try:
            tv = session.query(TvSeries).filter_by(id=tv_id).first()
            
            if not tv:
                return None
            
            for key, value in data.items():
                if hasattr(tv, key):
                    setattr(tv, key, value)
            
            tv.updated_at = datetime.utcnow()
            session.commit()
            
            logger.info(f"[TvRepo] Série atualizada: {tv.title}")
            return tv
            
        except Exception as e:
            session.rollback()
            logger.error(f"[TvRepo] Erro ao atualizar: {e}")
            return None
        finally:
            session.close()


class GenreRepository:
    """Repositório para gêneros"""
    
    def __init__(self):
        self.db = get_db()
    
    def add_genre(self, name: str) -> Optional[Genre]:
        """Adiciona novo gênero"""
        session = self.db.get_session()
        
        try:
            existing = session.query(Genre).filter_by(slug=slugify(name)).first()
            if existing:
                return existing
            
            genre = Genre(name=name, slug=slugify(name))
            session.add(genre)
            session.commit()
            
            logger.info(f"[GenreRepo] Gênero adicionado: {name}")
            return genre
            
        except Exception as e:
            session.rollback()
            logger.error(f"[GenreRepo] Erro: {e}")
            return None
        finally:
            session.close()
    
    def get_all_genres(self) -> List[Genre]:
        """Obtém todos os gêneros"""
        session = self.db.get_session()
        genres = session.query(Genre).all()
        session.close()
        return genres
    
    def get_genre(self, genre_id: int) -> Optional[Genre]:
        """Obtém gênero por ID"""
        session = self.db.get_session()
        genre = session.query(Genre).filter_by(id=genre_id).first()
        session.close()
        return genre
    
    def get_genre_by_name(self, name: str) -> Optional[Genre]:
        """Obtém gênero por nome"""
        session = self.db.get_session()
        genre = session.query(Genre).filter_by(slug=slugify(name)).first()
        session.close()
        return genre
