"""app/tmdb/enricher.py — TMDbEnricher com matching por score de confiança"""
from __future__ import annotations
import re
import json
import logging
from html import escape
from typing import Optional, Dict, List, Tuple, Any

from app.tmdb.config import (
    TMDB_ENABLED, TMDB_ARTICLE_ENRICHMENT,
    TMDB_MAX_ARTICLE_BLOCKS, TMDB_MIN_MATCH_CONFIDENCE,
)
from app.tmdb.matching import (
    is_media_related_title, is_media_related_category,
    is_generic_title, pick_best_match, extract_year_from_text,
)

logger = logging.getLogger(__name__)

# Regex para extrair títulos entre aspas ou em negrito
_TITLE_RE = re.compile(
    r'(?:'
    r'"([^"]{3,80})"'
    r'|\u201c([^\u201d]{3,80})\u201d'
    r"|'([^']{3,80})'"
    r'|<strong>([^<]{3,80})</strong>'
    r'|<em>([^<]{3,80})</em>'
    r')',
    re.IGNORECASE,
)

# Categorias elegíveis
_ELIGIBLE_CATEGORIES = {"Filmes", "Séries", "filmes", "séries", "movies", "tv"}


def _extract_candidate_titles(html: str, article_title: str) -> List[str]:
    """Extrai candidatos de título do HTML + título do artigo."""
    seen, candidates = set(), []

    def _add(t: str) -> None:
        t = t.strip()
        if t and t not in seen and not is_generic_title(t) and len(t) >= 3:
            seen.add(t)
            candidates.append(t)

    _add(article_title)  # título do artigo tem prioridade máxima
    for m in _TITLE_RE.finditer(html):
        for g in m.groups():
            if g:
                _add(g)
    return candidates


def _build_stars(rating: float) -> str:
    filled = min(10, max(0, round(rating)))
    return f'<span style="color:#F5C518">{"★" * filled}{"☆" * (10 - filled)}</span>'


def _format_runtime(minutes: int) -> str:
    if not minutes:
        return ""
    h, m = divmod(int(minutes), 60)
    return f"{h}h {m:02d}m" if h else f"{m}m"


