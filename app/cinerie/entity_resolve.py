"""Nome -> id interno do catalogo -> bloco ``entityCard``. Sem transporte.

O `entityId` do contrato e um id **interno** do catalogo do Cinerie, e o CMS
**nao tem como conferi-lo**: o catalogo vive no `screen-db`, noutro banco, e o
CMS aceita qualquer inteiro respondendo `201`. Quem confere e a renderizacao — e
quando ela nao acha, ela **descarta o bloco em silencio**.

Isso desenha os dois modos de falha, e eles nao tem o mesmo preco:

| entityId | o que acontece |
| --- | --- |
| inexistente | o bloco some da materia, e ninguem e avisado |
| existente e **errado** | a pagina mostra a OBRA ERRADA com cara de certo |

O segundo e o caro, e e ele que organiza este modulo inteiro: **um ``null`` e
inofensivo; um id errado e uma mentira publicada.** Nada aqui devolve palpite.
Resultado sem `entityId` nao vira bloco — ponto, sem plano B e sem aproximacao.

Tres decisoes que nao sao obvias:

**A rota nao faz fuzzy, e este modulo tambem nao.** Os tres casamentos que ela
oferece — por identificador externo, por titulo+ano e por nome — sao todos
EXATOS. A busca do site tem aproximacao e esta certa, porque la existe uma
pessoa lendo a lista e escolhendo. Aqui nao existe ninguem: o que sair daqui
vira bloco publicado. Quais dos tres podem virar bloco e decisao de fora
(`accepted_match_kinds`), nunca um default escondido aqui.

**Filme e serie so viajam com ano.** Sem `year` a rota responde
`title_requires_year`, e nao ha ano para inventar: colar o primeiro ano do texto
num titulo qualquer produziria `exact_title_year` sobre uma coincidencia — o
casamento MAIS confiante da rota, ancorado num chute. So entra ano que o proprio
texto declara COLADO ao titulo (`Titulo (2026)`).

**Nome de pessoa nao precisa de ano, e por isso e o que mais resolve.** O que
mantem `exact_name` honesto e a UNICIDADE, que a rota confere: dois homonimos
derrubam para `null`, e nao para "o mais popular".
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Final, List, Mapping, Optional, Sequence, Tuple

from .identity import is_stable_id
from .text import collapse, plain_text

logger = logging.getLogger(__name__)

BLOCK_ENTITY_CARD: Final[str] = "entityCard"

ENTITY_RESOLVE_PATH: Final[str] = "/api/internal/entity-resolve"

#: Os tipos que a rota resolve, e so eles. `season` e `episode` NAO sao
#: resolviveis la: nao tem titulo proprio estavel ("Temporada 2" existe as
#: centenas) e so se identificam pela serie mais o numero. Pedir um deles
#: devolve `unsupported_kind`, e nao um palpite.
RESOLVABLE_KINDS: Final[Tuple[str, ...]] = ("movie", "tv", "person")

#: Teto de itens por chamada, do contrato. Acima disso o pedido INTEIRO e
#: recusado (`422 too_many_items`) — nunca truncado, porque truncar devolveria
#: menos resultados do que itens enviados e o cliente alinharia os resultados
#: errados aos itens errados. Aqui o corte acontece ANTES de enviar.
MAX_RESOLVE_ITEMS: Final[int] = 50

#: Prefixo dos ids dos blocos de ficha. Estavel entre revisoes da mesma materia
#: porque nao e posicional: `ec1` continua sendo `ec1` quando um paragrafo entra
#: antes dele. Um id posicional faria o CMS ler "outro bloco" a cada reescrita.
_CARD_ID_PREFIX: Final[str] = "ec"

#: Ano de estreia plausivel, na janela que a propria rota aceita (1870-2200).
_YEAR: Final[str] = r"(?:1[89]\d{2}|2[01]\d{2})"

#: `Titulo (2026)` — a UNICA forma de ano aceita, e a razao esta no docstring do
#: modulo: o parenteses colado e o texto DECLARANDO de que ano e aquela obra.
#: Um ano solto tres frases adiante nao declara nada.
_TITLE_WITH_YEAR: Final[re.Pattern[str]] = re.compile(
    rf"(?P<titulo>[^()\n]{{2,180}}?)\s*\(\s*(?P<ano>{_YEAR})\s*\)"
)

#: Palavras minusculas que pertencem a um titulo quando aparecem NO MEIO dele
#: (`Clube da Luta`, `O Senhor dos Aneis`). Fora dessa posicao elas sao texto da
#: materia, e e isso que corta `A estreia de Ruptura` em `Ruptura`.
_TITLE_CONNECTORS: Final[str] = (
    r"d[aeo]s?|de la|e|em|no|na|nos|nas|ao|aos|para|com|sem|por|the|of|and|in|on|to|a|o|as|os"
)

#: O titulo e o SUFIXO do trecho que vem antes do parenteses: a ultima corrida de
#: palavras que comeca em maiuscula (ou digito) e vai ate o `(`.
#:
#: LIMITE HONESTO: isto acerta `Ruptura` em "A estreia de Ruptura (2022)" e erra
#: para menos em titulo que comece por palavra minuscula. O erro e barato de
#: proposito — um titulo cortado ou esticado nao casa, a rota responde
#: `not_found` e a materia sai sem ficha. Nao existe caminho em que este recorte
#: produza a ficha de OUTRA obra: quem casa e a rota, por igualdade exata.
_TITLE_TAIL: Final[re.Pattern[str]] = re.compile(
    rf"(?:[A-ZÀ-Ý0-9][\wÀ-ÿ'’:!?.,-]*)"
    rf"(?:\s+(?:(?:{_TITLE_CONNECTORS})\s+)*[A-ZÀ-Ý0-9][\wÀ-ÿ'’:!?.,-]*)*$"
)

#: Nome proprio de gente: 2 a 4 palavras capitalizadas, com as particulas do
#: portugues e do ingles no meio. Uma palavra so nao entra — "Superman" sozinho
#: e tao provavelmente a obra quanto a pessoa, e a rota so casa `exact_name` em
#: `person`.
_PERSON_NAME: Final[re.Pattern[str]] = re.compile(
    r"\b[A-ZÀ-Ý][a-zà-ÿ'’-]{1,}"
    r"(?:\s+(?:d[aeo]s?|de la|van|von|der|of|the)\s+[A-ZÀ-Ý][\wÀ-ÿ'’-]*"
    r"|\s+[A-ZÀ-Ý][\wÀ-ÿ'’.-]{1,})"
    r"(?:\s+[A-ZÀ-Ý][\wÀ-ÿ'’.-]{1,}){0,2}\b"
)

#: Ruido que casa com a forma de nome proprio e nao e pessoa. Lista fechada e
#: curta de proposito: ela nao existe para acertar a classificacao, e sim para
#: nao gastar item de lote com o que seguramente nao resolve. Um falso negativo
#: aqui custa uma ficha a menos; nao custa ficha errada.
_NOT_A_PERSON: Final[frozenset] = frozenset(
    {
        "prime video", "hbo max", "disney plus", "apple tv", "amazon prime",
        "warner bros", "marvel studios", "dc studios", "sony pictures",
        "universal pictures", "paramount pictures", "walt disney", "20th century",
        "estados unidos", "reino unido", "nova york", "los angeles", "san diego",
        "comic con", "rotten tomatoes", "box office", "the hollywood", "screen rant",
    }
)


def fold(value: str) -> str:
    """A "dobra" do casamento exato: NFD, sem acento, minusculo, espacos colapsados.

    A MESMA transformacao que a rota aplica dos dois lados — e a igualdade entre
    as duas dobras e travada por teste de governanca no repositorio do Cinerie
    (`entity-resolve-fold.test.ts`). Divergir daqui nao quebra nada
    ruidosamente: so faz o casamento exato deixar de casar, e a rota responder
    `not_found` para titulos que existem.
    """
    texto = unicodedata.normalize("NFD", str(value or ""))
    sem_acento = "".join(ch for ch in texto if unicodedata.category(ch) != "Mn")
    return collapse(sem_acento).lower()


# ===========================================================================
# Candidatos
# ===========================================================================


@dataclass(frozen=True)
class EntityCandidate:
    """Uma entidade que a materia PODE estar tratando — ainda sem id nenhum.

    ``prominence`` e a unica ordenacao que existe antes da resposta da rota:
    quem aparece no titulo e no lead e sobre o que a materia trata, e quem
    aparece so no decimo paragrafo e mencao. Ela desempata o corte de
    ``MAX_RESOLVE_ITEMS`` — nao a escolha das fichas, que e por confianca.
    """

    name: str
    kind: str
    year: Optional[int] = None
    #: 0 = titulo, 1 = lead, 2 = corpo. Menor e mais proeminente.
    prominence: int = 2

    def as_resolve_item(self) -> Dict[str, Any]:
        """O item do lote, no formato do contrato.

        ``title`` e ``name`` sao o MESMO campo com dois nomes, e os dois sao
        aceitos; cada um vai com o nome que o tipo torna legivel.
        """
        item: Dict[str, Any] = {"kind": self.kind}
        if self.kind == "person":
            item["name"] = self.name
        else:
            item["title"] = self.name
            if self.year is not None:
                item["year"] = self.year
        return item


def _paragraph_texts(blocks: Sequence[Mapping[str, Any]]) -> List[str]:
    return [
        str(block.get("text") or "")
        for block in blocks or []
        if isinstance(block, Mapping) and block.get("type") == "paragraph"
    ]


def _person_candidates(texto: str, *, prominence: int) -> List[EntityCandidate]:
    achados: List[EntityCandidate] = []
    for match in _PERSON_NAME.finditer(texto or ""):
        nome = collapse(match.group(0)).strip(" .,;:!?")
        if len(nome) < 5 or fold(nome) in _NOT_A_PERSON:
            continue
        achados.append(EntityCandidate(name=nome, kind="person", prominence=prominence))
    return achados


def _work_candidates(texto: str, *, prominence: int, kinds: Sequence[str]) -> List[EntityCandidate]:
    """Obras com ano DECLARADO ao lado do titulo.

    Sai um candidato por `kind` pedido (filme e serie): o texto nao diz qual dos
    dois e, e a rota resolve por `kind` — mandar os dois e deixar que ela
    responda `null` para o que nao existe custa um item de lote e nao custa
    nenhuma suposicao. O contrario — escolher o `kind` aqui, por heuristica —
    seria decidir "isto e serie" sem nada no texto dizendo isso.
    """
    achados: List[EntityCandidate] = []
    for match in _TITLE_WITH_YEAR.finditer(texto or ""):
        bruto = collapse(match.group("titulo"))
        # Corta a clausula que vier antes do titulo. O casamento pega ate 180
        # caracteres para tras porque nao ha como saber onde o titulo comeca; o
        # sufixo capitalizado e a melhor pista que o texto oferece.
        cauda = _TITLE_TAIL.search(bruto)
        titulo = collapse(cauda.group(0) if cauda is not None else bruto).strip(" .,;:!?—-")
        if len(titulo) < 2:
            continue
        ano = int(match.group("ano"))
        for kind in kinds:
            achados.append(
                EntityCandidate(name=titulo, kind=kind, year=ano, prominence=prominence)
            )
    return achados


def collect_entity_candidates(
    *,
    title: str,
    lead: str,
    blocks: Sequence[Mapping[str, Any]] = (),
    limit: int = MAX_RESOLVE_ITEMS,
) -> List[EntityCandidate]:
    """As entidades candidatas do rascunho, do titulo para o corpo.

    A prioridade e do enunciado da tarefa e e a certa: titulo e lead sao onde a
    materia declara do que trata. O corpo entra depois, e so ate o teto do lote.

    Deduplica por (dobra do nome, tipo, ano) — o mesmo nome citado dez vezes e
    UMA entidade, e mandar dez itens iguais gastaria o lote com uma pergunta so.
    """
    candidatos: List[EntityCandidate] = []
    for prominence, texto in ((0, title), (1, lead)):
        candidatos.extend(_person_candidates(texto, prominence=prominence))
        candidatos.extend(_work_candidates(texto, prominence=prominence, kinds=("movie", "tv")))
    for paragrafo in _paragraph_texts(blocks):
        candidatos.extend(_person_candidates(paragrafo, prominence=2))
        candidatos.extend(_work_candidates(paragrafo, prominence=2, kinds=("movie", "tv")))

    vistos: Dict[Tuple[str, str, Optional[int]], EntityCandidate] = {}
    for candidato in candidatos:
        chave = (fold(candidato.name), candidato.kind, candidato.year)
        anterior = vistos.get(chave)
        if anterior is None or candidato.prominence < anterior.prominence:
            vistos[chave] = candidato

    ordenados = sorted(vistos.values(), key=lambda c: (c.prominence, c.name))
    if len(ordenados) > max(0, limit):
        # O corte e local e ANTES do envio, exatamente para nao ouvir
        # `422 too_many_items` — que recusaria o lote inteiro, e nao o excesso.
        logger.info(
            "[CINERIE_ENTITY] %s candidatos acima do teto de %s; enviados os mais proeminentes",
            len(ordenados), limit,
        )
        ordenados = ordenados[: max(0, limit)]
    return ordenados


# ===========================================================================
# Resposta
# ===========================================================================


@dataclass(frozen=True)
class ResolvedEntity:
    """Um resultado da rota, ja alinhado ao candidato que o originou."""

    candidate: EntityCandidate
    entity_kind: str
    entity_id: str
    matched_by: str
    confidence: float
    canonical_title: Optional[str] = None
    path: Optional[str] = None

    @property
    def public_link(self) -> Optional[str]:
        """O caminho que deve virar link na pagina publicada, quando a rota o deu."""
        return self.path

    def safe_log_fields(self) -> Dict[str, Any]:
        return {
            "origem": self.candidate.name,
            "entity_kind": self.entity_kind,
            "entity_id": self.entity_id,
            "matched_by": self.matched_by,
            "confidence": self.confidence,
            "canonical_title": self.canonical_title,
            "path": self.path,
        }


@dataclass
class EntityResolution:
    """O que sobrou de uma chamada a rota: o que resolveu e o que ficou de fora."""

    resolved: List[ResolvedEntity] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    #: `reason` -> quantos. So para o log: o motivo de nao ter resolvido nao vai
    #: para o pedido, porque uma entidade nao citada nao e um defeito da materia.
    reasons: Dict[str, int] = field(default_factory=dict)


def parse_resolution(
    results: Any,
    candidates: Sequence[EntityCandidate],
    *,
    accepted_match_kinds: Sequence[str],
) -> EntityResolution:
    """Alinha a resposta aos candidatos e aplica o limiar de confianca.

    A rota promete **um resultado por item, na mesma ordem**, inclusive para os
    que nao resolveram — e o `index` de cada resultado confirma o alinhamento.
    Ele e conferido aqui em vez de presumido: alinhar errado publicaria a ficha
    de uma entidade sob o nome de outra, que e a falha exata que este modulo
    existe para impedir.

    ``accepted_match_kinds`` chega de fora (`app.config`) e nao tem default
    aqui: o limiar e decisao editorial, e um default escondido no modulo faria a
    variavel de ambiente parecer configurar algo que ela nao configura.
    """
    resolution = EntityResolution()
    linhas = results if isinstance(results, list) else []
    aceitos = {str(item) for item in accepted_match_kinds}

    for posicao, linha in enumerate(linhas):
        if not isinstance(linha, Mapping):
            continue
        indice = linha.get("index")
        indice = int(indice) if isinstance(indice, int) else posicao
        if not 0 <= indice < len(candidates):
            resolution.warnings.append("ENTITY_RESOLVE_INDICE_FORA_DA_FAIXA")
            continue
        candidato = candidates[indice]

        entity_id = collapse(linha.get("entityId"))
        matched_by = collapse(linha.get("matchedBy"))
        reason = collapse(linha.get("reason"))

        if not entity_id:
            # `entityId is None` <=> `matchedBy is None` e `reason` preenchido.
            # Nao existe resultado meio resolvido, e um `null` NAO vira bloco.
            chave = reason or "sem_motivo"
            resolution.reasons[chave] = resolution.reasons.get(chave, 0) + 1
            continue

        if not is_stable_id(entity_id):
            # O contrato do CMS cobra `^[1-9][0-9]*$` e o do bloco cobra
            # `stableId`. Um id fora de forma seria recusado la; recusar aqui
            # troca um pedido inteiro reprovado por uma ficha a menos.
            resolution.warnings.append(f"ENTITY_ID_FORA_DE_FORMA:{candidato.name[:40]}")
            continue

        if matched_by not in aceitos:
            resolution.warnings.append(f"ENTITY_MATCH_ABAIXO_DO_LIMIAR:{matched_by or '-'}")
            continue

        confidence = linha.get("confidence")
        confidence = float(confidence) if isinstance(confidence, (int, float)) else 0.0
        entity_kind = collapse(linha.get("entityKind")) or candidato.kind

        resolution.resolved.append(
            ResolvedEntity(
                candidate=candidato,
                entity_kind=entity_kind,
                entity_id=entity_id,
                matched_by=matched_by,
                confidence=confidence,
                canonical_title=collapse(linha.get("canonicalTitle")) or None,
                path=collapse(linha.get("path")) or None,
            )
        )

    return resolution


# ===========================================================================
# Blocos
# ===========================================================================


def _first_mention_index(blocks: Sequence[Mapping[str, Any]], name: str) -> Optional[int]:
    """Indice do PRIMEIRO paragrafo que menciona o nome, ou ``None``.

    A comparacao usa a mesma dobra do casamento (sem acento, minusculo), com
    fronteira de palavra dos dois lados: sem ela, "Ana" casaria dentro de
    "Anakin" e a ficha entraria depois do paragrafo errado.
    """
    alvo = fold(name)
    if not alvo:
        return None
    padrao = re.compile(rf"(?<![\w]){re.escape(alvo)}(?![\w])")
    for indice, block in enumerate(blocks or []):
        if not isinstance(block, Mapping) or block.get("type") != "paragraph":
            continue
        if padrao.search(fold(str(block.get("text") or ""))):
            return indice
    return None


def select_entity_cards(
    resolution: EntityResolution,
    *,
    maximum: int,
) -> List[ResolvedEntity]:
    """As fichas que entram, no maximo ``maximum`` — as de MAIOR confianca.

    Desempate por proeminencia (titulo antes do corpo) e depois pelo nome, para
    que a mesma materia reprocessada escolha as mesmas fichas. Um desempate
    instavel faria a revisao seguinte trocar os blocos sem que nada tivesse
    mudado no texto.

    Uma entidade so entra uma vez, mesmo que dois candidatos tenham chegado ao
    mesmo `entityId` — o que acontece quando o titulo casa como filme e como
    serie, ou quando o nome aparece em duas formas.
    """
    ordenados = sorted(
        resolution.resolved,
        key=lambda item: (-item.confidence, item.candidate.prominence, item.candidate.name),
    )
    teto = max(0, maximum)
    escolhidos: List[ResolvedEntity] = []
    vistos: set = set()
    for item in ordenados:
        # O teto e conferido ANTES de acrescentar. Conferir depois faria
        # ``maximum=0`` emitir uma ficha — "nenhuma" nao pode ser uma.
        if len(escolhidos) >= teto:
            break
        chave = (item.entity_kind, item.entity_id)
        if chave in vistos:
            continue
        vistos.add(chave)
        escolhidos.append(item)
    return escolhidos


def attach_entity_cards(
    blocks: List[Dict[str, Any]],
    cards: Sequence[ResolvedEntity],
    *,
    max_total_blocks: int,
) -> List[str]:
    """Insere as fichas no corpo. Devolve os avisos.

    **POSICAO, e a regra e uma so:** logo DEPOIS do paragrafo em que a entidade e
    mencionada pela primeira vez. E onde o leitor acabou de encontrar o nome, e e
    uma posicao DEDUZIVEL do texto — nao um numero escolhido a mao que a proxima
    materia desmente. Quando o nome so aparece no titulo (e nao no corpo), a
    ficha vai depois do PRIMEIRO paragrafo, que e o lugar mais proximo do lead.

    As insercoes sao aplicadas de tras para a frente, sobre indices calculados na
    lista ORIGINAL: inserir da frente para tras deslocaria os indices ainda nao
    usados e a segunda ficha cairia um bloco adiante do paragrafo dela.
    """
    avisos: List[str] = []
    if not cards:
        return avisos

    cabem = max(0, max_total_blocks - len(blocks))
    if cabem < len(cards):
        # Truncar em silencio publicaria menos fichas do que o log diz ter
        # emitido; e passar do teto reprovaria o pedido INTEIRO por causa de um
        # enriquecimento.
        avisos.append(f"ENTITY_CARDS_NAO_COUBERAM:{len(cards) - cabem}")
        cards = list(cards)[:cabem]
        if not cards:
            return avisos

    posicoes: List[Tuple[int, int, Dict[str, Any]]] = []
    for ordem, card in enumerate(cards, start=1):
        alvo = _first_mention_index(blocks, card.candidate.name)
        if alvo is None:
            alvo = next(
                (i for i, b in enumerate(blocks) if b.get("type") == "paragraph"), None
            )
        if alvo is None:
            avisos.append(f"ENTITY_CARD_SEM_PARAGRAFO:{card.candidate.name[:40]}")
            continue
        bloco: Dict[str, Any] = {
            "id": f"{_CARD_ID_PREFIX}{ordem}",
            "type": BLOCK_ENTITY_CARD,
            "entityKind": card.entity_kind,
            "entityId": card.entity_id,
        }
        # `note` e opcional e so entra quando ha algo a dizer que o card ja nao
        # mostra: o titulo canonico do catalogo, quando ele difere do nome que o
        # texto usou. Repetir o mesmo nome ali seria ruido.
        if card.canonical_title and fold(card.canonical_title) != fold(card.candidate.name):
            nota = plain_text(card.canonical_title, max_length=500)
            if nota:
                bloco["note"] = nota
        posicoes.append((alvo, ordem, bloco))

    # De tras para a frente, sobre indices calculados na lista ORIGINAL. Inserir
    # da frente para tras deslocaria os indices ainda nao usados, e a segunda
    # ficha cairia um bloco adiante do paragrafo dela.
    #
    # A `ordem` entra na chave para o caso de duas fichas caírem no MESMO
    # paragrafo: sem ela as duas seriam inseridas na mesma posicao e sairiam
    # invertidas, com a ficha de MAIOR confianca abaixo da de menor.
    for alvo, _ordem, bloco in sorted(posicoes, key=lambda item: (item[0], item[1]), reverse=True):
        blocks.insert(alvo + 1, bloco)

    return avisos


# ===========================================================================
# `entityLinks[]` (topo)
# ===========================================================================

#: Onde a entidade aparece -> qual e o papel dela na materia. E a MESMA
#: ``prominence`` que ja ordena os candidatos, lida agora como papel: o titulo
#: declara o assunto, o lead declara o assunto secundario, o resto e mencao.
#:
#: Nao ha heuristica NOVA aqui, e isso e o ponto. A posicao ja foi apurada na
#: coleta, contra o texto; o que esta tabela faz e batiza-la. Se a mesma
#: entidade aparecer em dois lugares, ``collect_entity_candidates`` ja guardou a
#: posicao mais forte — logo, o papel mais forte sai por construcao.
_RELATION_BY_PROMINENCE: Final[Tuple[str, ...]] = (
    "primary_subject",    # 0 — titulo
    "secondary_subject",  # 1 — lead
    "mentioned",          # 2 — corpo
)

#: Teto de `entityLinks[]` no contrato (`maxItems: 100`). Passar dele reprovaria
#: o pedido INTEIRO por causa de enriquecimento, entao o corte acontece aqui.
MAX_ENTITY_LINKS: Final[int] = 100

#: A partir de quanto o Cinerie faz o vinculo nascer `verified: true` (ADR 0019,
#: que emenda o 0018). Nao e regra deste lado e nao muda nada do que sai daqui:
#: existe para o LOG dizer o que esperar de cada linha enviada. Arredondar
#: confianca para alcancar este numero faria o Cinerie auto-verificar o que a
#: regra dele recusa de proposito — ver ``to_entity_links``.
CINERIE_VERIFIED_CUTOFF: Final[float] = 0.9


def relation_for(prominence: int) -> str:
    """O papel da entidade, derivado da posicao em que o texto a cita."""
    indice = max(0, int(prominence))
    return _RELATION_BY_PROMINENCE[min(indice, len(_RELATION_BY_PROMINENCE) - 1)]


def to_entity_links(resolution: EntityResolution) -> List[Dict[str, Any]]:
    """`entityLinks[]` do pedido — SO o que a rota resolveu, com a confianca DELA.

    Tres decisoes, e nenhuma e cosmetica:

    **Entram todas as resolvidas, nao so as que viraram ficha.** Sao listas com
    precos diferentes: `entityCard` ocupa um bloco no corpo e por isso tem teto
    editorial (``MNSCR_CINERIE_ENTITY_CARD_MAX``); um link de topo nao ocupa
    nada e alimenta a secao "Destaques de hoje". Cortar aqui pelo teto de fichas
    esconderia da Home entidade que a materia trata de fato.

    **A confianca vai EXATA, sem arredondar.** Do outro lado o corte e
    ``CINERIE_VERIFIED_CUTOFF``: ``exact_title_year`` (0.90) nasce verificado e
    ``exact_name`` (0.85) espera um humano. Essa diferenca e deliberada —
    ``exact_name`` e o unico casamento sem um segundo campo confirmando
    identidade, e unicidade no nosso catalogo hoje nao e unicidade no mundo
    amanha. Empurrar 0.85 para 0.9 faria o Cinerie auto-verificar exatamente o
    vinculo que a regra dele segura.

    **Uma entidade, um vinculo.** A dobra e por ``(entityKind, entityId)``, que
    e o par que o Cinerie projeta em `entity_news_links` — o mesmo nome citado
    em duas formas, ou o mesmo id alcancado por dois candidatos, e UMA linha.
    """
    ordenados = sorted(
        resolution.resolved,
        key=lambda item: (item.candidate.prominence, -item.confidence, item.candidate.name),
    )
    links: List[Dict[str, Any]] = []
    vistos: set = set()
    for item in ordenados:
        chave = (item.entity_kind, item.entity_id)
        if chave in vistos:
            continue
        vistos.add(chave)
        links.append(
            {
                "entityKind": item.entity_kind,
                "entityId": item.entity_id,
                "relation": relation_for(item.candidate.prominence),
                "confidence": float(item.confidence),
            }
        )
        if len(links) >= MAX_ENTITY_LINKS:
            break
    return links


def log_emitted_links(links: Sequence[Mapping[str, Any]]) -> None:
    """Uma linha por vinculo enviado. Auditoria do que a Home vai receber.

    ``nasce_verificado`` e uma EXPECTATIVA sobre o outro lado, nao um campo do
    pedido: quem decide e o Cinerie. Vai no log porque e a diferenca entre "o
    vinculo chegou" e "o vinculo aparece na pagina hoje", e sem ela uma secao
    vazia parece falha de envio quando e so a fila da curadoria.
    """
    for link in links:
        confidence = float(link.get("confidence") or 0.0)
        logger.info(
            "[CINERIE_ENTITY_LINK] %s",
            {
                "entity_kind": link.get("entityKind"),
                "entity_id": link.get("entityId"),
                "relation": link.get("relation"),
                "confidence": confidence,
                "nasce_verificado": confidence >= CINERIE_VERIFIED_CUTOFF,
            },
        )


def log_emitted_cards(cards: Sequence[ResolvedEntity]) -> None:
    """Uma linha por ficha publicada. Auditoria, nao depuracao.

    ``exact_name`` sobe para WARNING de proposito. Ele e o unico dos tres
    casamentos que nao tem um segundo campo confirmando a identidade — nem
    identificador, nem ano —, e o que o segura e a unicidade do nome no
    catalogo. Isso basta para publicar e nao basta para publicar sem deixar
    rastro: a linha carrega o nome de ORIGEM e o id resolvido, que e o par que
    uma auditoria posterior precisa para conferir se a pagina mostrou a obra
    certa.
    """
    for card in cards:
        campos = card.safe_log_fields()
        if card.matched_by == "exact_name":
            logger.warning("[CINERIE_ENTITY_CARD] auditoria=exact_name %s", campos)
        else:
            logger.info("[CINERIE_ENTITY_CARD] %s", campos)


#: Assinatura do resolvedor injetado: recebe os itens ja no formato do contrato
#: e devolve `results`. Um `Callable` em vez do cliente inteiro mantem este
#: modulo sem transporte — e permite exercitar o caminho todo sem socket.
EntityResolver = Callable[[Sequence[Mapping[str, Any]]], Any]


__all__ = [
    "BLOCK_ENTITY_CARD",
    "CINERIE_VERIFIED_CUTOFF",
    "ENTITY_RESOLVE_PATH",
    "EntityCandidate",
    "EntityResolution",
    "EntityResolver",
    "MAX_ENTITY_LINKS",
    "MAX_RESOLVE_ITEMS",
    "RESOLVABLE_KINDS",
    "ResolvedEntity",
    "attach_entity_cards",
    "collect_entity_candidates",
    "fold",
    "log_emitted_cards",
    "log_emitted_links",
    "parse_resolution",
    "relation_for",
    "select_entity_cards",
    "to_entity_links",
]
