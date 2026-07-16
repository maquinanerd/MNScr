"""
Processamento em lote de artigos para otimizar chamadas à API.
"""
import json
import logging
from typing import List, Dict, Any
from dataclasses import dataclass

from .ai_client_gemini import AIClient
from .store import Article
from .token_bucket import TokenBucket

# RPM alvo conservador (2 chamadas/minuto = 1 a cada 30s)
RPM_TARGET = 2.0
token_bucket = TokenBucket(rate=RPM_TARGET/60, capacity=1)

logger = logging.getLogger(__name__)

BATCH_SIZE = 3  # Processa N artigos por chamada

@dataclass
class ArticleBatch:
    ids: List[str]
    titles: List[str]
    excerpts: List[str]
    contents: List[str]

    @staticmethod
    def from_articles(articles: List[Article]) -> 'ArticleBatch':
        """Cria um lote a partir de uma lista de artigos."""
        return ArticleBatch(
            ids=[a.wp_id for a in articles],
            titles=[a.title for a in articles],
            excerpts=[a.excerpt for a in articles],
            contents=[a.content for a in articles]
        )

def build_batch_prompt(batch: ArticleBatch) -> str:
    """Constrói o prompt para processar um lote de artigos."""
    return f"""
Você receberá dados de {len(batch.ids)} artigos para processar:

{json.dumps([{
    'id': id,
    'title': title,
    'excerpt': excerpt,
    'content': content
} for id, title, excerpt, content in zip(
    batch.ids, batch.titles, batch.excerpts, batch.contents
)], ensure_ascii=False, indent=2)}

Para cada artigo no array, gere um objeto JSON com os campos:
- rewritten_title: título reescrito otimizado para SEO
- rewritten_excerpt: resumo reescrito em tom jornalístico
- main_tags: array com 3-5 tags principais em português, em minúsculas
- seo_meta: meta description otimizada para SEO, máx 160 caracteres
- focus_tag: a tag mais relevante em português, em minúscula
- suggested_links: array com 2-3 sugestões de textos relacionados

Retorne um array de objetos na mesma ordem dos artigos recebidos.
"""

def process_batch(client: AIClient, articles: List[Article]) -> List[Dict[str, Any]]:
    """
    Processa um lote de artigos em uma única chamada à API.
    Retorna lista de resultados na mesma ordem dos artigos.
    """
    if not articles:
        return []
        
    batch = ArticleBatch.from_articles(articles)
    
    # Constrói um prompt mais direto e estruturado para processamento em lote
    prompt = f"""
INSTRUÇÕES: Processe os {len(articles)} artigos abaixo em uma única análise.
Para cada artigo, retorne um objeto no array JSON com exatamente os campos especificados.
Mantenha a mesma ordem dos artigos de entrada.

ARTIGOS:
{json.dumps([{
    'id': id,
    'title': title,
    'content': content
} for id, title, content in zip(batch.ids, batch.titles, batch.contents)], ensure_ascii=False, indent=2)}

FORMATO RESPOSTA:
[
  {
    "titulo_final": "...",
    "meta_description": "...",
    "focus_keyphrase": "...",
    "related_keyphrases": ["...", "...", "..."],
    "slug": "...",
    "categorias": [
      {"nome": "...", "grupo": "...", "evidence": "..."}
    ],
    "tags_sugeridas": ["...", "...", "..."]
  },
  ...  // Um objeto para cada artigo na mesma ordem
]
"""
    
    # Espera token disponível (implementa rate limit rigoroso)
    token_bucket.consume()
    
    try:
        response = client.generate_text(
            prompt,
            generation_config={
                "temperature": 0.7,
                "top_p": 0.8,
                "max_output_tokens": 2048
            }
        )
        results = json.loads(response)
        
        if not isinstance(results, list):
            raise ValueError("API não retornou array")
            
        if len(results) != len(articles):
            raise ValueError(f"API retornou {len(results)} resultados para {len(articles)} artigos")
            
        return results
        
    except Exception as e:
        logger.error("Erro processando lote: %s", e)
        raise

def process_articles(client: AIClient, articles: List[Article]) -> List[Dict[str, Any]]:
    """
    Processa uma lista de artigos em lotes.
    Retorna lista de resultados na mesma ordem dos artigos originais.
    """
    results = []
    
    # Processa em lotes de BATCH_SIZE
    for i in range(0, len(articles), BATCH_SIZE):
        batch = articles[i:i + BATCH_SIZE]
        batch_results = process_batch(client, batch)
        results.extend(batch_results)
        
    return results