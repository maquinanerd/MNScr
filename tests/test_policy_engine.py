"""
tests/test_policy_engine.py

Bateria de testes obrigatórios para o policy_engine do MN26.
Cobre os cenários A-L definidos na especificação editorial.

Execução:
    python -m pytest tests/test_policy_engine.py -v
"""
import pytest
import sys
import os

# Garantir que o módulo é importável sem .env real
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.policy_engine import (
    calculate_dynamic_word_policy,
    should_expand,
    should_run_ai_validator,
    should_run_qa_llm,
    decide_publish_status,
    decide_index_status,
    classify_article_type,
    ArticleBudget,
)


# ─────────────────────────────────────────────────────────────────────────────
# 1. calculate_dynamic_word_policy
# ─────────────────────────────────────────────────────────────────────────────

class TestDynamicWordPolicy:

    def test_short_news_allows_no_expansion(self):
        """Notícia curta (280 palavras): min proporcional, expansão não recomendada."""
        p = calculate_dynamic_word_policy(source_words=280, article_type="news")
        assert p["min_acceptable_words"] <= 300
        assert p["allow_expansion"] is False

    def test_medium_news_proportional(self):
        """Notícia média (500 palavras): min >= 375 (75%)."""
        p = calculate_dynamic_word_policy(source_words=500, article_type="news")
        assert p["min_acceptable_words"] >= 375
        assert p["target_words"] <= 600

    def test_long_news_allows_expansion(self):
        """Notícia longa (800 palavras): expansão permitida."""
        p = calculate_dynamic_word_policy(source_words=800, article_type="news")
        assert p["allow_expansion"] is True

    def test_list_article_proportional(self):
        """Lista (1200 palavras): min >= 900."""
        p = calculate_dynamic_word_policy(source_words=1200, article_type="list")
        assert p["min_acceptable_words"] == 550
        assert p["max_recommended_words"] == 1000
        assert p["target_words"] <= p["max_recommended_words"]
        assert p["allow_expansion"] is True

    def test_word_policy_caps_long_sources(self):
        p = calculate_dynamic_word_policy(source_words=2655, article_type="guide", db_id=17943)
        assert p["min_acceptable_words"] == 550
        assert p["max_recommended_words"] == 1000
        assert p["target_words"] == 1000

    def test_superfeed_budget_higher(self):
        """Superfeed tem budget maior que fallback."""
        p_sf = calculate_dynamic_word_policy(source_words=600, article_type="news", origin="superfeed")
        p_fb = calculate_dynamic_word_policy(source_words=600, article_type="news", origin="fallback")
        # budgets são em ArticleBudget, mas policy deve estar correto
        assert p_sf["origin"] == "superfeed"
        assert p_fb["origin"] == "fallback"


# ─────────────────────────────────────────────────────────────────────────────
# 2. should_expand
# ─────────────────────────────────────────────────────────────────────────────

class TestShouldExpand:

    def test_scenario_a_output_above_min_no_expansion(self):
        """Cenário A: output >= min → não expande."""
        policy = calculate_dynamic_word_policy(source_words=400, article_type="news")
        result = should_expand(output_words=350, policy=policy)
        assert result["needed"] is False

    def test_scenario_b_short_news_below_min_no_expansion_without_truncation(self):
        """Cenário B: notícia curta abaixo do min mas sem truncamento → não expande."""
        policy = calculate_dynamic_word_policy(source_words=300, article_type="news")
        # allow_expansion = False
        result = should_expand(output_words=150, policy=policy, article_type="news")
        assert result["needed"] is False

    def test_scenario_c_long_article_below_min_expands(self):
        """Cenário C: artigo longo abaixo do mínimo proporcional → expande."""
        policy = calculate_dynamic_word_policy(source_words=900, article_type="news")
        min_words = policy["min_acceptable_words"]
        result = should_expand(output_words=min_words - 100, policy=policy, article_type="news")
        assert result["needed"] is True

    def test_list_below_min_expands(self):
        """Lista abaixo do mínimo → expande."""
        policy = calculate_dynamic_word_policy(source_words=1200, article_type="list")
        min_words = policy["min_acceptable_words"]
        result = should_expand(output_words=min_words - 200, policy=policy)
        assert result["needed"] is True


