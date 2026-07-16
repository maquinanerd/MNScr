# app/ai_rewrite.py
"""
Phase 2 of the 3-phase AI pipeline: editorial rewrite.

Transforms sanitized HTML into an original Máquina Nerd article written
in journalistic Português-Brasil, injecting internal links and videos.
"""
import logging
import os
import re
from typing import TYPE_CHECKING, Dict, Any, List

from bs4 import BeautifulSoup

from .exceptions import BlockedPromptError

if TYPE_CHECKING:
    from .ai_client_gemini import AIClient

logger = logging.getLogger(__name__)

# Portais concorrentes — parágrafos/itens que os mencionem são removidos automaticamente
_COMPETITORS = ("omelete", "jovem nerd", "ign brasil", "adorocinema", "cineclick")

# Fontes/portais externos — usados para detectar resíduo de agregador (sem remoção automática)
_AGGREGATOR_SOURCES = (
    "omelete", "jovem nerd", "ign brasil", "adorocinema", "cineclick",
    "screenrant", "collider", "cbr", "deadline",
)

# Frases de padding — sentenças que as contenham são removidas; <p> residual muito curto é eliminado
_PADDING_PHRASES = (
    "isso pode indicar",
    "isso sugere",
    "isso reforça",
    "os fãs podem esperar",
    "a série tem potencial",
    "o filme promete",
    "isso abre possibilidade",
    "isso levanta a possibilidade",
)

# Marcadores analíticos em PT-BR — presença em um parágrafo longo indica bloco editorial
_ANALYTICAL_MARKERS = (
    "vale lembrar",
    "vale destacar",
    "na prática",
    "no universo",
    "ao longo",
    "historicamente",
    "em comparação",
    "ao contrário",
    "diferente de",
    "desde que",
    "embora",
    "apesar",
    "o que isso representa",
    "o que muda",
    "a questão é",
    "é importante",
    "isso significa",
    "o impacto",
    "contexto",
    "conexão com",
    "relação entre",
    "implicações",
    "consequência",
)

# Sinais editoriais específicos de contexto/franquia — usados por has_editorial_block()
_EDITORIAL_SIGNALS = (
    "no mcu",
    "na marvel",
    "na dc",
    "nos quadrinhos",
    "na franquia",
    "no universo",
    "em obras anteriores",
    "nos filmes anteriores",
    "na cronologia",
    "isso coloca",
    "isso muda",
    "isso abre espaço",
    "isso conecta",
    "isso indica um caminho",
    "dentro da história",
    "na adaptação",
)

