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

**Video do YouTube vira bloco proprio.** Quando o corpo chega aqui, o embed ja
foi normalizado para um paragrafo com a URL nua (``html_utils
.strip_credits_and_normalize_youtube``), e ate agora esse paragrafo saia como
``paragraph`` — uma URL crua no meio do texto. O contrato tem ``video``, e o
Cinerie sabe montar o player; o que faltava era emitir.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Dict, Final, FrozenSet, List, Optional, Sequence, Tuple

from .contract import load_schema
from .identity import is_stable_id
from .text import collapse, find_forbidden_markup, plain_text

logger = logging.getLogger(__name__)

BLOCK_PARAGRAPH: Final[str] = "paragraph"
BLOCK_HEADING: Final[str] = "heading"
BLOCK_QUOTE: Final[str] = "quote"
BLOCK_DIVIDER: Final[str] = "divider"
BLOCK_VIDEO: Final[str] = "video"
BLOCK_SOURCE_LIST: Final[str] = "sourceList"

PROVIDER_YOUTUBE: Final[str] = "youtube"

#: Niveis de heading aceitos. ``h1`` e do titulo da pagina; o corpo comeca em h2.
_HEADING_LEVELS: Final[Dict[str, int]] = {"h1": 2, "h2": 2, "h3": 3, "h4": 4, "h5": 4, "h6": 4}

#: Id do bloco de video. Nao e posicional de proposito: ``b7`` viraria ``b8`` na
#: revisao que ganhasse um paragrafo antes dele, e o CMS leria isso como "outro
#: bloco". Um id fixo sobrevive a reescrita do corpo inteiro.
_VIDEO_BLOCK_ID: Final[str] = "vid1"

#: Id do bloco de fontes. Mesmo motivo do video: ele e sempre o ultimo, e o
#: numero do ultimo muda a cada paragrafo que entra ou sai.
SOURCE_LIST_BLOCK_ID: Final[str] = "srcs"

#: Marca do paragrafo de credito que ``html_utils.append_source_credit_block``
#: deixa no fim do corpo. Ele existe porque ate agora nao havia bloco de fontes;
#: quando o ``sourceList`` sai, ele e a MESMA informacao em texto morto.
_CREDIT_LINE_CLASS: Final[str] = "mn-source-credit-line"


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
    #: Id do paragrafo "Fonte: X" quando o corpo trouxe um. Quem monta o pedido
    #: usa isso para troca-lo pelo ``sourceList`` — e so quando o ``sourceList``
    #: realmente sair, para nao apagar a atribuicao e nao por nada no lugar.
    credit_block_id: Optional[str] = None

    @property
    def is_empty(self) -> bool:
        return not self.blocks


#: Um id de video do YouTube tem exatamente 11 caracteres deste alfabeto. E a
#: unica validacao possivel sem ir a rede — e ja basta para nao emitir um player
#: apontando para o que o extrator confundiu com id.
_YOUTUBE_ID: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9_-]{11}$")

#: As formas de URL do YouTube que sobrevivem ate aqui. A normalizacao anterior
#: entrega ``watch?v=``; as outras estao aqui porque o conversor tambem roda
#: sobre corpo que nao passou por ela (replay, teste, artefato antigo).
_YOUTUBE_URL_PATTERNS: Final[Tuple[re.Pattern[str], ...]] = (
    re.compile(r"^https?://(?:www\.|m\.)?youtube(?:-nocookie)?\.com/watch\?(?:[^#]*&)?v=([^&#\s]+)", re.I),
    re.compile(r"^https?://(?:www\.)?youtu\.be/([^?&#/\s]+)", re.I),
    re.compile(r"^https?://(?:www\.|m\.)?youtube(?:-nocookie)?\.com/embed/([^?&#/\s]+)", re.I),
    re.compile(r"^https?://(?:www\.|m\.)?youtube(?:-nocookie)?\.com/shorts/([^?&#/\s]+)", re.I),
)


def youtube_video_id(value: str) -> Optional[str]:
    """Id do video em uma URL do YouTube, ou ``None``.

    ``None`` cobre dois casos que o chamador trata igual — descartar com aviso:
    a URL nao e do YouTube, ou o que veio no lugar do id nao tem a forma de um.
    """
    text = collapse(value)
    if not text:
        return None
    for pattern in _YOUTUBE_URL_PATTERNS:
        match = pattern.match(text)
        if match is None:
            continue
        candidate = match.group(1).strip()
        return candidate if _YOUTUBE_ID.match(candidate) else None
    return None


def _video_block(video_id: str) -> Dict[str, Any]:
    """Bloco ``video`` a partir de um id ja validado.

    ``externalId`` so entra quando o id CABE no formato do contrato. Os dois
    alfabetos quase coincidem — ``stableId`` e ``[A-Za-z0-9][A-Za-z0-9._:-]*`` e
    aceita ``_`` e ``-`` —, mas ele exige que o PRIMEIRO caractere seja
    alfanumerico, e um id do YouTube pode comecar por ``-`` ou ``_``. Mandar um
    desses reprovaria o pedido INTEIRO por causa de um campo opcional; a
    ``url``, que nao tem esse formato, carrega o video sozinha.
    """
    block: Dict[str, Any] = {
        "type": BLOCK_VIDEO,
        "provider": PROVIDER_YOUTUBE,
        "url": f"https://www.youtube.com/watch?v={video_id}",
    }
    if is_stable_id(video_id):
        block["externalId"] = video_id
    return block


