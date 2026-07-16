"""app/tmdb/widgets.py — Widgets HTML reutilizáveis para o hub TMDB"""
from __future__ import annotations
import json
import logging
from html import escape
from typing import List, Dict, Any

logger = logging.getLogger(__name__)


def _poster_card(item: Dict, link: str = "#", accent: str = "#E50914") -> str:
    title = escape(str(item.get("title", "")))
    poster = item.get("poster_url", "")
    rating = float(item.get("rating", 0) or 0)
    year = str(item.get("release_date") or item.get("first_air_date") or "")[:4]
    return f"""<div style="border-radius:10px;overflow:hidden;box-shadow:0 4px 16px rgba(0,0,0,.3);transition:transform .25s" onmouseover="this.style.transform='scale(1.04)'" onmouseout="this.style.transform='scale(1)'">
  {'<img src="' + poster + '" alt="' + title + '" style="width:100%;height:240px;object-fit:cover;display:block" loading="lazy">' if poster else '<div style="width:100%;height:240px;background:#1c1c1c;display:flex;align-items:center;justify-content:center;color:#666">Sem poster</div>'}
  <div style="padding:12px;background:#161b22">
    <p style="margin:0 0 4px;font-size:.88em;font-weight:600;color:#e6edf3;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{title}</p>
    <div style="display:flex;justify-content:space-between;font-size:.75em;color:#6e7681">
      <span style="color:#F5C518">⭐ {rating:.1f}</span>
      <span>{year}</span>
    </div>
  </div>
</div>"""


def _grid(cards: List[str], cols: int = 5) -> str:
    inner = "\n".join(cards)
    return f'<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:16px">{inner}</div>'


def trending_movies_widget(movies: List[Dict], limit: int = 10) -> str:
    if not movies:
        return ""
    cards = [_poster_card(m) for m in movies[:limit]]
    return f"""<div class="mn-trending-movies" style="margin:32px 0">
  <h2 style="font-size:1.3em;border-bottom:3px solid #E50914;padding-bottom:8px;margin-bottom:20px;color:#fff">🔥 Filmes em Alta</h2>
  {_grid(cards)}
</div>"""


def trending_tv_widget(shows: List[Dict], limit: int = 10) -> str:
    if not shows:
        return ""
    cards = [_poster_card(s, accent="#4169E1") for s in shows[:limit]]
    return f"""<div class="mn-trending-tv" style="margin:32px 0">
  <h2 style="font-size:1.3em;border-bottom:3px solid #4169E1;padding-bottom:8px;margin-bottom:20px;color:#fff">📺 Séries em Alta</h2>
  {_grid(cards)}
</div>"""


def upcoming_movies_widget(movies: List[Dict], limit: int = 8) -> str:
    if not movies:
        return ""
    rows = ""
    for m in movies[:limit]:
        title = escape(str(m.get("title", "")))
        date = str(m.get("release_date", ""))
        poster = m.get("poster_url", "")
        overview = escape(str(m.get("overview", "") or ""))[:120]
        rows += f"""<div style="display:flex;gap:14px;align-items:center;padding:12px 0;border-bottom:1px solid rgba(255,255,255,.06)">
  {'<img src="' + poster + '" alt="' + title + '" style="width:50px;height:75px;object-fit:cover;border-radius:6px;flex-shrink:0" loading="lazy">' if poster else ''}
  <div>
    <p style="margin:0 0 3px;font-weight:600;color:#e6edf3">{title}</p>
    <p style="margin:0 0 4px;font-size:.78em;color:#F5C518">🗓 {date}</p>
    <p style="margin:0;font-size:.78em;color:#8b949e">{overview}{"…" if len(overview) >= 120 else ""}</p>
  </div>
</div>"""
    return f"""<div class="mn-upcoming" style="background:#0d1117;border:1px solid rgba(255,255,255,.08);border-radius:12px;padding:20px;margin:32px 0">
  <h2 style="font-size:1.2em;margin:0 0 16px;color:#fff">🗓 Próximos Lançamentos</h2>
  {rows}
</div>"""


def watch_providers_widget(providers_data: Dict, title: str = "") -> str:
    """Gera bloco 'Onde assistir' a partir de dados TMDB watch_providers."""
    if isinstance(providers_data, str):
        try:
            providers_data = json.loads(providers_data)
        except Exception:
            return ""
    br = (providers_data.get("results", providers_data) or {}).get("BR", {})
    stream = br.get("flatrate", [])
    rent = br.get("rent", [])
    buy = br.get("buy", [])
    if not (stream or rent or buy):
        return ""

    def _prov_row(label: str, items: List[Dict]) -> str:
        if not items:
            return ""
        logos = ""
        for p in items[:6]:
            lp = p.get("logo_path", "")
            pn = escape(p.get("provider_name", ""))
            if lp:
                logos += f'<img src="https://image.tmdb.org/t/p/w92{lp}" alt="{pn}" title="{pn}" style="height:30px;border-radius:6px" loading="lazy"> '
        return f'<div style="margin-bottom:10px"><p style="margin:0 0 6px;font-size:.78em;color:#aaa;text-transform:uppercase;letter-spacing:.5px">{label}</p><div style="display:flex;flex-wrap:wrap;gap:8px;align-items:center">{logos}</div></div>'

    heading = f"Onde assistir{' ' + escape(title) if title else ''}"
    return f"""<div class="mn-watch-providers" style="background:linear-gradient(135deg,#667eea,#764ba2);color:#fff;border-radius:12px;padding:20px;margin:24px 0">
  <h3 style="margin:0 0 16px;font-size:1.1em">{heading}</h3>
  {_prov_row("🎬 Streaming", stream)}
  {_prov_row("🎫 Aluguel", rent)}
  {_prov_row("🛒 Compra", buy)}
  <p style="margin:10px 0 0;font-size:.68em;opacity:.7">Dados: <a href="https://www.themoviedb.org" target="_blank" rel="noopener" style="color:#fff">TMDB</a> via JustWatch</p>
</div>"""


def genre_related_widget(movies: List[Dict], genre: str = "", limit: int = 6) -> str:
    """Widget de filmes relacionados por gênero."""
    if not movies:
        return ""
    heading = f"Mais {escape(genre)}" if genre else "Relacionados"
    cards = [_poster_card(m) for m in movies[:limit]]
    return f"""<div class="mn-related-genre" style="margin:32px 0">
  <h3 style="font-size:1.1em;border-bottom:2px solid #E50914;padding-bottom:6px;margin-bottom:16px;color:#333">🎬 {heading}</h3>
  {_grid(cards, cols=6)}
</div>"""
