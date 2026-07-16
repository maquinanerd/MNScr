"""
cluster_engine.py — Event Scoring + Cluster Decision

Decide se uma notícia merece gerar páginas evergreen adicionais.
Custo adicional: zero (análise local, sem chamada de API).
"""
import logging

logger = logging.getLogger(__name__)

HIGH_VALUE_ENTITIES = {
    # Marvel / DC / Superhérois
    "marvel", "avengers", "spider-man", "batman", "superman",
    "dc comics", "x-men", "thor", "iron man", "deadpool",
    "justice league", "flash", "aquaman", "pantera negra",
    "homem-aranha", "vingadores", "liga da justiça",
    # Star Wars / Disney
    "star wars", "the mandalorian", "andor", "ahsoka", "jedi",
    "guerra nas estrelas",
    # Games
    "gta", "grand theft auto", "the witcher", "zelda",
    "elden ring", "call of duty", "god of war", "cyberpunk",
    "resident evil", "final fantasy", "hogwarts legacy",
    "mortal kombat", "assassin's creed", "red dead redemption",
    # Séries
    "game of thrones", "the last of us", "stranger things",
    "the boys", "house of the dragon", "the witcher",
    "wednesday", "euphoria", "succession", "the bear",
    "andor", "obi-wan kenobi", "loki",
    # Filmes / Franquias
    "harry potter", "senhor dos anéis", "indiana jones",
    "missão impossível", "john wick", "oppenheimer",
    "barbie", "duna", "dune", "avatar", "jurassic",
    "transformers", "velozes e furiosos",
    # Anime
    "one piece", "dragon ball", "naruto", "attack on titan",
    "demon slayer", "jujutsu kaisen", "my hero academia",
    "fullmetal alchemist", "death note", "bleach",
    "chainsaw man", "spy x family",
}

EVERGREEN_TEMPLATES = {
    "timeline":         "timeline e ordem cronológica completa",
    "cast_guide":       "elenco completo e guia de personagens",
    "villains":         "vilões explicados e ranqueados",
    "ending_explained": "final explicado",
    "easter_eggs":      "easter eggs e referências escondidas",
    "powers":           "poderes e habilidades explicados",
}

HIGH_DEMAND_TAGS = {
    "trailer", "confirmado", "estreia", "temporada",
    "sequel", "remake", "reboot", "confirmed", "announced",
    "teaser", "data de lançamento", "lançamento", "renovado",
    "cancelado", "renewed", "cancelled", "release date",
}


def score_event(article_data: dict) -> dict:
    """
    Calcula score do evento para decisão de cluster evergreen.

    Args:
        article_data: dict com title, content (str), tags (list[str]),
                      categories (list), source_count (int, opcional)

    Returns:
        dict com: score, should_cluster (bool), entity (str|None),
                  templates (list[str]), reason (str)
    """
    haystack = (
        article_data.get("title", "") + " " +
        article_data.get("content", "")[:500]
    ).lower()

    score = 0
    entity = None
    reasons = []

    # Critério 1 — Entidade de alta popularidade (+30)
    for ent in HIGH_VALUE_ENTITIES:
        if ent in haystack:
            score += 30
            entity = ent
            reasons.append(f"entidade={ent}")
            break

    # Critério 2 — Tags de alta demanda (+25)
    tags_lower = {t.lower() for t in article_data.get("tags", [])}
    if tags_lower & HIGH_DEMAND_TAGS:
        score += 25
        reasons.append("tag_demanda_alta")

    # Critério 3 — Templates evergreen disponíveis (+20)
    templates = list(EVERGREEN_TEMPLATES.keys()) if entity else []
    if templates:
        score += 20
        reasons.append(f"{len(templates)}_templates")

    # Critério 4 — Profundidade narrativa (+15)
    depth_kw = ["saga", "universe", "universo", "temporada", "season",
                "franquia", "franchise", "trilogy", "trilogia"]
    if entity and any(kw in haystack for kw in depth_kw):
        score += 15
        reasons.append("franchise_depth")

    # Critério 5 — Cobertura multi-fonte (+10)
    if article_data.get("source_count", 1) > 1:
        score += 10
        reasons.append("multi_source")

    should_cluster = score >= 60 and entity is not None

    logger.info(
        f"[SCORE] {article_data.get('title', '')[:45]} | "
        f"score={score} entity={entity} cluster={should_cluster}"
    )

    return {
        "score": score,
        "should_cluster": should_cluster,
        "entity": entity,
        "templates": templates,
        "reason": " | ".join(reasons) or "sem_entidade",
    }