#: Um paragrafo que E o video: so a URL, nada mais. Um paragrafo que MENCIONA o
#: YouTube no meio de uma frase continua sendo texto.
_YOUTUBE_HOST: Final[re.Pattern[str]] = re.compile(
    r"^https?://(?:www\.|m\.)?(?:youtube(?:-nocookie)?\.com|youtu\.be)/\S*$", re.I
)


def _looks_like_youtube(value: str) -> bool:
    return bool(_YOUTUBE_HOST.match(collapse(value)))


def _attach_video(result: BlockConversion, videos: List[str]) -> None:
    """Poe o video depois do primeiro ``h2``, ou no fim quando nao ha nenhum.

    Depois do primeiro intertitulo porque e onde o leitor ja sabe do que a
    materia trata e ainda nao chegou ao fim — e porque a posicao e DEDUZIVEL do
    corpo, e nao um numero escolhido a mao que a proxima materia desmente.
    Sem ``h2``, o fim e o unico lugar que nao corta o texto ao meio.

    Um video por materia. O segundo nao entra: dois players na mesma pagina
    disputam a atencao e o Cinerie desenha os dois do mesmo tamanho.
    """
    if not videos:
        return
    if len(videos) > 1:
        logger.info(
            "[BLOCKS] %s videos no corpo; publicado o primeiro (%s), ignorados %s",
            len(videos), videos[0], ", ".join(videos[1:]),
        )
        result.warnings.append(f"VIDEOS_EXCEDENTES:{len(videos) - 1}")

    block = _video_block(videos[0])
    block["id"] = _VIDEO_BLOCK_ID

    posicao = len(result.blocks)
    for index, existente in enumerate(result.blocks):
        if existente.get("type") == BLOCK_HEADING and existente.get("level") == 2:
            posicao = index + 1
            break
    result.blocks.insert(posicao, block)


BLOCK_IMAGE: Final[str] = "image"


def attach_inline_images(
    result: BlockConversion, images: Sequence[Dict[str, Any]]
) -> List[str]:
    """Distribui blocos ``image`` pelo corpo, um apos cada ``h2`` a partir do
    SEGUNDO. Devolve os ids dos blocos emitidos.

    O primeiro ``h2`` fica de fora porque o video ja mora ali
    (``_attach_video``) e a capa acabou de aparecer no topo — mais uma imagem
    nesse trecho empilharia tres midias antes do segundo paragrafo. Sem ``h2``
    suficientes, o que sobrar entra no fim do corpo, antes do ``sourceList``
    (que ainda nao foi anexado quando isto roda — ver ``build_publication_request``).

    ``images`` traz ``mediaId`` + ``alt`` de midia JA INGERIDA no CMS (e o
    contrato exige exatamente isso: ``mediaRef`` que casa com ``media[].mediaId``
    do mesmo pedido). Item sem ``mediaId`` valido e pulado — bloco de imagem
    apontando para o nada reprovaria o pedido inteiro.
    """
    emitted: List[str] = []
    if not images:
        return emitted

    usable: List[Dict[str, Any]] = []
    for item in images:
        media_id = collapse(item.get("mediaId") or item.get("media_id"))
        if not is_stable_id(media_id):
            continue
        alt = plain_text(
            item.get("alt") or item.get("altSuggestion") or "Imagem da materia",
            max_length=block_field_limit(BLOCK_IMAGE, "alt", 500),
        )
        usable.append({"mediaRef": media_id, "alt": alt or "Imagem da materia"})

    if not usable:
        return emitted

    posicoes: List[int] = []
    h2_vistos = 0
    for index, existente in enumerate(result.blocks):
        if existente.get("type") == BLOCK_HEADING and existente.get("level") == 2:
            h2_vistos += 1
            if h2_vistos >= 2:
                posicoes.append(index + 1)

    limite = max_blocks()
    for ordinal, image in enumerate(usable, start=1):
        if len(result.blocks) + 1 > limite:
            result.warnings.append("IMAGENS_INLINE_NAO_COUBERAM")
            break
        block = {
            "id": f"img{ordinal}",
            "type": BLOCK_IMAGE,
            "mediaRef": image["mediaRef"],
            "alt": image["alt"],
        }
        if posicoes:
            posicao = posicoes.pop(0)
            result.blocks.insert(posicao, block)
            # Os proximos pontos de insercao andaram um indice para a frente.
            posicoes = [p + 1 for p in posicoes]
        else:
            result.blocks.append(block)
        emitted.append(block["id"])
    return emitted


def attach_source_list(result: BlockConversion, source_ids: Sequence[str]) -> Optional[str]:
    """Fecha a materia com ``sourceList``, no lugar do paragrafo "Fonte: X".

    Os ids vem de ``externalSources`` do MESMO pedido e sao conferidos aqui um a
    um: ``sourceRefs`` que aponta para fonte que nao esta na lista e um link
    quebrado publicado — e o contrato nao tem como pegar isso, porque ele so
    conhece o formato do id, nao a lista.

    O paragrafo de credito so sai quando o bloco entra. Se nao houver nenhuma
    fonte referenciavel, o texto continua la: perder a atribuicao seria pior do
    que exibi-la sem link, que e exatamente o estado que este bloco veio
    corrigir.

    Devolve o id do bloco emitido, ou ``None`` quando nao houve o que emitir.
    """
    refs: List[str] = []
    for candidate in source_ids or []:
        text = collapse(candidate)
        if text and is_stable_id(text) and text not in refs:
            refs.append(text)
    # Teto lido do contrato, nao redigitado: `sourceRefs` e array, entao o
    # limite mora em `maxItems` (e nao em `maxLength`, que e de campo de texto).
    variant = _block_variants().get(BLOCK_SOURCE_LIST) or {}
    max_items = variant.get("properties", {}).get("sourceRefs", {}).get("maxItems")
    if isinstance(max_items, int) and max_items > 0:
        refs = refs[:max_items]

    if not refs:
        result.warnings.append("FONTES_SEM_ID_ESTAVEL")
        return None

    if result.credit_block_id is not None:
        result.blocks[:] = [
            block for block in result.blocks if block.get("id") != result.credit_block_id
        ]
        result.credit_block_id = None
    elif len(result.blocks) + 1 > max_blocks():
        # Sem paragrafo de credito para trocar, o bloco e um a mais; num corpo
        # ja no teto ele empurraria a materia para fora do contrato.
        result.warnings.append("SOURCE_LIST_NAO_COUBE")
        return None

    result.blocks.append(
        {"id": SOURCE_LIST_BLOCK_ID, "type": BLOCK_SOURCE_LIST, "sourceRefs": refs}
    )
    return SOURCE_LIST_BLOCK_ID


