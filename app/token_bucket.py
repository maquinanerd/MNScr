"""
Token bucket para controle rigoroso de taxa de requisições.
"""
import time
from threading import Lock

class TokenBucket:
    def __init__(self, rate: float, capacity: int):
        """
        Inicializa o token bucket.
        
        Args:
            rate: Tokens por segundo
            capacity: Número máximo de tokens
        """
        self.rate = float(rate)
        self.capacity = float(capacity)
        self.tokens = float(capacity)
        self.last_update = time.time()
        self.lock = Lock()
        
    def _add_tokens(self):
        """Adiciona tokens baseado no tempo decorrido."""
        now = time.time()
        elapsed = now - self.last_update
        new_tokens = elapsed * self.rate
        
        self.tokens = min(self.capacity, self.tokens + new_tokens)
        self.last_update = now
        
    def try_consume(self, tokens: int = 1) -> bool:
        """
        Tenta consumir tokens. Se não houver tokens suficientes, retorna False.
        
        Args:
            tokens: Número de tokens a consumir
            
        Returns:
            bool: True se tokens foram consumidos, False caso contrário
        """
        with self.lock:
            self._add_tokens()
            
            if self.tokens >= tokens:
                self.tokens -= tokens
                return True
                
            return False
            
    def consume(self, tokens: int = 1):
        """
        Consome tokens, esperando se necessário.
        
        Args:
            tokens: Número de tokens a consumir
        """
        while not self.try_consume(tokens):
            # Calcula tempo necessário para ter tokens suficientes
            required = tokens - self.tokens
            wait_time = required / self.rate
            time.sleep(wait_time)