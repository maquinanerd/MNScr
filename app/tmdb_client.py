"""
The Movie Database (TMDb) API Client
Fornece dados enriquecidos de filmes e séries para o pipeline.
"""

import requests
import logging
from typing import Optional, Dict, List, Any
from datetime import datetime
import os

logger = logging.getLogger(__name__)

class TMDbClient:
    """Cliente para a API da TMDb v3"""
    
    BASE_URL = "https://api.themoviedb.org/3"
    IMAGE_BASE_URL = "https://image.tmdb.org/t/p"
    
    def __init__(self, api_key: str):
        """
        Inicializa o cliente TMDb
        
        Args:
            api_key: Chave de API da TMDb (v3)
        """
        self.api_key = api_key
        self.session = requests.Session()
        self.session.headers.update({
            'Content-Type': 'application/json;charset=utf-8'
        })
        
    def _make_request(self, endpoint: str, params: Dict = None) -> Optional[Dict]:
        """
        Faz uma requisição à API TMDb
        
        Args:
            endpoint: Endpoint da API (ex: /search/movie)
            params: Parâmetros adicionais da query
            
        Returns:
            Resposta JSON ou None se erro
        """
        try:
            url = f"{self.BASE_URL}{endpoint}"
            req_params = params or {}
            req_params['api_key'] = self.api_key
            
            response = self.session.get(url, params=req_params, timeout=10)
            response.raise_for_status()
            
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"[TMDb] Erro na requisição: {str(e)}")
            return None
    
    def search_movie(self, query: str, year: Optional[int] = None) -> List[Dict]:
        """
        Busca filmes por título
        
        Args:
            query: Título do filme
            year: Ano opcional para refinar busca
            
        Returns:
            Lista de filmes encontrados
        """
        params = {
            'query': query,
            'language': 'pt-BR'
        }
        
        if year:
            params['year'] = year
        
        result = self._make_request('/search/movie', params)
        
        if result and 'results' in result:
            logger.info(f"[TMDb] Encontrados {len(result['results'])} filmes para '{query}'")
            return result['results']
        
        return []
    
    def search_tv(self, query: str) -> List[Dict]:
        """
        Busca séries por título
        
        Args:
            query: Título da série
            
        Returns:
            Lista de séries encontradas
        """
        params = {
            'query': query,
            'language': 'pt-BR'
        }
        
        result = self._make_request('/search/tv', params)
        
        if result and 'results' in result:
            logger.info(f"[TMDb] Encontradas {len(result['results'])} séries para '{query}'")
            return result['results']
        
        return []
    
    def get_movie_details(self, movie_id: int) -> Optional[Dict]:
        """
        Obtém detalhes completos de um filme
        
        Args:
            movie_id: ID da TMDb do filme
            
        Returns:
            Dicionário com detalhes do filme
        """
        params = {
            'language': 'pt-BR',
            'append_to_response': 'videos,images,credits'
        }
        
        result = self._make_request(f'/movie/{movie_id}', params)
        return result
    
    def get_tv_details(self, tv_id: int) -> Optional[Dict]:
        """
        Obtém detalhes completos de uma série
        
        Args:
            tv_id: ID da TMDb da série
            
        Returns:
            Dicionário com detalhes da série
        """
        params = {
            'language': 'pt-BR',
            'append_to_response': 'videos,images,credits'
        }
        
        result = self._make_request(f'/tv/{tv_id}', params)
        return result
    
    def get_trending(self, media_type: str = 'movie', time_window: str = 'week') -> List[Dict]:
        """
        Obtém conteúdo em tendência
        
        Args:
            media_type: 'movie' ou 'tv'
            time_window: 'day' ou 'week'
            
        Returns:
            Lista de itens em tendência
        """
        params = {'language': 'pt-BR'}
        result = self._make_request(f'/trending/{media_type}/{time_window}', params)
        
        if result and 'results' in result:
            logger.info(f"[TMDb] {len(result['results'])} {media_type}s em tendência")
            return result['results']
        
        return []
    
    def get_upcoming_movies(self) -> List[Dict]:
        """
        Obtém filmes em breve
        
        Returns:
            Lista de filmes próximos
        """
        params = {'language': 'pt-BR'}
        result = self._make_request('/movie/upcoming', params)
        
        if result and 'results' in result:
            return result['results']
        
        return []
    
    def get_image_url(self, path: str, size: str = 'w500') -> str:
        """
        Gera URL completa de uma imagem
        
        Args:
            path: Caminho da imagem retornado pela API
            size: Tamanho ('w92', 'w154', 'w185', 'w342', 'w500', 'w780', 'original')
            
        Returns:
            URL completa da imagem
        """
        if not path:
            return ''
        
        return f"{self.IMAGE_BASE_URL}/{size}{path}"
    
    def format_movie_data(self, movie: Dict) -> Dict:
        """
        Formata dados de um filme para uso no pipeline
        
        Args:
            movie: Dados brutos do filme da TMDb
            
        Returns:
            Dicionário formatado
        """
        return {
            'tmdb_id': movie.get('id'),
            'title': movie.get('title'),
            'release_date': movie.get('release_date'),
            'overview': movie.get('overview'),
            'rating': movie.get('vote_average'),
            'popularity': movie.get('popularity'),
            'genres': movie.get('genres', []),
            'poster_url': self.get_image_url(movie.get('poster_path')),
            'backdrop_url': self.get_image_url(movie.get('backdrop_path')),
            'runtime': movie.get('runtime'),
            'budget': movie.get('budget'),
            'revenue': movie.get('revenue'),
            'cast': movie.get('credits', {}).get('cast', [])[:5],
            'videos': movie.get('videos', {}).get('results', [])
        }
    
    def format_tv_data(self, tv: Dict) -> Dict:
        """
        Formata dados de uma série para uso no pipeline
        
        Args:
            tv: Dados brutos da série da TMDb
            
        Returns:
            Dicionário formatado
        """
        return {
            'tmdb_id': tv.get('id'),
            'title': tv.get('name'),
            'first_air_date': tv.get('first_air_date'),
            'overview': tv.get('overview'),
            'rating': tv.get('vote_average'),
            'popularity': tv.get('popularity'),
            'genres': tv.get('genres', []),
            'poster_url': self.get_image_url(tv.get('poster_path')),
            'backdrop_url': self.get_image_url(tv.get('backdrop_path')),
            'total_seasons': tv.get('number_of_seasons'),
            'total_episodes': tv.get('number_of_episodes'),
            'status': tv.get('status'),
            'networks': tv.get('networks', []),
            'cast': tv.get('credits', {}).get('cast', [])[:5],
            'videos': tv.get('videos', {}).get('results', [])
        }


def get_tmdb_client(api_key: Optional[str] = None) -> Optional[TMDbClient]:
    """
    Factory para criar instância do cliente TMDb
    
    Args:
        api_key: Chave API (se None, lê de TMDB_API_KEY env)
        
    Returns:
        Instância de TMDbClient ou None se sem chave
    """
    key = api_key or os.getenv('TMDB_API_KEY')
    
    if not key:
        logger.warning("[TMDb] Nenhuma chave de API configurada (TMDB_API_KEY)")
        return None
    
    return TMDbClient(key)
