# app/ai_client_gemini.py
import logging
import os
import random
import time
import warnings
from http import HTTPStatus

from google.api_core import exceptions as google_exceptions

from .exceptions import BlockedPromptError
from .limiter import KeyPool, RateLimiter

try:
    from google import genai as modern_genai
except ImportError:
    modern_genai = None

legacy_genai = None
if modern_genai is None:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FutureWarning)
        import google.generativeai as legacy_genai

ARTICLE_RESPONSE_SCHEMA = {
    "type": "object",
    "required": ["resultados"],
    "properties": {
        "resultados": {
            "type": "array",
            "minItems": 1,
            "maxItems": 1,
            "items": {
                "type": "object",
                "required": [
                    "titulo_final",
                    "conteudo_final",
                    "meta_description",
                    "subtitle",
                    "focus_keyphrase",
                    "slug",
                ],
                "properties": {
                    "titulo_final": {"type": "string"},
                    "conteudo_final": {"type": "string"},
                    "meta_description": {"type": "string"},
                    "subtitle": {"type": "string"},
                    "focus_keyphrase": {"type": "string"},
                    "related_keyphrases": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "slug": {"type": "string"},
                    "categorias": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "nome": {"type": "string"},
                                "grupo": {"type": "string"},
                                "evidence": {"type": "string"},
                            },
                        },
                    },
                    "tags_sugeridas": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    # Obras citadas no texto. NAO e taxonomia: e o que a
                    # resolucao de entidade pergunta ao catalogo do Cinerie.
                    # Sem este campo, filme e serie so chegavam la quando o
                    # texto trazia `Titulo (ano)` colado — o que a prosa em
                    # portugues praticamente nunca faz.
                    "obras_citadas": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "titulo": {"type": "string"},
                                "ano": {"type": "integer"},
                            },
                        },
                    },
                    "image_alt_texts": {"type": "object"},
                    # Sugestões sociais NEUTRAS. Este bloco era `yoast_meta`, o
                    # objeto de um plugin de WordPress: pedir aquele formato
                    # trazia junto `_yoast_wpseo_canonical` e o controle de
                    # indexação, decisões que pertencem ao lado público. Os
                    # valores úteis continuam aqui, com os nomes que o contrato
                    # do Cinerie realmente aceita.
                    "openGraphTitleSuggestion": {"type": "string"},
                    "openGraphDescriptionSuggestion": {"type": "string"},
                    "twitterTitleSuggestion": {"type": "string"},
                    "twitterDescriptionSuggestion": {"type": "string"},
                },
            },
        }
    },
}


#: Schema for semantic claim extraction (factual_claim_extraction_v2 prompt).
#: Distinct from ARTICLE_RESPONSE_SCHEMA — forcing the article schema here
#: makes the model write a second article instead of listing claims.
CLAIMS_RESPONSE_SCHEMA = {
    "type": "object",
    "required": ["claims"],
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["display_text", "claim_type", "location"],
                "properties": {
                    "display_text": {"type": "string"},
                    "claim_type": {"type": "string"},
                    "subject": {"type": "string"},
                    "predicate": {"type": "string"},
                    "value": {"type": "string"},
                    "semantic_evidence_query": {"type": "string"},
                    "location": {"type": "string"},
                    "source_sentence": {"type": "string"},
                    "is_material": {"type": "boolean"},
                    "confidence": {"type": "number"},
                },
            },
        }
    },
}


def _gemini_log_prefix(model: str = "") -> str:
    """Returns log prefix that reflects the actual model being used."""
    if model.startswith("gemini-3.1"):
        return "[GEMINI 3.1]"
    return "[GEMINI]"
MODEL = os.getenv("GEMINI_MODEL_ID", "gemini-3.1-flash-lite")
FALLBACK_MODELS = [
    model.strip()
    for model in os.getenv("GEMINI_FALLBACK_MODELS", "gemini-2.5-flash").split(",")
    if model.strip()
]
MODEL_CHAIN = list(dict.fromkeys([MODEL, *FALLBACK_MODELS]))
MAX_AI_ATTEMPTS = int(os.getenv("AI_MAX_RETRIES", "8"))
TRANSIENT_STATUS_CODES = {
    int(HTTPStatus.INTERNAL_SERVER_ERROR),
    int(HTTPStatus.BAD_GATEWAY),
    int(HTTPStatus.SERVICE_UNAVAILABLE),
    int(HTTPStatus.GATEWAY_TIMEOUT),
}


def parse_retry_after(headers):
    """Parses Retry-After header, returns seconds as int or None."""
    if "retry-after" in headers:
        try:
            return int(headers["retry-after"])
        except (ValueError, TypeError):
            return None
    return None


