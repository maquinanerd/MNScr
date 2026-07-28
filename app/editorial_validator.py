# app/editorial_validator.py
"""
Camada de validacao editorial final.

Detecta tipo de materia, valida estrutura, profundidade, imagens, titulos,
meta, subtitle, schema, CTAs residuais e fontes.

Retorna um dict padrao com 'ok', 'status' e 'issues'.
Nao faz chamadas externas. Nao publica. Nao altera banco.
"""
import logging
import re
from typing import Any, Dict, List, Optional

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constantes editoriais
# ---------------------------------------------------------------------------

# Palavras mínimas por tipo — usadas apenas para geração de issues de auditoria.
# NÃO bloqueiam publicação. São proporcionais: em vez de mínimos fixos universais,
# o pipeline usa calculate_dynamic_word_policy() do policy_engine.
MIN_WORDS: Dict[str, int] = {
    "news": 250,       # notícia curta proporcional é válida
    "analysis": 400,
    "listicle": 400,   # regime proporcional; fonte define profundidade
    "guide": 400,      # idem
    "review": 400,
    "opinion_like": 250,
    "unknown": 200,
}

# Sinais para detectar tipo editorial
_LISTICLE_PATTERNS = re.compile(
    r"\b(\d{1,2}\s*(melhores?|piores?|filmes?|s[eé]ries?|personagens?|motivos?|"
    r"raz[oõ]es?|momentos?|coisas?|fatos?|cenas?|epis[oó]dios?)|"
    r"melhores?\s+\w+|piores?\s+\w+|ranking|lista\s+d[eo]|top\s+\d+|"
    r"selecion[ao]|subestimad|esquecid|explicad|ordem\s+correto?|"
    r"vale\s+assistir|val[ae]\s+a\s+pena)\b",
    re.IGNORECASE,
)
_GUIDE_PATTERNS = re.compile(
    r"\b(como\s+\w+|guia\b|entenda\s+|explica[cç][aã]o|ordem\s+cronol|"
    r"cronologia|para\s+iniciante|passo\s+a\s+passo|tutorial)\b",
    re.IGNORECASE,
)
_REVIEW_PATTERNS = re.compile(
    r"\b(an[aá]lise|review|resenha|avalia[cç][aã]o|nota\s+\d|"
    r"\d\s*/\s*10|\d\.\d\s*/\s*10|estrelas?|pontua[cç][aã]o)\b",
    re.IGNORECASE,
)
_OPINION_PATTERNS = re.compile(
    r"\b(opini[aã]o|acredito\s+que|eu\s+acho|minha\s+vis[aã]o|"
    r"coluna|editorial|carta\s+ao\s+leitor)\b",
    re.IGNORECASE,
)

# CTAs residuais proibidos
_CTA_RESIDUAL = re.compile(
    r"\b(subscribe\b|sign[\s\-]?up\b|read\s+more\b|keep\s+reading\b|"
    r"continue\s+with\b|create\s+an\s+account\b|newsletter\b|"
    r"terms\s+of\s+use\b|privacy\s+policy\b|advertisement\b|"
    r"more\s+from\b|follow\s+us\b|thank\s+you\s+for\s+reading\b|"
    r"clique\s+aqui\b|leia\s+mais\b|cadastre[\s\-]?se\b|"
    r"inscreva[\s\-]?se\b|assine\s+agora\b|fique\s+atualizado\b|"
    r"nos\s+siga\b|obrigado\s+por\s+ler\b|n[aã]o\s+esque[cç]a\s+de\s+se\s+inscrever\b)",
    re.IGNORECASE,
)

# Concorrentes proibidos no corpo
_FORBIDDEN_COMPETITORS = re.compile(
    r"\b(omelete\.com\.br|cineclick|adorocinema|adoro\s+cinema|papelpop|cinepop)\b",
    re.IGNORECASE,
)

# HTML no titulo
_HTML_IN_TITLE = re.compile(r"<[a-z/!][^>]{0,100}>", re.IGNORECASE)