_PROMPT_TEMPLATE = """\
Você é um jornalista sênior especializado em entretenimento (filmes, séries e cultura pop).

Sua função é transformar o conteúdo fornecido em uma matéria completamente original em português do Brasil (PT-BR), com alto valor editorial, profundidade analítica e fluidez natural.

O texto final DEVE parecer uma publicação independente — e NÃO uma reescrita ou tradução.

━━━━━━━━━━━━━━━━━━━━━━━
REGRAS CRÍTICAS (OBRIGATÓRIAS)
━━━━━━━━━━━━━━━━━━━━━━━

1. NÃO siga a mesma estrutura do artigo original
2. NÃO mantenha a mesma ordem das informações
3. NÃO traduza ou reescreva frase por frase
4. NÃO reutilize construções ou frases da fonte
5. NÃO use estruturas típicas de agregadores
6. O texto DEVE parecer escrito do zero
7. O texto DEVE conter interpretação e análise própria

━━━━━━━━━━━━━━━━━━━━━━━
PROIBIDO (ANTI-AGREGADOR)
━━━━━━━━━━━━━━━━━━━━━━━

Evite estruturas como:
- "O que você precisa saber"
- "Tudo o que sabemos"
- listas de resumo em bullet points
- blocos de recapitulação

Evite frases genéricas como:
- "isso pode indicar"
- "isso sugere"
- "isso reforça"
- "os fãs podem esperar"
- "a série promete"
- "isso abre possibilidade"
- "isso levanta a possibilidade"

Evite enrolação, repetição e frases vazias.

━━━━━━━━━━━━━━━━━━━━━━━
EXIGÊNCIA PRINCIPAL
━━━━━━━━━━━━━━━━━━━━━━━

O artigo DEVE conter pelo menos UM parágrafo analítico forte que:

- explique por que essa informação é relevante
- conecte com o universo/franquia maior
- interprete possíveis consequências ou direções da história
- adicione contexto que NÃO está explícito na fonte

Esse parágrafo deve parecer análise de especialista, não especulação genérica.

━━━━━━━━━━━━━━━━━━━━━━━
ESTRUTURA (NATURAL, NÃO MECÂNICA)
━━━━━━━━━━━━━━━━━━━━━━━

- Comece com um lead forte (2–3 frases)
- Desenvolva o conteúdo de forma progressiva (sem listas)
- Use de 2 a 4 subtítulos (H2), naturais e jornalísticos
- Mantenha fluidez (sem quebras artificiais)
- Evite estrutura engessada ou repetitiva

━━━━━━━━━━━━━━━━━━━━━━━
ESTILO
━━━━━━━━━━━━━━━━━━━━━━━

- Escreva em português do Brasil (PT-BR)
- Use linguagem natural, fluida e profissional
- Priorize clareza e leitura agradável
- Evite exagero e clickbait
- Varie a construção das frases (não robotizar)

━━━━━━━━━━━━━━━━━━━━━━━
NEGRITO (IMPORTANTE)
━━━━━━━━━━━━━━━━━━━━━━━

Sempre aplique <strong> na primeira ocorrência de:
- nomes de filmes
- nomes de séries
- nomes de personagens
- nomes de atores
- franquias (Marvel, DC, MCU, etc.)

━━━━━━━━━━━━━━━━━━━━━━━
FORMATAÇÃO HTML
━━━━━━━━━━━━━━━━━━━━━━━

- Output em HTML válido
- Use apenas: <p>, <h2>, <strong>
- NÃO use <h1>
- NÃO escreva "Fonte:"
- NÃO mencione o site original

━━━━━━━━━━━━━━━━━━━━━━━
SEO (SEM QUEBRAR NATURALIDADE)
━━━━━━━━━━━━━━━━━━━━━━━

- Inclua palavras-chave naturalmente (filme, série, personagens, franquia)
- NÃO repita palavras de forma forçada
- Escreva para humanos, não para robôs
- Links internos (use 1 a 3): <a href="https://{domain}/tag/tag-aqui">Texto âncora</a>
  Âncora = nome de franquia, série, filme ou ator. NUNCA "clique aqui" ou "saiba mais".

━━━━━━━━━━━━━━━━━━━━━━━
SAÍDA
━━━━━━━━━━━━━━━━━━━━━━━

Retorne APENAS o HTML final do artigo.
Não explique nada.
Não adicione comentários.
Não inclua metadados.
Não use blocos de código ou marcadores markdown.

━━━━━━━━━━━━━━━━━━━━━━━
INPUT
━━━━━━━━━━━━━━━━━━━━━━━

ARTIGO ORIGINAL:
{content}

DOMÍNIO:
{domain}

BLOCO DE LINKS:
{link_block}

VÍDEOS DISPONÍVEIS (incorpore no máximo 2, no formato abaixo):
<figure class="video-container"><iframe src="URL_AQUI" loading="lazy" referrerpolicy="no-referrer-when-downgrade" allowfullscreen></iframe></figure>
{videos_list}"""


def _remove_sentences_containing(text: str, needles: tuple) -> str:
    """Remove individual sentences from a text block that contain any of the needles."""
    # Split on sentence-ending punctuation followed by space or end-of-string
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    cleaned = [
        s for s in sentences
        if not any(needle in s.lower() for needle in needles)
    ]
    return " ".join(cleaned)


def _post_process(html: str) -> str:
    """
    Post-processing of rewritten HTML — enforces structural rules programmatically:
    - Downgrade <h1> → <h2>
    - Remove <p>/<li> that start with "Fonte:"
    - Remove entire <p>/<li> blocks containing competitor names
    - Remove sentences inside <p> blocks that contain padding phrases;
      decompose the <p> if what remains is too short (< 20 chars)
    """
    soup = BeautifulSoup(html, "html.parser")

    # ── 1. Downgrade h1 → h2 ─────────────────────────────────────────────────
    for h1 in soup.find_all("h1"):
        h1.name = "h2"
        logger.debug("[REWRITE] <h1> downgraded to <h2>")

    # ── 2. Remove "Fonte:" paragraphs ─────────────────────────────────────────
    for tag in soup.find_all(["p", "li"]):
        if tag.get_text(strip=True).lower().startswith("fonte:"):
            tag.decompose()
            logger.debug("[REWRITE] Removed 'Fonte:' element")

    # ── 3. Remove blocks containing competitor names ──────────────────────────
    for tag in soup.find_all(["p", "li", "figcaption"]):
        text = tag.get_text(separator=" ", strip=True).lower()
        for comp in _COMPETITORS:
            if comp in text:
                logger.warning(f"[REWRITE] Removed block mentioning competitor '{comp}'")
                tag.decompose()
                break

    # ── 4. Strip padding sentences from <p> blocks ────────────────────────────
    for p in soup.find_all("p"):
        raw = p.get_text(separator=" ", strip=True)
        if not any(phrase in raw.lower() for phrase in _PADDING_PHRASES):
            continue
        cleaned = _remove_sentences_containing(raw, _PADDING_PHRASES)
        if len(cleaned) < 20:
            logger.debug(f"[REWRITE] Removed padding-only <p>: '{raw[:60]}'")
            p.decompose()
        else:
            # Replace the paragraph's text content while keeping any child tags
            # (e.g. <strong>, <a>) that are not in the offending sentence.
            # Simple approach: replace with plain text paragraph.
            p.clear()
            p.append(cleaned)
            logger.debug(f"[REWRITE] Cleaned padding from <p>: '{cleaned[:60]}'")

    return str(soup)


