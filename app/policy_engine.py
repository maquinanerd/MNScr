# app/policy_engine.py
"""
Motor central de políticas do MN26.

Responsabilidades:
  - calculate_dynamic_word_policy: régua proporcional à fonte original
  - should_expand: expansão somente quando há razão real
  - should_run_ai_validator: AI Validator condicional
  - should_run_qa_llm: QA-LLM excepcional
  - decide_publish_status: publicar tudo que for processável
  - decide_index_status: forca robots index/follow para publicacoes
  - ArticleBudget: orçamento de tokens por artigo

Não faz chamadas externas. Não publica. Não altera banco.
"""

import os
import logging
from typing import Any, Dict, List, Optional

from .config import (
    AI_POST_WRITER_BUDGET_PER_1K_SOURCE,
    AI_POST_WRITER_BUDGET_TOKENS,
    INDEX_GATE_ENABLED,
    INDEX_GATE_MIN_INTERNAL_LINKS,
    INDEX_GATE_MIN_SCORE,
    INDEX_GATE_REQUIRE_WORDCOUNT_MIN,
    QA_LLM_ENABLED,
    QA_LLM_MIN_ORIGINALITY,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configurações via .env
# ---------------------------------------------------------------------------

PUBLISH_ALL_PROCESSABLE: bool = os.getenv("PUBLISH_ALL_PROCESSABLE", "true").lower() == "true"
BLOCK_PIPELINE_NOINDEX: bool = True
FORCE_INDEX_ALL_POSTS: bool = True
ALLOW_LOW_SCORE_PUBLISH: bool = os.getenv("ALLOW_LOW_SCORE_PUBLISH", "true").lower() == "true"
ALLOW_MANUAL_INDEX_OVERRIDE: bool = os.getenv("ALLOW_MANUAL_INDEX_OVERRIDE", "false").lower() == "true"
ENABLE_QA_LLM_BORDERLINE: bool = os.getenv("ENABLE_QA_LLM_BORDERLINE", "false").lower() == "true"

# Orçamentos de tokens por article_type/origem
AI_BUDGET: Dict[str, int] = {
    "news":       int(os.getenv("AI_BUDGET_NEWS_TOKENS", "12000")),
    "list":       int(os.getenv("AI_BUDGET_LIST_TOKENS", "25000")),
    "guide":      int(os.getenv("AI_BUDGET_GUIDE_TOKENS", "25000")),
    "analysis":   int(os.getenv("AI_BUDGET_ANALYSIS_TOKENS", "25000")),
    "superfeed":  int(os.getenv("AI_BUDGET_SUPERFEED_TOKENS", "35000")),
    "fallback":   int(os.getenv("AI_BUDGET_FALLBACK_TOKENS", "12000")),
}


# ---------------------------------------------------------------------------
# 1. Política dinâmica de tamanho
# ---------------------------------------------------------------------------

def calculate_dynamic_word_policy(
    source_words: int,
    article_type: str = "news",
    origin: str = "fallback",
    source_id: str = "",
    db_id: Any = "?",
) -> Dict[str, Any]:
    """
    Retorna política proporcional à fonte original.

    article_type aceita: "news", "guide", "list", "analysis"
    """
    t = article_type.lower()

    if t in ("guide", "list", "analysis"):
        min_acceptable = int(source_words * 0.75)
        target = int(source_words * 0.90)
        max_recommended = int(source_words * 1.10)
        allow_expansion = True
        reason = f"deep_content_{t}_proportional_to_source"
    else:
        # news (default)
        if source_words <= 350:
            min_acceptable = max(250, int(source_words * 0.75))
            target = int(source_words * 1.00)
            max_recommended = int(source_words * 1.25)
            allow_expansion = False
            reason = "short_news_no_expansion"
        elif source_words <= 600:
            min_acceptable = int(source_words * 0.75)
            target = int(source_words * 1.00)
            max_recommended = int(source_words * 1.25)
            allow_expansion = False  # só se output < min ou perda factual
            reason = "medium_news_proportional"
        elif source_words <= 900:
            min_acceptable = int(source_words * 0.80)
            target = int(source_words * 1.00)
            max_recommended = int(source_words * 1.20)
            allow_expansion = True
            reason = "long_news_allow_if_below_min"
        else:
            min_acceptable = int(source_words * 0.75)
            target = int(source_words * 0.90)
            max_recommended = int(source_words * 1.10)
            allow_expansion = True
            reason = "very_long_news_preserve_depth"

    min_calculated = min_acceptable
    max_calculated = max_recommended
    min_acceptable = min(min_calculated, 550)
    max_recommended = min(max_calculated, 1000)
    target = min(target, max_recommended)
    logger.info(
        "[WORD_POLICY_CAP] source_words=%s min_calculado=%s min_aplicado=%s max_calculado=%s max_aplicado=%s db_id=%s",
        source_words,
        min_calculated,
        min_acceptable,
        max_calculated,
        max_recommended,
        db_id,
    )

    policy = {
        "source_words": source_words,
        "min_acceptable_words": min_acceptable,
        "target_words": target,
        "max_recommended_words": max_recommended,
        "allow_expansion": allow_expansion,
        "reason": reason,
        "article_type": t,
        "origin": origin,
    }

    logger.debug(
        "[WORD_POLICY] type=%s source=%s min=%s target=%s max=%s allow_expansion=%s reason=%s",
        t, source_words, min_acceptable, target, max_recommended, allow_expansion, reason,
    )
    return policy


# ---------------------------------------------------------------------------
# 2. Decisão de expansão
# ---------------------------------------------------------------------------

def should_expand(
    output_words: int,
    policy: Dict[str, Any],
    structural_issues: Optional[List[str]] = None,
    db_id: Any = "?",
    article_type: str = "news",
) -> Dict[str, Any]:
    """
    Decide se a expansão é necessária.

    Expansão apenas quando há razão real — nunca para bater mínimo fixo.
    """
    source_words = policy.get("source_words", 0)
    min_acceptable = policy.get("min_acceptable_words", 0)
    allow_expansion = policy.get("allow_expansion", False)
    issues = structural_issues or []

    # Checar se o output está dentro da faixa proporcional
    if output_words >= min_acceptable:
        logger.info(
            "[EXPANSION_DECISION] needed=false reason=proportional_output_ok "
            "source_words=%s output_words=%s min_acceptable=%s db_id=%s",
            source_words, output_words, min_acceptable, db_id,
        )
        return {"needed": False, "reason": "proportional_output_ok"}

    # Se allow_expansion é False (notícia curta), pular
    if not allow_expansion:
        diff = min_acceptable - output_words
        if diff <= 80:  # diferença pequena não justifica expansão
            logger.info(
                "[EXPANSION_DECISION] needed=false reason=news_short_but_complete "
                "source_words=%s output_words=%s db_id=%s",
                source_words, output_words, db_id,
            )
            return {"needed": False, "reason": "news_short_but_complete"}

        # Se allow_expansion=False mas output está muito abaixo, só expande se conteúdo truncado
        truncation_signals = [i for i in issues if "TRUNCAT" in i or "MISSING" in i]
        if not truncation_signals:
            logger.info(
                "[EXPANSION_DECISION] needed=false reason=expansion_not_allowed_for_type "
                "article_type=%s source_words=%s output_words=%s min_acceptable=%s db_id=%s",
                article_type, source_words, output_words, min_acceptable, db_id,
            )
            return {"needed": False, "reason": "expansion_not_allowed_for_type"}

    # Output abaixo do mínimo dinâmico → expansão permitida
    logger.info(
        "[EXPANSION_DECISION] needed=true reason=output_below_dynamic_min "
        "source_words=%s output_words=%s min_acceptable=%s db_id=%s",
        source_words, output_words, min_acceptable, db_id,
    )
    return {"needed": True, "reason": "output_below_dynamic_min"}


# ---------------------------------------------------------------------------
# 3. Decisão do AI Validator
# ---------------------------------------------------------------------------

def should_run_ai_validator(
    output_words: int,
    policy: Dict[str, Any],
    structural_score: Optional[int] = None,
    title: str = "",
    meta: str = "",
    content_html: str = "",
    article_type: str = "news",
    origin: str = "fallback",
    issues: Optional[List[str]] = None,
    db_id: Any = "?",
) -> Dict[str, Any]:
    """
    Decide se o AI Validator deve rodar.

    Roda apenas quando há risco editorial real que não pode ser resolvido
    por checagem determinística.
    """
    _issues = issues or []
    min_acceptable = policy.get("min_acceptable_words", 0)

    # Condições que DISPENSAM o AI Validator
    title_ok = bool(title and title.strip())
    meta_ok = bool(meta and len(meta.strip()) >= 80)
    content_ok = bool(content_html and len(content_html.strip()) > 100)
    output_proportional = output_words >= min_acceptable

    blocking_issues = [i for i in _issues if any(k in i for k in (
        "TITLE_MISSING", "TITLE_HAS_HTML", "FORBIDDEN_COMPETITOR",
        "SOURCES_SKIPPED_IN_CREDIT",
    ))]

    # Se não há issues bloqueantes e checks básicos passam → pular AI Validator
    if (
        title_ok
        and meta_ok
        and content_ok
        and output_proportional
        and (structural_score is None or structural_score >= 35)
        and not blocking_issues
        and article_type == "news"
    ):
        if structural_score is None:
            reason = "score_unavailable_but_deterministic_checks_passed"
        else:
            reason = "deterministic_checks_passed"
        logger.info(
            "[AI_VALIDATOR_DECISION] run=false reason=%s db_id=%s",
            reason, db_id,
        )
        return {"run": False, "reason": reason}

    # Notícia simples proporcional e completa
    if (
        article_type == "news"
        and output_proportional
        and title_ok
        and content_ok
        and not blocking_issues
    ):
        logger.info(
            "[AI_VALIDATOR_DECISION] run=false reason=short_news_proportional_and_complete db_id=%s",
            db_id,
        )
        return {"run": False, "reason": "short_news_proportional_and_complete"}

    # Score estrutural muito baixo → rodar
    if structural_score is not None and structural_score < 45:
        logger.info(
            "[AI_VALIDATOR_DECISION] run=true reason=low_structural_score score=%s db_id=%s",
            structural_score, db_id,
        )
        return {"run": True, "reason": f"low_structural_score score={structural_score}"}

    # Output muito abaixo do mínimo → rodar
    if output_words < min_acceptable:
        logger.info(
            "[AI_VALIDATOR_DECISION] run=true reason=output_below_min output=%s min=%s db_id=%s",
            output_words, min_acceptable, db_id,
        )
        return {"run": True, "reason": f"output_below_min output={output_words} min={min_acceptable}"}

    # Sem título ou meta → rodar
    if not title_ok:
        logger.info("[AI_VALIDATOR_DECISION] run=true reason=title_missing db_id=%s", db_id)
        return {"run": True, "reason": "title_missing"}
    if not meta_ok:
        logger.info("[AI_VALIDATOR_DECISION] run=true reason=meta_missing_or_short db_id=%s", db_id)
        return {"run": True, "reason": "meta_missing_or_short"}

    # Issues de bloqueio detectadas
    if blocking_issues:
        logger.info(
            "[AI_VALIDATOR_DECISION] run=true reason=blocking_issues issues=%s db_id=%s",
            blocking_issues, db_id,
        )
        return {"run": True, "reason": f"blocking_issues: {blocking_issues}"}

    # Default: pular (economizar tokens)
    logger.info(
        "[AI_VALIDATOR_DECISION] run=false reason=deterministic_checks_passed db_id=%s",
        db_id,
    )
    return {"run": False, "reason": "deterministic_checks_passed"}


# ---------------------------------------------------------------------------
# 4. Decisão do QA-LLM
# ---------------------------------------------------------------------------

def should_run_qa_llm(
    structural_score: int,
    publish_allowed: bool,
    index_allowed: bool,
    article_type: str = "news",
    db_id: Any = "?",
) -> Dict[str, Any]:
    """
    Decide se o QA-LLM de originalidade deve rodar.

    Com QA_LLM_ENABLED=True roda para todos os artigos (resultado vai para o
    gate de indexação, nunca bloqueia publicação).
    Com QA_LLM_ENABLED=False o comportamento é idêntico ao anterior.
    """
    if not QA_LLM_ENABLED:
        logger.info(
            "[QA_LLM_DECISION] run=false reason=feature_disabled_by_config db_id=%s", db_id,
        )
        return {"run": False, "reason": "feature_disabled_by_config"}

    logger.info(
        "[QA_LLM_DECISION] run=true reason=enabled_for_all_articles db_id=%s", db_id,
    )
    return {"run": True, "reason": "enabled_for_all_articles"}


# ---------------------------------------------------------------------------
# 5. Decisão de publicação
# ---------------------------------------------------------------------------

def decide_publish_status(
    content_html: str = "",
    title: str = "",
    technical_errors: Optional[List[str]] = None,
    is_duplicate: bool = False,
    db_id: Any = "?",
    structural_score: int = 0,
) -> Dict[str, Any]:
    """
    PUBLICAÇÃO: tudo que for tecnicamente processável é publicado.

    Score baixo, warnings editoriais, texto curto proporcional
    NÃO bloqueiam publicação.

    Apenas erros técnicos críticos bloqueiam.
    """
    _errors = technical_errors or []

    # Erro técnico crítico
    critical = [e for e in _errors if any(k in e for k in (
        "critical", "CRITICAL", "BLOCKED", "FATAL",
    ))]
    if critical:
        logger.info(
            "[PUBLISH_DECISION] allowed=false reason=critical_technical_failure errors=%s db_id=%s",
            critical, db_id,
        )
        return {"allowed": False, "reason": "critical_technical_failure"}

    # Duplicata já publicada
    if is_duplicate:
        logger.info(
            "[PUBLISH_DECISION] allowed=false reason=duplicate_already_published db_id=%s", db_id,
        )
        return {"allowed": False, "reason": "duplicate_already_published"}

    # Conteúdo vazio
    if not content_html or not content_html.strip():
        logger.info(
            "[PUBLISH_DECISION] allowed=false reason=invalid_empty_content db_id=%s", db_id,
        )
        return {"allowed": False, "reason": "invalid_empty_content"}

    # Título vazio
    if not title or not title.strip():
        logger.info(
            "[PUBLISH_DECISION] allowed=false reason=invalid_empty_content db_id=%s", db_id,
        )
        return {"allowed": False, "reason": "invalid_empty_content"}

    # Tudo OK → publicar
    logger.info(
        "[PUBLISH_DECISION] allowed=true reason=processable_content score=%s db_id=%s",
        structural_score, db_id,
    )
    return {"allowed": True, "reason": "processable_content"}


# ---------------------------------------------------------------------------
# 6. Decisão de indexação
# ---------------------------------------------------------------------------

def decide_index_status(
    structural_score: int,
    publish_allowed: bool,
    db_id: str = "?",
    editorial_issues: Optional[List[str]] = None,
    content_type: str = "unknown",
    word_count: int = 0,
    internal_links: int = 0,
    min_acceptable_words: int = 0,
    editorial_status: str = "",
    qa_llm_result: Optional[Dict[str, Any]] = None,
    index_min_score: int = 30,
) -> Dict[str, Any]:
    """
    Decide robots.

    Regra operacional atual: todo artigo publicado pelo pipeline recebe
    index,follow. O gate editorial continua existindo para diagnostico em
    codigo legado, mas nao pode transformar publicacao em noindex.
    """
    if not publish_allowed:
        logger.info(f"[INDEX_DECISION] allowed=false reason=not_published db_id={db_id}")
        return {
            "allowed": False,
            "indexable": False,
            "robots": "noindex,follow",
            "noindex_value": "1",
            "reason": "not_published",
        }

    if FORCE_INDEX_ALL_POSTS or BLOCK_PIPELINE_NOINDEX:
        logger.info("[INDEX_DECISION] robots=index,follow reason=force_index_all_posts")
        return {
            "allowed": True,
            "indexable": True,
            "robots": "index,follow",
            "noindex_value": "0",
            "reason": "force_index_all_posts",
            "failed": [],
        }

    if INDEX_GATE_ENABLED:
        failed: List[str] = []
        if structural_score < INDEX_GATE_MIN_SCORE:
            failed.append(f"score<{INDEX_GATE_MIN_SCORE}")
        if internal_links < INDEX_GATE_MIN_INTERNAL_LINKS:
            failed.append(f"links_int<{INDEX_GATE_MIN_INTERNAL_LINKS}")
        if INDEX_GATE_REQUIRE_WORDCOUNT_MIN and min_acceptable_words and word_count < min_acceptable_words:
            failed.append(f"words<{min_acceptable_words}")
        if (editorial_status or "").upper() == "FAIL":
            failed.append("editorial_qa=FAIL")

        # (e) QA-LLM originality gate
        _originality: Optional[int] = None
        if QA_LLM_ENABLED and qa_llm_result:
            _originality = qa_llm_result.get("originality_score")
            if _originality is not None and _originality < QA_LLM_MIN_ORIGINALITY:
                failed.append(f"originality<{QA_LLM_MIN_ORIGINALITY}")

        indexable = not failed
        robots = "index,follow" if indexable else "noindex,follow"
        if indexable:
            reason = "passed_index_gate"
            logger.info(
                "[INDEX_DECISION] allowed=True robots=%s score=%s links_int=%s words=%s min_words=%s editorial_status=%s originality=%s db_id=%s",
                robots,
                structural_score,
                internal_links,
                word_count,
                min_acceptable_words,
                editorial_status or "UNKNOWN",
                _originality if _originality is not None else "N/A",
                db_id,
            )
        else:
            reason = "gate_failed"
            logger.info(
                "[INDEX_DECISION] allowed=False robots=%s reason=%s failed=%s score=%s links_int=%s words=%s min_words=%s editorial_status=%s originality=%s db_id=%s",
                robots,
                reason,
                failed,
                structural_score,
                internal_links,
                word_count,
                min_acceptable_words,
                editorial_status or "UNKNOWN",
                _originality if _originality is not None else "N/A",
                db_id,
            )
        return {
            "allowed": indexable,
            "indexable": indexable,
            "robots": robots,
            "noindex_value": "0" if indexable else "1",
            "reason": reason,
            "failed": failed,
        }

    from .editorial_validator import decide_index_allowed

    verdict = decide_index_allowed(
        structural_score=structural_score,
        editorial_issues=editorial_issues or [],
        content_type=content_type,
        word_count=word_count,
        qa_llm_result=qa_llm_result,
        index_min_score=index_min_score,
    )

    indexable = bool(verdict.get("allowed"))
    robots = "index,follow" if indexable else "noindex,follow"
    reason = verdict.get("reason", "quality_gate")
    logger.info(
        "[INDEX_DECISION] allowed=%s reason=%s score=%s words=%s robots=%s db_id=%s",
        indexable,
        reason,
        structural_score,
        word_count,
        robots,
        db_id,
    )
    return {
        "allowed": indexable,
        "indexable": indexable,
        "robots": robots,
        "noindex_value": "0" if indexable else "1",
        "reason": reason,
        "failed": [],
    }


# ---------------------------------------------------------------------------
# 7. Orçamento de tokens por artigo
# ---------------------------------------------------------------------------

class ArticleBudget:
    """Controla o orçamento de tokens por artigo para evitar chamadas descontroladas."""

    def __init__(
        self,
        article_type: str = "news",
        origin: str = "fallback",
        db_id: Any = "?",
        source_words: int = 0,
    ):
        self.db_id = db_id
        self.article_type = article_type
        self.origin = origin
        self.source_words = int(source_words or 0)

        proportional_budget = 0
        if AI_POST_WRITER_BUDGET_PER_1K_SOURCE > 0 and self.source_words > 0:
            proportional_budget = int(
                (self.source_words / 1000) * AI_POST_WRITER_BUDGET_PER_1K_SOURCE
            )
        self.budget = max(AI_POST_WRITER_BUDGET_TOKENS, proportional_budget)

        self.writer_tokens: int = 0
        self.post_writer_tokens: int = 0
        self.used_tokens: int = 0
        self._stages: List[str] = []
        self._skipped: List[str] = []

    def consume(self, tokens: int, stage: str) -> None:
        token_count = max(0, int(tokens or 0))
        if stage == "main_writer":
            self.writer_tokens += token_count
        else:
            self.post_writer_tokens += token_count
        self.used_tokens = self.writer_tokens + self.post_writer_tokens
        if not hasattr(self, 'input_tokens'):
            self.input_tokens = 0
        if not hasattr(self, 'output_tokens'):
            self.output_tokens = 0
        self._stages.append(stage)
        logger.debug(
            "[AI_BUDGET] consumed stage=%s tokens=%s writer_tokens=%s post_writer_tokens=%s budget=%s db_id=%s",
            stage, token_count, self.writer_tokens, self.post_writer_tokens, self.budget, self.db_id,
        )

    def has_budget(self, stage: str, estimated_tokens: int = 1000) -> bool:
        remaining = self.budget - self.post_writer_tokens
        if remaining < estimated_tokens:
            self._skipped.append(stage)
            logger.info(
                "[AI_BUDGET] skip_stage=%s reason=post_budget_exceeded post_writer_tokens=%s budget=%s writer_tokens=%s db_id=%s",
                stage, self.post_writer_tokens, self.budget, self.writer_tokens, self.db_id,
            )
            return False
        logger.info(
            "[AI_BUDGET] stage=%s allowed post_writer_tokens=%s budget=%s writer_tokens=%s db_id=%s",
            stage, self.post_writer_tokens, self.budget, self.writer_tokens, self.db_id,
        )
        return True

    def report(
        self,
        wp_post_id: Optional[int] = None,
        publish: bool = False,
        index: bool = False,
        robots: str = "noindex,follow",
        score: int = 0,
        estimated_usd: float = 0.0,
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> str:
        calls = len(self._stages)
        skipped = ",".join(self._skipped) if self._skipped else "none"
        in_tok = getattr(self, 'input_tokens', 0)
        out_tok = getattr(self, 'output_tokens', 0)
        total = self.used_tokens or (in_tok + out_tok)
        
        prices = {
            "gemini-3.1-flash-lite": (0.25, 1.50),
            "gemini-2.5-flash-lite": (0.10, 0.40),
            "gemini-2.5-flash": (0.30, 2.50),
        }
        # Tokens are currently aggregated across stages, so use the 3.1 rate as a conservative ceiling.
        # TODO: track input/output tokens per model for exact mixed-model article costs.
        cost_model = "gemini-3.1-flash-lite"
        input_price, output_price = prices.get(cost_model, prices["gemini-3.1-flash-lite"])
        usd = (in_tok * input_price / 1000000) + (out_tok * output_price / 1000000)

        line = (
            f"[AI_COST_ARTICLE] db_id={self.db_id} wp_post_id={wp_post_id or 'N/A'} "
            f"calls={calls} input_tokens={in_tok} output_tokens={out_tok} "
            f"total_tokens={total} estimated_usd={usd:.4f} "
            f"skipped={skipped} publish={publish} index={index} "
            f"robots={robots} score={score}"
        )
        logger.info(line)
        return line


# ---------------------------------------------------------------------------
# 8. Classificação de article_type a partir de sinais do conteúdo
# ---------------------------------------------------------------------------

import re as _re

_LIST_SIGNALS = _re.compile(
    r"\b(\d{1,2}\s*(melhores?|piores?|filmes?|s[eé]ries?|personagens?|motivos?|"
    r"raz[oõ]es?|momentos?|coisas?|fatos?|cenas?|epis[oó]dios?|jogos?|quotes?|frases?|cita[cç][oõ]es?)|"
    r"melhores?\s+\w+|piores?\s+\w+|ranking|lista\s+d[eo]|top\s+\d+|"
    r"selecion[ao]|subestimad|esquecid|explicad|ordem\s+correto?|"
    r"vale\s+assistir|val[ae]\s+a\s+pena|quotes?|frases?|cita[cç][oõ]es?)\b",
    _re.IGNORECASE,
)
_GUIDE_SIGNALS = _re.compile(
    r"\b(como\s+\w+|guia\b|entenda\s+|explica[cç][aã]o|ordem\s+cronol|"
    r"cronologia|para\s+iniciante|passo\s+a\s+passo|tutorial)\b",
    _re.IGNORECASE,
)
_ANALYSIS_SIGNALS = _re.compile(
    r"\b(an[aá]lise|review|resenha|avalia[cç][aã]o|opini[aã]o|"
    r"acredito\s+que|minha\s+vis[aã]o|coluna)\b",
    _re.IGNORECASE,
)
_NEWS_SIGNALS = _re.compile(
    r"\b(lan[cç]amento|atualiza[cç][aã]o|demiss[aã]o|declara[cç][aã]o|"
    r"trailer|rumor|an[uú]ncio|data\s+de\s+lançamento|confirmado|"
    r"confirma|revela|estreia|cancelado|renovado|estreia\s+em)\b",
    _re.IGNORECASE,
)


def classify_article_type(
    title: str,
    source_words: int,
    content_snippet: str = "",
    db_id: Any = "?",
    previous_type: str = "",
) -> str:
    """
    Classifica o tipo editorial do artigo com base em sinais.

    Nunca aplica régua de guide/list em notícia factual curta.
    """
    combined = f"{title} {content_snippet[:1500]}"

    # Listas têm precedência máxima por sinal explícito
    if _LIST_SIGNALS.search(combined):
        detected = "list"
    elif _GUIDE_SIGNALS.search(combined) and source_words >= 600:
        detected = "guide"
    elif _ANALYSIS_SIGNALS.search(combined):
        detected = "analysis"
    elif _NEWS_SIGNALS.search(combined) or source_words < 700:
        detected = "news"
    elif source_words >= 1500:
        detected = "guide"
    else:
        detected = "news"

    # Proteção: notícia factual curta nunca vira guide/list
    if source_words <= 600 and detected in ("guide", "list"):
        logger.info(
            "[ARTICLE_TYPE] corrected=news previous=%s reason=short_factual_article "
            "source_words=%s db_id=%s",
            detected, source_words, db_id,
        )
        return "news"

    if detected != previous_type and previous_type:
        logger.info(
            "[ARTICLE_TYPE] corrected=%s previous=%s reason=signal_based source_words=%s db_id=%s",
            detected, previous_type, source_words, db_id,
        )
    else:
        logger.info(
            "[ARTICLE_TYPE] detected=%s reason=signal_based source_words=%s db_id=%s",
            detected, source_words, db_id,
        )

    return detected
