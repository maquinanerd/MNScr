# app/ai_sanitize.py
"""
Phase 1 of the 3-phase AI pipeline: sanitize raw HTML from the source.

Removes CTAs, competitor names, author bios, affiliate links, decorative
images and other junk while preserving all journalistic facts, editorial
images, lists and quotes.
"""
import logging
import os
from typing import TYPE_CHECKING

from .exceptions import BlockedPromptError

if TYPE_CHECKING:
    from .ai_client_gemini import AIClient

logger = logging.getLogger(__name__)

_PROMPT = """\
Você é um editor responsável por limpar HTML bruto extraído de um portal de notícias.

REMOVA do HTML:
- CTAs e junk: "inscreva-se", "siga-nos", "clique aqui", "obrigado por ler", "subscribe", "follow us", "thank you for reading", "sign up" e variações em PT/EN.
- Nomes de portais concorrentes (Omelete, Jovem Nerd, IGN Brasil, AdoroCinema). Substitua por "o veículo" ou reescreva a frase neutralmente.
- Author boxes, bios de autor, formulários de newsletter, links de afiliados.
- Fichas técnicas isoladas com campos como Release Date, Runtime, Director, Cast (quando em formato de tabela ou caixa separada do texto jornalístico).
- Imagens decorativas: logos, ícones, widgets, avatares, banners de interface, placeholders.

PRESERVE integralmente:
- Todos os fatos verificáveis: nomes, datas, números, bilheteria, scores, rankings.
- Imagens editoriais que ilustram o conteúdo (fotos de atores em cena, pôsteres oficiais, screenshots de trailers).
- Todos os itens de listas de conteúdo (séries, filmes, jogos, etc.).
- Citações diretas de atores, diretores e produtores.
- Vídeos embeddados (<iframe>, <figure>).

Retorne apenas o HTML limpo, sem texto adicional, sem JSON, sem marcadores markdown.

CONTEÚDO:
{content}"""


def sanitize(html_raw: str, client: "AIClient") -> str:
    """
    Phase 1: strip CTAs, competitor names and junk from raw HTML.

    Args:
        html_raw: Raw HTML extracted from the source article.
        client:   Shared AIClient instance.

    Returns:
        Cleaned HTML string. Falls back to the original HTML on AI failure.
    """
    if not html_raw or not html_raw.strip():
        return html_raw

    prompt = _PROMPT.format(content=html_raw)

    generation_config = {
        "temperature": 0.1,
        "max_output_tokens": 16000,
    }

    try:
        response_data = client.generate_text(prompt, generation_config=generation_config)

        if isinstance(response_data, tuple):
            response_text, tokens_info = response_data
        else:
            response_text = response_data
            tokens_info = {}

        _log_phase_tokens(tokens_info, phase="sanitize")

        if not response_text or not response_text.strip():
            logger.warning("[SANITIZE] AI returned empty response — using original HTML")
            return html_raw

        # Strip accidental markdown fences the model may add
        text = response_text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            # Remove first line (```html or ```) and last closing ```
            text = "\n".join(lines[1:]).rstrip("`").strip()

        logger.info(f"[SANITIZE] OK — {len(html_raw)} → {len(text)} chars")
        return text

    except BlockedPromptError:
        logger.warning("[SANITIZE] Prompt blocked by model policy")
        raise
    except Exception as exc:
        logger.error(f"[SANITIZE] AI call failed: {exc} — using original HTML", exc_info=True)
        return html_raw


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
        logger.debug(f"[SANITIZE] token logging skipped: {exc}")