def _next_id(index: int) -> str:
    """``b1``, ``b2``, ... — deterministico e dentro de ``stableId``.

    Deterministico importa: o id ancora comentario e correcao no CMS, e um id
    aleatorio faria a mesma materia reprocessada apontar para "outro" bloco.
    """
    return f"b{index}"


#: Tags que o navegador desenha em linha PROPRIA. Elas — e so elas — merecem uma
#: fronteira quando o texto e achatado.
_BLOCK_LEVEL_TAGS: Final[frozenset] = frozenset(
    {
        "address", "article", "aside", "blockquote", "br", "dd", "div", "dl", "dt",
        "figcaption", "figure", "footer", "h1", "h2", "h3", "h4", "h5", "h6",
        "header", "hr", "li", "main", "nav", "ol", "p", "pre", "section", "table",
        "tbody", "td", "tfoot", "th", "thead", "tr", "ul",
    }
)


def node_text(node: Any) -> str:
    """Texto visivel do no, com o espacamento que o NAVEGADOR desenharia.

    ``get_text(separador)`` do BeautifulSoup nao serve para texto que vai ser
    publicado, e erra nos dois sentidos:

    - **Sem separador** ele emenda. ``<span>Season 2</span><span>Disney/Hulu
      </span>`` vira ``Season 2Disney/Hulu`` — duas palavras coladas, do jeito
      que apareceu no log de hoje na legenda da Hollywood Reporter.
    - **Com separador** ele separa demais. ``<b>Rothman</b>.`` vira
      ``Rothman .``, com espaco antes do ponto, porque o separador entra entre
      TODOS os pedacos, inclusive onde a marcacao nao tinha espaco nenhum.

    A regra certa e a do navegador: entre dois trechos em LINHA (``<b>``,
    ``<a>``, ``<span>``) nao ha fronteira nenhuma — o espaco, quando existe,
    esta no proprio texto e e preservado. Fronteira so entra onde a tag e de
    bloco, e ai como ``\\n``, que ``collapse`` depois vira um espaco e que
    ``_paragraph_texts`` usa para achar onde um paragrafo termina.
    """
    from bs4 import Comment, NavigableString

    pedacos: List[str] = []

    def visitar(atual: Any) -> None:
        if isinstance(atual, Comment):
            # Comentario nao e texto visivel; sem esta guarda ele sai publicado,
            # porque `Comment` e subclasse de `NavigableString`.
            return
        if isinstance(atual, NavigableString):
            pedacos.append(str(atual))
            return
        name = (getattr(atual, "name", "") or "").lower()
        if name in ("script", "style", "noscript"):
            return
        de_bloco = name in _BLOCK_LEVEL_TAGS
        if de_bloco:
            pedacos.append("\n")
        for filho in getattr(atual, "children", []):
            visitar(filho)
        if de_bloco:
            pedacos.append("\n")

    visitar(node)
    return "".join(pedacos)


#: Tag HTML -> tipo de marcacao do contrato. Fechada de proposito: `<span>` com
#: classe, `<mark>`, `<u>` e afins nao viram nada. O que nao esta aqui vira texto
#: limpo, que e o comportamento de sempre.
_MARK_BY_TAG: Final[Dict[str, str]] = {
    "strong": "bold",
    "b": "bold",
    "em": "italic",
    "i": "italic",
    "a": "link",
}

#: Teto de marcacoes por paragrafo (`LIMITS.marks` do contrato). Acima disso o
#: pedido inteiro seria recusado — o corte acontece aqui, e o texto sai inteiro
#: com menos enfase, nunca a materia recusada por causa de enfase.
_MAX_MARKS_PER_PARAGRAPH: Final[int] = 200


