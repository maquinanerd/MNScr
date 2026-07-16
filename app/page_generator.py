"""
Gerador de Páginas HTML para Filmes & Séries
Cria páginas individuais com design responsivo
"""

import logging
from typing import Optional, Dict, List
from datetime import datetime
from html import escape

logger = logging.getLogger(__name__)


class MoviePageGenerator:
    """Gerador de página para filme"""
    
    @staticmethod
    def generate_movie_page(movie: Dict) -> str:
        """
        Gera página HTML completa para um filme
        
        Args:
            movie: Dados do filme (modelo ORM)
            
        Returns:
            HTML completo da página
        """
        
        # Header com backdrop
        header = f"""
<!-- MOVIE PAGE HEADER -->
<div class="movie-header" style="
    background: linear-gradient(135deg, rgba(0,0,0,0.7), rgba(0,0,0,0.3)), 
                url('{movie.get('backdrop_url')}') center/cover;
    padding: 60px 20px;
    color: white;
    text-align: center;
">
    <h1 style="font-size: 3em; margin: 0; text-shadow: 2px 2px 8px rgba(0,0,0,0.8);">
        {escape(movie.get('title', 'Sem Título'))}
    </h1>
    <p style="font-size: 1.2em; margin: 10px 0; opacity: 0.9;">
        {movie.get('release_date', 'Data desconhecida')}
    </p>
</div>
"""
        
        # Container principal
        main_content = f"""
<div class="movie-container" style="max-width: 1200px; margin: 0 auto; padding: 40px 20px;">
    <div style="display: grid; grid-template-columns: 250px 1fr; gap: 40px;">
        
        <!-- COLUNA ESQUERDA: POSTER & INFO RÁPIDA -->
        <div class="movie-sidebar">
            <img src="{movie.get('poster_url')}" alt="{escape(movie.get('title'))}" 
                 style="width: 100%; border-radius: 8px; box-shadow: 0 8px 24px rgba(0,0,0,0.3);">
            
            <div class="quick-info" style="margin-top: 20px;">
                {MoviePageGenerator._generate_rating_section(movie)}
                {MoviePageGenerator._generate_quick_info(movie)}
                {MoviePageGenerator._generate_watch_providers(movie)}
            </div>
        </div>
        
        <!-- COLUNA DIREITA: CONTEÚDO -->
        <div class="movie-content">
            {MoviePageGenerator._generate_overview(movie)}
            {MoviePageGenerator._generate_cast_section(movie)}
            {MoviePageGenerator._generate_details_table(movie)}
        </div>
    </div>
</div>
<!-- END MOVIE CONTAINER -->
"""
        
        return header + main_content
    
    @staticmethod
    def _generate_rating_section(movie: Dict) -> str:
        """Gera seção de rating"""
        rating = movie.get('rating', 0)
        stars = '⭐' * int(rating / 2)
        
        return f"""
<div class="rating-section" style="
    background: #f5f5f5;
    padding: 20px;
    border-radius: 8px;
    margin-bottom: 20px;
">
    <div style="font-size: 2em; font-weight: bold; color: #FFC107;">
        {rating}/10 {stars}
    </div>
    <p style="margin: 5px 0; color: #666; font-size: 0.9em;">
        {movie.get('vote_count', 0)} avaliações
    </p>
</div>
"""
    
    @staticmethod
    def _generate_quick_info(movie: Dict) -> str:
        """Gera informações rápidas"""
        runtime = movie.get('runtime', 0)
        director = movie.get('director', 'Desconhecido')
        
        html = f"""
<div class="quick-info" style="background: #f5f5f5; padding: 20px; border-radius: 8px;">
    <h3 style="margin-top: 0; color: #333;">Informações</h3>
"""
        
        if runtime:
            hours = runtime // 60
            minutes = runtime % 60
            html += f"<p><strong>Duração:</strong> {hours}h {minutes}m</p>"
        
        if director:
            html += f"<p><strong>Diretor:</strong> {escape(director)}</p>"
        
        if movie.get('budget'):
            budget = f"${movie.get('budget'):,.0f}"
            html += f"<p><strong>Orçamento:</strong> {budget}</p>"
        
        if movie.get('revenue'):
            revenue = f"${movie.get('revenue'):,.0f}"
            html += f"<p><strong>Renda:</strong> {revenue}</p>"
        
        html += "</div>"
        return html
    
    @staticmethod
    def _generate_watch_providers(movie: Dict) -> str:
        """Gera seção de "onde assistir" """
        providers = movie.get('watch_providers', {})
        
        if not providers:
            return '<p style="color: #666; font-size: 0.9em;">Informações de streaming indisponíveis</p>'
        
        html = """
<div class="watch-providers" style="
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    color: white;
    padding: 20px;
    border-radius: 8px;
    margin-top: 20px;
">
    <h3 style="margin-top: 0; margin-bottom: 15px;">Onde Assistir</h3>
"""
        
        # Streaming
        stream = providers.get('stream', [])
        if stream:
            html += '<p><strong>🎬 Streaming:</strong></p>'
            html += '<div style="display: flex; gap: 10px; flex-wrap: wrap;">'
            for provider in stream:
                html += f'<span style="background: rgba(255,255,255,0.2); padding: 5px 10px; border-radius: 4px;">{escape(provider.get("name", ""))}</span>'
            html += '</div>'
        
        # Aluguel
        rent = providers.get('rent', [])
        if rent:
            html += '<p style="margin-top: 10px;"><strong>🎫 Aluguel:</strong></p>'
            html += '<div style="display: flex; gap: 10px; flex-wrap: wrap;">'
            for provider in rent:
                html += f'<span style="background: rgba(255,255,255,0.2); padding: 5px 10px; border-radius: 4px;">{escape(provider.get("name", ""))}</span>'
            html += '</div>'
        
        # Compra
        buy = providers.get('buy', [])
        if buy:
            html += '<p style="margin-top: 10px;"><strong>🛒 Compra:</strong></p>'
            html += '<div style="display: flex; gap: 10px; flex-wrap: wrap;">'
            for provider in buy:
                html += f'<span style="background: rgba(255,255,255,0.2); padding: 5px 10px; border-radius: 4px;">{escape(provider.get("name", ""))}</span>'
            html += '</div>'
        
        html += '</div>'
        return html
    
    @staticmethod
    def _generate_overview(movie: Dict) -> str:
        """Gera seção de sinopse"""
        return f"""
<div class="overview" style="margin-bottom: 40px;">
    <h2 style="border-bottom: 3px solid #667eea; padding-bottom: 10px;">Sinopse</h2>
    <p style="line-height: 1.6; color: #333; font-size: 1.1em;">
        {escape(movie.get('overview', 'Sinopse indisponível'))}
    </p>
</div>
"""
    
    @staticmethod
    def _generate_cast_section(movie: Dict) -> str:
        """Gera seção de elenco"""
        cast = movie.get('cast', [])
        
        if not cast:
            return ''
        
        html = """
<div class="cast-section" style="margin-bottom: 40px;">
    <h2 style="border-bottom: 3px solid #667eea; padding-bottom: 10px;">Elenco</h2>
    <div style="display: grid; grid-template-columns: repeat(auto-fill, minmax(120px, 1fr)); gap: 20px;">
"""
        
        for actor in cast[:10]:
            html += f"""
        <div class="actor-card" style="text-align: center;">
            <img src="{actor.get('profile_path', 'https://via.placeholder.com/150')}" 
                 alt="{escape(actor.get('name'))}"
                 style="width: 100%; height: 180px; object-fit: cover; border-radius: 8px; margin-bottom: 10px;">
            <h4 style="margin: 5px 0; font-size: 0.9em;">{escape(actor.get('name', 'Desconhecido'))}</h4>
            <p style="margin: 0; font-size: 0.85em; color: #666;">{escape(actor.get('character', ''))}</p>
        </div>
"""
        
        html += """
    </div>
</div>
"""
        return html
    
    @staticmethod
    def _generate_details_table(movie: Dict) -> str:
        """Gera tabela de detalhes"""
        genres = movie.get('genres', [])
        genres_str = ', '.join(genres) if genres else 'Não informado'
        
        html = f"""
<div class="details-section" style="margin-bottom: 40px;">
    <h2 style="border-bottom: 3px solid #667eea; padding-bottom: 10px;">Detalhes</h2>
    <table style="width: 100%; border-collapse: collapse;">
        <tr style="border-bottom: 1px solid #eee;">
            <td style="padding: 10px 0; font-weight: bold; color: #333;">Gêneros</td>
            <td style="padding: 10px 0; color: #666;">{genres_str}</td>
        </tr>
        <tr style="border-bottom: 1px solid #eee;">
            <td style="padding: 10px 0; font-weight: bold; color: #333;">Data de Lançamento</td>
            <td style="padding: 10px 0; color: #666;">{movie.get('release_date', 'Desconhecida')}</td>
        </tr>
        <tr style="border-bottom: 1px solid #eee;">
            <td style="padding: 10px 0; font-weight: bold; color: #333;">Popularidade</td>
            <td style="padding: 10px 0; color: #666;">{movie.get('popularity', 0):.1f}</td>
        </tr>
"""
        
        if movie.get('imdb_id'):
            html += f"""
        <tr>
            <td style="padding: 10px 0; font-weight: bold; color: #333;">IMDb ID</td>
            <td style="padding: 10px 0; color: #666;">
                <a href="https://imdb.com/title/{movie.get('imdb_id')}" target="_blank">
                    {movie.get('imdb_id')} ↗
                </a>
            </td>
        </tr>
"""
        
        html += """
    </table>
</div>
"""
        return html