def _stringify_reason(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    name = getattr(value, "name", None)
    if name:
        return str(name)
    text = str(value)
    return text if text else None


def _get_block_reason(resp) -> str | None:
    prompt_feedback = getattr(resp, "prompt_feedback", None)
    if not prompt_feedback:
        return None

    reason = _stringify_reason(getattr(prompt_feedback, "block_reason", None))
    if reason and reason not in {"BLOCK_REASON_UNSPECIFIED", "None", "0"}:
        return reason
    return None


def _raise_if_prompt_blocked(resp) -> None:
    block_reason = _get_block_reason(resp)
    candidates = getattr(resp, "candidates", None) or []
    if block_reason and not candidates:
        raise BlockedPromptError(
            f"Prompt blocked by model policy: {block_reason}",
            block_reason=block_reason,
        )


def _extract_text(resp) -> str:
    _raise_if_prompt_blocked(resp)

    candidates = getattr(resp, "candidates", None) or []
    collected = []
    for candidate in candidates:
        content = getattr(candidate, "content", None)
        parts = getattr(content, "parts", None) or []
        for part in parts:
            part_text = getattr(part, "text", None)
            if part_text:
                collected.append(part_text)
    if collected:
        return "\n".join(collected).strip()

    try:
        text = getattr(resp, "text", None)
    except ValueError as exc:
        block_reason = _get_block_reason(resp)
        if block_reason:
            raise BlockedPromptError(
                f"Prompt blocked by model policy: {block_reason}",
                block_reason=block_reason,
            ) from exc
        raise

    if text:
        return text.strip()
    return ""


def _extract_status_code(exc: Exception) -> int | None:
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if code is None:
        response = getattr(exc, "response", None)
        code = getattr(response, "status_code", None)
    try:
        return int(code)
    except (TypeError, ValueError):
        text = str(exc)
        for status_code in (429, 500, 502, 503, 504):
            if str(status_code) in text:
                return status_code
    return None


def _is_transient_ai_error(exc: Exception) -> bool:
    return _extract_status_code(exc) in TRANSIENT_STATUS_CODES


def _generate_content(api_key: str, prompt: str, model: str = MODEL, **kwargs):
    response_schema = kwargs.pop("response_schema", ARTICLE_RESPONSE_SCHEMA)
    incoming_config = kwargs.get("generation_config") or {}
    base_config = {
        "response_mime_type": "application/json",
        "temperature": 0.2,
        "top_p": 0.9,
        "max_output_tokens": 32000,
        **incoming_config,
    }
    if modern_genai is not None:
        client = modern_genai.Client(api_key=api_key)
        config_with_schema = {**base_config}
        if response_schema is not None:
            config_with_schema["response_schema"] = response_schema
        try:
            return client.models.generate_content(
                model=model,
                contents=prompt,
                config=config_with_schema,
            )
        except (TypeError, ValueError) as exc:
            logging.warning(
                "[GEMINI_SCHEMA] SDK rejeitou response_schema como dict (%s). Rodando sem schema. Atualize google-genai para versao mais recente.",
                type(exc).__name__,
            )
            return client.models.generate_content(
                model=model,
                contents=prompt,
                config=base_config,
            )

    legacy_genai.configure(api_key=api_key)
    legacy_model = legacy_genai.GenerativeModel(model)
    return legacy_model.generate_content(prompt, **{**kwargs, "generation_config": base_config})


class AIClient:
    def __init__(self, keys, min_interval_s, backoff_base=20, backoff_max=300):
        self.pool = KeyPool(keys)
        self.rl = RateLimiter(min_interval_s)
        self.backoff_base = backoff_base
        self.backoff_max = backoff_max
        self.last_used_key = None
        self.last_used_model = MODEL
        logging.info(f"{_gemini_log_prefix(MODEL_CHAIN[0] if MODEL_CHAIN else '')} AI CLIENT: Inicializado com {len(keys)} chaves de API | modelos={MODEL_CHAIN}")

    def generate_text(self, prompt: str, **kwargs) -> tuple:
        """Generate text and return (text, tokens_info) tuple."""
        model_override = kwargs.pop("model_override", None)
        generation_config = kwargs.get("generation_config") or {}
        max_output_tokens = int(generation_config.get("max_output_tokens") or 32000)
        model_chain = MODEL_CHAIN
        if model_override:
            model_override = str(model_override).strip()
            model_chain = [model_override, *[model for model in MODEL_CHAIN if model != model_override]]

        backoff = self.backoff_base
        attempt = 0

        while attempt < MAX_AI_ATTEMPTS:
            attempt += 1
            slot = self.pool.next_ready()
            self.rl.wait()
            self.last_used_key = slot.key

            last_error: Exception | None = None
            key_penalized = False

            for model in model_chain:
                self.last_used_model = model
                try:
                    logging.info(f"{_gemini_log_prefix(model)} IA TENTATIVA {attempt}: Chave ****{slot.key[-4:]} | Modelo {model}")
                    resp = _generate_content(slot.key, prompt, model=model, **kwargs)

                    tokens_info = {
                        "prompt_tokens": 0,
                        "completion_tokens": 0,
                        "model": model,
                        "max_output_tokens": max_output_tokens,
                        "truncated_by_token_ceiling": False,
                    }
                    if hasattr(resp, "usage_metadata") and resp.usage_metadata:
                        prompt_tokens = getattr(resp.usage_metadata, "prompt_token_count", None)
                        completion_tokens = getattr(resp.usage_metadata, "candidates_token_count", None)
                        cached_tokens = (
                            getattr(resp.usage_metadata, "cached_content_token_count", None)
                            or getattr(resp.usage_metadata, "cachedContentTokenCount", None)
                            or 0
                        )

                        if prompt_tokens is not None:
                            tokens_info["prompt_tokens"] = int(prompt_tokens)
                        if completion_tokens is not None:
                            tokens_info["completion_tokens"] = int(completion_tokens)
                            tokens_info["truncated_by_token_ceiling"] = (
                                int(completion_tokens) * 100 >= max_output_tokens * 95
                            )
                        tokens_info["cached_tokens"] = int(cached_tokens)

                        total_tokens = tokens_info["prompt_tokens"] + tokens_info["completion_tokens"]
                        logging.info(
                            "TOKENS CAPTURADOS: Entrada=%s | Saida=%s | Total=%s | Cache=%s | Modelo=%s",
                            tokens_info["prompt_tokens"],
                            tokens_info["completion_tokens"],
                            total_tokens,
                            tokens_info["cached_tokens"],
                            model,
                        )
                    else:
                        logging.warning(f"AVISO: Sem metadata de tokens. Estado da resposta: {type(resp).__name__}")

                    total = tokens_info["prompt_tokens"] + tokens_info["completion_tokens"]
                    if total == 0:
                        logging.warning("TOKENS ZERADOS! Pode indicar erro na resposta.")
                        logging.debug(
                            "Response type: %s, Has usage_metadata: %s",
                            type(resp),
                            hasattr(resp, "usage_metadata"),
                        )

                    text = _extract_text(resp)
                    logging.info(
                        f"{_gemini_log_prefix(model)} IA OK: Tentativa {attempt} sucesso com chave ****{slot.key[-4:]} | Modelo {model}"
                    )
                    return (text, tokens_info)

                except BlockedPromptError as exc:
                    logging.warning(
                        f"IA BLOQUEADA: Chave ****{slot.key[-4:]} | Modelo {model} | "
                        f"Motivo={exc.block_reason or 'unknown'}"
                    )
                    raise

                except google_exceptions.ResourceExhausted as exc:
                    logging.error(f"IA ERRO 429: Chave ****{slot.key[-4:]} quota excedida | Modelo {model}")
                    self.pool.penalize(slot, retry_after=30)
                    key_penalized = True
                    last_error = exc
                    break

                except google_exceptions.GoogleAPICallError as exc:
                    status_code = _extract_status_code(exc)
                    if status_code in TRANSIENT_STATUS_CODES:
                        logging.warning(
                            f"IA ERRO {status_code}: Chave ****{slot.key[-4:]} | Modelo {model} | tentando fallback"
                        )
                        last_error = exc
                        continue
                    logging.error(f"IA ERRO {status_code or 'desconhecido'}: {exc}")
                    raise RuntimeError(f"Erro {status_code or 'desconhecido'}: {exc}") from exc

                except Exception as exc:
                    status_code = _extract_status_code(exc)
                    if status_code == 429:
                        logging.error(f"IA ERRO 429: Chave ****{slot.key[-4:]} quota excedida | Modelo {model}")
                        self.pool.penalize(slot, retry_after=30)
                        key_penalized = True
                        last_error = exc
                        break

                    if _is_transient_ai_error(exc):
                        logging.warning(
                            f"IA ERRO {status_code}: Chave ****{slot.key[-4:]} | Modelo {model} | "
                            f"fallback/retry acionado: {exc}"
                        )
                        last_error = exc
                        continue

                    logging.error(
                        f"IA ERRO: Excecao inesperada na chave ****{slot.key[-4:]} | Modelo {model}: {exc}",
                        exc_info=True,
                    )
                    self.pool.penalize(slot, retry_after=60)
                    key_penalized = True
                    last_error = exc
                    break

            if not key_penalized:
                retry_after = min(backoff, self.backoff_max) + random.uniform(0, 5)
                self.pool.penalize(slot, retry_after=retry_after)

            backoff = min(backoff * 2, self.backoff_max)
            logging.warning(
                "IA RETRY: tentativa %s/%s falhou em todos os modelos. Ultimo erro: %s",
                attempt,
                MAX_AI_ATTEMPTS,
                str(last_error)[:200] if last_error else "desconhecido",
            )
            time.sleep(min(5, backoff))

        raise RuntimeError(f"IA falhou apos {MAX_AI_ATTEMPTS} tentativas usando modelos {model_chain}")

    def get_last_used_key(self) -> str:
        """Retorna as ultimas 4 caracteres da chave usada (para logging)."""
        if self.last_used_key:
            return f"****{self.last_used_key[-4:]}"
        return "UNKNOWN"

    def get_last_used_model(self) -> str:
        return self.last_used_model or MODEL