def _marked_paragraph(
    node: Any, *, limit: int, allowed_hrefs: FrozenSet[str] = frozenset()
) -> Optional[Tuple[str, List[Dict[str, Any]]]]:
    """Texto limpo + `marks`, com offsets no texto FINAL.

    O contrato nao aceita HTML: a enfase viaja como intervalo `start`/`end` sobre
    o texto ja normalizado. Isso torna a ORDEM das operacoes o problema inteiro —
    calcular offset sobre o texto cru e normalizar depois desalinha tudo, porque
    `collapse` muda o comprimento. Aqui a normalizacao acontece DURANTE a
    montagem, e o offset nasce ja no espaco final.

    Devolve ``None`` quando nao ha texto aproveitavel ou quando o texto carrega
    markup proibido — o chamador segue pelo caminho de sempre.
    """
    from bs4 import NavigableString

    pedacos: List[str] = []
    tamanho = 0
    marcas: List[Dict[str, Any]] = []

    def escrever(bruto: str) -> None:
        nonlocal tamanho
        texto = re.sub(r"\s+", " ", bruto)
        if not texto:
            return
        # Junta sem criar espaco duplo: `collapse` colapsaria, e o offset ja
        # gravado apontaria para o lugar errado depois.
        if texto.startswith(" ") and (tamanho == 0 or pedacos[-1].endswith(" ")):
            texto = texto.lstrip(" ")
        if not texto:
            return
        pedacos.append(texto)
        tamanho += len(texto)

    def andar(atual: Any) -> None:
        for filho in getattr(atual, "children", []) or []:
            if isinstance(filho, NavigableString):
                escrever(str(filho))
                continue
            tag = (getattr(filho, "name", "") or "").lower()
            if tag in ("script", "style", "noscript"):
                continue
            tipo = _MARK_BY_TAG.get(tag)
            href: Optional[str] = None
            if tipo == "link":
                href = collapse(filho.get("href"))
                # Link relativo ou `javascript:` nao vira marcacao: o contrato so
                # aceita http(s), e mandar outra coisa reprova o pedido inteiro.
                if not href.lower().startswith(("http://", "https://")):
                    tipo = None
                # E o destino precisa ser um que a MATERIA declarou.
                #
                # Todo `<a>` que sobra no corpo depois da limpeza foi escrito
                # pela IA, e ela inventa endereco com naturalidade: na materia
                # 128 saiu `https://www.cinerie.com.br/tag/industry` — dominio
                # errado (o site e `.com`) e caminho de tag do WordPress, que o
                # Cinerie nem tem. Um link quebrado publicado e pior do que
                # texto sem link, e o leitor nao tem como saber que ele foi
                # inventado.
                elif href not in allowed_hrefs:
                    tipo = None
                    href = None
            inicio = tamanho
            andar(filho)
            if tipo is not None and tamanho > inicio:
                marca: Dict[str, Any] = {"start": inicio, "end": tamanho, "type": tipo}
                if href:
                    marca["href"] = href
                marcas.append(marca)

    andar(node)

    texto = "".join(pedacos)
    # `collapse` tira as pontas; o deslocamento da esquerda precisa ser
    # descontado de toda marcacao, senao a enfase anda para a direita.
    esquerda = len(texto) - len(texto.lstrip(" "))
    texto = texto.strip(" ")
    if not texto:
        return None

    cortado = plain_text(texto, max_length=limit)
    if not cortado or find_forbidden_markup(cortado) is not None:
        return None

    limite = len(cortado)
    finais: List[Dict[str, Any]] = []
    for marca in marcas:
        inicio = marca["start"] - esquerda
        fim = min(marca["end"] - esquerda, limite)
        if inicio < 0 or fim <= inicio:
            # Marcacao que ficou toda fora do texto truncado. Some sem aviso: o
            # que ela marcava tambem sumiu.
            continue
        nova = dict(marca)
        nova["start"] = _utf16_offset(cortado, inicio)
        nova["end"] = _utf16_offset(cortado, fim)
        finais.append(nova)

    return cortado, finais[:_MAX_MARKS_PER_PARAGRAPH]


def _utf16_offset(texto: str, indice: int) -> int:
    """Indice de PONTO DE CODIGO (Python) -> indice de unidade UTF-16 (JavaScript).

    O contrato diz, com todas as letras, que os offsets sao unidades UTF-16 — a
    mesma que `String.prototype.slice` usa no render. Python conta pontos de
    codigo, e as duas contagens so divergem fora do BMP: um emoji vale 1 aqui e
    2 la.

    Sem esta conversao, um 🎬 no comeco do paragrafo empurraria toda a enfase
    seguinte uma casa para a esquerda no site — e o pior e que o pedido passaria
    na validacao dos dois lados, porque o intervalo continua dentro do texto. O
    erro so apareceria na pagina, com a palavra errada em negrito.
    """
    return len(texto[:indice].encode("utf-16-le")) // 2


def _paragraph_texts(node: Any, *, limit: int) -> List[str]:
    """Um ``<p>`` que carrega varios paragrafos vira varios blocos.

    A materia publicada em 05/08 saiu como UM bloco de ~2.755 caracteres, com os
    intertitulos como texto solto e quebra dupla dentro dele. O prompt pede
    ``<h2>`` e paragrafos separados; naquela geracao o modelo devolveu tudo
    dentro de um ``<p>`` so, e o conversor embarcou fielmente o que recebeu.

    A divisao precisa acontecer ANTES da limpeza: ``collapse`` troca as quebras
    por espaco, e quem tentar separar depois nao encontra mais nada para separar.
    (Foi o primeiro jeito que tentei, e era um no-op silencioso.)

    Nao ha adivinhacao de intertitulo: transformar texto solto em ``heading``
    exigiria decidir por heuristica que uma linha curta e um titulo, e errar isso
    publica hierarquia FALSA — pior para SEO do que um paragrafo a mais. O que se
    recupera aqui e so o que o texto declara sem ambiguidade: onde um paragrafo
    termina.
    """
    bruto = node_text(node)
    partes = [parte for parte in re.split(r"\n[ \t]*\n", bruto or "") if parte.strip()]

    textos: List[str] = []
    for parte in partes:
        limpo = plain_text(collapse(parte), max_length=limit)
        if limpo and find_forbidden_markup(limpo) is None:
            textos.append(limpo)
    return textos


def _clean_block_text(node: Any, *, limit: int) -> Optional[str]:
    text = plain_text(node_text(node), max_length=limit)
    if not text:
        return None
    # Cinto e suspensorio: `node_text` ja remove tags, mas um texto que contenha
    # `<script` como TEXTO literal ainda seria recusado la.
    if find_forbidden_markup(text) is not None:
        return None
    return text