class TvPageGenerator:
    """Gerador de página para série"""
    
    @staticmethod
    def generate_tv_page(tv: Dict) -> str:
        """Gera página HTML completa para uma série"""
        
        header = f"""
<!-- TV PAGE HEADER -->
<div class="tv-header" style="
    background: linear-gradient(135deg, rgba(0,0,0,0.7), rgba(0,0,0,0.3)), 
                url('{tv.get('backdrop_url')}') center/cover;
    padding: 60px 20px;
    color: white;
    text-align: center;
">
    <h1 style="font-size: 3em; margin: 0; text-shadow: 2px 2px 8px rgba(0,0,0,0.8);">
        {escape(tv.get('title', 'Sem Título'))}
    </h1>
    <p style="font-size: 1.2em; margin: 10px 0; opacity: 0.9;">
        {tv.get('status', 'Status desconhecido')}
    </p>
</div>
"""
        
        main_content = f"""
<div class="tv-container" style="max-width: 1200px; margin: 0 auto; padding: 40px 20px;">
    <div style="display: grid; grid-template-columns: 250px 1fr; gap: 40px;">
        
        <!-- COLUNA ESQUERDA -->
        <div class="tv-sidebar">
            <img src="{tv.get('poster_url')}" alt="{escape(tv.get('title'))}" 
                 style="width: 100%; border-radius: 8px; box-shadow: 0 8px 24px rgba(0,0,0,0.3);">
            
            <div class="quick-info" style="margin-top: 20px;">
                {TvPageGenerator._generate_tv_rating(tv)}
                {TvPageGenerator._generate_tv_info(tv)}
                {TvPageGenerator._generate_watch_providers(tv)}
            </div>
        </div>
        
        <!-- COLUNA DIREITA -->
        <div class="tv-content">
            {TvPageGenerator._generate_overview(tv)}
            {TvPageGenerator._generate_cast_section(tv)}
            {TvPageGenerator._generate_tv_details_table(tv)}
        </div>
    </div>
</div>
"""
        
        return header + main_content
    
    @staticmethod
    def _generate_tv_rating(tv: Dict) -> str:
        """Rating para série"""
        rating = tv.get('rating', 0)
        stars = '⭐' * int(rating / 2)
        
        return f"""
<div class="rating-section" style="
    background: #f5f5f5;
    padding: 20px;
    border-radius: 8px;
    margin-bottom: 20px;
">
    <div style="font-size: 2em; font-weight: bold; color: #4169E1;">
        {rating}/10 {stars}
    </div>
    <p style="margin: 5px 0; color: #666; font-size: 0.9em;">
        {tv.get('vote_count', 0)} avaliações
    </p>
</div>
"""
    
    @staticmethod
    def _generate_tv_info(tv: Dict) -> str:
        """Informações da série"""
        networks = tv.get('networks', [])
        networks_str = ', '.join(networks) if networks else 'Desconhecido'
        
        html = f"""
<div class="tv-info" style="background: #f5f5f5; padding: 20px; border-radius: 8px;">
    <h3 style="margin-top: 0; color: #333;">Informações</h3>
    <p><strong>Rede(s):</strong> {networks_str}</p>
    <p><strong>Temporadas:</strong> {tv.get('total_seasons', 0)}</p>
    <p><strong>Episódios:</strong> {tv.get('total_episodes', 0)}</p>
    <p><strong>Status:</strong> {tv.get('status', 'Desconhecido')}</p>
</div>
"""
        return html
    
    @staticmethod
    def _generate_watch_providers(tv: Dict) -> str:
        """Onde assistir série"""
        providers = tv.get('watch_providers', {})
        
        if not providers:
            return '<p style="color: #666; font-size: 0.9em;">Informações de streaming indisponíveis</p>'
        
        html = """
<div class="watch-providers" style="
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    color: white;
    padding: 20px;
    border-radius: 8px;
    margin-top: 20px;
">
    <h3 style="margin-top: 0; margin-bottom: 15px;">Onde Assistir</h3>
"""
        
        stream = providers.get('stream', [])
        if stream:
            html += '<p><strong>🎬 Streaming:</strong></p>'
            html += '<div style="display: flex; gap: 10px; flex-wrap: wrap;">'
            for provider in stream:
                html += f'<span style="background: rgba(255,255,255,0.2); padding: 5px 10px; border-radius: 4px;">{escape(provider.get("name", ""))}</span>'
            html += '</div>'
        
        html += '</div>'
        return html
    
    @staticmethod
    def _generate_overview(tv: Dict) -> str:
        """Sinopse"""
        return f"""
<div class="overview" style="margin-bottom: 40px;">
    <h2 style="border-bottom: 3px solid #4169E1; padding-bottom: 10px;">Sinopse</h2>
    <p style="line-height: 1.6; color: #333; font-size: 1.1em;">
        {escape(tv.get('overview', 'Sinopse indisponível'))}
    </p>
</div>
"""
    
    @staticmethod
    def _generate_cast_section(tv: Dict) -> str:
        """Elenco"""
        cast = tv.get('cast', [])
        
        if not cast:
            return ''
        
        html = """
<div class="cast-section" style="margin-bottom: 40px;">
    <h2 style="border-bottom: 3px solid #4169E1; padding-bottom: 10px;">Elenco</h2>
    <div style="display: grid; grid-template-columns: repeat(auto-fill, minmax(120px, 1fr)); gap: 20px;">
"""
        
        for actor in cast[:10]:
            html += f"""
        <div class="actor-card" style="text-align: center;">
            <img src="{actor.get('profile_path', 'https://via.placeholder.com/150')}" 
                 alt="{escape(actor.get('name'))}"
                 style="width: 100%; height: 180px; object-fit: cover; border-radius: 8px; margin-bottom: 10px;">
            <h4 style="margin: 5px 0; font-size: 0.9em;">{escape(actor.get('name', 'Desconhecido'))}</h4>
            <p style="margin: 0; font-size: 0.85em; color: #666;">{escape(actor.get('character', ''))}</p>
        </div>
"""
        
        html += """
    </div>
</div>
"""
        return html
    
    @staticmethod
    def _generate_tv_details_table(tv: Dict) -> str:
        """Detalhes da série"""
        genres = tv.get('genres', [])
        genres_str = ', '.join(genres) if genres else 'Não informado'
        
        html = f"""
<div class="details-section" style="margin-bottom: 40px;">
    <h2 style="border-bottom: 3px solid #4169E1; padding-bottom: 10px;">Detalhes</h2>
    <table style="width: 100%; border-collapse: collapse;">
        <tr style="border-bottom: 1px solid #eee;">
            <td style="padding: 10px 0; font-weight: bold; color: #333;">Gêneros</td>
            <td style="padding: 10px 0; color: #666;">{genres_str}</td>
        </tr>
        <tr style="border-bottom: 1px solid #eee;">
            <td style="padding: 10px 0; font-weight: bold; color: #333;">Primeira Exibição</td>
            <td style="padding: 10px 0; color: #666;">{tv.get('first_air_date', 'Desconhecida')}</td>
        </tr>
        <tr style="border-bottom: 1px solid #eee;">
            <td style="padding: 10px 0; font-weight: bold; color: #333;">Última Exibição</td>
            <td style="padding: 10px 0; color: #666;">{tv.get('last_air_date', '-')}</td>
        </tr>
        <tr style="border-bottom: 1px solid #eee;">
            <td style="padding: 10px 0; font-weight: bold; color: #333;">Popularidade</td>
            <td style="padding: 10px 0; color: #666;">{tv.get('popularity', 0):.1f}</td>
        </tr>
    </table>
</div>
"""
        return html