# ── Public check functions (importable by test scripts) ─────────────────────

def has_editorial_block(html: str) -> bool:
    """
    Check if the HTML contains at least one analytical/contextual paragraph.

    A paragraph qualifies if:
    - word count >= 20
    - contains at least one signal from _EDITORIAL_SIGNALS OR _ANALYTICAL_MARKERS
    """
    soup = BeautifulSoup(html, "html.parser")
    all_signals = _EDITORIAL_SIGNALS + _ANALYTICAL_MARKERS
    for p in soup.find_all("p"):
        text = p.get_text(separator=" ", strip=True)
        if len(text.split()) >= 20 and any(s in text.lower() for s in all_signals):
            return True
    return False


def count_padding_phrases(html: str) -> int:
    """Count how many padding/generic phrases appear in the HTML text."""
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(separator=" ", strip=True).lower()
    return sum(1 for phrase in _PADDING_PHRASES if phrase in text)


def find_aggregator_residue(html: str) -> list:
    """
    Detect aggregator/source residue in the HTML text.

    Checks for:
    - "fonte:" prefix
    - "via " attribution
    - mention of known competitor/source portals (_AGGREGATOR_SOURCES)

    Returns a list of found residue strings (empty = clean).
    """
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(separator=" ", strip=True).lower()
    found = []
    if "fonte:" in text:
        found.append("fonte:")
    if re.search(r'\bvia\s+\w', text):
        found.append("via [atribuição]")
    for source in _AGGREGATOR_SOURCES:
        if source in text:
            found.append(source)
    return found


def _check_editorial_block(html: str) -> bool:
    """Internal alias kept for backward compatibility — delegates to has_editorial_block."""
    return has_editorial_block(html)


def rewrite(html_clean: str, meta: Dict[str, Any], client: "AIClient") -> str:
    """
    Phase 2: transform clean HTML into original Máquina Nerd journalism.

    Args:
        html_clean: Sanitized HTML from phase 1.
        meta:       Dict with keys: domain, link_block, videos (list of dicts with embed_url).
        client:     Shared AIClient instance.

    Returns:
        Rewritten HTML string. Falls back to html_clean on AI failure.
    """
    if not html_clean or not html_clean.strip():
        return html_clean

    domain = meta.get("domain", "")
    link_block = meta.get("link_block", "Nenhum")
    videos: List[Dict[str, Any]] = meta.get("videos", [])
    videos_list = "\n".join(
        v.get("embed_url", "") for v in videos if isinstance(v, dict) and v.get("embed_url")
    ) or "Nenhum"

    prompt = _PROMPT_TEMPLATE.format(
        domain=domain,
        link_block=link_block,
        videos_list=videos_list,
        content=html_clean,
    )

    generation_config = {
        "temperature": 0.5,
        "max_output_tokens": 16000,
    }

    try:
        response_data = client.generate_text(prompt, generation_config=generation_config)

        if isinstance(response_data, tuple):
            response_text, tokens_info = response_data
        else:
            response_text = response_data
            tokens_info = {}

        _log_phase_tokens(tokens_info, phase="rewrite")

        if not response_text or not response_text.strip():
            logger.warning("[REWRITE] AI returned empty response — using sanitized HTML")
            return html_clean

        text = response_text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:]).rstrip("`").strip()

        # Post-processing: enforce structural rules regardless of AI compliance
        text = _post_process(text)

        # ── Editorial quality checks ──────────────────────────────────────
        if not has_editorial_block(text):
            logger.warning(
                "[REWRITE][NO-EDITORIAL] Nenhum parágrafo com bloco editorial detectado"
            )

        padding_count = count_padding_phrases(text)
        if padding_count >= 2:
            logger.warning(f"[REWRITE][PADDING] Excesso de frases genéricas: {padding_count}")

        residues = find_aggregator_residue(text)
        if residues:
            logger.warning(f"[REWRITE][AGGREGATOR] Resíduos detectados: {residues}")

        logger.info(f"[REWRITE] OK — {len(html_clean)} → {len(text)} chars")
        return text

    except BlockedPromptError:
        logger.warning("[REWRITE] Prompt blocked by model policy")
        raise
    except Exception as exc:
        logger.error(f"[REWRITE] AI call failed: {exc} — using sanitized HTML", exc_info=True)
        return html_clean


def _log_phase_tokens(tokens_info: dict, phase: str) -> None:
    try:
        from .token_tracker import log_tokens
        prompt_tokens = int(tokens_info.get("prompt_tokens", 0))
        completion_tokens = int(tokens_info.get("completion_tokens", 0))
        if prompt_tokens + completion_tokens > 0:
            log_tokens(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                api_type="gemini",
                model=os.getenv("GEMINI_MODEL_ID", "gemini-3.1-flash-lite"),
                metadata={"operation": f"3phase_{phase}"},
            )
    except Exception as exc:
        logger.debug(f"[REWRITE] token logging skipped: {exc}")
