from bs4 import BeautifulSoup

def clean_html_for_globo_esporte(soup: BeautifulSoup) -> BeautifulSoup:
    """
    Limpa o HTML de uma página do Globo Esporte, removendo elementos indesejados
    antes da extração principal de conteúdo.
    """
    # Remove os players de vídeo, que contêm imagens de thumbnail
    video_players = soup.find_all('div', class_='video-player')
    for player in video_players:
        player.decompose()
        
    return soup

def clean_html_for_lance(soup: BeautifulSoup) -> BeautifulSoup:
    """
    Limpa o HTML de uma página do Lance!, removendo elementos indesejados
    antes da extração principal de conteúdo.
    """
    # Remove o SVG de carregamento que às vezes é incluído como uma imagem
    for figure in soup.find_all('figure'):
        if figure.find('img', src=lambda s: s and 'dotsInCircle.svg' in s):
            figure.decompose()
            
    # Remove iframes de publicidade ou outros embeds não relacionados a vídeo
    for iframe in soup.find_all('iframe'):
        if 'youtube.com' not in iframe.get('src', ''):
            iframe.decompose()
            
    return soup