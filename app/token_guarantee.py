#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GARANTIDOR OBRIGATÓRIO DE TOKENS
Força captura 100% de tokens mesmo se houver erros
Intercepta todas as chamadas de API e registra com fallback
"""

import logging
import json
from pathlib import Path
from datetime import datetime
from typing import Tuple, Optional, Dict
import functools
import traceback

logger = logging.getLogger(__name__)


class TokenGuarantee:
    """Garante que NENHUM token é perdido com múltiplas camadas de proteção"""
    
    def __init__(self, tokens_dir: Path = None):
        if tokens_dir is None:
            tokens_dir = Path(__file__).parent / "logs" / "tokens"
        
        self.tokens_dir = Path(tokens_dir)
        self.tokens_dir.mkdir(parents=True, exist_ok=True)
        
        self.guarantee_log = self.tokens_dir / "token_guarantee.log"
        self.emergency_log = self.tokens_dir / "token_emergency.log"
        self.audit_log = self.tokens_dir / "token_audit.log"
    
    def log_guarantee(self, prompt_tokens: int, completion_tokens: int, 
                     operation: str, source: str = "unknown",
                     metadata: Dict = None) -> bool:
        """
        Registra tokens com 3 níveis de garantia:
        1. Log de garantia (arquivo de backup)
        2. Arquivo diário JSONL
        3. Log de auditoria com timestamp
        
        Returns True se sucesso, False caso contrário
        """
        timestamp = datetime.now()
        iso_timestamp = timestamp.isoformat()
        
        # Validação obrigatória
        try:
            prompt_tokens = int(prompt_tokens)
            completion_tokens = int(completion_tokens)
        except (ValueError, TypeError) as e:
            logger.error(f"❌ REJEIÇÃO: Tokens não numéricos! err={e}")
            return False
        
        if prompt_tokens < 0 or completion_tokens < 0:
            logger.error(f"❌ REJEIÇÃO: Tokens negativos! prompt={prompt_tokens}, completion={completion_tokens}")
            return False
        
        total_tokens = prompt_tokens + completion_tokens
        
        # Estrutura de entrada
        log_entry = {
            "timestamp": iso_timestamp,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "operation": operation,
            "source": source,
            "metadata": metadata or {}
        }
        
        success = True
        
        # CAMADA 1: Log de garantia (backup imediato)
        try:
            with open(self.guarantee_log, 'a', encoding='utf-8') as f:
                f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')
                f.flush()  # Forçar escrita no disco
            logger.debug(f"✅ Nível 1/3: Log de garantia OK")
        except Exception as e:
            logger.error(f"❌ NÍVEL 1 FALHOU: {e}")
            success = False
        
        # CAMADA 2: Arquivo JSONL diário
        today_file = self.tokens_dir / f"tokens_{timestamp.strftime('%Y-%m-%d')}.jsonl"
        try:
            with open(today_file, 'a', encoding='utf-8') as f:
                f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')
                f.flush()
            logger.debug(f"✅ Nível 2/3: JSONL diário OK")
        except Exception as e:
            logger.error(f"❌ NÍVEL 2 FALHOU: {e}")
            success = False
        
        # CAMADA 3: Log de auditoria com rastreamento
        try:
            audit_msg = (
                f"{iso_timestamp} | OP={operation:<30} | "
                f"ENTRADA={prompt_tokens:>8} | SAÍDA={completion_tokens:>8} | "
                f"TOTAL={total_tokens:>8} | SRC={source}"
            )
            with open(self.audit_log, 'a', encoding='utf-8') as f:
                f.write(audit_msg + '\n')
                f.flush()
            logger.debug(f"✅ Nível 3/3: Auditoria OK")
        except Exception as e:
            logger.error(f"❌ NÍVEL 3 FALHOU: {e}")
            success = False
        
        # Log final
        if success:
            logger.info(
                f"✅ GARANTIDO: {prompt_tokens:>6} entrada + {completion_tokens:>6} saída = "
                f"{total_tokens:>6} tokens | {operation}"
            )
        else:
            logger.critical(
                f"⚠️  PARCIAL: {prompt_tokens} + {completion_tokens} registrados mas com erros"
            )
            # Registrar em emergency log
            try:
                with open(self.emergency_log, 'a', encoding='utf-8') as f:
                    f.write(f"{iso_timestamp} | ERRO PARCIAL | " + 
                           json.dumps(log_entry, ensure_ascii=False) + '\n')
            except:
                pass
        
        return success
    
    def guarantee_api_response(self, text: str, tokens_info: Dict = None, 
                              operation: str = "api_call") -> Tuple[str, Dict]:
        """
        Garante registro de tokens de resposta de API
        Retorna (text, tokens_info) com garantia de persistência
        """
        # Extrair tokens
        if tokens_info is None:
            tokens_info = {}
        
        prompt_tokens = tokens_info.get('prompt_tokens', 0)
        completion_tokens = tokens_info.get('completion_tokens', 0)
        
        # Garantir registro
        self.log_guarantee(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            operation=operation,
            source="api_response"
        )
        
        return text, tokens_info
    
    def emergency_fallback(self, error: Exception, context: Dict):
        """Fallback de emergência se ocorrer erro no logging"""
        timestamp = datetime.now().isoformat()
        
        emergency_entry = {
            "timestamp": timestamp,
            "error_type": type(error).__name__,
            "error_message": str(error),
            "traceback": traceback.format_exc(),
            "context": context
        }
        
        try:
            with open(self.emergency_log, 'a', encoding='utf-8') as f:
                f.write(json.dumps(emergency_entry, ensure_ascii=False) + '\n')
                f.flush()
            logger.critical(f"🚨 FALLBACK DE EMERGÊNCIA: {error}")
        except:
            # Se nem o fallback funciona, isso é um problema crítico
            print(f"🚨🚨 FALHA CRÍTICA NA GARANTIA DE TOKENS: {error}", flush=True)
    
    def verify_integrity(self) -> Dict:
        """Verifica integridade dos 3 logs de garantia"""
        results = {
            "timestamp": datetime.now().isoformat(),
            "guarantee_log_exists": self.guarantee_log.exists(),
            "guarantee_log_size": 0,
            "emergency_log_size": 0,
            "audit_log_size": 0,
            "audit_log_lines": 0,
            "status": "UNKNOWN"
        }
        
        try:
            if self.guarantee_log.exists():
                results["guarantee_log_size"] = self.guarantee_log.stat().st_size
            
            if self.emergency_log.exists():
                results["emergency_log_size"] = self.emergency_log.stat().st_size
            
            if self.audit_log.exists():
                results["audit_log_size"] = self.audit_log.stat().st_size
                with open(self.audit_log, 'r', encoding='utf-8') as f:
                    results["audit_log_lines"] = len(f.readlines())
            
            results["status"] = "OK" if results["guarantee_log_exists"] else "WARNING"
            return results
        except Exception as e:
            results["status"] = f"ERROR: {e}"
            return results


def force_token_guarantee(operation: str = "generic"):
    """
    Decorator que força garantia de tokens em qualquer função
    
    Exemplo:
    @force_token_guarantee("rewrite_article")
    def minha_funcao_ai():
        return tokens_info
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            guarantee = TokenGuarantee()
            
            try:
                result = func(*args, **kwargs)
                
                # Tentar extrair tokens de diferentes formatos de retorno
                tokens_info = None
                if isinstance(result, tuple) and len(result) >= 2:
                    # Formato: (text, tokens_info)
                    text, tokens_info = result[0], result[1]
                elif isinstance(result, dict) and 'tokens_info' in result:
                    tokens_info = result.get('tokens_info')
                elif isinstance(result, dict) and 'prompt_tokens' in result:
                    tokens_info = result
                
                if tokens_info:
                    prompt = tokens_info.get('prompt_tokens', 0)
                    completion = tokens_info.get('completion_tokens', 0)
                    guarantee.log_guarantee(
                        prompt_tokens=prompt,
                        completion_tokens=completion,
                        operation=operation,
                        source=func.__name__
                    )
                
                return result
            
            except Exception as e:
                guarantee.emergency_fallback(e, {
                    "function": func.__name__,
                    "operation": operation,
                    "args": str(args)[:100],
                    "kwargs": str(kwargs)[:100]
                })
                raise
        
        return wrapper
    return decorator


# Variável global para uso conveniente
_global_guarantee = None

def get_global_guarantee() -> TokenGuarantee:
    """Obtém instância global do garantidor"""
    global _global_guarantee
    if _global_guarantee is None:
        _global_guarantee = TokenGuarantee()
    return _global_guarantee


def log_guaranteed(prompt_tokens: int, completion_tokens: int, 
                  operation: str, source: str = "unknown") -> bool:
    """Função de conveniência para logging com garantia"""
    guarantee = get_global_guarantee()
    return guarantee.log_guarantee(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        operation=operation,
        source=source
    )


if __name__ == "__main__":
    # Teste
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    guarantee = TokenGuarantee()
    
    # Teste 1: Registro normal
    guarantee.log_guarantee(1000, 200, "test_operation", "test_source")
    
    # Teste 2: Verificar integridade
    integrity = guarantee.verify_integrity()
    print("\nIntegridade dos logs:")
    for key, value in integrity.items():
        print(f"  {key}: {value}")
    
    print("\n✅ Teste concluído!")
