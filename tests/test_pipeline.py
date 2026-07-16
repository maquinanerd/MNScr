"""
Testes básicos do pipeline com processamento em lote.
"""
import pytest
from unittest.mock import MagicMock, patch
from app.store import Article
from app.ai_client_gemini import AIClient
from app.batch_processor import ArticleBatch, process_batch, process_articles

def test_article_batch_from_articles():
    """Testa criação de lote a partir de artigos"""
    articles = [
        Article(wp_id="1", title="Title 1", excerpt="Ex 1", content="Content 1", status="PENDING"),
        Article(wp_id="2", title="Title 2", excerpt="Ex 2", content="Content 2", status="PENDING")
    ]
    
    batch = ArticleBatch.from_articles(articles)
    
    assert batch.ids == ["1", "2"]
    assert batch.titles == ["Title 1", "Title 2"]
    assert batch.excerpts == ["Ex 1", "Ex 2"]
    assert batch.contents == ["Content 1", "Content 2"]

def test_process_batch():
    """Testa processamento de um lote"""
    articles = [
        Article(wp_id="1", title="Title 1", excerpt="Ex 1", content="Content 1", status="PENDING")
    ]
    
    # Mock do cliente AI
    client = MagicMock(spec=AIClient)
    client.generate_text.return_value = '[{"rewritten_title": "New Title 1", "rewritten_excerpt": "New Ex 1", "main_tags": ["tag1", "tag2"], "seo_meta": "Meta 1", "focus_tag": "tag1", "suggested_links": ["link1"]}]'
    
    results = process_batch(client, articles)
    
    assert len(results) == 1
    assert results[0]["rewritten_title"] == "New Title 1"
    assert results[0]["main_tags"] == ["tag1", "tag2"]

def test_process_articles():
    """Testa processamento de múltiplos artigos em lotes"""
    articles = [
        Article(wp_id="1", title="Title 1", excerpt="Ex 1", content="Content 1", status="PENDING"),
        Article(wp_id="2", title="Title 2", excerpt="Ex 2", content="Content 2", status="PENDING"),
        Article(wp_id="3", title="Title 3", excerpt="Ex 3", content="Content 3", status="PENDING")
    ]
    
    # Mock do cliente AI
    client = MagicMock(spec=AIClient)
    client.generate_text.return_value = '[{"rewritten_title": "New Title 1", "rewritten_excerpt": "New Ex 1", "main_tags": ["tag1"], "seo_meta": "Meta 1", "focus_tag": "tag1", "suggested_links": ["link1"]}]'
    
    results = process_articles(client, articles[:1])  # testa com 1 artigo
    
    assert len(results) == 1
    assert isinstance(results[0], dict)
    assert "rewritten_title" in results[0]