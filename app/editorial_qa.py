import re
from html import unescape
from typing import Any, Dict, List

BAD_AI_PHRASES = [
    "a expectativa é que",
    "promete agradar os fãs",
    "experiência mais coesa",
    "marca um novo capítulo",
    "fortes emoções",
    "os fãs podem esperar",
    "resta saber",
    "a produção promete",
    "a novidade movimentou",
    "deve conquistar novos públicos",
    "a espera finalmente acabou",
    "em uma revelação surpreendente",
    "o universo de",
    "a obra reforça",
]

GENERIC_H2 = [
    "impacto e recepção da obra",
    "a visão original",
    "o futuro da franquia",
    "expectativas para a nova temporada",
    "desenvolvimento da trama",
    "o que esperar",
    "detalhes da produção",
    "uma nova fase",
    "tudo sobre a novidade",
    "o impacto da decisão",
]

LEAD_PATTERNS = [
    r"^A franquia\b",
    r"^A produção\b",
    r"^Os fãs\b",
    r"^O universo de\b",
    r"^A obra\b",
    r"^O título chega\b",
    r"^A novidade\b",
    r"^Em uma revelação\b",
    r"^A espera\b",
]

QUOTE_CHARS = ('"', "“", "”", "„")


def _plain_text(html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html or "")
    return re.sub(r"\s+", " ", unescape(text)).strip()


def _word_count(html: str) -> int:
    text = _plain_text(html)
    return len(text.split()) if text else 0


def _first_paragraph_text(html: str) -> str:
    match = re.search(r"<p\b[^>]*>(.*?)</p>", html or "", flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return ""
    return _plain_text(match.group(1))


def _has_quotes(text: str) -> bool:
    return any(char in (text or "") for char in QUOTE_CHARS)


def run_editorial_style_qa(titulo: str, conteudo_html: str, source_had_quotes: bool = False) -> dict:
    """
    Avalia o conteúdo gerado pela IA contra regras anti-texto-artificial.
    Retorna um dict com a lista de warnings encontrados e contagens.
    Não bloqueia publicação. Apenas observa.
    """
    warnings: List[Dict[str, Any]] = []
    counts = {
        "vague_phrases": 0,
        "generic_h2": 0,
        "suspicious_lead": 0,
        "missing_quotes": 0,
        "missing_h2": 0,
    }

    html = conteudo_html or ""

    for phrase in BAD_AI_PHRASES:
        matches = re.findall(re.escape(phrase), html, flags=re.IGNORECASE)
        if matches:
            count = len(matches)
            warnings.append({"type": "vague_phrase", "match": phrase, "count": count})
            counts["vague_phrases"] += count

    h2_matches = re.findall(r"<h2\b[^>]*>(.*?)</h2>", html, flags=re.IGNORECASE | re.DOTALL)
    for h2_html in h2_matches:
        h2_text = _plain_text(h2_html)
        for generic in GENERIC_H2:
            if h2_text.casefold() == generic.casefold():
                warnings.append({"type": "generic_h2", "match": h2_text})
                counts["generic_h2"] += 1
                break

    lead = _first_paragraph_text(html)
    if lead:
        for pattern in LEAD_PATTERNS:
            match = re.search(pattern, lead, flags=re.IGNORECASE)
            if match:
                warnings.append({"type": "suspicious_lead", "match": match.group(0)})
                counts["suspicious_lead"] += 1
                break

    if source_had_quotes and not _has_quotes(html):
        warnings.append({"type": "missing_quotes", "source_had_quotes": True})
        counts["missing_quotes"] = 1

    words = _word_count(html)
    if words > 400 and not h2_matches:
        warnings.append({"type": "missing_h2", "word_count": words})
        counts["missing_h2"] = 1

    return {
        "passed": not warnings,
        "warnings": warnings,
        "counts": counts,
    }
