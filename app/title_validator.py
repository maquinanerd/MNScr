# app/title_validator.py
"""
Validador de Títulos e SEO Titles conforme regras editoriais.
Garante: caracteres, concordância, acentuação, regência, tom, verbos no presente.
"""
import re
import logging
from typing import Dict, List, Tuple, Optional

logger = logging.getLogger(__name__)

# Dicionário de correções comuns de português
PORTUGUESE_CORRECTIONS = {
    # Concordância
    r'\bmais (?:filme|serie|game|conteúdo|ator|atriz)s\b': lambda m: m.group(0).replace('mais ', 'mais '),  # redundância
    r'\bvários? (?:filmes|séries|games)\b': 'múltiplos',  # preferir "múltiplos"
    r'\bos? (?:series|filmes|games)\b': 'As séries|Os filmes|Os games',  # concordância
    
    # Verbos (infinitivo → presente)
    r'\b(chegar|confirmar|revelar|anunciar|ganhar|perder|estrear|vencer|sucumbir)\s+(?:de|em)\b': '',  # remover preposição errada
    
    # Regência (ficou de lado → ficou de fora)
    r'\bficou de lado\b': 'ficou de fora',
    r'\bficou fora\b': 'ficou de fora',
    r'\bsaiu do GOTY\b': 'fica de fora do prêmio',
    
    # Termos vazios/sensacionalismo
    r'\b(?:surpreendente|surpreende|impressionante|impressiona|explode|bomba|bomba|nerfado|matou|morreu)\b': '',
    r'\b(?:demais|muito|realmente)\b': '',  # adjetivos fracos
    
    # Acentuação
    r'\bgratis\b': 'de graça',
    r'\bpara sempre\b': 'permanentemente',  # melhor tom
    
    # Caixa-alta errada
    r'\b(?:marvel|dc|netflix|disney|hbo|max|prime|paramount|warner|sony)\b': lambda m: m.group(0).upper() if m.group(0).lower() in ['marvel', 'dc', 'netflix', 'disney', 'hbo', 'warner', 'sony'] else m.group(0),
}

PROBLEMATIC_WORDS = {
    'sucesso surpreendente': 'sucesso',
    'explode nas redes': 'viralizando',
    'bomba': 'grande lançamento',
    'nerfado': 'ajustado',
    'morreu': 'saiu do elenco',
    'matou': 'saiu do elenco',
    'nerd': 'fã',  # contexto dependente
    'mais um de': 'novo',  # redundância
}

BANNED_PATTERNS = [
    r'\b(?:veja|entenda|descubra|saiba)(?:\s+como)?\b',  # frases fracas
    r'\b(?:você\s+)?não\s+(?:vai|pode)\s+(?:acreditar|imaginar)\b',  # clickbait
    r'\b\?.*\?\b',  # múltiplas interrogações
    r'\bDois-pontos\s*:\s*Mais-dois-pontos\s*:\b',  # duplos dois-pontos
]

REQUIRED_PATTERNS = [
    # Título deve começar com entidade (franquia/ator/plataforma)
    r'^(?:O|A|Um|Uma|Os|As)?\s*(?:[A-Z][a-zã-ú]*(?:\s+[A-Z][a-zã-ú]*)?)\s*',  # Nome próprio no início
]

