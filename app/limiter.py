# limiter.py
import time, random, logging
from collections import deque
from time import monotonic

logger = logging.getLogger(__name__)

class RateLimiter:
    def __init__(self, min_interval_s=60.0):  # Intervalo MUITO conservador: 60 segundos (1 RPM) - proteção contra RPM limits
        self.min_interval_s = float(min_interval_s)
        self.last_call = 0.0
        self._jitter = 5.0  # Reduzido para 5s para ser mais previsível com intervalo maior
    def wait(self):
        elapsed = monotonic() - self.last_call
        base_wait = self.min_interval_s - elapsed
        if base_wait > 0:
            # Adiciona jitter aleatório entre 0-10s
            jitter = random.uniform(0, self._jitter)
            total_wait = base_wait + jitter
            time.sleep(total_wait)
        self.last_call = monotonic()

class KeySlot:
    def __init__(self, key): 
        self.key = key
        self.cooldown_until = 0.0

class KeyPool:
    def __init__(self, keys):
        self.slots = deque(KeySlot(k) for k in keys)
        self.rotation_index = 0  # Contador para rodízio
        logger.info(f"KEYPOOL: Inicializado com {len(self.slots)} chaves")
        
    def next_ready(self):
        now = monotonic()
        num_slots = len(self.slots)
        
        # Primeiro, tenta encontrar uma chave pronta começando do índice de rotação
        for attempt in range(num_slots):
            idx = (self.rotation_index + attempt) % num_slots
            s = self.slots[idx]
            
            if s.cooldown_until <= now:
                # Chave pronta! Avança o índice de rotação para a próxima
                self.rotation_index = (idx + 1) % num_slots
                logger.info(f"KEYPOOL: Usando chave ****{s.key[-4:]} (índice {idx})")
                return s
        
        # Se nenhuma está pronta, aguarda a mais próxima
        wait = min(s.cooldown_until for s in self.slots) - now
        if wait > 0: 
            logger.warning(f"KEYPOOL: Todas em cooldown. Aguardando {wait:.1f}s...")
            time.sleep(wait)
        return self.next_ready()
        
    def penalize(self, slot, retry_after=None, base=30):
        dur = (retry_after or base) + random.uniform(0, 2)
        slot.cooldown_until = monotonic() + dur
        logger.error(f"KEYPOOL: Chave ****{slot.key[-4:]} penalizada por {dur:.1f}s")
        self.slots.rotate(-1)