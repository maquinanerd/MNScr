"""
Adapter: transforma item do RSS Prime Superfeed em objeto compatível com o pipeline MNScr.
"""

def is_superfeed_item(entry: dict) -> bool:
    """Item do superfeed com agrupamento editorial de origem.

    O criterio e "veio do superfeed E tem `event_key`" — nao "e cacho".

    A distincao custou caro: enquanto isto exigia `is_cluster`, todo item de
    fonte unica caia no caminho antigo, o de antes do Cinerie existir, quando o
    destino era o WordPress e ninguem precisava de `event_key`. Nesse caminho o
    `event_key` some do `art_data` reconstruido — e a materia morre em
    `normalize_cluster_id` DEPOIS de a IA ja ter escrito o texto inteiro.

    `is_cluster` continua significando o que sempre significou, MULTI-FONTE, e
    e por isso que ele nao entra na conta principal: promover fonte unica a
    cacho para "resolver rapido" mentiria para `build_multi_source_payload`,
    para o merge da IA e para o CLUSTER_INPUT_AUDIT, que leem esse campo como
    fato editorial. Ele sobra so como reconhecimento de item legado, em que o
    `origin` nao foi preservado.
    """
    if not entry.get("event_key"):
        return False
    return entry.get("origin") == "superfeed" or bool(entry.get("is_cluster"))


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
        "origin":          entry.get("origin", ""),
        # Copiado, nunca afirmado: um item de fonte unica que passa por aqui
        # continua sendo de fonte unica do outro lado.
        "is_cluster":      bool(entry.get("is_cluster")),
        "multi_source":    bool(entry.get("multi_source") or entry.get("is_multi_source")),
        "event_key":       entry["event_key"],
        "primary_source":  entry.get("primary_source", ""),
        "all_sources":     entry.get("all_sources", []),
        "cluster_size":    entry.get("cluster_size", 1),
        "additional_urls": entry.get("additional_urls", []),
        "topic":           entry.get("topic", ""),
    }