class TitleValidator:
    """Valida e corrige títulos conforme regras editoriais."""
    
    def __init__(self):
        self.errors = []
        self.warnings = []
        self.corrections = []
    
    def reset(self):
        """Limpa erros, avisos e correções."""
        self.errors = []
        self.warnings = []
        self.corrections = []
    
    def validate(self, title: str, meta_title: str = None) -> Dict[str, any]:
        """
        Valida título e meta_title (SEO title).
        Retorna dict com status, erros, avisos e sugestões.
        """
        self.reset()
        result = {
            'titulo': title,
            'meta_titulo': meta_title or title,
            'status': 'VÁLIDO',
            'erros': [],
            'avisos': [],
            'sugestoes': [],
            'titulo_corrigido': title,
            'meta_titulo_corrigido': meta_title or title,
        }
        
        # 1. Verificar comprimento
        if len(title) < 30:
            self.errors.append(f"❌ Título muito curto ({len(title)} chars). Mínimo: 55 caracteres.")
        if len(title) > 65:
            self.warnings.append(
                f"⚠️ Título acima do recomendado ({len(title)} chars). Prefira até 65 caracteres."
            )
        
        if meta_title and len(meta_title) > 65:
            self.warnings.append(
                f"⚠️ Meta title acima do recomendado ({len(meta_title)} chars). Prefira até 65 caracteres."
            )
        
        # 2. Verificar presente para notícias quentes
        infinitive_verbs = re.findall(r'\b(?:falar|dizer|contar|revelar|demonstrar|mostrar|deixar|fazer)\b', title, re.IGNORECASE)
        if infinitive_verbs:
            self.warnings.append(f"⚠️ Verbo no infinitivo detectado: {', '.join(set(infinitive_verbs))}. Use presente: 'revela', 'mostra', 'conta'.")
        
        # 3. Verificar concordância
        if re.search(r'\bos serie\b', title, re.IGNORECASE):
            self.errors.append("❌ Erro de concordância: 'os serie' → 'as séries'")
        
        # 4. Verificar acentuação
        if re.search(r'\bgratis\b', title, re.IGNORECASE):
            self.errors.append("❌ Acentuação: 'grátis' ou 'de graça' (preferir 'de graça' em tom neutro).")
        
        # 5. Verificar regência
        if re.search(r'\bficou de lado\b', title, re.IGNORECASE):
            self.warnings.append("⚠️ Regência: prefira 'ficou de fora' ou 'saiu do GOTY'.")
        
        # 6. Verificar caixa-alta excessiva
        uppercase_count = sum(1 for c in title if c.isupper())
        if uppercase_count > len(title) * 0.3:  # > 30% maiúsculas
            self.warnings.append(f"⚠️ Caixa-alta excessiva ({uppercase_count} letras maiúsculas). Reservar para nomes próprios.")
        
        # 7. Verificar sensacionalismo (expandido)
        sensationalism = re.findall(r'\b(?:surpreendente|impressionante|explode|bomba|nerfado|morto|mata|matou)\b', title, re.IGNORECASE)
        if sensationalism:
            self.errors.append(f"❌ Sensacionalismo detectado: {', '.join(set(sensationalism))}. Use termos factuais.")
        
        # 7.5 Verificar "sucesso surpreendente" (aviso - termo vazio comum)
        if re.search(r'\bsucesso\s+surpreendentes?\b', title, re.IGNORECASE):
            self.warnings.append("⚠️ Termo vazio: 'sucesso surpreendente'. Use apenas 'sucesso' ou adicione contexto (bilheteria, audiência).")
        
        # 8. Verificar pergunta (evitar em títulos)
        if title.strip().endswith('?'):
            self.warnings.append("⚠️ Título termina em interrogação. Prefira afirmação: 'O que causou...' → 'Razões da mudança'.")
        
        # 9. Verificar dois dois-pontos
        if title.count(':') > 1:
            self.errors.append("❌ Duplos dois-pontos detectados. Use: 'Termo: —'.")
        
        # 10. Verificar frases fracas
        weak_phrases = re.findall(r'\b(?:veja|entenda|descubra|saiba)\b', title, re.IGNORECASE)
        if weak_phrases:
            self.errors.append(f"❌ Frases fracas: {', '.join(set(weak_phrases))}. Use afirmação direta.")
        
        # 11. Verificar plataforma no final (aviso leve)
        if re.search(r'(?:Netflix|Disney|HBO|Prime|Paramount|Max)\s+(?:revela|anuncia|apresenta|libera)', title, re.IGNORECASE):
            self.warnings.append("⚠️ Plataforma no início. Prefira colocar no final: '...na Netflix'.")
        
        # 12. Verificar termos vazios (mais permissivo - avisar)
        if re.search(r'\bsucesso surpreendente\b', title, re.IGNORECASE):
            self.warnings.append("⚠️ Termo vazio: 'sucesso surpreendente'. Use apenas 'sucesso'.")
        
        # 13. Verificar gíria agressiva (avisar, não bloquear)
        gíria = re.findall(r'\b(?:nerfado|nerf)\b', title, re.IGNORECASE)
        if gíria:
            self.warnings.append("⚠️ Gíria agressiva detectada: 'nerfado'. Use 'ajustado' ou 'modificado'.")
        
        # 13.5 Verificar "ficou de lado" (regência - avisar)
        if re.search(r'\bficou de lado\b', title, re.IGNORECASE):
            self.warnings.append("⚠️ Regência: 'ficou de lado' é pouco claro. Use 'ficou de fora', 'saiu', 'foi removido'.")
        
        # 14. Verificar múltiplas interrogações ou exclamações (bloquear)
        if title.count('?') > 1 or title.count('!') > 1:
            self.errors.append("❌ Múltiplas interrogações/exclamações. Máximo 1.")
        
        # 15. Verificar "vários" (avisar - vago)
        if re.search(r'\bvários\s+(?:filmes|séries|games|títulos)\b', title, re.IGNORECASE):
            self.warnings.append("⚠️ 'Vários' é vago. Prefira: 'múltiplos', 'três', 'cinco', 'novo'.")
        
        # Compilar resultado
        result['erros'] = self.errors
        result['avisos'] = self.warnings
        result['sugestoes'] = self.corrections
        result['status'] = 'ERRO' if self.errors else ('AVISO' if self.warnings else 'VÁLIDO')
        
        return result
    
    def suggest_correction(self, title: str) -> str:
        """
        Sugere correção automática do título.
        """
        corrected = title
        
        # Correções básicas
        corrected = re.sub(r'\bgratis\b', 'de graça', corrected, flags=re.IGNORECASE)
        corrected = re.sub(r'\bvários\b', 'múltiplos', corrected, flags=re.IGNORECASE)
        corrected = re.sub(r'\bsucesso surpreendente\b', 'sucesso', corrected, flags=re.IGNORECASE)
        corrected = re.sub(r'\bexplode nas redes\b', 'viralizando', corrected, flags=re.IGNORECASE)
        
        # Remover sensacionalismo
        corrected = re.sub(r'\b(?:nerfado|nerf)\b', 'ajustado', corrected, flags=re.IGNORECASE)
        
        # Limpar espaços extras
        corrected = re.sub(r'\s+', ' ', corrected).strip()
        
        return corrected
    
    def batch_validate(self, titles: List[str]) -> List[Dict]:
        """Valida um lote de títulos."""
        return [self.validate(title) for title in titles]


# ========== EXEMPLOS DE USO ==========
if __name__ == '__main__':
    validator = TitleValidator()
    
    # Exemplos de títulos BOM e RUIM
    test_titles = [
        # ✅ BONS
        "Batman 2 tem estreia confirmada pela DC em 2025",
        "Marvel revela calendário completo da Fase 6",
        "The Last of Us ganha trailer da 2ª temporada na HBO Max",
        
        # ❌ RUINS
        "O que causou a queda surpreendente de Batman?",
        "Série NERFADA por Warner Bros.",
        "Vários filmes explodem nas redes em 2025!!!",
        "Entenda por quê Star Wars foi cancelado",
        "DC Studios - SUCESSO SURPREENDENTE - Novo filme",
    ]
    
    for title in test_titles:
        result = validator.validate(title)
        print(f"\n📌 Título: {title}")
        print(f"   Status: {result['status']}")
        if result['erros']:
            for err in result['erros']:
                print(f"   {err}")
        if result['avisos']:
            for warn in result['avisos']:
                print(f"   {warn}")
