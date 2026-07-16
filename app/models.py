"""
Modelos ORM para Hub de Filmes & Séries
Usa SQLAlchemy para gerenciar banco de dados
"""

from sqlalchemy import create_engine, Column, Integer, String, Float, Text, DateTime, Boolean, ForeignKey, Table, JSON
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, sessionmaker
from datetime import datetime
import logging
import os

logger = logging.getLogger(__name__)

Base = declarative_base()

# Tabela de associação M2M para géneros
movie_genre_association = Table(
    'movie_genre_association',
    Base.metadata,
    Column('movie_id', Integer, ForeignKey('movies.id')),
    Column('genre_id', Integer, ForeignKey('genres.id'))
)

tv_genre_association = Table(
    'tv_genre_association',
    Base.metadata,
    Column('tv_id', Integer, ForeignKey('tv_series.id')),
    Column('genre_id', Integer, ForeignKey('genres.id'))
)

# Tabela de associação para atores
actor_movie_association = Table(
    'actor_movie_association',
    Base.metadata,
    Column('actor_id', Integer, ForeignKey('actors.id')),
    Column('movie_id', Integer, ForeignKey('movies.id'))
)

actor_tv_association = Table(
    'actor_tv_association',
    Base.metadata,
    Column('actor_id', Integer, ForeignKey('actors.id')),
    Column('tv_id', Integer, ForeignKey('tv_series.id'))
)


class Genre(Base):
    """Modelo de Gênero"""
    __tablename__ = 'genres'
    
    id = Column(Integer, primary_key=True)
    name = Column(String(100), unique=True, nullable=False)
    slug = Column(String(100), unique=True, nullable=False)
    
    # Relacionamentos
    movies = relationship('Movie', secondary=movie_genre_association, back_populates='genres')
    tv_series = relationship('TvSeries', secondary=tv_genre_association, back_populates='genres')
    
    def __repr__(self):
        return f"<Genre {self.name}>"


class Actor(Base):
    """Modelo de Ator/Atriz"""
    __tablename__ = 'actors'
    
    id = Column(Integer, primary_key=True)
    tmdb_id = Column(Integer, unique=True, nullable=False)
    name = Column(String(255), nullable=False)
    profile_url = Column(Text)
    biography = Column(Text)
    birth_date = Column(String(50))
    death_date = Column(String(50))
    popularity = Column(Float, default=0)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relacionamentos
    movies = relationship('Movie', secondary=actor_movie_association)
    tv_series = relationship('TvSeries', secondary=actor_tv_association)
    
    def __repr__(self):
        return f"<Actor {self.name}>"


class Movie(Base):
    """Modelo de Filme"""
    __tablename__ = 'movies'
    
    id = Column(Integer, primary_key=True)
    tmdb_id = Column(Integer, unique=True, nullable=False, index=True)
    title = Column(String(255), nullable=False, index=True)
    slug = Column(String(255), unique=True, nullable=False, index=True)
    overview = Column(Text)
    release_date = Column(String(50), index=True)
    runtime = Column(Integer)
    budget = Column(Integer)
    revenue = Column(Integer)
    
    # Ratings
    rating = Column(Float, default=0)
    vote_count = Column(Integer, default=0)
    popularity = Column(Float, default=0)
    
    # URLs
    poster_url = Column(Text)
    backdrop_url = Column(Text)
    trailer_url = Column(Text)
    
    # IDs externos
    imdb_id = Column(String(20))
    
    # Director
    director = Column(String(255))
    
    # Watch Providers (JSON)
    watch_providers = Column(JSON)
    
    # Flags
    is_trending = Column(Boolean, default=False)
    is_featured = Column(Boolean, default=False)
    
    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relacionamentos
    genres = relationship('Genre', secondary=movie_genre_association, back_populates='movies')
    actors = relationship('Actor', secondary=actor_movie_association)
    
    def __repr__(self):
        return f"<Movie {self.title} ({self.release_date})>"


