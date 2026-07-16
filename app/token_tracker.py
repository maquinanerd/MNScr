#!/usr/bin/env python3
"""
Sistema de rastreamento de tokens para APIs (Gemini, etc)
Registra entrada (prompt_tokens), saída (completion_tokens) e total
"""

import os
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Tuple

class TokenTracker:
    """Rastreia uso de tokens em chamadas de API"""
    
    def __init__(self, log_dir: Optional[str] = None):
        """
        Inicializa o rastreador de tokens
        
        Args:
            log_dir: Diretório para armazenar logs. Se None, usa 'logs/tokens'
        """
        if log_dir is None:
            log_dir = Path(__file__).parent.parent / 'logs' / 'tokens'
        else:
            log_dir = Path(log_dir)
        
        self.log_dir = log_dir
        self.log_dir.mkdir(parents=True, exist_ok=True)
        
        # Arquivo de log diário
        self.log_file = self.log_dir / f"tokens_{datetime.now().strftime('%Y-%m-%d')}.jsonl"
        
        # Arquivo de estatísticas
        self.stats_file = self.log_dir / 'token_stats.json'
        
        # Arquivo de erro/debug
        self.debug_file = self.log_dir / 'token_debug.log'
        
        # Estatísticas em memória
        self.stats = self._load_stats()
        
        self.logger = logging.getLogger('TokenTracker')
        self.logger.setLevel(logging.DEBUG)
        
        # Handler para arquivo de debug
        if not self.logger.handlers:
            fh = logging.FileHandler(self.debug_file)
            fh.setLevel(logging.DEBUG)
            formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
            fh.setFormatter(formatter)
            self.logger.addHandler(fh)
    
    def log_tokens(
        self, 
        prompt_tokens: int,
        completion_tokens: int,
        api_type: str = "gemini",
        model: str = "unknown",
        api_key_suffix: str = "unknown",
        success: bool = True,
        error_message: Optional[str] = None,
        metadata: Optional[Dict] = None,
        source_url: Optional[str] = None,
        wp_post_id: Optional[int] = None,
        article_title: Optional[str] = None
    ) -> bool:
        """
        Registra o uso de tokens em uma chamada de API
        
        Args:
            prompt_tokens: Número de tokens na entrada (prompt)
            completion_tokens: Número de tokens na saída (resposta)
            api_type: Tipo de API (gemini, openai, etc) - padrão: gemini
            model: Modelo usado (ex: gemini-2.5-flash)
            api_key_suffix: Últimas 4 caracteres da chave para identificação
            success: Se a chamada foi bem-sucedida
            error_message: Mensagem de erro se falhou
            metadata: Dados adicionais para registrar
            source_url: URL de origem do artigo (novo!)
            wp_post_id: ID do post no WordPress após publicação (novo!)
            article_title: Título do artigo processado (novo!)
        
        Returns:
            bool: True se registrou com sucesso, False caso contrário
        """
        try:
            total_tokens = prompt_tokens + completion_tokens
            timestamp = datetime.now().isoformat()
            
            log_entry = {
                "timestamp": timestamp,
                "api_type": api_type,
                "model": model,
                "api_key_suffix": api_key_suffix,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
                "success": success,
                "error_message": error_message,
                "metadata": metadata or {},
                "source_url": source_url,
                "wp_post_id": wp_post_id,
                "article_title": article_title
            }
            
            # Escrever no arquivo JSONL
            with open(self.log_file, 'a', encoding='utf-8') as f:
                f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')
            
            # Atualizar estatísticas
            self._update_stats(log_entry)
            
            if total_tokens > 0:
                self.logger.info(
                    f"[{api_type.upper()}] Entrada: {prompt_tokens} | "
                    f"Saída: {completion_tokens} | Total: {total_tokens}"
                )
            else:
                title_str = f'"{article_title[:70]}" ' if article_title else ""
                id_str = f"| Post ID: {wp_post_id} " if wp_post_id else ""
                self.logger.info(
                    f"[{api_type.upper()}] {title_str}{id_str}"
                    f"— publicado no WordPress (tokens de IA registrados em chamadas anteriores)"
                )
            
            return True
            
        except Exception as e:
            self.logger.error(f"Erro ao registrar tokens: {e}", exc_info=True)
            return False
    
    def _update_stats(self, log_entry: Dict) -> None:
        """Atualiza arquivo de estatísticas"""
        try:
            api_type = log_entry['api_type']
            model = log_entry['model']
            
            # Inicializar se necessário
            if api_type not in self.stats:
                self.stats[api_type] = {}
            
            if model not in self.stats[api_type]:
                self.stats[api_type][model] = {
                    'total_prompt_tokens': 0,
                    'total_completion_tokens': 0,
                    'total_tokens': 0,
                    'total_requests': 0,
                    'successful_requests': 0,
                    'failed_requests': 0,
                    'last_updated': None
                }
            
            stats_entry = self.stats[api_type][model]
            stats_entry['total_prompt_tokens'] += log_entry['prompt_tokens']
            stats_entry['total_completion_tokens'] += log_entry['completion_tokens']
            stats_entry['total_tokens'] += log_entry['total_tokens']
            stats_entry['total_requests'] += 1
            
            if log_entry['success']:
                stats_entry['successful_requests'] += 1
            else:
                stats_entry['failed_requests'] += 1
            
            stats_entry['last_updated'] = log_entry['timestamp']
            
            # Salvar estatísticas
            with open(self.stats_file, 'w', encoding='utf-8') as f:
                json.dump(self.stats, f, indent=2, ensure_ascii=False)
        
        except Exception as e:
            self.logger.error(f"Erro ao atualizar estatísticas: {e}", exc_info=True)
    
    def _load_stats(self) -> Dict:
        """Carrega estatísticas existentes"""
        try:
            if self.stats_file.exists():
                with open(self.stats_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception as e:
            self.logger.warning(f"Não foi possível carregar estatísticas: {e}")
        
        return {}
    
    def get_summary(self) -> Dict:
        """Retorna um resumo das estatísticas totais"""
        summary = {
            "total_prompt_tokens": 0,
            "total_completion_tokens": 0,
            "total_tokens": 0,
            "total_requests": 0,
            "successful_requests": 0,
            "failed_requests": 0,
            "by_api_type": {}
        }
        
        for api_type, models_data in self.stats.items():
            summary["by_api_type"][api_type] = {
                "total_prompt_tokens": 0,
                "total_completion_tokens": 0,
                "total_tokens": 0,
                "total_requests": 0,
                "successful_requests": 0,
                "failed_requests": 0,
                "by_model": {}
            }
            
            for model, model_stats in models_data.items():
                summary["total_prompt_tokens"] += model_stats['total_prompt_tokens']
                summary["total_completion_tokens"] += model_stats['total_completion_tokens']
                summary["total_tokens"] += model_stats['total_tokens']
                summary["total_requests"] += model_stats['total_requests']
                summary["successful_requests"] += model_stats['successful_requests']
                summary["failed_requests"] += model_stats['failed_requests']
                
                summary["by_api_type"][api_type]["total_prompt_tokens"] += model_stats['total_prompt_tokens']
                summary["by_api_type"][api_type]["total_completion_tokens"] += model_stats['total_completion_tokens']
                summary["by_api_type"][api_type]["total_tokens"] += model_stats['total_tokens']
                summary["by_api_type"][api_type]["total_requests"] += model_stats['total_requests']
                summary["by_api_type"][api_type]["successful_requests"] += model_stats['successful_requests']
                summary["by_api_type"][api_type]["failed_requests"] += model_stats['failed_requests']
                
                summary["by_api_type"][api_type]["by_model"][model] = model_stats
        
        return summary
    
    def print_summary(self) -> None:
        """Imprime um resumo formatado das estatísticas"""
        summary = self.get_summary()
        
        print("\n" + "="*80)
        print("📊 RESUMO DE TOKENS")
        print("="*80)
        
        print(f"\n🔢 TOTALS GERAIS:")
        print(f"   📥 Tokens de Entrada (Prompts): {summary['total_prompt_tokens']:,}")
        print(f"   📤 Tokens de Saída (Respostas): {summary['total_completion_tokens']:,}")
        print(f"   ✅ Total de Tokens: {summary['total_tokens']:,}")
        print(f"   📋 Total de Requisições: {summary['total_requests']:,}")
        print(f"   ✔️  Bem-sucedidas: {summary['successful_requests']:,}")
        print(f"   ❌ Falhadas: {summary['failed_requests']:,}")
        
        for api_type, api_stats in summary['by_api_type'].items():
            print(f"\n🔌 {api_type.upper()}:")
            print(f"   📥 Entrada: {api_stats['total_prompt_tokens']:,}")
            print(f"   📤 Saída: {api_stats['total_completion_tokens']:,}")
            print(f"   ✅ Total: {api_stats['total_tokens']:,}")
            print(f"   📋 Requisições: {api_stats['total_requests']:,}")
            
            for model, model_stats in api_stats['by_model'].items():
                print(f"\n      🤖 Modelo: {model}")
                print(f"         📥 Entrada: {model_stats['total_prompt_tokens']:,}")
                print(f"         📤 Saída: {model_stats['total_completion_tokens']:,}")
                print(f"         ✅ Total: {model_stats['total_tokens']:,}")
                print(f"         📋 Requisições: {model_stats['total_requests']:,} "
                      f"({model_stats['successful_requests']}✔️ {model_stats['failed_requests']}❌)")
        
        print("\n" + "="*80 + "\n")

# Instância global (singleton)
_tracker: Optional[TokenTracker] = None

def get_tracker(log_dir: Optional[str] = None) -> TokenTracker:
    """Retorna a instância global do rastreador de tokens"""
    global _tracker
    if _tracker is None:
        _tracker = TokenTracker(log_dir)
    return _tracker

def log_tokens(
    prompt_tokens: int,
    completion_tokens: int,
    **kwargs
) -> bool:
    """Função de conveniência para registrar tokens usando a instância global"""
    return get_tracker().log_tokens(prompt_tokens, completion_tokens, **kwargs)