def _quote_attribution(node: Any) -> Optional[str]:
    """Autor declarado em ``<cite>``/``<footer>`` — e retirado do corpo da citacao.

    Extrair sem retirar deixava o nome duas vezes na materia: dentro do texto da
    citacao e no campo ``attribution``, que o Cinerie desenha logo abaixo.
    """
    for tag in ("cite", "footer"):
        found = node.find(tag)
        if found is not None:
            text = collapse(node_text(found))
            found.extract()
            if text:
                return plain_text(text, max_length=block_field_limit(BLOCK_QUOTE, "attribution", 500))
    return None


#: Verbos que marcam fala ATRIBUIDA. Lista fechada de proposito: e ela que
#: separa "alguem disse isto" de uma expressao entre aspas, e um verbo generico
#: a mais transformaria qualquer aspa em citacao.
_SPEECH_VERBS: Final[str] = (
    r"disse|afirmou|declarou|explicou|contou|revelou|acrescentou|completou|comentou|"
    r"ressaltou|destacou|refor[cç]ou|admitiu|garantiu|concluiu|lembrou|observou|"
    r"defendeu|resumiu|brincou|adiantou"
)

#: Nome proprio: 1 a 4 palavras comecando em maiuscula, com as particulas do
#: portugues no meio (``Jorg Hillebrand``, ``Destin Daniel Cretton``).
_PROPER_NAME: Final[str] = (
    r"[A-ZÀ-Ý][\wÀ-ÿ'’-]*(?:\s+(?:d[aeo]s?|e|van|von|de la)\s+[A-ZÀ-Ý][\wÀ-ÿ'’-]*"
    r"|\s+[A-ZÀ-Ý][\wÀ-ÿ'’-]*){0,3}"
)

_OPEN_QUOTE: Final[str] = r"[\"“„]"
_CLOSE_QUOTE: Final[str] = r"[\"”“]"

#: ``"...", afirmou Rothman.`` — a forma esmagadoramente mais comum no corpo
#: gerado. O ponto e a virgula ganham ``\s*`` porque a marcacao inline pode ter
#: deixado espaco antes deles.
_QUOTE_AFTER: Final[re.Pattern[str]] = re.compile(
    rf"{_OPEN_QUOTE}(?P<texto>[^\"“”„]{{40,4000}}){_CLOSE_QUOTE}"
    rf"\s*,?\s*(?:{_SPEECH_VERBS})\s+(?P<autor>{_PROPER_NAME})\s*\.",
)

#: ``Rothman afirmou: "..."`` — a forma invertida.
_QUOTE_BEFORE: Final[re.Pattern[str]] = re.compile(
    rf"(?P<autor>{_PROPER_NAME})\s+(?:{_SPEECH_VERBS})\s*:\s*"
    rf"{_OPEN_QUOTE}(?P<texto>[^\"“”„]{{40,4000}}){_CLOSE_QUOTE}\s*\.?",
)

#: ``"...", afirmou.`` — verbo de fala SEM nome, fechando a frase. Sozinha, a
#: forma e ambigua ("afirmou quem?"); ela so promove quando a materia tem UM
#: sujeito dominante identificado (ver ``dominant_speaker``), e e a ele que a
#: fala e atribuida. O ``[.!]`` colado ao verbo e o que continua rejeitando
#: ``afirmou o executivo.`` — ali ha um sujeito declarado, e ele e generico.
_QUOTE_AFTER_BARE: Final[re.Pattern[str]] = re.compile(
    rf"{_OPEN_QUOTE}(?P<texto>[^\"“”„]{{40,4000}}){_CLOSE_QUOTE}"
    rf"\s*,?\s*(?:{_SPEECH_VERBS})\s*[.!]",
)

#: ``Murphy admitiu que a participacao foi fundamental: "..."`` — citacao
#: introduzida por dois-pontos, com o verbo de fala em qualquer lugar da oracao
#: introdutoria (nao colado aos dois-pontos, que e o caso de ``_QUOTE_BEFORE``).
#: So o fim de paragrafo entra: no meio do texto os dois-pontos tem usos demais
#: para a leitura ser inequivoca.
_QUOTE_COLON_BARE: Final[re.Pattern[str]] = re.compile(
    rf":\s*{_OPEN_QUOTE}(?P<texto>[^\"“”„]{{40,4000}}){_CLOSE_QUOTE}\s*\.?\s*$",
)

#: Palavras capitalizadas que NAO sao nome de gente. Sem esta lista, ``Ele
#: disse`` elegeria "Ele" como falante da materia inteira.
_SPEAKER_STOPWORDS: Final[frozenset] = frozenset(
    {
        "Ele", "Ela", "Eles", "Elas", "Eu", "Nos", "Nós", "Voce", "Você",
        "O", "A", "Os", "As", "Um", "Uma", "Segundo", "Em", "No", "Na",
        "Mas", "Depois", "Antes", "Apesar", "Quem", "Isso", "Isto",
    }
)

_NAME_THEN_VERB: Final[re.Pattern[str]] = re.compile(
    rf"(?P<autor>{_PROPER_NAME})\s+(?:{_SPEECH_VERBS})\b"
)
_VERB_THEN_NAME: Final[re.Pattern[str]] = re.compile(
    rf"\b(?:{_SPEECH_VERBS})\s+(?P<autor>{_PROPER_NAME})"
)