class TvSeries(Base):
    """Modelo de Série"""
    __tablename__ = 'tv_series'
    
    id = Column(Integer, primary_key=True)
    tmdb_id = Column(Integer, unique=True, nullable=False, index=True)
    title = Column(String(255), nullable=False, index=True)
    slug = Column(String(255), unique=True, nullable=False, index=True)
    overview = Column(Text)
    first_air_date = Column(String(50), index=True)
    last_air_date = Column(String(50))
    status = Column(String(50))  # 'Returning Series', 'Ended', etc
    total_seasons = Column(Integer, default=0)
    total_episodes = Column(Integer, default=0)
    networks = Column(JSON)  # ['Netflix', 'HBO Max', etc]
    
    # Ratings
    rating = Column(Float, default=0)
    vote_count = Column(Integer, default=0)
    popularity = Column(Float, default=0)
    
    # URLs
    poster_url = Column(Text)
    backdrop_url = Column(Text)
    trailer_url = Column(Text)
    
    # IDs externos
    imdb_id = Column(String(20))
    
    # Creators
    creators = Column(JSON)  # ['Creator1', 'Creator2']
    
    # Watch Providers (JSON)
    watch_providers = Column(JSON)
    
    # Flags
    is_trending = Column(Boolean, default=False)
    is_featured = Column(Boolean, default=False)
    is_ongoing = Column(Boolean, default=True)
    
    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relacionamentos
    genres = relationship('Genre', secondary=tv_genre_association, back_populates='tv_series')
    actors = relationship('Actor', secondary=actor_tv_association)
    
    def __repr__(self):
        return f"<TvSeries {self.title} ({self.status})>"


class WatchProvider(Base):
    """Modelo de Provedor de Streaming"""
    __tablename__ = 'watch_providers'
    
    id = Column(Integer, primary_key=True)
    name = Column(String(100), unique=True, nullable=False)
    logo_url = Column(Text)
    color = Column(String(20))  # Cor hexadecimal
    emoji = Column(String(10))  # Emoji associado
    
    def __repr__(self):
        return f"<WatchProvider {self.name}>"


class MovieReview(Base):
    """Modelo de Review de Filme (para avaliações do site)"""
    __tablename__ = 'movie_reviews'
    
    id = Column(Integer, primary_key=True)
    movie_id = Column(Integer, ForeignKey('movies.id'), nullable=False)
    author = Column(String(255), nullable=False)
    content = Column(Text, nullable=False)
    rating = Column(Float)
    helpful_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def __repr__(self):
        return f"<MovieReview Movie:{self.movie_id}>"


class TvReview(Base):
    """Modelo de Review de Série"""
    __tablename__ = 'tv_reviews'
    
    id = Column(Integer, primary_key=True)
    tv_id = Column(Integer, ForeignKey('tv_series.id'), nullable=False)
    author = Column(String(255), nullable=False)
    content = Column(Text, nullable=False)
    rating = Column(Float)
    helpful_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def __repr__(self):
        return f"<TvReview TvSeries:{self.tv_id}>"


class List(Base):
    """Modelo de Listas Personalizadas"""
    __tablename__ = 'lists'
    
    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False)
    slug = Column(String(255), unique=True, nullable=False)
    description = Column(Text)
    list_type = Column(String(50))  # 'trending', 'top_rated', 'upcoming', 'custom'
    thumbnail_url = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def __repr__(self):
        return f"<List {self.name}>"


class DatabaseManager:
    """Gerenciador de banco de dados"""
    
    def __init__(self, db_path: str = None):
        """
        Inicializa o gerenciador do BD
        
        Args:
            db_path: Caminho do arquivo SQLite (padrão: app.db)
        """
        if db_path is None:
            db_path = os.path.join(os.path.dirname(__file__), '..', 'movie_hub.db')
        
        self.db_path = db_path
        self.engine = create_engine(f'sqlite:///{db_path}', echo=False)
        self.SessionLocal = sessionmaker(bind=self.engine)
        
        logger.info(f"[MovieHub] Banco de dados: {db_path}")
    
    def create_all_tables(self):
        """Cria todas as tabelas no banco de dados"""
        try:
            Base.metadata.create_all(self.engine)
            logger.info("[MovieHub] Tabelas criadas com sucesso")
        except Exception as e:
            logger.error(f"[MovieHub] Erro ao criar tabelas: {e}")
            raise
    
    def get_session(self):
        """Retorna uma nova sessão do SQLAlchemy"""
        return self.SessionLocal()
    
    def close(self):
        """Fecha o engine do banco de dados"""
        self.engine.dispose()


# Instância global
db_manager = None


def init_db(db_path: str = None) -> DatabaseManager:
    """Inicializa o banco de dados global"""
    global db_manager
    db_manager = DatabaseManager(db_path)
    db_manager.create_all_tables()
    return db_manager


def get_db() -> DatabaseManager:
    """Retorna o gerenciador de BD global"""
    global db_manager
    if db_manager is None:
        db_manager = init_db()
    return db_manager