# URL bruta fora de bloco de credito
_RAW_URL = re.compile(r"(?<![\"'=])(https?://\S{10,})", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Deteccao de tipo editorial
# ---------------------------------------------------------------------------

def detect_content_type(title: str, content_text: str, content_html: str = "") -> str:
    """Identifica o tipo editorial da materia com base em sinais no titulo e texto.

    Regra de protecao: noticia factual curta (<= 600 palavras) nao e classificada
    como guide ou listicle apenas por palavras no titulo. Isso evita
    CONTENT_TOO_THIN indevido em noticias simples.

    A protecao so se aplica quando existe corpo suficiente para julgar. Sem corpo,
    o sinal do titulo decide. E ela nunca sobrepoe o sinal estrutural: um corpo com
    varios H2 numerados e uma lista real, independentemente do tamanho.
    """
    combined = f"{title} {content_text[:2000]}"
    word_count = len(content_text.split())

    if _OPINION_PATTERNS.search(combined):
        return "opinion_like"
    if _REVIEW_PATTERNS.search(combined):
        return "review"

    # A heuristica estrutural precisa de HTML; content_text ja vem sem tags.
    structural_list = _listicle_by_h2_count(content_html or content_text)

    # Protecao: conteudo curto nao e guide/listicle por padrao.
    # Sem corpo (word_count == 0) nao ha o que proteger.
    has_body = word_count > 0
    is_short_factual = has_body and word_count <= 600

    if _LISTICLE_PATTERNS.search(combined) or structural_list:
        if is_short_factual and not structural_list:
            # Fonte curta com sinal de lista = provavelmente noticia sobre lista,
            # nao uma lista real de itens extensos
            logger.debug(
                "[CONTENT_TYPE] short_source_with_list_signal -> news (word_count=%s)",
                word_count,
            )
            return "news"
        return "listicle"

    if _GUIDE_PATTERNS.search(combined):
        if is_short_factual:
            logger.debug(
                "[CONTENT_TYPE] short_source_with_guide_signal -> news (word_count=%s)",
                word_count,
            )
            return "news"
        return "guide"

    # Noticia: texto relativamente curto e factual
    if word_count < 900:
        return "news"
    return "analysis"


def _listicle_by_h2_count(content_html: str) -> bool:
    """Heuristica adicional: muitos H2 com numeral ou padrao de lista."""
    soup = BeautifulSoup(content_html, "html.parser")
    h2s = soup.find_all("h2")
    if len(h2s) < 4:
        return False
    numbered = sum(1 for h in h2s if re.search(r"^\s*\d+[\.\):\-]", h.get_text()))
    return numbered >= 3


# ---------------------------------------------------------------------------
# Verificacoes individuais
# ---------------------------------------------------------------------------

def _count_words(content_html: str) -> int:
    soup = BeautifulSoup(content_html, "html.parser")
    return len(soup.get_text(separator=" ").split())


def _count_h2(content_html: str) -> int:
    soup = BeautifulSoup(content_html, "html.parser")
    return len(soup.find_all("h2"))


def _count_images(content_html: str) -> int:
    soup = BeautifulSoup(content_html, "html.parser")
    return len(soup.find_all("img"))


def _has_h1_inside(content_html: str) -> bool:
    soup = BeautifulSoup(content_html, "html.parser")
    return bool(soup.find("h1"))


def _image_stack_detected(content_html: str) -> bool:
    """Retorna True se houver 2+ imagens consecutivas sem texto editorial entre elas."""
    soup = BeautifulSoup(content_html, "html.parser")
    img_consecutive = 0
    for el in soup.find_all(["p", "h2", "h3", "ul", "ol", "blockquote", "figure", "img"]):
        # Skip <img> nested inside <figure> — the figure is already counted
        if el.name == "img" and el.parent and el.parent.name == "figure":
            continue
        if el.name in ("p", "h2", "h3", "ul", "ol", "blockquote"):
            text = el.get_text(strip=True)
            if len(text) > 30:
                img_consecutive = 0
                continue
        if el.name in ("figure", "img"):
            img_consecutive += 1
            if img_consecutive >= 2:
                return True
        elif el.find("img"):
            img_consecutive += 1
            if img_consecutive >= 2:
                return True
        else:
            img_consecutive = 0
    return False


def _duplicated_headings(content_html: str) -> bool:
    soup = BeautifulSoup(content_html, "html.parser")
    texts = [h.get_text(strip=True).lower() for h in soup.find_all(["h2", "h3"]) if h.get_text(strip=True)]
    return len(texts) != len(set(texts))


def _empty_headings(content_html: str) -> bool:
    soup = BeautifulSoup(content_html, "html.parser")
    return any(not h.get_text(strip=True) for h in soup.find_all(["h2", "h3", "h4"]))


def _cta_residual_in_content(content_html: str) -> bool:
    soup = BeautifulSoup(content_html, "html.parser")
    text = soup.get_text(separator=" ")
    return bool(_CTA_RESIDUAL.search(text))


def _forbidden_competitor_mention(content_html: str) -> bool:
    soup = BeautifulSoup(content_html, "html.parser")
    text = soup.get_text(separator=" ")
    return bool(_FORBIDDEN_COMPETITORS.search(text))


def _title_has_html(title: str) -> bool:
    return bool(_HTML_IN_TITLE.search(title))


def _title_too_long(title: str, max_len: int = 90) -> bool:
    return len(title) > max_len


def _excerpt_equals_meta(subtitle: str, meta: str) -> bool:
    if not subtitle or not meta:
        return False
    return subtitle.strip().lower() == meta.strip().lower()


def _excerpt_equals_title(subtitle: str, title: str) -> bool:
    if not subtitle or not title:
        return False
    return subtitle.strip().lower() == title.strip().lower()


def _sources_skipped_in_credit(content_html: str, sources_skipped: list) -> bool:
    if not sources_skipped:
        return False
    soup = BeautifulSoup(content_html, "html.parser")
    text = soup.get_text(separator=" ").lower()
    for skipped in sources_skipped:
        name = (skipped.get("domain") or skipped.get("url") or "").lower()
        if name and name[:20] in text:
            return True
    return False


def _raw_url_outside_credit(content_html: str) -> bool:
    """Detecta URL bruta solta no corpo (fora de href/src)."""
    # Remove href e src para nao contar links validos
    cleaned = re.sub(r'(?:href|src)=["\'][^"\']*["\']', "", content_html)
    return bool(_RAW_URL.search(cleaned))


def _schema_recommendation(content_type: str) -> str:
    mapping = {
        "listicle": "ITEMLIST",
        "guide": "HOWTO_IF_STEPS",
        "review": "REVIEW_IF_REAL",
        "news": "NEWSARTICLE",
        "analysis": "NEWSARTICLE",
        "opinion_like": "NEWSARTICLE",
        "unknown": "NEWSARTICLE",
    }
    return mapping.get(content_type, "NEWSARTICLE")


# ---------------------------------------------------------------------------
# Validador principal
# ---------------------------------------------------------------------------

def validate_editorial_quality(
    article_payload: Dict[str, Any],
    *,
    block_on_thin: bool = True,
    block_on_image_stack: bool = True,
    block_on_cta: bool = True,
    block_on_html_title: bool = True,
) -> Dict[str, Any]:
    """
    Valida a qualidade editorial de um artigo antes da publicacao.

    Parametros esperados em article_payload:
        title        (str)  — titulo final sanitizado
        content_html (str)  — conteudo HTML final
        meta         (str)  — meta description final
        subtitle     (str)  — excerpt/subtitle final
        sources_skipped (list) — fontes descartadas pelo multi_source_builder
        subtitle_status (str) — status da normalizacao do subtitle
        meta_status  (str)  — status da normalizacao da meta description

    Retorno:
        {
            "ok": bool,
            "status": str,
            "content_type": str,
            "word_count": int,
            "h2_count": int,
            "image_count": int,
            "image_stack_detected": bool,
            "title_status": str,
            "meta_status": str,
            "subtitle_status": str,
            "schema_recommendation": str,
            "issues": list[str],
        }
    """
    title = (article_payload.get("title") or "").strip()
    content_html = article_payload.get("content_html") or ""
    meta = (article_payload.get("meta") or "").strip()
    subtitle = (article_payload.get("subtitle") or "").strip()
    sources_skipped = article_payload.get("sources_skipped") or []
    subtitle_status = article_payload.get("subtitle_status") or "UNKNOWN"
    meta_status = article_payload.get("meta_status") or "UNKNOWN"

    soup = BeautifulSoup(content_html, "html.parser")
    content_text = soup.get_text(separator=" ", strip=True)

    content_type = article_payload.get("article_type") or detect_content_type(
        title, content_text, content_html=content_html
    )
    word_count = _count_words(content_html)
    h2_count = _count_h2(content_html)
    image_count = _count_images(content_html)

    issues: List[str] = []
    blocking = False

    # --- Profundidade de conteudo ---
    # Nao bloqueia publicacao: afeta apenas decisao de indexacao
    min_words = MIN_WORDS.get(content_type, MIN_WORDS["unknown"])
    if word_count < min_words:
        issues.append(f"CONTENT_TOO_THIN: {word_count}w < {min_words}w para tipo={content_type}")

    # --- Estrutura H2 ---
    # Nao bloqueia publicacao: afeta apenas decisao de indexacao
    if h2_count == 0:
        issues.append("NO_H2")
    elif content_type in ("listicle", "analysis", "guide") and h2_count < 2:
        issues.append(f"INSUFFICIENT_H2: {h2_count} para tipo={content_type}")

    # --- H1 dentro do corpo ---
    if _has_h1_inside(content_html):
        issues.append("H1_INSIDE_CONTENT")

    # --- Headings duplicados ou vazios ---
    if _duplicated_headings(content_html):
        issues.append("DUPLICATED_HEADINGS")
    if _empty_headings(content_html):
        issues.append("EMPTY_HEADING")

    # --- Imagens empilhadas ---
    # Nao bloqueia publicacao: afeta apenas decisao de indexacao
    img_stack = _image_stack_detected(content_html)
    if img_stack:
        issues.append("IMAGE_STACK_DETECTED")

    # --- Titulo ---
    # TITLE_MISSING e TITLE_HAS_HTML sao erros tecnicos reais que bloqueiam publicacao
    title_status = "OK"
    if not title:
        issues.append("TITLE_MISSING")
        title_status = "MISSING"
        blocking = True
    elif _title_has_html(title):
        issues.append("TITLE_HAS_HTML")
        title_status = "INVALID"
        if block_on_html_title:
            blocking = True
    elif _title_too_long(title):
        issues.append(f"TITLE_TOO_LONG: {len(title)} chars")
        title_status = "TOO_LONG"

    # --- Meta description ---
    if not meta:
        issues.append("META_MISSING")
    elif len(meta) < 120:
        issues.append(f"META_TOO_SHORT: {len(meta)} chars")
    elif len(meta) > 160:
        issues.append(f"META_TOO_LONG: {len(meta)} chars")

    # --- Subtitle / excerpt ---
    # Nao bloqueia publicacao: afeta apenas decisao de indexacao
    if _excerpt_equals_meta(subtitle, meta):
        issues.append("SUBTITLE_EQUALS_META")
    if _excerpt_equals_title(subtitle, title):
        issues.append("SUBTITLE_EQUALS_TITLE")

    # --- CTA residual ---
    # Nao bloqueia publicacao: afeta apenas decisao de indexacao
    cta_found = _cta_residual_in_content(content_html)
    if cta_found:
        issues.append("CTA_RESIDUAL_DETECTED")

    # --- Concorrentes proibidos ---
    # Politica de conteudo: bloqueia publicacao
    if _forbidden_competitor_mention(content_html):
        issues.append("FORBIDDEN_COMPETITOR_MENTION")
        blocking = True

    # --- Sources skipped no credito ---
    # Problema de atribuicao: bloqueia publicacao
    if _sources_skipped_in_credit(content_html, sources_skipped):
        issues.append("SOURCES_SKIPPED_IN_CREDIT")
        blocking = True

    # --- URL bruta fora de link ---
    # Nao bloqueia publicacao: afeta apenas decisao de indexacao
    if _raw_url_outside_credit(content_html):
        issues.append("RAW_URL_OUTSIDE_CREDIT")

    schema_recommendation = _schema_recommendation(content_type)

    status = "BLOCKED_FOR_REVIEW" if blocking else ("OK" if not issues else "WARNING_ONLY")

    result = {
        "ok": not blocking,
        "status": status,
        "content_type": content_type,
        "word_count": word_count,
        "h2_count": h2_count,
        "image_count": image_count,
        "image_stack_detected": img_stack,
        "title_status": title_status,
        "meta_status": meta_status,
        "subtitle_status": subtitle_status,
        "schema_recommendation": schema_recommendation,
        "issues": issues,
    }

    logger.info(
        "[EDITORIAL_QA] type=%s words=%s h2=%s ok=%s status=%s issues=%s title=%s",
        content_type,
        word_count,
        h2_count,
        result["ok"],
        status,
        issues or "none",
        title[:60] if title else "",
    )
    return result


# ---------------------------------------------------------------------------
# Relatorio de auditoria editorial em Markdown
# ---------------------------------------------------------------------------

def build_editorial_audit_report(
    article_payload: Dict[str, Any],
    validation_result: Dict[str, Any],
    wp_post_id: Optional[int] = None,
) -> str:
    """Gera relatorio Markdown de auditoria editorial para um artigo."""
    title = article_payload.get("title") or ""
    canonical = article_payload.get("canonical") or "N/A"
    robots = article_payload.get("robots") or "N/A"
    schema = validation_result.get("schema_recommendation") or "N/A"

    issues = validation_result.get("issues") or []
    ok = validation_result.get("ok", False)
    status = validation_result.get("status", "UNKNOWN")

    thin = "SIM" if any("CONTENT_TOO_THIN" in i for i in issues) else "NAO"
    img_stack = "SIM" if validation_result.get("image_stack_detected") else "NAO"
    cta = "SIM" if any("CTA_RESIDUAL" in i for i in issues) else "NAO"
    competitor = "SIM" if any("FORBIDDEN_COMPETITOR" in i for i in issues) else "NAO"
    dup_title = "SIM" if any("SUBTITLE_EQUALS_TITLE" in i for i in issues) else "NAO"
    h1_body = "SIM" if any("H1_INSIDE_CONTENT" in i for i in issues) else "NAO"

    wp_id_str = str(wp_post_id) if wp_post_id else "nao_publicado"

    lines = [
        "# Auditoria editorial",
        "",
        "## Identificacao",
        f"- titulo: {title[:120]}",
        f"- tipo: {validation_result.get('content_type', 'unknown')}",
        f"- word_count: {validation_result.get('word_count', 0)}",
        f"- h2_count: {validation_result.get('h2_count', 0)}",
        f"- image_count: {validation_result.get('image_count', 0)}",
        f"- status: {status}",
        f"- wp_post_id: {wp_id_str}",
        "",
        "## SEO",
        f"- title_status: {validation_result.get('title_status', 'N/A')}",
        f"- meta_status: {validation_result.get('meta_status', 'N/A')}",
        f"- subtitle_status: {validation_result.get('subtitle_status', 'N/A')}",
        f"- canonical: {canonical}",
        f"- robots: {robots}",
        f"- schema: {schema}",
        "",
        "## Conteudo",
        f"- thin_content: {thin}",
        f"- image_stack: {img_stack}",
        f"- cta_residual: {cta}",
        f"- forbidden_competitor: {competitor}",
        f"- duplicated_title: {dup_title}",
        f"- h1_inside_content: {h1_body}",
        "",
        "## Issues",
    ]
    if issues:
        for issue in issues:
            lines.append(f"- {issue}")
    else:
        lines.append("- nenhum")

    lines += [
        "",
        "## Decisao",
        f"- [{'x' if ok else ' '}] APROVADO_PUBLICACAO",
        f"- [{'x' if status == 'WARNING_ONLY' else ' '}] PUBLICADO_COM_AVISOS",
        f"- [{'x' if not ok else ' '}] BLOQUEADO_ERRO_TECNICO",
    ]

    return "\n".join(lines)
