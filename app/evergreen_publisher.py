"""
evergreen_publisher.py — Geração e publicação gradual de clusters evergreen.

Scheduling: T+1h timeline, T+6h cast guide, T+24h villains.
Fila: tabela evergreen_queue no SQLite (data/app.db).
Custo adicional: usa o mesmo AIClient e WordPressClient já instanciados.
"""
import json
import logging
import os
import sqlite3
import time

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompts de geração — um por template
# ---------------------------------------------------------------------------
EVERGREEN_PROMPTS = {
    "timeline": """Você é editor do The Screen / Cinerie.
Crie artigo completo de 900-1.200 palavras em português brasileiro:
"{entity} — Timeline Completa e Ordem Cronológica Explicada"

ESTRUTURA OBRIGATÓRIA:
- Lead forte de abertura (quem, o quê, quando)
- <h2>O que você precisa saber</h2> com 3 bullets (<ul><li>)
- <h2>Cronologia Completa</h2> com <h3>s por período ou fase
- <h2>Obras Essenciais em Ordem</h2> com <h3>s por tipo (filmes, séries, games)
Mínimo 900 palavras. Cada <h2> DEVE ter pelo menos um <h3> filho.
Retorne APENAS o HTML do corpo do artigo (sem <html>, <head> ou <body>).""",

    "cast_guide": """Você é editor do The Screen / Cinerie.
Crie artigo guia de 900-1.200 palavras em português brasileiro:
"{entity} — Elenco Completo e Guia de Personagens"

ESTRUTURA OBRIGATÓRIA:
- Lead forte de abertura
- <h2>O que você precisa saber</h2> com 3 bullets (<ul><li>)
- <h2>Personagens Principais</h2> com <h3> para cada personagem central
- <h2>Personagens Secundários Importantes</h2> com <h3>s por grupo ou arco
Mínimo 900 palavras. Cada <h2> DEVE ter pelo menos um <h3> filho.
Retorne APENAS o HTML do corpo do artigo (sem <html>, <head> ou <body>).""",

    "villains": """Você é editor do The Screen / Cinerie.
Crie artigo de 900-1.200 palavras em português brasileiro:
"{entity} — Todos os Vilões Explicados e Ranqueados"

ESTRUTURA OBRIGATÓRIA:
- Lead forte sobre os antagonistas da franquia
- <h2>O que você precisa saber</h2> com 3 bullets (<ul><li>)
- <h2>Vilões Principais</h2> com <h3> para cada vilão (origem, motivação, poderes)
- <h2>Ranking: do Menos ao Mais Poderoso</h2> com <h3>s por tier
Mínimo 900 palavras. Cada <h2> DEVE ter pelo menos um <h3> filho.
Retorne APENAS o HTML do corpo do artigo (sem <html>, <head> ou <body>).""",

    "ending_explained": """Você é editor do The Screen / Cinerie.
Crie artigo de 900-1.200 palavras em português brasileiro:
"{entity} — Final Explicado em Detalhes"

ESTRUTURA OBRIGATÓRIA:
- Lead forte que contextualiza o final sem spoiler no primeiro parágrafo
- <h2>O que você precisa saber</h2> com 3 bullets (<ul><li>)
- <h2>O Que Acontece no Final</h2> com <h3>s por cena ou capítulo
- <h2>Teorias e Interpretações</h2> com <h3>s por teoria principal
Mínimo 900 palavras. Cada <h2> DEVE ter pelo menos um <h3> filho.
Retorne APENAS o HTML do corpo do artigo (sem <html>, <head> ou <body>).""",

    "easter_eggs": """Você é editor do The Screen / Cinerie.
Crie artigo de 900-1.200 palavras em português brasileiro:
"{entity} — Todos os Easter Eggs e Referências Escondidas"

ESTRUTURA OBRIGATÓRIA:
- Lead forte sobre a tradição de easter eggs da franquia
- <h2>O que você precisa saber</h2> com 3 bullets (<ul><li>)
- <h2>Easter Eggs Visuais</h2> com <h3>s por cena ou episódio
- <h2>Referências a Outras Obras</h2> com <h3>s por franquia referenciada
Mínimo 900 palavras. Cada <h2> DEVE ter pelo menos um <h3> filho.
Retorne APENAS o HTML do corpo do artigo (sem <html>, <head> ou <body>).""",
}

TITLES = {
    "timeline":         "{entity}: Timeline Completa e Ordem Cronológica",
    "cast_guide":       "{entity}: Elenco Completo e Guia de Personagens",
    "villains":         "{entity}: Todos os Vilões Explicados e Ranqueados",
    "ending_explained": "{entity}: Final Explicado em Detalhes",
    "easter_eggs":      "{entity}: Todos os Easter Eggs e Referências Escondidas",
}

# T+1h, T+6h, T+24h
DELAYS_H = [1, 6, 24]


