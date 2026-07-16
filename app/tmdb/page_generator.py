"""app/tmdb/page_generator.py — Gerador de páginas HTML para filmes e séries"""
from __future__ import annotations
import json
import logging
from html import escape
from pathlib import Path
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).parent.parent.parent / "templates" / "tmdb"


def _load_template(name: str) -> str:
    path = _TEMPLATES_DIR / name
    if path.exists():
        return path.read_text(encoding="utf-8")
    logger.warning("[TMDB/pagegen] Template não encontrado: %s — usando fallback inline", name)
    return ""


def _parse_json_field(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return []
    return value or []


def _render(template: str, ctx: Dict[str, str]) -> str:
    """Substitui {{chave}} pelo valor no template."""
    result = template
    for key, value in ctx.items():
        result = result.replace("{{" + key + "}}", str(value or ""))
    return result


def generate_movie_page(movie: Dict[str, Any]) -> str:
    """Gera HTML completo de página de filme."""
    tmpl = _load_template("movie_page.html")

    genres = _parse_json_field(movie.get("genres", []))
    cast = _parse_json_field(movie.get("cast", []))
    watch_providers = _parse_json_field(movie.get("watch_providers", {}))

    genre_str = ", ".join(escape(str(g)) for g in genres) if genres else "—"
    cast_str = ", ".join(
        escape(str(a.get("name", a) if isinstance(a, dict) else a)) for a in cast[:8]
    )

    # Watch providers Brasil
    providers_br = (watch_providers.get("results", watch_providers) or {}).get("BR", {})
    stream = providers_br.get("flatrate", [])
    stream_str = ", ".join(escape(str(p.get("provider_name", ""))) for p in stream[:6]) if stream else "—"

    runtime = movie.get("runtime", 0) or 0
    h, m = divmod(int(runtime), 60)
    runtime_str = f"{h}h {m:02d}m" if h else (f"{m}m" if m else "—")

    rating = float(movie.get("rating", 0) or 0)
    trailer_url = movie.get("trailer_url", "")
    yt_embed = ""
    if trailer_url and "youtu" in trailer_url:
        yt_id = trailer_url.split("v=")[-1].split("&")[0] if "v=" in trailer_url else trailer_url.split("/")[-1].split("?")[0]
        yt_embed = f'<iframe src="https://www.youtube.com/embed/{yt_id}" width="100%" height="380" frameborder="0" allowfullscreen loading="lazy" title="Trailer {escape(str(movie.get("title", "")))}"></iframe>'

    ctx = {
        "title": escape(str(movie.get("title", ""))),
        "overview": escape(str(movie.get("overview", "") or "")),
        "rating": f"{rating:.1f}",
        "vote_count": f"{int(movie.get('vote_count', 0) or 0):,}",
        "release_date": str(movie.get("release_date", "") or ""),
        "runtime": runtime_str,
        "director": escape(str(movie.get("director", "") or "")),
        "genres": genre_str,
        "cast": cast_str,
        "poster_url": str(movie.get("poster_url", "") or ""),
        "backdrop_url": str(movie.get("backdrop_url", "") or ""),
        "imdb_id": str(movie.get("imdb_id", "") or ""),
        "streaming": stream_str,
        "trailer_embed": yt_embed,
        "tmdb_id": str(movie.get("tmdb_id", "")),
        "budget": f"${int(movie.get('budget', 0) or 0):,}" if movie.get("budget") else "—",
        "revenue": f"${int(movie.get('revenue', 0) or 0):,}" if movie.get("revenue") else "—",
    }

    if tmpl:
        return _render(tmpl, ctx)
    return _fallback_movie_page(ctx)


def generate_tv_page(tv: Dict[str, Any]) -> str:
    """Gera HTML completo de página de série."""
    tmpl = _load_template("tv_page.html")

    genres = _parse_json_field(tv.get("genres", []))
    cast = _parse_json_field(tv.get("cast", []))
    networks = _parse_json_field(tv.get("networks_json") or tv.get("networks", []))
    creators = _parse_json_field(tv.get("creators_json") or tv.get("creators", []))
    watch_providers = _parse_json_field(tv.get("watch_providers_json") or tv.get("watch_providers", {}))

    providers_br = (watch_providers.get("results", watch_providers) or {}).get("BR", {})
    stream = providers_br.get("flatrate", [])

    rating = float(tv.get("rating", 0) or 0)
    trailer_url = tv.get("trailer_url", "")
    yt_embed = ""
    if trailer_url and "youtu" in trailer_url:
        yt_id = trailer_url.split("v=")[-1].split("&")[0] if "v=" in trailer_url else trailer_url.split("/")[-1].split("?")[0]
        yt_embed = f'<iframe src="https://www.youtube.com/embed/{yt_id}" width="100%" height="380" frameborder="0" allowfullscreen loading="lazy" title="Trailer {escape(str(tv.get("title", "")))}"></iframe>'

    ctx = {
        "title": escape(str(tv.get("title", ""))),
        "overview": escape(str(tv.get("overview", "") or "")),
        "rating": f"{rating:.1f}",
        "vote_count": f"{int(tv.get('vote_count', 0) or 0):,}",
        "first_air_date": str(tv.get("first_air_date", "") or ""),
        "last_air_date": str(tv.get("last_air_date", "") or ""),
        "status": escape(str(tv.get("status", "") or "")),
        "total_seasons": str(tv.get("total_seasons", 0) or 0),
        "total_episodes": str(tv.get("total_episodes", 0) or 0),
        "networks": ", ".join(escape(str(n)) for n in networks[:4]) if networks else "—",
        "creators": ", ".join(escape(str(c)) for c in creators[:3]) if creators else "—",
        "genres": ", ".join(escape(str(g)) for g in genres) if genres else "—",
        "cast": ", ".join(escape(str(a.get("name", a) if isinstance(a, dict) else a)) for a in cast[:8]),
        "poster_url": str(tv.get("poster_url", "") or ""),
        "backdrop_url": str(tv.get("backdrop_url", "") or ""),
        "streaming": ", ".join(escape(str(p.get("provider_name", ""))) for p in stream[:6]) if stream else "—",
        "trailer_embed": yt_embed,
        "tmdb_id": str(tv.get("tmdb_id", "")),
    }

    if tmpl:
        return _render(tmpl, ctx)
    return _fallback_tv_page(ctx)


def _fallback_movie_page(ctx: Dict) -> str:
    """HTML mínimo quando o template não existe."""
    return f"""<!-- mn-movie-page -->
<article class="mn-movie-hub" itemscope itemtype="https://schema.org/Movie">
  {'<img src="' + ctx["backdrop_url"] + '" alt="' + ctx["title"] + '" style="width:100%;max-height:400px;object-fit:cover;border-radius:12px" loading="lazy">' if ctx.get("backdrop_url") else ""}
  <h1 itemprop="name">{ctx["title"]}</h1>
  <p><strong>Avaliação TMDB:</strong> <span itemprop="aggregateRating">{ctx["rating"]}/10</span> ({ctx["vote_count"]} votos)</p>
  <p><strong>Lançamento:</strong> <span itemprop="datePublished">{ctx["release_date"]}</span></p>
  <p><strong>Duração:</strong> {ctx["runtime"]}</p>
  <p><strong>Direção:</strong> {ctx["director"]}</p>
  <p><strong>Gêneros:</strong> {ctx["genres"]}</p>
  <p><strong>Elenco:</strong> {ctx["cast"]}</p>
  <p><strong>Onde assistir (BR):</strong> {ctx["streaming"]}</p>
  <h2>Sinopse</h2>
  <p itemprop="description">{ctx["overview"]}</p>
  {ctx["trailer_embed"]}
  {"<p><a href='https://www.imdb.com/title/" + ctx["imdb_id"] + "' target='_blank' rel='noopener'>Ver no IMDb</a></p>" if ctx.get("imdb_id") else ""}
  <p style="font-size:.75em;color:#999">Dados: <a href="https://www.themoviedb.org" target="_blank" rel="noopener">TMDB</a></p>
</article>"""


def _fallback_tv_page(ctx: Dict) -> str:
    return f"""<!-- mn-tv-page -->
<article class="mn-tv-hub" itemscope itemtype="https://schema.org/TVSeries">
  {'<img src="' + ctx["backdrop_url"] + '" alt="' + ctx["title"] + '" style="width:100%;max-height:400px;object-fit:cover;border-radius:12px" loading="lazy">' if ctx.get("backdrop_url") else ""}
  <h1 itemprop="name">{ctx["title"]}</h1>
  <p><strong>Avaliação TMDB:</strong> {ctx["rating"]}/10 ({ctx["vote_count"]} votos)</p>
  <p><strong>Status:</strong> {ctx["status"]}</p>
  <p><strong>Temporadas:</strong> {ctx["total_seasons"]} | <strong>Episódios:</strong> {ctx["total_episodes"]}</p>
  <p><strong>Redes:</strong> {ctx["networks"]}</p>
  <p><strong>Criadores:</strong> {ctx["creators"]}</p>
  <p><strong>Gêneros:</strong> {ctx["genres"]}</p>
  <p><strong>Elenco:</strong> {ctx["cast"]}</p>
  <p><strong>Onde assistir (BR):</strong> {ctx["streaming"]}</p>
  <h2>Sinopse</h2>
  <p itemprop="description">{ctx["overview"]}</p>
  {ctx["trailer_embed"]}
  <p style="font-size:.75em;color:#999">Dados: <a href="https://www.themoviedb.org" target="_blank" rel="noopener">TMDB</a></p>
</article>"""