def dominant_speaker(body_text: str, title: str = "") -> Optional[str]:
    """O unico sujeito que fala na materia, ou ``None`` quando ha ambiguidade.

    A regra de promocao de citacao exige nome colado as aspas porque
    ``"...", afirmou o executivo`` nao diz quem falou. Mas ela ficava apertada
    demais na materia em que TODAS as falas sao da mesma pessoa — quatro
    citacoes de Ryan Murphy diluidas em paragrafo porque "afirmou" veio sem
    nome. Aqui se decide quando "afirmou" sozinho e inequivoco:

    - coletam-se os nomes proprios adjacentes a verbos de fala no corpo;
    - grafias que sao subconjunto uma da outra ("Murphy" e "Ryan Murphy") sao o
      MESMO falante, e a forma mais longa vence;
    - um unico falante -> ele e o dominante. Dois ou mais -> ``None``, e a
      ambiguidade continua bloqueando a promocao sem nome.

    O titulo desempata a forma: se o falante aparece nele com grafia mais
    completa, e essa que vai para ``attribution``.
    """
    flat = collapse(body_text)
    if not flat:
        return None

    candidates: List[str] = []
    for pattern in (_NAME_THEN_VERB, _VERB_THEN_NAME):
        for match in pattern.finditer(flat):
            name = collapse(match.group("autor")).rstrip(" ,.;:")
            tokens = name.split()
            # Poda pronomes/conectivos capitalizados na frente do nome
            # ("Depois Murphy disse" -> "Murphy").
            while tokens and tokens[0] in _SPEAKER_STOPWORDS:
                tokens = tokens[1:]
            if not tokens:
                continue
            candidates.append(" ".join(tokens))

    if not candidates:
        return None

    groups: List[Dict[str, Any]] = []
    for name in candidates:
        tokens = set(name.split())
        merged = False
        for group in groups:
            if tokens <= group["tokens"] or group["tokens"] <= tokens:
                group["tokens"] |= tokens
                if len(name) > len(group["form"]):
                    group["form"] = name
                merged = True
                break
        if not merged:
            groups.append({"tokens": set(tokens), "form": name})

    if len(groups) != 1:
        return None

    dominant = groups[0]["form"]
    # A grafia do titulo costuma ser a mais completa ("Ryan Murphy" no titulo,
    # "Murphy" no corpo). Se o titulo contem o falante com mais contexto, e a
    # forma do titulo que assina a citacao.
    title_flat = collapse(title)
    if title_flat:
        for match in re.finditer(rf"{_PROPER_NAME}", title_flat):
            form = collapse(match.group(0)).rstrip(" ,.;:")
            if set(dominant.split()) <= set(form.split()) and len(form) > len(dominant):
                dominant = form
                break
    return dominant


#: Teto de citacoes promovidas por materia. Acima disso a materia deixa de ser
#: uma reportagem com uma fala em destaque e vira uma transcricao fatiada.
_MAX_PROMOTED_QUOTES: Final[int] = 2

#: Fim de frase. O que vier depois do ultimo destes, antes da citacao, e a
#: clausula que APRESENTA a fala — nao um paragrafo por si.
_SENTENCE_END: Final[re.Pattern[str]] = re.compile(r"[.!?…](?=[^.!?…]*$)")

#: Ate onde a clausula de apresentacao pode ir. Acima disso o "resto" nao e uma
#: clausula: e texto da materia, e a divisao estava errada desde o comeco.
_MAX_LEAD_IN: Final[int] = 120

#: Teto da oracao que introduz citacao por dois-pontos ("Murphy admitiu que a
#: participacao foi fundamental:"). E maior que ``_MAX_LEAD_IN`` porque essa
#: oracao carrega conteudo e FICA no paragrafo — mas um bloco de texto corrido
#: sem pontuacao nenhuma antes dos dois-pontos nao e uma oracao introdutoria,
#: e uma parede de texto, e promover ali e leitura errada do paragrafo.
_MAX_COLON_INTRO: Final[int] = 160


def _trim_lead_in(before: str) -> Optional[str]:
    """Corta a clausula que apresenta a citacao, ou desiste da promocao.

    ``Em comunicado oficial, a Universal declarou: "..."`` deixava para tras o
    paragrafo ``Em comunicado oficial, a`` — uma frase cortada no artigo, que
    foi publicada assim na materia 21. O pedaco entre o ultimo ponto final e a
    citacao pertence a atribuicao, e a atribuicao ja esta no bloco.

    ``None`` significa "nao promova": o pedaco e grande demais para ser uma
    clausula de apresentacao, entao ele e conteudo, e cortar perderia texto.
    """
    texto = collapse(before)
    if not texto:
        return ""
    # O lookahead de `_SENTENCE_END` ja garante que o unico casamento e o ULTIMO
    # fim de frase; nao ha laco a fazer aqui.
    match = _SENTENCE_END.search(texto)
    corte = match.end() if match is not None else 0
    if len(collapse(texto[corte:])) > _MAX_LEAD_IN:
        return None
    return collapse(texto[:corte])


@dataclass
class _QuoteSplit:
    """Um paragrafo partido em: o que vem antes, a citacao, o que vem depois."""

    before: str
    text: str
    attribution: str
    after: str