# ---------------------------------------------------------------------------
# SQLite helpers
# ---------------------------------------------------------------------------
def _db_path() -> str:
    return os.path.join(os.path.dirname(__file__), "../data/app.db")


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.execute("""
        CREATE TABLE IF NOT EXISTS evergreen_queue (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            entity          TEXT    NOT NULL,
            template_key    TEXT    NOT NULL,
            prompt          TEXT    NOT NULL,
            title           TEXT    NOT NULL,
            category_ids    TEXT    NOT NULL,
            source_post_id  INTEGER,
            scheduled_for   INTEGER NOT NULL,
            status          TEXT    DEFAULT 'pending',
            wp_post_id      INTEGER,
            created_at      INTEGER DEFAULT (strftime('%s','now'))
        )
    """)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def schedule_cluster_pages(
    entity: str,
    source_post_id: int,
    source_title: str,
    templates: list,
    category_ids: list,
) -> None:
    """Enfileira até 3 páginas evergreen com scheduling gradual (T+1h / T+6h / T+24h)."""
    selected = [t for t in templates if t in EVERGREEN_PROMPTS][:3]
    if not selected:
        logger.debug(f"[QUEUE] Nenhum template válido para {entity} — skip.")
        return

    entity_title = entity.title()
    with _get_conn() as conn:
        for i, tmpl in enumerate(selected):
            prompt = EVERGREEN_PROMPTS[tmpl].replace("{entity}", entity_title)
            title  = TITLES.get(tmpl, "{entity}: Guia Completo").replace(
                         "{entity}", entity_title)
            delay_h = DELAYS_H[i] if i < len(DELAYS_H) else DELAYS_H[-1]
            sched   = int(time.time()) + delay_h * 3600
            conn.execute(
                """INSERT INTO evergreen_queue
                   (entity,template_key,prompt,title,category_ids,source_post_id,scheduled_for)
                   VALUES (?,?,?,?,?,?,?)""",
                (entity, tmpl, prompt, title,
                 json.dumps(category_ids), source_post_id, sched),
            )
            logger.info(f"[QUEUE] {entity}/{tmpl} → T+{delay_h}h (post {source_post_id})")


def process_evergreen_queue(max_per_cycle: int = 2) -> int:
    """
    Lê entradas pendentes com scheduled_for <= agora, gera conteúdo via Gemini
    e publica no WordPress. Chamado no início de cada ciclo do pipeline.

    Usa internamente o AIClient e WordPressClient já configurados no módulo.
    Retorna: número de posts publicados com sucesso.
    """
    # Imports locais para evitar importação circular no topo do arquivo
    from .ai_processor import AIProcessor
    from .wordpress import WordPressClient
    from .config import WORDPRESS_CONFIG, WORDPRESS_CATEGORIES

    now   = int(time.time())
    count = 0

    with _get_conn() as conn:
        rows = conn.execute(
            """SELECT id,entity,template_key,prompt,title,category_ids,source_post_id
               FROM evergreen_queue
               WHERE status='pending' AND scheduled_for<=?
               ORDER BY scheduled_for ASC LIMIT ?""",
            (now, max_per_cycle),
        ).fetchall()

    if not rows:
        return 0

    # Instanciar clientes (singletons — sem custo de re-criação)
    try:
        ai_proc   = AIProcessor()
        wp_client = WordPressClient(
            config=WORDPRESS_CONFIG, categories_map=WORDPRESS_CATEGORIES
        )
    except Exception as exc:
        logger.error(f"[EVERGREEN] Falha ao inicializar clientes: {exc}")
        return 0

    try:
        for row in rows:
            qid, entity, tmpl, prompt, title, cat_json, src_id = row
            try:
                logger.info(f"[EVERGREEN] Gerando {entity}/{tmpl}...")
                content_html, _ = ai_proc._ai_client.generate_text(prompt)

                if not content_html or len(content_html) < 200:
                    raise ValueError(f"Conteúdo vazio ou muito curto ({len(content_html)} chars)")

                # Buscar imagem de destaque reutilizável da biblioteca WP
                featured_media_id = wp_client.find_media_by_search(entity)
                if not featured_media_id:
                    # Fallback: buscar pelo título da entidade sem o template
                    featured_media_id = wp_client.find_media_by_search(entity.split()[0])

                post_payload = {
                    "title":      title,
                    "content":    content_html,
                    "categories": json.loads(cat_json),
                    "status":     "publish",
                    "featured_media": featured_media_id or 0,
                    "meta": {
                        "_yoast_wpseo_meta-robots-noindex":  "0",
                        "_yoast_wpseo_meta-robots-nofollow": "0",
                    },
                }
                wp_id = wp_client.create_post(post_payload)

                with _get_conn() as conn:
                    conn.execute(
                        "UPDATE evergreen_queue SET status='done', wp_post_id=? WHERE id=?",
                        (wp_id, qid),
                    )
                logger.info(f"[EVERGREEN] ✓ {title[:50]} → WP ID {wp_id}")
                count += 1
                time.sleep(5)

            except Exception as exc:
                logger.error(f"[EVERGREEN] Erro {entity}/{tmpl}: {exc}")
                with _get_conn() as conn:
                    conn.execute(
                        "UPDATE evergreen_queue SET status='error' WHERE id=?", (qid,)
                    )
    finally:
        wp_client.close()

    return count
