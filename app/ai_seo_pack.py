# app/ai_seo_pack.py
"""
Phase 3 of the 3-phase AI pipeline: SEO packaging.

Receives the rewritten HTML from phase 2 and generates all SEO metadata
fields (title, slug, meta description, excerpt/subtitle, keyphrases, categories, tags,
Yoast meta). The rewritten HTML is injected as `conteudo_final` without
asking the AI to re-generate it, saving significant tokens.
"""
import json
import logging
import os
import re
from typing import TYPE_CHECKING, Any, Dict, Optional

from .exceptions import BlockedPromptError

if TYPE_CHECKING:
    from .ai_client_gemini import AIClient

logger = logging.getLogger(__name__)

_PROMPT_TEMPLATE = """\
Com base no artigo abaixo, gere os metadados SEO.

Retorne EXCLUSIVAMENTE um JSON válido com os campos listados. NÃO inclua o conteúdo HTML do artigo no JSON.

{{
  "titulo_final": "Título SEO (55–65 chars, texto puro, sem HTML)",
  "meta_description": "Frase factual 140–155 chars com keyword, fato principal e contexto editorial, sem CTA",
  "subtitle": "Resumo editorial 140–220 chars para o excerpt do CMS, sem CTA",
  "focus_keyphrase": "frase-chave principal (máx 60 chars)",
  "related_keyphrases": ["variação 1", "variação 2", "variação 3"],
  "slug": "url-amigavel-ate-5-palavras",
  "categorias": [
    {{"nome": "Nome da Franquia/Obra", "grupo": "franquias", "evidence": "trecho literal do texto"}}
  ],
  "tags_sugeridas": ["tag-1", "tag-2", "tag-3", "tag-4", "tag-5"],
  "image_alt_texts": {{"nome-imagem.jpg": "descrição com keyword, ator ou personagem"}},
  "yoast_meta": {{
    "_yoast_wpseo_title": "Título para o Google (máx 65 chars)",
    "_yoast_wpseo_metadesc": "Meta description (máx 155 chars)",
    "_yoast_wpseo_focuskw": "palavra-chave foco",
    "_yoast_news_keywords": "kw1, kw2, kw3",
    "_yoast_wpseo_opengraph-title": "Título para redes sociais",
    "_yoast_wpseo_opengraph-description": "Descrição para redes sociais",
    "_yoast_wpseo_twitter-title": "Título para o Twitter",
    "_yoast_wpseo_twitter-description": "Descrição para o Twitter"
  }}
}}

REGRAS PARA titulo_final:
- Começa com entidade (franquia, ator, plataforma, série, filme)
- Verbo no presente (não infinitivo)
- 55–65 caracteres — MÁXIMO 65
- Priorize clareza e precisão antes de CTR
- Proibido: sensacionalismo, caixa-alta excessiva, inglês, infinitivo, resíduos de tradução
- Nunca use pergunta ou "você"

REGRAS PARA meta_description:
- 140–155 caracteres
- Uma frase factual com fato principal + entidade + contexto, consequência ou tensão editorial
- Deve conter a focus_keyphrase naturalmente
- Não copiar o título nem o primeiro parágrafo literalmente
- Não começar com "Saiba", "Entenda", "Confira" ou "Veja"
- Não usar estrutura mecânica do tipo "Keyword: resumo..."
- Sem CTA e sem adjetivos vazios como "imperdível", "surpreendente" ou "emocionante"

REGRAS PARA subtitle:
- 140–220 caracteres
- Resumo editorial exibido abaixo do título no site e enviado como excerpt do CMS
- Deve ter o mesmo cuidado factual da meta_description, mas com texto próprio
- Não pode ser igual ao título nem igual à meta_description
- Sem CTA, opinião ou fato ausente do artigo

REGRAS PARA categorias:
- Até 3 categorias baseadas em nomes que aparecem literalmente no texto
- Grupos válidos: editorias, franquias, obras
- Não invente categorias genéricas como "Filme" ou "Série"

TÍTULO ORIGINAL: {title}

ARTIGO:
{content}"""


_FALLBACK: Dict[str, Any] = {
    "titulo_final": "",
    "meta_description": "",
    "subtitle": "",
    "focus_keyphrase": "",
    "related_keyphrases": [],
    "slug": "",
    "categorias": [],
    "tags_sugeridas": [],
    "image_alt_texts": {},
    "yoast_meta": {},
}


def seo_pack(
    html_rewritten: str,
    title: str,
    meta: Dict[str, Any],
    client: "AIClient",
) -> Optional[Dict[str, Any]]:
    """
    Phase 3: generate SEO metadata for the rewritten article.

    The rewritten HTML is NOT sent back by the AI — it is injected
    programmatically as `conteudo_final` to save output tokens.

    Args:
        html_rewritten: Final HTML from phase 2.
        title:          Original article title (context for the AI).
        meta:           Dict with key: domain (unused in prompt but available for extension).
        client:         Shared AIClient instance.

    Returns:
        Dict shape-compatible with the legacy `rewritten_data` dict used by
        pipeline.py. Returns None on critical failure so the pipeline can
        mark the article for retry.
    """
    if not html_rewritten or not html_rewritten.strip():
        logger.error("[SEO_PACK] Received empty HTML — cannot generate SEO metadata")
        return None

    prompt = _PROMPT_TEMPLATE.format(
        title=title or "",
        content=html_rewritten,
    )

    generation_config = {
        "response_mime_type": "application/json",
        "temperature": 0.2,
        "max_output_tokens": 2000,
    }

    try:
        response_data = client.generate_text(prompt, generation_config=generation_config)

        if isinstance(response_data, tuple):
            response_text, tokens_info = response_data
        else:
            response_text = response_data
            tokens_info = {}

        _log_phase_tokens(tokens_info, phase="seo_pack")

        if not response_text or not response_text.strip():
            logger.warning("[SEO_PACK] AI returned empty JSON — using fallback metadata")
            return _build_result(html_rewritten, _FALLBACK)

        parsed = _parse_json(response_text)
        if parsed is None:
            logger.error("[SEO_PACK] Could not parse AI JSON response — using fallback")
            return _build_result(html_rewritten, _FALLBACK)

        logger.info(f"[SEO_PACK] OK — title: {parsed.get('titulo_final', '')[:60]}")
        return _build_result(html_rewritten, parsed)

    except BlockedPromptError:
        logger.warning("[SEO_PACK] Prompt blocked by model policy")
        raise
    except Exception as exc:
        logger.error(f"[SEO_PACK] AI call failed: {exc}", exc_info=True)
        return None  # Signals pipeline to retry


def _build_result(html_rewritten: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """Merge AI metadata with html_rewritten into the legacy rewritten_data shape."""
    result = dict(data)
    result["conteudo_final"] = html_rewritten
    result["subtitle"] = result.get("subtitle", "") or ""

    # Alias focus_keyphrase → focus_keyword for backward-compat with pipeline.py
    if "focus_keyphrase" in result and "focus_keyword" not in result:
        result["focus_keyword"] = result["focus_keyphrase"]

    return result


def _parse_json(text: str) -> Optional[Dict[str, Any]]:
    """Parse JSON from AI response, stripping markdown fences if present."""
    text = text.strip()

    # Strip ```json ... ``` fences
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:]).rstrip("`").strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Try to extract first {...} block
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
    return None


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
        logger.debug(f"[SEO_PACK] token logging skipped: {exc}")