def split_promotable_quote(
    paragraph: str, *, dominant: Optional[str] = None
) -> Optional[_QuoteSplit]:
    """A citacao com autor identificavel dentro de um paragrafo, se houver uma.

    Duas condicoes, e as duas sao do TEXTO — nada e inferido:

    1. ha um trecho entre aspas com corpo de frase (40 caracteres ou mais), e
    2. a autoria e inequivoca: um nome proprio colado as aspas, OU — quando a
       materia tem UM sujeito dominante (``dominant``) — um verbo de fala
       fechando a frase sem sujeito nenhum ("..., afirmou.").

    Sem isso, a resposta e ``None`` e a citacao continua no paragrafo. E o
    desfecho certo para ``a frase traduzida e "Hair today, Gorn tomorrow"`` (sem
    verbo de fala) e para ``afirmou o executivo`` (sujeito generico declarado):
    promover ali seria inventar quem falou, e uma atribuicao errada e pior do
    que nenhuma. O sujeito dominante nao afrouxa esses dois casos — o verbo com
    sujeito generico continua nao casando com nenhuma forma.
    """
    for pattern in (_QUOTE_AFTER, _QUOTE_BEFORE):
        match = pattern.search(paragraph)
        if match is None:
            continue
        texto = collapse(match.group("texto"))
        autor = collapse(match.group("autor")).rstrip(" ,.;:")
        if not texto or not autor:
            continue
        before = _trim_lead_in(paragraph[: match.start()])
        if before is None:
            continue
        return _QuoteSplit(
            before=before,
            text=texto,
            attribution=autor,
            after=collapse(paragraph[match.end():]),
        )

    if not dominant:
        return None

    match = _QUOTE_AFTER_BARE.search(paragraph)
    if match is not None:
        texto = collapse(match.group("texto"))
        before = _trim_lead_in(paragraph[: match.start()])
        if texto and before is not None:
            return _QuoteSplit(
                before=before,
                text=texto,
                attribution=dominant,
                after=collapse(paragraph[match.end():]),
            )

    match = _QUOTE_COLON_BARE.search(paragraph)
    if match is not None:
        texto = collapse(match.group("texto"))
        intro = paragraph[: match.start()]
        # A oracao que introduz os dois-pontos precisa DIZER que alguem falou:
        # sem verbo de fala nela, os dois-pontos podem estar apresentando um
        # slogan, um titulo, uma lista — e ai nao ha citacao nenhuma.
        ultimo_fim = _SENTENCE_END.search(collapse(intro))
        oracao = collapse(intro)[ultimo_fim.end():] if ultimo_fim else collapse(intro)
        oracao_valida = (
            texto
            and len(collapse(oracao)) <= _MAX_COLON_INTRO
            and re.search(rf"\b(?:{_SPEECH_VERBS})\b", oracao)
        )
        if oracao_valida:
            # Se a propria oracao nomeia o falante ("Landgraf admitiu que..."),
            # e ELE que assina — o dominante so cobre a fala sem sujeito.
            nomeado = _NAME_THEN_VERB.search(oracao)
            autor = dominant
            if nomeado is not None:
                candidato = collapse(nomeado.group("autor")).rstrip(" ,.;:")
                tokens = candidato.split()
                while tokens and tokens[0] in _SPEAKER_STOPWORDS:
                    tokens = tokens[1:]
                if tokens:
                    autor = " ".join(tokens)
                    if set(autor.split()) <= set(dominant.split()):
                        autor = dominant
            # A oracao introdutoria carrega informacao ("a participacao de
            # Lange foi fundamental") — ela FICA no paragrafo, com os dois-
            # pontos, que e como o texto anuncia a citacao destacada a seguir.
            before = collapse(intro) + ":"
            return _QuoteSplit(
                before=before,
                text=texto,
                attribution=autor,
                after=collapse(paragraph[match.end():]),
            )
    return None