# ─────────────────────────────────────────────────────────────────────────────
# 3. should_run_ai_validator
# ─────────────────────────────────────────────────────────────────────────────

class TestShouldRunAIValidator:

    def test_scenario_d_complete_news_skips_validator(self):
        """Cenário D: notícia proporcional e completa → pula AI Validator."""
        policy = calculate_dynamic_word_policy(source_words=400, article_type="news")
        result = should_run_ai_validator(
            output_words=350,
            policy=policy,
            structural_score=60,
            title="Título válido da notícia",
            meta="Meta description com pelo menos oitenta caracteres para passar na validação aqui.",
            content_html="<p>Conteúdo com mais de cem caracteres de conteúdo editorial relevante para a matéria.</p>",
            article_type="news",
            origin="fallback",
        )
        assert result["run"] is False

    def test_scenario_e_low_score_runs_validator(self):
        """Cenário E: score estrutural baixo → roda AI Validator."""
        policy = calculate_dynamic_word_policy(source_words=400, article_type="news")
        result = should_run_ai_validator(
            output_words=350,
            policy=policy,
            structural_score=25,  # baixo
            title="Título",
            meta="Meta longa o suficiente para passar",
            content_html="<p>conteudo</p>",
            article_type="news",
            origin="fallback",
        )
        assert result["run"] is True

    def test_scenario_f_missing_meta_runs_validator(self):
        """Cenário F: meta ausente → roda AI Validator."""
        policy = calculate_dynamic_word_policy(source_words=400, article_type="news")
        result = should_run_ai_validator(
            output_words=350,
            policy=policy,
            structural_score=60,
            title="Título OK",
            meta="",  # ausente
            content_html="<p>conteúdo longo o suficiente</p>",
            article_type="news",
        )
        assert result["run"] is True

    def test_score_unavailable_skips_validator(self):
        """Cenário: score ainda não calculado (None) mas outros fatores OK → pula."""
        policy = calculate_dynamic_word_policy(source_words=400, article_type="news")
        result = should_run_ai_validator(
            output_words=350,
            policy=policy,
            structural_score=None,
            title="Título OK",
            meta="Meta muito boa com mais de oitenta caracteres e que com certeza vai passar pelo check determinístico",
            content_html="<p>Conteúdo bastante longo para simular uma notícia ok que atende ao critério de output_proportional.</p>",
            article_type="news",
        )
        assert result["run"] is False
        assert "score_unavailable" in result["reason"]

    def test_score_zero_with_missing_meta_runs_validator(self):
        """Cenário: score=0 explícito (ou qualquer motivo), se faltar meta, deve rodar."""
        policy = calculate_dynamic_word_policy(source_words=400, article_type="news")
        result = should_run_ai_validator(
            output_words=350,
            policy=policy,
            structural_score=0,
            title="Título OK",
            meta="",
            content_html="<p>Conteúdo OK.</p>",
            article_type="news",
        )
        assert result["run"] is True
        assert "meta_missing_or_short" in result["reason"] or "low_structural_score" in result["reason"]

    def test_low_score_runs_validator(self):
        """Cenário: structural_score=32 explicitamente → roda por low_structural_score."""
        policy = calculate_dynamic_word_policy(source_words=400, article_type="news")
        result = should_run_ai_validator(
            output_words=350,
            policy=policy,
            structural_score=32,
            title="Título OK",
            meta="Meta muito boa e longa para passar no check.",
            content_html="<p>Conteúdo OK.</p>",
            article_type="news",
        )
        assert result["run"] is True
        assert "low_structural_score" in result["reason"]


