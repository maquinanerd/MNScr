"""Fallbacks deterministicos para os campos obrigatorios que dependem da IA.

O MNScr carrega o proprio JSON Schema do Cinerie. Isso muda o que conta como
defeito: emitir um pedido invalido nao e "o servidor recusou", e "o cliente
mandou algo que ele mesmo sabia que seria recusado". Uma materia com gate limpo
morreu exatamente assim — a IA devolveu ``focusKeyphrase = None`` e o pedido
saiu com o campo vazio.

Este modulo existe para que a resposta a "a IA nao devolveu o campo" seja um
valor **derivado do proprio material**, e nunca um valor inventado nem um pedido
quebrado. A distincao importa:

* **derivar** e reaproveitar texto que a materia ja tem — o titulo vira
  frase-chave, o lead vira meta description. Nada de novo e afirmado.
* **inventar** seria escrever uma frase que nenhuma fonte sustenta. Isso o
  modulo nao faz em lugar nenhum.

Todo fallback aplicado devolve a ORIGEM junto com o valor, e quem chama
transforma isso num aviso. Um campo remendado em silencio esconderia que a
geracao esta com defeito, e o defeito continuaria acontecendo.

Os minimos respeitados aqui sao os do **Cinerie**, nao os do arquivo
``.schema.json``: ``seo.title`` precisa de 15 caracteres e
``seo.metaDescription`` de 70, e os dois vivem em ``superRefine``, que
``z.toJSONSchema`` descarta (ver ``refinements.py``). Um fallback que so olhasse
``minLength: 1`` passaria aqui e morreria no servidor.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, List, Optional, Sequence

from app.stopwords import trim_function_words

from .slug import normalize_slug
from .text import collapse, find_forbidden_markup, plain_text

#: Separadores de manchete. O que vem antes costuma ser o assunto; o que vem
#: depois e o complemento ("Stranger Things: o que se sabe sobre o final").
_HEADLINE_SPLITTERS: Final[tuple[str, ...]] = (" — ", " – ", " - ", ": ", " | ")

#: Quantas palavras de CONTEUDO uma frase-chave derivada pode ter. Acima disso
#: ela deixa de ser frase-chave e vira o titulo de novo.
_MAX_KEYPHRASE_CONTENT_WORDS: Final[int] = 5


@dataclass(frozen=True)
class Derived:
    """Um valor e de onde ele veio.

    ``source`` vazio significa "nao foi possivel derivar nada"; ``"ia"``
    significa que o valor da IA foi aproveitado como veio. Qualquer outro valor
    e o campo de onde o fallback tirou o texto.
    """

    value: str
    source: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.value)

    @property
    def is_fallback(self) -> bool:
        return bool(self.value) and self.source != "ia"


_AI: Final[str] = "ia"


def _usable(value: object, *, max_length: int) -> str:
    """Texto limpo, ou vazio quando ele traz markup.

    Markup num campo editorial nao e "quase bom": o contrato recusa o pedido
    inteiro por causa dele. Tratar como ausente e o que permite o fallback
    entrar em vez de o pedido morrer.
    """
    text = plain_text(value, max_length=max_length)
    if not text or find_forbidden_markup(text) is not None:
        return ""
    return text


def _first_segment(text: str) -> str:
    """O trecho antes do primeiro separador de manchete, se ele se sustenta."""
    for splitter in _HEADLINE_SPLITTERS:
        head, sep, _ = text.partition(splitter)
        if sep and len(trim_function_words(head.split())) >= 2:
            return head.strip()
    return text


def keyphrase_from_text(text: object, *, max_length: int) -> str:
    """Frase-chave derivada de um texto corrido, deterministicamente.

    Corta no separador da manchete, descarta as palavras funcionais das pontas e
    para depois de algumas palavras de conteudo. Nao ha sorteio nem modelo: a
    mesma entrada produz sempre a mesma saida.
    """
    cleaned = collapse(text)
    if not cleaned or find_forbidden_markup(cleaned) is not None:
        return ""

    words = trim_function_words(_first_segment(cleaned).split())
    if not words:
        return ""

    picked: List[str] = []
    content_words = 0
    from app.stopwords import is_function_word

    for word in words:
        candidate = " ".join([*picked, word])
        if len(candidate) > max_length:
            break
        picked.append(word)
        if not is_function_word(word):
            content_words += 1
        if content_words >= _MAX_KEYPHRASE_CONTENT_WORDS:
            break

    picked = trim_function_words(picked)
    if not picked:
        # Nenhuma palavra inteira coube no limite: o primeiro token truncado
        # ainda e melhor do que campo vazio, e o limite do contrato e respeitado.
        return plain_text(words[0], max_length=max_length)
    return " ".join(picked)


def derive_focus_keyphrase(
    *,
    focus: object,
    title: object = "",
    slug: object = None,
    lead: object = "",
    minimum: int,
    maximum: int,
) -> Derived:
    """A frase-chave, da IA ou derivada do titulo/slug/lead.

    Ordem deliberada: o titulo e o que a redacao escolheu como assunto; a slug e
    o mesmo titulo ja normalizado (serve quando o titulo veio com markup); o
    lead e o ultimo recurso, porque a primeira frase costuma ter o assunto mas
    embrulhado em contexto.
    """
    given = _usable(focus, max_length=maximum)
    if len(given) >= minimum:
        return Derived(given, _AI)

    slug_text = str(slug or "").replace("-", " ")
    for source, raw in (
        ("content.title", title),
        ("seo.slugSuggestion", slug_text),
        ("blocks[0]", lead),
    ):
        candidate = keyphrase_from_text(raw, max_length=maximum)
        if len(candidate) >= minimum:
            return Derived(candidate, source)

    return Derived("")


def _extend_to_minimum(head: str, extras: Sequence[object], *, minimum: int, maximum: int) -> str:
    """Completa um texto curto com o material seguinte, sem passar do teto.

    Usado quando o titulo ou a meta description existem mas nao alcancam o
    minimo do Cinerie. Concatenar o proximo trecho da propria materia mantem o
    texto verdadeiro; escrever uma frase nova nao manteria.
    """
    text = head
    for extra in extras:
        if len(text) >= minimum:
            break
        addition = collapse(extra)
        if not addition or find_forbidden_markup(addition) is not None:
            continue
        if addition.casefold() == text.casefold():
            continue
        joined = f"{text} {addition}".strip() if text else addition
        if text and not text.endswith((".", "!", "?", ":", "—", "–")):
            joined = f"{text}: {addition}"
        text = plain_text(joined, max_length=maximum)
    return text


def derive_seo_title(
    *,
    title: object,
    subtitle: object = "",
    summary: object = "",
    lead: object = "",
    minimum: int,
    maximum: int,
) -> Derived:
    """``seo.title`` dentro da faixa de transporte do Cinerie (>= 15).

    Um titulo curto e legitimo ("Duna 3"), mas o contrato nao o transporta
    sozinho. Compor com o subtitulo e o que a propria redacao ja faz na pagina;
    o valor continua sendo texto que a materia tem.
    """
    given = _usable(title, max_length=maximum)
    if len(given) >= minimum:
        return Derived(given, _AI)

    extended = _extend_to_minimum(
        given, (subtitle, summary, lead), minimum=minimum, maximum=maximum
    )
    if len(extended) >= minimum:
        return Derived(extended, "content.subtitle" if given else "blocks[0]")

    fallback = _usable(lead, max_length=maximum)
    if len(fallback) >= minimum:
        return Derived(fallback, "blocks[0]")

    return Derived(extended or given)


def derive_meta_description(
    *,
    meta: object,
    summary: object = "",
    lead: object = "",
    body: Sequence[object] = (),
    minimum: int,
    maximum: int,
) -> Derived:
    """``seo.metaDescription`` dentro da faixa de transporte (>= 70).

    O lead e a fonte natural: e o resumo que a propria materia ja escreveu. Se
    ele nao alcanca o minimo, os paragrafos seguintes completam — o texto
    continua sendo o da materia, so mais dele.
    """
    given = _usable(meta, max_length=maximum)
    if len(given) >= minimum:
        return Derived(given, _AI)

    for source, head in (("content.summary", summary), ("blocks[0]", lead)):
        candidate = _usable(head, max_length=maximum)
        if len(candidate) >= minimum:
            return Derived(candidate, source)
        if candidate:
            extended = _extend_to_minimum(
                candidate, body, minimum=minimum, maximum=maximum
            )
            if len(extended) >= minimum:
                return Derived(extended, source)

    extended = _extend_to_minimum(given, body, minimum=minimum, maximum=maximum)
    if len(extended) >= minimum:
        return Derived(extended, "blocks")
    return Derived(extended or given)


def derive_summary(
    *,
    summary: object,
    subtitle: object = "",
    meta: object = "",
    lead: object = "",
    maximum: int,
) -> Derived:
    """``summary`` do pedido — obrigatorio, minimo de um caractere."""
    for source, value in (
        (_AI, summary),
        ("content.subtitle", subtitle),
        ("seo.metaDescription", meta),
        ("blocks[0]", lead),
    ):
        candidate = _usable(value, max_length=maximum)
        if candidate:
            return Derived(candidate, source)
    return Derived("")


def derive_slug(
    *,
    slug: object,
    title: object = "",
    focus: object = "",
    cluster_id: object = "",
) -> Derived:
    """A slug sugerida, que o contrato exige como string nao vazia.

    ``normalize_slug`` devolve ``None`` quando nao sobra nada utilizavel — um
    resultado honesto que, aqui, ainda precisa virar alguma coisa: o campo e
    obrigatorio. O ultimo recurso e o identificador do agrupamento, que ja e
    estavel e ja esta no pedido. Slug continua sendo SUGESTAO: quem decide a
    canonica, a colisao e o redirect e o Cinerie.
    """
    for source, value in (
        (_AI, slug),
        ("content.title", title),
        ("seo.focusKeyphrase", focus),
        ("sourceClusterId", cluster_id),
    ):
        candidate = normalize_slug(value)
        if candidate:
            return Derived(candidate, source)
    return Derived("")


def body_texts(blocks: Sequence[object]) -> List[str]:
    """Os textos dos paragrafos, na ordem — material para completar campos curtos."""
    texts: List[str] = []
    for block in blocks or []:
        if not isinstance(block, dict):
            continue
        if block.get("type") != "paragraph":
            continue
        text = collapse(block.get("text"))
        if text:
            texts.append(text)
    return texts


def describe(field: str, derived: Derived) -> Optional[str]:
    """Aviso legivel para um campo remendado, ou ``None`` quando nao houve remendo."""
    if not derived.is_fallback:
        return None
    return f"{field} ausente ou fora da faixa; derivado de {derived.source}"


__all__ = [
    "Derived",
    "body_texts",
    "derive_focus_keyphrase",
    "derive_meta_description",
    "derive_seo_title",
    "derive_slug",
    "derive_summary",
    "describe",
    "keyphrase_from_text",
]