def build_info_block(media_data: Dict[str, Any], is_tv: bool = False) -> str:
    """Gera bloco HTML editorial com dados TMDB."""
    title = escape(media_data.get("title", ""))
    overview = escape(str(media_data.get("overview", "") or ""))[:350]
    rating = float(media_data.get("rating", 0) or 0)
    vote_count = int(media_data.get("vote_count", 0) or 0)
    poster_url = media_data.get("poster_url", "")
    trailer_url = media_data.get("trailer_url", "")
    director = escape(str(media_data.get("director", "") or ""))
    genres_raw = media_data.get("genres", [])
    if isinstance(genres_raw, str):
        try:
            genres_raw = json.loads(genres_raw)
        except Exception:
            genres_raw = []
    genres = [escape(str(g)) for g in genres_raw[:4]] if genres_raw else []
    cast_raw = media_data.get("cast", [])
    if isinstance(cast_raw, str):
        try:
            cast_raw = json.loads(cast_raw)
        except Exception:
            cast_raw = []
    cast_names = [escape(str(a.get("name", a) if isinstance(a, dict) else a)) for a in cast_raw[:5]]
    release = str(media_data.get("release_date") or media_data.get("first_air_date", "") or "")[:4]
    runtime = _format_runtime(int(media_data.get("runtime", 0) or 0))
    imdb_id = media_data.get("imdb_id", "")

    # Watch providers
    wp_raw = media_data.get("watch_providers", {})
    if isinstance(wp_raw, str):
        try:
            wp_raw = json.loads(wp_raw)
        except Exception:
            wp_raw = {}
    providers_br = (wp_raw.get("results", wp_raw) or {}).get("BR", {})
    stream = providers_br.get("flatrate", [])

    accent = "#4169E1" if is_tv else "#E50914"
    icon = "📺" if is_tv else "🎬"
    label = "Série" if is_tv else "Filme"

    # Providers HTML
    prov_html = ""
    if stream:
        logos = ""
        for p in stream[:6]:
            lp = p.get("logo_path", "")
            pn = escape(p.get("provider_name", ""))
            if lp:
                logos += f'<img src="https://image.tmdb.org/t/p/w92{lp}" alt="{pn}" title="{pn}" style="height:26px;border-radius:5px;border:1px solid rgba(255,255,255,0.15)" loading="lazy"> '
            else:
                logos += f'<span style="background:rgba(255,255,255,0.12);padding:2px 8px;border-radius:4px;font-size:.72em">{pn}</span> '
        prov_html = f'<div style="margin-top:10px"><p style="margin:0 0 5px;font-size:.75em;color:#aaa;text-transform:uppercase;letter-spacing:.5px">Onde assistir</p><div style="display:flex;flex-wrap:wrap;gap:6px;align-items:center">{logos}</div></div>'

    # Trailer embed
    trailer_html = ""
    if trailer_url:
        yt_id = ""
        if "v=" in trailer_url:
            yt_id = trailer_url.split("v=")[-1].split("&")[0]
        elif "youtu.be/" in trailer_url:
            yt_id = trailer_url.split("youtu.be/")[-1].split("?")[0]
        if yt_id:
            trailer_html = (
                f'<div style="margin-top:14px">'
                f'<div style="position:relative;padding-bottom:56.25%;height:0;border-radius:8px;overflow:hidden">'
                f'<iframe src="https://www.youtube.com/embed/{yt_id}" style="position:absolute;top:0;left:0;width:100%;height:100%;border:0" loading="lazy" allowfullscreen title="Trailer {title}"></iframe>'
                f'</div></div>'
            )

    genres_html = ""
    if genres:
        tags = "".join(f'<span style="background:rgba(255,255,255,0.1);padding:2px 8px;border-radius:12px;font-size:.72em">{g}</span>' for g in genres)
        genres_html = f'<div style="display:flex;flex-wrap:wrap;gap:4px;margin-top:6px">{tags}</div>'

    imdb_link = ""
    if imdb_id:
        imdb_link = f'<a href="https://www.imdb.com/title/{imdb_id}" target="_blank" rel="noopener" style="display:inline-block;margin-top:10px;background:#F5C518;color:#000;padding:3px 10px;border-radius:4px;font-size:.72em;font-weight:700;text-decoration:none">IMDb ↗</a>'

    return f"""
<!-- TMDB_ENRICHMENT_BLOCK -->
<div class="mn-tmdb-block" style="background:linear-gradient(135deg,#0d1117,#161b22);border:1px solid rgba(255,255,255,.08);border-left:4px solid {accent};border-radius:12px;padding:20px;margin:32px 0;color:#e6edf3;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;box-shadow:0 4px 24px rgba(0,0,0,.4)">
  <div style="display:flex;align-items:center;gap:8px;margin-bottom:14px">
    <span style="background:{accent};color:#fff;padding:3px 10px;border-radius:12px;font-size:.7em;font-weight:700;text-transform:uppercase;letter-spacing:.5px">{icon} {label}</span>
    <span style="color:#6e7681;font-size:.72em">via TMDB</span>
  </div>
  <div style="display:flex;gap:18px;align-items:flex-start">
    {'<img src="' + poster_url + '" alt="Pôster ' + title + '" style="width:90px;border-radius:8px;flex-shrink:0;box-shadow:0 4px 12px rgba(0,0,0,.5)" loading="lazy">' if poster_url else ''}
    <div style="flex:1;min-width:0">
      <h3 style="margin:0 0 6px;font-size:1.05em;color:#fff">{title}</h3>
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">{_build_stars(rating)} <span style="font-size:.9em;font-weight:600;color:#F5C518">{rating:.1f}</span> <span style="font-size:.72em;color:#6e7681">({vote_count:,} votos)</span></div>
      <div style="display:flex;flex-wrap:wrap;gap:10px;font-size:.78em;color:#8b949e;margin-bottom:6px">
        {'<span>📅 ' + release + '</span>' if release else ''}
        {'<span>⏱ ' + runtime + '</span>' if runtime else ''}
        {'<span>🎬 ' + director + '</span>' if director else ''}
      </div>
      {genres_html}
      {'<p style="margin:8px 0 0;font-size:.83em;line-height:1.5;color:#8b949e">' + overview + ('…' if len(overview) >= 350 else '') + '</p>' if overview else ''}
      {'<p style="margin:6px 0 0;font-size:.78em;color:#8b949e"><strong style="color:#ccc">Elenco:</strong> ' + ', '.join(cast_names) + '</p>' if cast_names else ''}
      {prov_html}
      {imdb_link}
    </div>
  </div>
  {trailer_html}
  <p style="margin:10px 0 0;font-size:.68em;color:#484f58;text-align:right">Dados: <a href="https://www.themoviedb.org" target="_blank" rel="noopener" style="color:#0d7adf">The Movie Database (TMDB)</a></p>
</div>
<!-- END TMDB_ENRICHMENT_BLOCK -->"""