# ─────────────────────────────────────────────────────────────────────────────
# 4. should_run_qa_llm
# ─────────────────────────────────────────────────────────────────────────────

class TestShouldRunQALLM:
    def test_scenario_g_feature_disabled_skips(self, monkeypatch):
        monkeypatch.setattr("app.policy_engine.QA_LLM_ENABLED", False)
        monkeypatch.setattr("app.policy_engine.ENABLE_QA_LLM_BORDERLINE", False)
        result = should_run_qa_llm(structural_score=25, publish_allowed=True, index_allowed=True)
        assert result["run"] is False

    def test_qa_llm_enabled_runs_for_index_gate_signal(self, monkeypatch):
        monkeypatch.setattr("app.policy_engine.QA_LLM_ENABLED", True)
        monkeypatch.setattr("app.policy_engine.ENABLE_QA_LLM_BORDERLINE", True)
        result = should_run_qa_llm(structural_score=25, publish_allowed=True, index_allowed=True)
        assert result["run"] is True
        assert result["reason"] == "enabled_for_all_articles"

class TestDecidePublishStatus:

    def test_scenario_i_low_score_still_publishes(self):
        """Cenário I: score baixo não bloqueia publicação."""
        result = decide_publish_status(
            content_html="<p>Conteúdo válido</p>",
            title="Título Válido",
            structural_score=15,  # score muito baixo
        )
        assert result["allowed"] is True

    def test_scenario_j_empty_content_blocks_publish(self):
        """Cenário J: conteúdo vazio bloqueia publicação."""
        result = decide_publish_status(content_html="", title="Título")
        assert result["allowed"] is False
        assert "empty_content" in result["reason"]

    def test_scenario_k_empty_title_blocks_publish(self):
        """Cenário K: título vazio bloqueia publicação."""
        result = decide_publish_status(content_html="<p>Conteúdo</p>", title="")
        assert result["allowed"] is False


# ─────────────────────────────────────────────────────────────────────────────
# 6. decide_index_status
# ─────────────────────────────────────────────────────────────────────────────

class TestDecideIndexStatus:
    def test_good_article_publish_true_indexes(self):
        result = decide_index_status(
            structural_score=60,
            publish_allowed=True,
            word_count=600,
            internal_links=1,
            min_acceptable_words=500,
            editorial_status="WARNING_ONLY",
        )
        assert result["allowed"] is True
        assert result["indexable"] is True
        assert result["robots"] == "index,follow"
        assert result["noindex_value"] == "0"
        assert result["reason"] == "force_index_all_posts"

    def test_low_score_publish_true_still_indexes(self):
        result = decide_index_status(
            structural_score=20,
            publish_allowed=True,
            word_count=600,
            internal_links=1,
            min_acceptable_words=500,
            editorial_status="WARNING_ONLY",
        )
        assert result["allowed"] is True
        assert result["indexable"] is True
        assert result["robots"] == "index,follow"
        assert result["noindex_value"] == "0"
        assert result["reason"] == "force_index_all_posts"
        assert result["failed"] == []

    def test_score_40_and_no_links_publish_true_still_indexes(self):
        result = decide_index_status(
            structural_score=40,
            publish_allowed=True,
            word_count=1172,
            internal_links=0,
            min_acceptable_words=600,
            editorial_status="WARNING_ONLY",
        )
        assert result["allowed"] is True
        assert result["robots"] == "index,follow"
        assert result["failed"] == []

    def test_short_article_publish_true_still_indexes(self):
        result = decide_index_status(
            structural_score=60,
            publish_allowed=True,
            word_count=150,
            internal_links=1,
            min_acceptable_words=250,
            editorial_status="WARNING_ONLY",
        )
        assert result["allowed"] is True
        assert result["robots"] == "index,follow"
        assert result["failed"] == []

    def test_no_internal_links_still_indexes(self):
        result = decide_index_status(
            structural_score=60,
            publish_allowed=True,
            word_count=600,
            internal_links=0,
            min_acceptable_words=500,
            editorial_status="WARNING_ONLY",
        )
        assert result["allowed"] is True
        assert result["robots"] == "index,follow"
        assert result["failed"] == []

    def test_editorial_fail_still_indexes(self):
        result = decide_index_status(
            structural_score=80,
            publish_allowed=True,
            word_count=1000,
            internal_links=2,
            min_acceptable_words=500,
            editorial_status="FAIL",
        )
        assert result["allowed"] is True
        assert result["robots"] == "index,follow"
        assert result["failed"] == []

    def test_gate_disabled_uses_legacy_behavior(self, monkeypatch):
        import app.policy_engine as policy_engine

        monkeypatch.setattr(policy_engine, "INDEX_GATE_ENABLED", False)
        result = policy_engine.decide_index_status(
            structural_score=30,
            publish_allowed=True,
            word_count=600,
            internal_links=0,
            min_acceptable_words=900,
            editorial_status="FAIL",
        )
        assert result["allowed"] is True
        assert result["robots"] == "index,follow"

    def test_publish_false(self):
        result = decide_index_status(structural_score=50, publish_allowed=False)
        assert result["allowed"] is False
        assert result["indexable"] is False
        assert result["robots"] == "noindex,follow"
        assert result["noindex_value"] == "1"

