"""
link_store.py — Banco local de artigos publicados para links internos automáticos.
Mantém os últimos 200 artigos. O pipeline consulta antes de gerar cada artigo
para sugerir links contextuais ao Gemini.
"""
import logging
import os
import sqlite3
from urllib.parse import urlparse

from .sqlite_utils import connect_sqlite

_DB = os.path.join(os.path.dirname(__file__), "..", "data", "app.db")
_LEGACY_DB = _DB
logger = logging.getLogger(__name__)
_INITIALIZED = False


def _is_public_url(value: str) -> bool:
    """O campo `url` guarda endereço público, e só isso.

    A checagem existe porque o pipeline já gravou aqui
    `artifacts\\local-drafts\\draft-....json` — o caminho do artefato em disco,
    que o linking interno depois injetou no corpo como <a href>. Um caminho de
    arquivo não é endereço de nada; a barreira fica no armazenamento para não
    depender de cada chamador lembrar disso.
    """
    try:
        parsed = urlparse(str(value or "").strip())
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _migrate_legacy_data(conn: sqlite3.Connection) -> None:
    if os.path.abspath(_LEGACY_DB) == os.path.abspath(_DB):
        return
    current_count = conn.execute("SELECT COUNT(*) FROM link_store").fetchone()[0]
    if current_count or not os.path.exists(_LEGACY_DB):
        return

    legacy_conn = connect_sqlite(_LEGACY_DB, row_factory=sqlite3.Row)
    try:
        if not _table_exists(legacy_conn, "link_store"):
            return

        rows = legacy_conn.execute(
            """
            SELECT title, url, category, entity, published
            FROM link_store
            ORDER BY published DESC
            LIMIT 200
            """
        ).fetchall()
        if not rows:
            return

        conn.executemany(
            """
            INSERT OR IGNORE INTO link_store (title, url, category, entity, published)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (row["title"], row["url"], row["category"], row["entity"], row["published"])
                for row in rows
            ],
        )
        logger.info(f"[LINKS] Migrados {len(rows)} artigos legados para data/link_store.db")
    finally:
        legacy_conn.close()


def _initialize() -> None:
    global _INITIALIZED
    if _INITIALIZED:
        return

    conn = connect_sqlite(_DB, row_factory=sqlite3.Row)
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS link_store (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            title       TEXT NOT NULL,
            url         TEXT NOT NULL UNIQUE,
            category    TEXT DEFAULT '',
            entity      TEXT DEFAULT '',
            published   INTEGER DEFAULT (strftime('%s','now'))
        )""")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_link_store_published ON link_store(published DESC)"
        )
        _migrate_legacy_data(conn)
        conn.commit()
        _INITIALIZED = True
    finally:
        conn.close()


def _conn() -> sqlite3.Connection:
    _initialize()
    return connect_sqlite(_DB, row_factory=sqlite3.Row)


def save_article(title: str, url: str, category: str = "", entity: str = "") -> None:
    """Salva artigo publicado. Mantém apenas os 200 mais recentes.

    Recusa o que não for endereço público: o link_store existe para virar
    <a href> no corpo da próxima matéria.
    """
    if not _is_public_url(url):
        logger.warning(
            "[LINKS] recusado no link_store: %r nao e URL publica (title=%r)",
            url, (title or "")[:60],
        )
        return
    try:
        with _conn() as c:
            c.execute(
                "INSERT OR IGNORE INTO link_store (title, url, category, entity) VALUES (?, ?, ?, ?)",
                (title, url, category or "", entity or ""),
            )
            # Manter apenas os 200 mais recentes
            c.execute("""DELETE FROM link_store WHERE id NOT IN (
                SELECT id FROM link_store ORDER BY published DESC LIMIT 200
            )""")
    except Exception as e:
        logger.warning(f"[LINKS] Erro ao salvar no link_store: {e}")


def get_related(entity: str = "", category: str = "", limit: int = 3) -> list:
    """
    Retorna artigos relacionados por entidade ou categoria.
    Prioriza mesma entidade; fallback para mesma categoria.
    """
    try:
        with _conn() as c:
            results = []
            if entity:
                rows = c.execute(
                    "SELECT title, url FROM link_store WHERE entity = ? ORDER BY published DESC LIMIT ?",
                    (entity, limit),
                ).fetchall()
                # Linhas gravadas antes da barreira de escrita ainda podem
                # carregar caminho de arquivo; elas não voltam para o prompt.
                results = [
                    {"title": r["title"], "url": r["url"]}
                    for r in rows if _is_public_url(r["url"])
                ]

            if len(results) < limit and category:
                needed = limit - len(results)
                existing_urls = {r["url"] for r in results}
                rows = c.execute(
                    "SELECT title, url FROM link_store WHERE category = ? ORDER BY published DESC LIMIT ?",
                    (category, needed * 2),
                ).fetchall()
                for r in rows:
                    if not _is_public_url(r["url"]):
                        continue
                    if r["url"] not in existing_urls:
                        results.append({"title": r["title"], "url": r["url"]})
                    if len(results) >= limit:
                        break

            return results[:limit]
    except Exception as e:
        logger.warning(f"[LINKS] Erro ao consultar link_store: {e}")
        return []


def get_link_map() -> dict:
    """
    Retorna todos os artigos recentes do link_store no formato esperado
    por add_internal_links(). Chamado a cada batch para manter dados frescos.
    """
    try:
        with _conn() as c:
            rows = c.execute(
                "SELECT title, url, entity FROM link_store ORDER BY published DESC LIMIT 200"
            ).fetchall()
        posts = []
        for row in rows:
            title = row["title"]
            url = row["url"]
            if not _is_public_url(url):
                continue
            entity = row["entity"]
            kws = []
            # Entity curta (ex: "marvel", "one piece") → ótima para match
            if entity and 3 <= len(entity) <= 50:
                kws.append(entity)
            # Extrair assunto do título antes do primeiro ":"
            # Ex: "Star Trek: Vilão..." → "Star Trek"
            if title:
                subject = title.split(":")[0].strip()
                if 3 <= len(subject) <= 50 and subject not in kws:
                    kws.append(subject)
                # Título completo como fallback de menor prioridade
                if len(title) >= 5 and title not in kws:
                    kws.append(title)
            if kws:
                posts.append({'link': url, 'keywords': kws, 'categories': []})
        return {'posts': posts}
    except Exception as e:
        logger.warning(f"[LINKS] Erro ao construir link_map: {e}")
        return {'posts': []}


def format_for_prompt(links: list) -> str:
    """
    Formata links como instrução legível para o prompt do Gemini.
    Retorna string vazia se não há links disponíveis.
    """
    if not links:
        return ""
    lines = ["LINKS INTERNOS DISPONÍVEIS — use 1 a 3 destes no corpo do artigo com texto âncora descritivo:"]
    for lk in links:
        lines.append(f'- URL: {lk["url"]} | Título: "{lk["title"]}"')
    lines.append(
        "REGRA DO TEXTO ÂNCORA: escolha palavras do TEMA do artigo de destino "
        "(ex: 'todos os filmes do Batman em ordem'). "
        "PROIBIDO: 'clique aqui', 'saiba mais', 'leia também', 'veja mais'."
    )
    return "\n".join(lines)