def html_to_blocks(
    body_html: str,
    *,
    article_title: str = "",
    allowed_link_hrefs: Sequence[str] = (),
) -> BlockConversion:
    """Converte o corpo HTML do draft na lista de blocos do contrato.

    ``article_title`` alimenta a deteccao do sujeito dominante — quem assina as
    citacoes com verbo de fala sem nome. Sem titulo a deteccao ainda funciona,
    so perde a chance de trocar "Murphy" pela grafia completa do titulo.

    ``allowed_link_hrefs`` e a lista FECHADA de destinos que podem virar
    marcacao de link. Vazia por padrao: sem ela, nenhum `<a>` do corpo vira
    link, e e assim que deve ser quando quem chama nao sabe dizer o que a
    materia declarou.
    """
    from bs4 import BeautifulSoup

    result = BlockConversion()
    source = str(body_html or "").strip()
    if not source:
        return result

    soup = BeautifulSoup(source, "html.parser")
    root = soup.body if soup.body is not None else soup

    speaker = dominant_speaker(node_text(root), article_title)

    paragraph_limit = block_field_limit(BLOCK_PARAGRAPH, "text", 5000)
    heading_limit = block_field_limit(BLOCK_HEADING, "text", 500)
    quote_limit = block_field_limit(BLOCK_QUOTE, "text", 5000)

    counter = 0

    def emit(payload: Dict[str, Any]) -> None:
        nonlocal counter
        counter += 1
        payload["id"] = _next_id(counter)
        result.blocks.append(payload)

    videos: List[str] = []
    quotes_promoted = 0

    def emit_paragraph(text: str, *, marks: Optional[List[Dict[str, Any]]] = None) -> None:
        """Paragrafo — ou a citacao que ele carrega, promovida a bloco proprio.

        Promover e PARTIR, nao duplicar: o que vem antes e o que vem depois da
        fala continuam como paragrafo. Repetir o texto nos dois lugares
        publicaria a mesma frase duas vezes na mesma pagina.

        Quando a citacao e promovida, a MARCACAO CAI. Ela e um intervalo sobre
        este texto, e partir o texto em tres move todo offset — remapear daria
        trabalho para o caso em que a enfase menos importa (fala entre aspas ja
        vira bloco proprio, com desenho proprio). Enfase errada e pior que enfase
        ausente, entao aqui ela sai.
        """
        nonlocal quotes_promoted

        bloco_simples: Dict[str, Any] = {"type": BLOCK_PARAGRAPH, "text": text}
        if marks:
            bloco_simples["marks"] = marks

        if quotes_promoted >= _MAX_PROMOTED_QUOTES:
            emit(bloco_simples)
            return
        split = split_promotable_quote(text, dominant=speaker)
        if split is None:
            emit(bloco_simples)
            return

        quotes_promoted += 1
        if split.before:
            emit({"type": BLOCK_PARAGRAPH, "text": plain_text(split.before, max_length=paragraph_limit)})
        result.blocks.append(
            {
                "id": f"q{quotes_promoted}",
                "type": BLOCK_QUOTE,
                "text": plain_text(split.text, max_length=quote_limit),
                "attribution": plain_text(
                    split.attribution, max_length=block_field_limit(BLOCK_QUOTE, "attribution", 500)
                ),
            }
        )
        if split.after:
            emit({"type": BLOCK_PARAGRAPH, "text": plain_text(split.after, max_length=paragraph_limit)})

    def collect_video(url: str, *, origem: str) -> None:
        """Guarda o video valido; descarta o invalido com aviso, nunca meio bloco."""
        video_id = youtube_video_id(url)
        if video_id is None:
            result.warnings.append(f"VIDEO_DESCARTADO:{origem}")
            logger.warning("[BLOCKS] video descartado origem=%s url=%r", origem, url[:120])
            return
        if video_id in videos:
            return
        videos.append(video_id)

    for node in root.find_all(recursive=False) or []:
        name = (node.name or "").lower()

        if name in _HEADING_LEVELS:
            text = _clean_block_text(node, limit=heading_limit)
            if text:
                emit({"type": BLOCK_HEADING, "level": _HEADING_LEVELS[name], "text": text})
            continue

        if name == "blockquote":
            # Atribuicao primeiro: ela RETIRA o `<cite>` do no, e o texto lido
            # depois ja sai sem o nome repetido dentro da citacao.
            attribution = _quote_attribution(node)
            text = _clean_block_text(node, limit=quote_limit)
            if text:
                block: Dict[str, Any] = {"type": BLOCK_QUOTE, "text": text}
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
            # Uma `<figure>` que so envolvia o embed guarda o video, nao a
            # imagem. So o que PARECE YouTube entra: um iframe de outro provedor
            # dentro de uma figure nao e video descartado, e sim nao-video.
            for frame in node.find_all("iframe"):
                src = collapse(frame.get("src"))
                if _looks_like_youtube(src):
                    collect_video(src, origem="iframe-figure")
            for wrapped in node.find_all("p"):
                texto = collapse(node_text(wrapped))
                if _looks_like_youtube(texto):
                    collect_video(texto, origem="p-figure")
            continue

        if name == "iframe":
            src = collapse(node.get("src"))
            if _looks_like_youtube(src):
                collect_video(src, origem="iframe")
            else:
                result.warnings.append(f"BLOCO_DESCARTADO:{name}")
            continue

        if name in ("script", "style", "object", "embed", "noscript"):
            result.warnings.append(f"BLOCO_DESCARTADO:{name}")
            continue

        if _CREDIT_LINE_CLASS in (node.get("class") or []):
            # O paragrafo "Fonte: X" e a MESMA informacao que `sourceList` leva
            # em forma clicavel. Ele fica marcado e so sai quando o bloco de
            # fontes entrar de fato — ver `attach_source_list`.
            text = _clean_block_text(node, limit=paragraph_limit)
            if text:
                emit({"type": BLOCK_PARAGRAPH, "text": text})
                result.credit_block_id = result.blocks[-1]["id"]
            continue

        pedacos = _paragraph_texts(node, limit=paragraph_limit)
        # A enfase so viaja quando o `<p>` e UM paragrafo. Quando ele carrega
        # varios (geracao degradada, ver `_paragraph_texts`), a divisao acontece
        # sobre o texto e os offsets do DOM deixam de valer — sai sem marcacao,
        # que e o comportamento de antes, e nunca marcacao no lugar errado.
        if len(pedacos) == 1 and not _looks_like_youtube(pedacos[0]):
            marcado = _marked_paragraph(
                node, limit=paragraph_limit, allowed_hrefs=frozenset(allowed_link_hrefs)
            )
            if marcado is not None and marcado[1] and marcado[0] == pedacos[0]:
                emit_paragraph(marcado[0], marks=marcado[1])
                continue
        for pedaco in pedacos:
            if _looks_like_youtube(pedaco):
                collect_video(pedaco, origem="paragrafo")
                continue
            emit_paragraph(pedaco)

    # Texto solto fora de qualquer tag: o parser o deixa como nó de nivel raiz e
    # ele seria perdido em silencio. `videos` entra na condicao porque um corpo
    # que so tem o video nao esta vazio — sem isso a URL voltava como paragrafo
    # ao lado do proprio bloco de video.
    if not result.blocks and not videos:
        text = plain_text(node_text(root), max_length=paragraph_limit)
        if text and find_forbidden_markup(text) is None:
            emit({"type": BLOCK_PARAGRAPH, "text": text})

    _attach_video(result, videos)

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
    "BLOCK_IMAGE",
    "BLOCK_PARAGRAPH",
    "BLOCK_QUOTE",
    "BLOCK_SOURCE_LIST",
    "BLOCK_VIDEO",
    "PROVIDER_YOUTUBE",
    "SOURCE_LIST_BLOCK_ID",
    "BlockConversion",
    "attach_inline_images",
    "attach_source_list",
    "block_field_limit",
    "blocks_summary",
    "dominant_speaker",
    "has_rating",
    "has_structured_list",
    "has_structured_steps",
    "html_to_blocks",
    "max_blocks",
    "node_text",
    "split_promotable_quote",
    "youtube_video_id",
]