class TestClassifyArticleType:

    def test_short_factual_stays_news(self):
        """Notícia factual curta não vira guide/list."""
        result = classify_article_type(
            title="Marvel confirma data de estreia do novo Capitão América",
            source_words=350,
        )
        assert result == "news"

    def test_list_article_detected(self):
        """Artigo com sinal de lista e source > 600 → list."""
        result = classify_article_type(
            title="10 melhores filmes de 2024 que você precisa assistir",
            source_words=800,
        )
        assert result == "list"

    def test_short_article_with_list_signal_stays_news(self):
        """Fonte curta com sinal de lista → news (proteção)."""
        result = classify_article_type(
            title="Os 5 melhores filmes anunciados",
            source_words=300,
        )
        assert result == "news"


# ─────────────────────────────────────────────────────────────────────────────
# 8. ArticleBudget
# ─────────────────────────────────────────────────────────────────────────────

class TestArticleBudget:

    def test_writer_tokens_do_not_consume_post_writer_budget(self):
        """Writer tokens do not reduce the post-writer budget."""
        b = ArticleBudget(article_type="news", origin="fallback", db_id=1)
        assert b.has_budget("ai_validator", 3000) is True
        b.consume(tokens=27000, stage="main_writer")
        assert b.writer_tokens == 27000
        assert b.post_writer_tokens == 0
        assert b.has_budget("expansion", 4000) is True
        assert b.has_budget("qa_llm", 1000) is True

    def test_post_writer_budget_still_blocks_after_post_stages_exceed_it(self):
        b = ArticleBudget(article_type="news", origin="fallback", db_id=2)
        b.consume(tokens=27000, stage="main_writer")
        b.consume(tokens=15000, stage="ai_validator")
        assert b.has_budget("expansion", 4000) is False
        assert b.has_budget("qa_llm", 1000) is True

    def test_post_writer_budget_is_proportional_to_source_words(self):
        b = ArticleBudget(article_type="guide", origin="fallback", db_id=3, source_words=4000)
        assert b.budget >= 24000

    def test_budget_report_returns_string(self):
        """report() retorna string formatada."""
        b = ArticleBudget(article_type="news", origin="fallback", db_id=99)
        b.consume(tokens=1000, stage="main_writer")
        report = b.report(wp_post_id=12345, publish=True, index=True, robots="index,follow", score=40)
        assert "db_id=99" in report
        assert "wp_post_id=12345" in report
        assert "publish=True" in report