class TMDbEnricher:
    """Enriquecedor de artigos com dados TMDB. Usa matching por score de confiança."""

    def __init__(self) -> None:
        from app.tmdb.client import get_client
        self._client = get_client()
        self._cache: Dict[str, Tuple[Optional[Dict], str]] = {}

    @property
    def available(self) -> bool:
        return TMDB_ENABLED and TMDB_ARTICLE_ENRICHMENT and self._client is not None

    def enrich_article(self, content_html: str, title: str, category: str = "") -> Tuple[str, List[str]]:
        """
        Enriquece artigo com bloco TMDB se houver match confiante.
        Retorna (html_enriquecido, títulos_encontrados).
        """
        if not self.available:
            return content_html, []

        is_eligible = is_media_related_category(category) or is_media_related_title(title)
        if not is_eligible:
            logger.debug("[TMDB/enricher] Artigo não elegível: %s", title[:60])
            return content_html, []

        candidates = _extract_candidate_titles(content_html, title)
        year = extract_year_from_text(title + " " + content_html[:500])

        enriched_blocks, enriched_titles, seen_ids = [], [], set()

        for candidate in candidates:
            if len(enriched_blocks) >= TMDB_MAX_ARTICLE_BLOCKS:
                break
            media_data, media_type, confidence = self._lookup(candidate, year)
            if not media_data:
                continue
            tmdb_id = media_data.get("tmdb_id")
            if tmdb_id in seen_ids:
                continue
            seen_ids.add(tmdb_id)
            enriched_blocks.append(build_info_block(media_data, is_tv=(media_type == "tv")))
            enriched_titles.append(media_data.get("title", candidate))
            logger.info("[TMDB/enricher] Match: '%s' → '%s' (conf=%.2f type=%s)",
                        candidate[:50], media_data.get("title", ""), confidence, media_type)

        if not enriched_blocks:
            return content_html, []

        injection = "\n".join(enriched_blocks)
        credit_marker = "<!-- SOURCE_CREDIT_BLOCK -->"
        if credit_marker in content_html:
            content_html = content_html.replace(credit_marker, injection + "\n" + credit_marker, 1)
        else:
            content_html += "\n" + injection

        return content_html, enriched_titles

    def _lookup(self, title: str, year: Optional[int] = None) -> Tuple[Optional[Dict], str, float]:
        """Busca no TMDB com cache. Retorna (dados, tipo, confiança)."""
        cache_key = f"{title.lower().strip()}:{year}"
        if cache_key in self._cache:
            cached = self._cache[cache_key]
            return cached[0], cached[1], cached[2] if len(cached) > 2 else 0.0

        if not self._client or is_generic_title(title):
            self._cache[cache_key] = (None, "", 0.0)
            return None, "", 0.0

        result = self._search(title, year)
        self._cache[cache_key] = result
        return result

    def _search(self, title: str, year: Optional[int]) -> Tuple[Optional[Dict], str, float]:
        """Busca filmes → séries com score de confiança."""
        try:
            movies = self._client.search_movie(title, year)
            best, score, _ = pick_best_match(title, movies, TMDB_MIN_MATCH_CONFIDENCE)
            if best:
                details = self._client.get_movie_details(best["id"])
                if details:
                    return self._client.format_movie_data(details), "movie", score

            shows = self._client.search_tv(title)
            best, score, _ = pick_best_match(title, shows, TMDB_MIN_MATCH_CONFIDENCE, title_field="name")
            if best:
                details = self._client.get_tv_details(best["id"])
                if details:
                    return self._client.format_tv_data(details), "tv", score

        except Exception as exc:
            logger.warning("[TMDB/enricher] Erro na busca '%s': %s", title[:50], exc)

        return None, "", 0.0


# Singleton
_enricher: Optional[TMDbEnricher] = None

def get_enricher() -> Optional[TMDbEnricher]:
    global _enricher
    if _enricher is None:
        _enricher = TMDbEnricher()
    return _enricher


def enrich_article_with_tmdb(content_html: str, title: str, category: str = "") -> Tuple[str, List[str]]:
    """Função pública de alto nível. Safe mesmo com TMDB desativado."""
    if not TMDB_ENABLED or not TMDB_ARTICLE_ENRICHMENT:
        return content_html, []
    try:
        enricher = get_enricher()
        if enricher and enricher.available:
            return enricher.enrich_article(content_html, title, category)
    except Exception as exc:
        logger.warning("[TMDB/enricher] Falha não-fatal: %s", exc)
    return content_html, []
