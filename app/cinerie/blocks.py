"""HTML editorial -> corpo em blocos discriminados.

O MNScr escreve HTML (``DraftContent.body_html``); o contrato do Cinerie recusa
HTML livre e exige uma lista de blocos com tipo, id estavel e texto **sem
markup**. A conversao mora aqui, na fronteira, e nao no gerador de texto: o
draft local continua sendo HTML, que e o que o artefato local e a revisao humana
usam hoje.

Tres decisoes que nao sao obvias:

**Markup inline desaparece do texto.** ``<strong>``, ``<em>`` e ``<a>`` viram
texto puro, porque ``plainText`` recusa qualquer tag. Isso perde a enfase e o
link — o link nao se perde de verdade: ele reaparece, estruturado e verificavel,
em ``seo.internalLinkSuggestions``.

**Imagem no corpo e descartada, com aviso.** O bloco ``image`` exige
``mediaRef`` apontando para midia **ja aprovada no CMS**, e o MNScr nao conhece
nenhum id de midia do Cinerie — ele so tem a URL da imagem que a fonte usou.
Emitir um ``mediaRef`` inventado seria pedir ao Cinerie que publicasse
apontando para algo que nao existe. Descartar e a leitura honesta; o aviso
registra o que ficou de fora.

**Lista vira um paragrafo por item.** O contrato nao tem bloco de lista. Um
``ItemList`` fabricado a partir de ``<li>`` seria structured data falso, que e
exatamente o que a matriz de ``schemaTypeRecommendation`` existe para impedir.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Dict, Final, List, Optional, Tuple

from .contract import load_schema
from .text import collapse, find_forbidden_markup, plain_text

logger = logging.getLogger(__name__)

BLOCK_PARAGRAPH: Final[str] = "paragraph"
BLOCK_HEADING: Final[str] = "heading"
BLOCK_QUOTE: Final[str] = "quote"
BLOCK_DIVIDER: Final[str] = "divider"

#: Niveis de heading aceitos. ``h1`` e do titulo da pagina; o corpo comeca em h2.
_HEADING_LEVELS: Final[Dict[str, int]] = {"h1": 2, "h2": 2, "h3": 3, "h4": 4, "h5": 4, "h6": 4}


@lru_cache(maxsize=1)
def _block_variants() -> Dict[str, Dict[str, Any]]:
    """Mapa ``tipo -> variante`` extraido do ``oneOf`` do JSON Schema."""
    schema = load_schema()
    variants: Dict[str, Dict[str, Any]] = {}
    try:
        options = schema["properties"]["blocks"]["items"]["oneOf"]
    except (KeyError, TypeError):
        return variants
    for option in options:
        if not isinstance(option, dict):
            continue
        type_schema = option.get("properties", {}).get("type", {})
        name = type_schema.get("const")
        if isinstance(name, str):
            variants[name] = option
    return variants


def block_field_limit(block_type: str, field_name: str, default: int) -> int:
    """Teto de um campo de bloco, lido do contrato em vez de redigitado."""
    variant = _block_variants().get(block_type)
    if not isinstance(variant, dict):
        return default
    value = variant.get("properties", {}).get(field_name, {}).get("maxLength")
    return value if isinstance(value, int) and value > 0 else default


@lru_cache(maxsize=1)
def max_blocks() -> int:
    schema = load_schema()
    value = schema.get("properties", {}).get("blocks", {}).get("maxItems")
    return value if isinstance(value, int) and value > 0 else 200


@dataclass
class BlockConversion:
    """Os blocos e o que ficou pelo caminho."""

    blocks: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    dropped_images: List[str] = field(default_factory=list)
    truncated: bool = False

    @property
    def is_empty(self) -> bool:
        return not self.blocks


def _next_id(index: int) -> str:
    """``b1``, ``b2``, ... — deterministico e dentro de ``stableId``.

    Deterministico importa: o id ancora comentario e correcao no CMS, e um id
    aleatorio faria a mesma materia reprocessada apontar para "outro" bloco.
    """
    return f"b{index}"


def _clean_block_text(node: Any, *, limit: int) -> Optional[str]:
    text = plain_text(node.get_text(" ", strip=True), max_length=limit)
    if not text:
        return None
    # Cinto e suspensorio: `get_text` ja remove tags, mas um texto que contenha
    # `<script` como TEXTO literal ainda seria recusado la.
    if find_forbidden_markup(text) is not None:
        return None
    return text


def _quote_attribution(node: Any) -> Optional[str]:
    for tag in ("cite", "footer"):
        found = node.find(tag)
        if found is not None:
            text = collapse(found.get_text(" ", strip=True))
            if text:
                return plain_text(text, max_length=block_field_limit(BLOCK_QUOTE, "attribution", 500))
    return None


def html_to_blocks(body_html: str) -> BlockConversion:
    """Converte o corpo HTML do draft na lista de blocos do contrato."""
    from bs4 import BeautifulSoup

    result = BlockConversion()
    source = str(body_html or "").strip()
    if not source:
        return result

    soup = BeautifulSoup(source, "html.parser")
    root = soup.body if soup.body is not None else soup

    paragraph_limit = block_field_limit(BLOCK_PARAGRAPH, "text", 5000)
    heading_limit = block_field_limit(BLOCK_HEADING, "text", 500)
    quote_limit = block_field_limit(BLOCK_QUOTE, "text", 5000)

    counter = 0

    def emit(payload: Dict[str, Any]) -> None:
        nonlocal counter
        counter += 1
        payload["id"] = _next_id(counter)
        result.blocks.append(payload)

    for node in root.find_all(recursive=False) or []:
        name = (node.name or "").lower()

        if name in _HEADING_LEVELS:
            text = _clean_block_text(node, limit=heading_limit)
            if text:
                emit({"type": BLOCK_HEADING, "level": _HEADING_LEVELS[name], "text": text})
            continue

        if name == "blockquote":
            text = _clean_block_text(node, limit=quote_limit)
            if text:
                block: Dict[str, Any] = {"type": BLOCK_QUOTE, "text": text}
                attribution = _quote_attribution(node)
                if attribution:
                    block["attribution"] = attribution
                emit(block)
            continue

        if name == "hr":
            emit({"type": BLOCK_DIVIDER})
            continue

        if name in ("ul", "ol"):
            for item in node.find_all("li", recursive=False):
                text = _clean_block_text(item, limit=paragraph_limit)
                if text:
                    emit({"type": BLOCK_PARAGRAPH, "text": text})
            continue

        if name in ("figure", "img", "picture"):
            images = [node] if name == "img" else node.find_all("img")
            for image in images:
                src = collapse(image.get("src"))
                if src:
                    result.dropped_images.append(src)
            continue

        if name in ("script", "style", "iframe", "object", "embed", "noscript"):
            result.warnings.append(f"BLOCO_DESCARTADO:{name}")
            continue

        text = _clean_block_text(node, limit=paragraph_limit)
        if text:
            emit({"type": BLOCK_PARAGRAPH, "text": text})

    # Texto solto fora de qualquer tag: o parser o deixa como nó de nivel raiz e
    # ele seria perdido em silencio.
    if not result.blocks:
        text = plain_text(root.get_text(" ", strip=True), max_length=paragraph_limit)
        if text and find_forbidden_markup(text) is None:
            emit({"type": BLOCK_PARAGRAPH, "text": text})

    if result.dropped_images:
        result.warnings.append(
            f"IMAGENS_NAO_ENVIADAS:{len(result.dropped_images)} "
            "(bloco de imagem exige mediaRef de midia ja aprovada no CMS)"
        )

    limit = max_blocks()
    if len(result.blocks) > limit:
        # Truncar em silencio publicaria uma materia pela metade. O aviso e
        # explicito e o chamador decide — hoje, bloqueando a entrega.
        result.truncated = True
        result.warnings.append(f"CORPO_ACIMA_DO_LIMITE:{len(result.blocks)}>{limit}")

    return result


def blocks_summary(blocks: List[Dict[str, Any]]) -> Tuple[int, Dict[str, int]]:
    """Total e contagem por tipo. Usado em log e no relatorio do CLI."""
    counts: Dict[str, int] = {}
    for block in blocks:
        name = str(block.get("type") or "?")
        counts[name] = counts.get(name, 0) + 1
    return len(blocks), counts


def has_structured_steps(blocks: List[Dict[str, Any]]) -> bool:
    """O corpo tem passos executaveis reais?

    Sempre ``False``: o contrato nao tem bloco de passo, entao o MNScr nao tem
    como PROVAR que a materia e um passo a passo. Recomendar ``HowTo`` sem essa
    prova seria structured data falso — e o lado publico recusaria de qualquer
    forma. A funcao existe para que a matriz de Schema.org tenha onde perguntar.
    """
    return False


def has_structured_list(blocks: List[Dict[str, Any]]) -> bool:
    """O corpo tem itens de lista estruturados? Ver ``has_structured_steps``."""
    return False


def has_rating(blocks: List[Dict[str, Any]]) -> bool:
    """O corpo tem avaliacao real? Ver ``has_structured_steps``."""
    return False


__all__ = [
    "BLOCK_DIVIDER",
    "BLOCK_HEADING",
    "BLOCK_PARAGRAPH",
    "BLOCK_QUOTE",
    "BlockConversion",
    "block_field_limit",
    "blocks_summary",
    "has_rating",
    "has_structured_list",
    "has_structured_steps",
    "html_to_blocks",
    "max_blocks",
]
