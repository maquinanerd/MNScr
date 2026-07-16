"""
Adapter: transforma item do RSSPRIME Superfeed em objeto compatível com o pipeline MN26.
"""

def is_superfeed_item(entry: dict) -> bool:
    """Retorna True se o item carrega metadados sf:* de cluster."""
    return bool(entry.get("is_cluster") and entry.get("event_key"))


def normalize_cluster_item(entry: dict) -> dict:
    """
    Garante que um item de cluster tenha todos os campos esperados
    pelo pipeline, com valores padrão seguros.
    """
    return {
        "id":              entry.get("id"),
        "db_id":           entry.get("db_id"),
        "url":             entry.get("url", ""),
        "title":           entry.get("title", ""),
        "published":       entry.get("published"),
        "summary":         entry.get("summary", ""),
        "source_id":       entry.get("source_id", ""),
        "is_cluster":      True,
        "event_key":       entry["event_key"],
        "primary_source":  entry.get("primary_source", ""),
        "all_sources":     entry.get("all_sources", []),
        "cluster_size":    entry.get("cluster_size", 1),
        "additional_urls": entry.get("additional_urls", []),
        "topic":           entry.get("topic", ""),
    }
