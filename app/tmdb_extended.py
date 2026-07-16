"""
Cliente TMDb Expandido com Watch Providers
Integra funcionalidades avançadas: onde assistir, trailers, elenco detalhado
"""

import requests
import logging
from typing import Optional, Dict, List, Any
from datetime import datetime
import os

logger = logging.getLogger(__name__)


class TMDbExtendedClient:
    """Cliente TMDb v3 com funcionalidades expandidas"""
    
    BASE_URL = "https://api.themoviedb.org/3"
    IMAGE_BASE_URL = "https://image.tmdb.org/t/p"
    
    # Mapeamento de providers para emojis/nomes amigáveis
    PROVIDER_NAMES = {
        'Netflix': '📺',
        'Amazon Prime Video': '🎬',
        'Disney+': '🎪',
        'HBO Max': '📽️',
        'Hulu': '🎭',
        'Apple TV+': '🍎',
        'Paramount+': '⭐',
        'Crunchyroll': '⚡',
        'YouTube': '▶️',
        'Google Play': '🎮',
    }
    
    def __init__(self, api_key: str, access_token: Optional[str] = None):
        """
        Inicializa cliente TMDb
        
        Args:
            api_key: Chave de API v3
            access_token: Token de acesso Bearer (opcional, mais seguro)
        """
        self.api_key = api_key
        self.access_token = access_token
        self.session = requests.Session()
        
        # Headers padrão
        headers = {'Content-Type': 'application/json;charset=utf-8'}
        
        # Se tiver token, usa Bearer authentication
        if access_token:
            headers['Authorization'] = f'Bearer {access_token}'
        
        self.session.headers.update(headers)
    
    def _make_request(self, endpoint: str, params: Dict = None) -> Optional[Dict]:
        """Faz requisição à API TMDb"""
        try:
            url = f"{self.BASE_URL}{endpoint}"
            req_params = params or {}
            
            # Se não tem token, usa API key
            if not self.access_token:
                req_params['api_key'] = self.api_key
            
            response = self.session.get(url, params=req_params, timeout=10)
            response.raise_for_status()
            
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"[TMDb] Erro na requisição: {str(e)}")
            return None
    
    # ===== BUSCA =====
    
    def search_movie(self, query: str, year: Optional[int] = None) -> List[Dict]:
        """Busca filmes"""
        params = {'query': query, 'language': 'pt-BR'}
        if year:
            params['year'] = year
        
        result = self._make_request('/search/movie', params)
        return result.get('results', []) if result else []
    
    def search_tv(self, query: str) -> List[Dict]:
        """Busca séries"""
        params = {'query': query, 'language': 'pt-BR'}
        result = self._make_request('/search/tv', params)
        return result.get('results', []) if result else []
    
    # ===== DETALHES =====
    
    def get_movie_details(self, movie_id: int) -> Optional[Dict]:
        """Obtém detalhes completos do filme com tudo"""
        params = {
            'language': 'pt-BR',
            'append_to_response': 'videos,images,credits,external_ids,watch/providers,release_dates'
        }
        return self._make_request(f'/movie/{movie_id}', params)
    
    def get_tv_details(self, tv_id: int) -> Optional[Dict]:
        """Obtém detalhes completos da série com tudo"""
        params = {
            'language': 'pt-BR',
            'append_to_response': 'videos,images,credits,external_ids,watch/providers,content_ratings'
        }
        return self._make_request(f'/tv/{tv_id}', params)
    
    # ===== WATCH PROVIDERS (ONDE ASSISTIR) =====
    
    def get_movie_watch_providers(self, movie_id: int, region: str = 'BR') -> Dict:
        """
        Obtém onde assistir um filme (por região)
        
        Args:
            movie_id: ID do filme
            region: Código do país (BR = Brasil, US = EUA, etc)
            
        Returns:
            Dicionário com informações de onde assistir
        """
        details = self.get_movie_details(movie_id)
        
        if not details or 'watch/providers' not in details:
            return {}
        
        providers_data = details['watch/providers'].get('results', {}).get(region, {})
        
        return {
            'stream': providers_data.get('flatrate', []),
            'rent': providers_data.get('rent', []),
            'buy': providers_data.get('buy', []),
            'free': providers_data.get('free_with_ads', []),
            'logo_path': providers_data.get('logo_path', ''),
        }
    
    def get_tv_watch_providers(self, tv_id: int, region: str = 'BR') -> Dict:
        """Obtém onde assistir uma série"""
        details = self.get_tv_details(tv_id)
        
        if not details or 'watch/providers' not in details:
            return {}
        
        providers_data = details['watch/providers'].get('results', {}).get(region, {})
        
        return {
            'stream': providers_data.get('flatrate', []),
            'rent': providers_data.get('rent', []),
            'buy': providers_data.get('buy', []),
            'free': providers_data.get('free_with_ads', []),
            'logo_path': providers_data.get('logo_path', ''),
        }
    
    # ===== TRENDING & DESCOBERTA =====
    
    def get_trending(self, media_type: str = 'movie', time_window: str = 'week') -> List[Dict]:
        """Obtém em tendência (movie/tv)"""
        params = {'language': 'pt-BR'}
        result = self._make_request(f'/trending/{media_type}/{time_window}', params)
        return result.get('results', []) if result else []
    
    def get_upcoming_movies(self) -> List[Dict]:
        """Filmes em breve"""
        params = {'language': 'pt-BR'}
        result = self._make_request('/movie/upcoming', params)
        return result.get('results', []) if result else []
    
    def get_popular_movies(self) -> List[Dict]:
        """Filmes populares"""
        params = {'language': 'pt-BR'}
        result = self._make_request('/movie/popular', params)
        return result.get('results', []) if result else []
    
    def get_top_rated_movies(self) -> List[Dict]:
        """Filmes melhores avaliados"""
        params = {'language': 'pt-BR'}
        result = self._make_request('/movie/top_rated', params)
        return result.get('results', []) if result else []
    
    def get_popular_tv(self) -> List[Dict]:
        """Séries populares"""
        params = {'language': 'pt-BR'}
        result = self._make_request('/tv/popular', params)
        return result.get('results', []) if result else []
    
    def get_top_rated_tv(self) -> List[Dict]:
        """Séries melhores avaliadas"""
        params = {'language': 'pt-BR'}
        result = self._make_request('/tv/top_rated', params)
        return result.get('results', []) if result else []
    
    # ===== GÊNEROS =====
    
    def get_movie_genres(self) -> Dict[int, str]:
        """Obtém lista de gêneros de filmes"""
        result = self._make_request('/genre/movie/list', {'language': 'pt-BR'})
        
        if result and 'genres' in result:
            return {g['id']: g['name'] for g in result['genres']}
        
        return {}
    
    def get_tv_genres(self) -> Dict[int, str]:
        """Obtém lista de gêneros de séries"""
        result = self._make_request('/genre/tv/list', {'language': 'pt-BR'})
        
        if result and 'genres' in result:
            return {g['id']: g['name'] for g in result['genres']}
        
        return {}
    
    def get_movies_by_genre(self, genre_id: int) -> List[Dict]:
        """Filmes de um gênero específico"""
        params = {
            'language': 'pt-BR',
            'with_genres': genre_id,
            'sort_by': 'popularity.desc'
        }
        result = self._make_request('/discover/movie', params)
        return result.get('results', []) if result else []
    
    def get_tv_by_genre(self, genre_id: int) -> List[Dict]:
        """Séries de um gênero específico"""
        params = {
            'language': 'pt-BR',
            'with_genres': genre_id,
            'sort_by': 'popularity.desc'
        }
        result = self._make_request('/discover/tv', params)
        return result.get('results', []) if result else []
    
    # ===== UTILS =====
    
    def get_image_url(self, path: str, size: str = 'w500') -> str:
        """Gera URL completa de imagem"""
        if not path:
            return ''
        return f"{self.IMAGE_BASE_URL}/{size}{path}"
    
    def format_movie_data(self, movie: Dict) -> Dict:
        """Formata dados de filme para banco de dados"""
        genres = movie.get('genres', [])
        genre_names = [g.get('name') for g in genres] if isinstance(genres, list) else []
        
        # Pega trailer do YouTube se houver
        trailer_url = None
        videos = movie.get('videos', {}).get('results', [])
        for video in videos:
            if video.get('type') == 'Trailer' and video.get('site') == 'YouTube':
                trailer_url = f"https://www.youtube.com/watch?v={video['key']}"
                break
        
        return {
            'tmdb_id': movie.get('id'),
            'title': movie.get('title'),
            'release_date': movie.get('release_date'),
            'overview': movie.get('overview'),
            'rating': round(movie.get('vote_average', 0), 1),
            'vote_count': movie.get('vote_count', 0),
            'popularity': movie.get('popularity', 0),
            'genres': genre_names,
            'runtime': movie.get('runtime', 0),
            'budget': movie.get('budget', 0),
            'revenue': movie.get('revenue', 0),
            'poster_url': self.get_image_url(movie.get('poster_path')),
            'backdrop_url': self.get_image_url(movie.get('backdrop_path'), 'w1280'),
            'cast': self._format_cast(movie.get('credits', {}).get('cast', [])),
            'director': self._get_director(movie.get('credits', {}).get('crew', [])),
            'trailer_url': trailer_url,
            'imdb_id': self._get_external_id(movie.get('external_ids', {}), 'imdb_id'),
            'watch_providers': movie.get('watch/providers', {}),
        }
    
    def format_tv_data(self, tv: Dict) -> Dict:
        """Formata dados de série para banco de dados"""
        genres = tv.get('genres', [])
        genre_names = [g.get('name') for g in genres] if isinstance(genres, list) else []
        
        # Pega trailer
        trailer_url = None
        videos = tv.get('videos', {}).get('results', [])
        for video in videos:
            if video.get('type') == 'Trailer' and video.get('site') == 'YouTube':
                trailer_url = f"https://www.youtube.com/watch?v={video['key']}"
                break
        
        return {
            'tmdb_id': tv.get('id'),
            'title': tv.get('name'),
            'first_air_date': tv.get('first_air_date'),
            'last_air_date': tv.get('last_air_date'),
            'overview': tv.get('overview'),
            'rating': round(tv.get('vote_average', 0), 1),
            'vote_count': tv.get('vote_count', 0),
            'popularity': tv.get('popularity', 0),
            'genres': genre_names,
            'total_seasons': tv.get('number_of_seasons', 0),
            'total_episodes': tv.get('number_of_episodes', 0),
            'status': tv.get('status'),
            'networks': [n.get('name') for n in tv.get('networks', [])],
            'poster_url': self.get_image_url(tv.get('poster_path')),
            'backdrop_url': self.get_image_url(tv.get('backdrop_path'), 'w1280'),
            'cast': self._format_cast(tv.get('credits', {}).get('cast', [])),
            'creators': [p.get('name') for p in tv.get('created_by', [])],
            'trailer_url': trailer_url,
            'imdb_id': self._get_external_id(tv.get('external_ids', {}), 'imdb_id'),
            'watch_providers': tv.get('watch/providers', {}),
        }
    
    def _format_cast(self, cast: List[Dict], limit: int = 10) -> List[Dict]:
        """Formata dados de elenco"""
        formatted = []
        for actor in cast[:limit]:
            formatted.append({
                'name': actor.get('name'),
                'character': actor.get('character'),
                'profile_path': self.get_image_url(actor.get('profile_path')),
                'tmdb_id': actor.get('id'),
            })
        return formatted
    
    def _get_director(self, crew: List[Dict]) -> str:
        """Extrai nome do diretor"""
        for person in crew:
            if person.get('job') == 'Director':
                return person.get('name', '')
        return ''
    
    def _get_external_id(self, external_ids: Dict, id_type: str) -> str:
        """Extrai ID externo (IMDb, etc)"""
        return external_ids.get(id_type, '')


def get_tmdb_extended_client(
    api_key: Optional[str] = None,
    access_token: Optional[str] = None
) -> Optional[TMDbExtendedClient]:
    """Factory para criar cliente TMDb expandido"""
    key = api_key or os.getenv('TMDB_API_KEY')
    token = access_token or os.getenv('TMDB_ACCESS_TOKEN')
    
    if not key:
        logger.warning("[TMDb] Nenhuma chave de API configurada")
        return None
    
    return TMDbExtendedClient(key, token)
