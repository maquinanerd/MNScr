"""
Build and qualify multi-source payloads for RSSPRIME Superfeed clusters.
"""

from __future__ import annotations

import logging
import os
import re
from html import unescape
from typing import Final
from urllib.parse import urlparse

from .content_limiter import truncate_html_by_visible_chars
from .feeds import canonicalize_url
from .html_utils import source_label_from_url
from .superfeed_policy import TRUSTED_SINGLE_SOURCES

logger = logging.getLogger(__name__)

MIN_CHARS_DEFAULT = 500
MAX_SECONDARY_DOCS = 2
MAX_CONTENT_CHARS = 8000

EXTRACTION_STATUSES = [
    "ARTICLE_BODY_OK",
    "TOO_SHORT",
    "PAYWALLED",
    "NAVIGATION_NOISE",
    "FETCH_ERROR",
    "ARTICLE_BODY_NOT_FOUND",
    "DUPLICATE",
]

PAYWALL_SIGNALS = [
    "assine",
    "assinante",
    "conte\u00fado exclusivo",
    "conteudo exclusivo",
    "para continuar lendo",
    "login",
    "entre ou cadastre-se",
    "paywall",
    "subscribe",
    "subscription",
    "j\u00e1 sou assinante",
    "ja sou assinante",
]

NAVIGATION_SIGNALS = [
    "PUBLICIDADE",
    "Mais lidas",
    "\u00daltimas:",
    "Ultimas:",
    "Assine",
    "Entrar",
    "Newsletter",
    "Cookies",
]


def _plain_text(content: str) -> str:
    text = re.sub(r"<[^>]+>", " ", content or "")
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _domain_from_url(url: str) -> str:
    try:
        host = urlparse(url or "").hostname or ""
        return host[4:].lower() if host.lower().startswith("www.") else host.lower()
    except Exception:
        return ""


def detect_extraction_status(content: str, url: str, min_chars: int = MIN_CHARS_DEFAULT) -> str:
    """Classifica o corpo extraido de uma fonte."""
    if content is None:
        return "FETCH_ERROR"

    text = _plain_text(content)
    if not text:
        return "ARTICLE_BODY_NOT_FOUND"

    lowered = text.lower()
    if any(signal.lower() in lowered for signal in PAYWALL_SIGNALS):
        return "PAYWALLED"

    navigation_hits = sum(1 for signal in NAVIGATION_SIGNALS if signal.lower() in lowered)
    if navigation_hits >= 3:
        return "NAVIGATION_NOISE"

    if len(text) < min_chars:
        return "TOO_SHORT"

    return "ARTICLE_BODY_OK"


def build_sources_audit_payload(sources: list[dict]) -> list[dict]:
    """Gera payload de auditoria enxuto (sem corpo completo) para o SQLite."""
    return [
        {
            "source_name": s.get("source_name", ""),
            "domain": s.get("domain", ""),
            "url": s.get("url", ""),
            "title": s.get("title", ""),
            "published_at": s.get("published_at", ""),
            "status": s.get("status", ""),
            "chars": s.get("chars", 0),
            "source_type": s.get("source_type", "media"),
        }
        for s in sources
    ]


def is_reliable_single_source(source_doc: dict) -> bool:
    """True se a unica fonte disponivel e confiavel o suficiente para publicar sozinha."""
    if source_doc.get("status") != "ARTICLE_BODY_OK":
        return False
    domain = (source_doc.get("domain") or _domain_from_url(source_doc.get("url", ""))).lower()
    return any(trusted in domain for trusted in TRUSTED_SINGLE_SOURCES)


def allow_single_source() -> bool:
    """Leitura de ``ALLOW_SINGLE_SOURCE`` no momento da chamada.

    No momento da chamada, e nao na importacao: e a mesma flag que a politica de
    entrada consulta, e ela precisa poder ser desligada sem reiniciar processo
    nem reimportar modulo.
    """
    return os.getenv("ALLOW_SINGLE_SOURCE", "false").strip().lower() == "true"


def _single_source_basis(sources_used: list[dict], *, declared_sources: int) -> str | None:
    """Em que base uma unica fonte extraida pode sustentar a materia — ou ``None``.

    Duas situacoes diferentes chegam aqui com a mesma aparencia, e trata-las
    igual seria o erro:

    ``declared_sources <= 1``
        o acontecimento so tem UMA fonte, e sempre teve. Nao houve perda. E o
        caso que ``ALLOW_SINGLE_SOURCE`` governa: com a flag ligada, publica;
        desligada, o item e recusado — e agora recusado CEDO, por
        ``evaluate_early_rejection``, antes de a IA escrever qualquer coisa.

    ``declared_sources >= 2``
        o cacho DEGRADOU: prometeu varias fontes e so uma sobreviveu a
        extracao. Aqui a exigencia de dominio confiavel continua valendo
        integralmente, porque o que falta e justamente a corroboracao que o
        item dizia ter. A flag nao afrouxa isto: ela declara que fonte unica
        publica, nao que fonte unica sirva de substituta silenciosa para as
        fontes que se perderam no caminho.
    """
    if len(sources_used) != 1:
        return None
    if is_reliable_single_source(sources_used[0]):
        return "single_source_trusted"
    if declared_sources <= 1 and allow_single_source():
        return "single_source_allowed"
    return None


def _source_identity(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower()).removesuffix("com")


def _source_name_for(cluster: dict, url: str, index: int) -> str:
    del index  # Source identity comes from the URL, never from array position.
    domain = _domain_from_url(url)
    domain_identity = _source_identity(domain.split(".")[0])
    candidates = [
        cluster.get("primary_source"),
        *(cluster.get("all_sources") or []),
    ]
    for source_name in candidates:
        if source_name and _source_identity(str(source_name)) == domain_identity:
            return str(source_name)
    return source_label_from_url(url)


def _extract_source(cluster: dict, extractor, url: str, index: int, min_chars: int) -> dict:
    source_name = _source_name_for(cluster, url, index)
    domain = _domain_from_url(url)
    doc = {
        "source": source_name,
        "source_name": source_name,
        "source_type": "media",
        "domain": domain,
        "url": url,
        "title": "",
        "content": "",
        "published_at": "",
        "featured_image_url": None,
        "images": [],
        "videos": [],
        "chars": 0,
        "status": "FETCH_ERROR",
    }
    try:
        html = extractor._fetch_html(url)
        if not html:
            return doc
        data = extractor.extract(html, url=url)
        if not data or not data.get("content"):
            doc["status"] = "ARTICLE_BODY_NOT_FOUND"
            return doc

        content = data.get("content", "")
        text = _plain_text(content)
        doc.update(
            {
                "title": data.get("title", ""),
                "content": truncate_html_by_visible_chars(content, MAX_CONTENT_CHARS),
                "featured_image_url": data.get("featured_image_url"),
                "images": data.get("images", []),
                "videos": data.get("videos", []),
                "chars": len(text),
                "status": detect_extraction_status(content, url, min_chars=min_chars),
            }
        )
        return doc
    except Exception as exc:
        logger.error("[MULTI_SOURCE] Erro ao extrair %s: %s", url, exc)
        return doc


#: Palavras que aparecem em manchete de qualquer assunto e nao identificam
#: NENHUM. Elas existem aqui porque duas materias sem nada em comum podem
#: compartilhar meia manchete: "Kit Harington Breaks Silence on Industry" e "Dan
#: Stevens Breaks Silence on RoboCop" dividem `Breaks` e `Silence`, e foi
#: exatamente esse par que fez o cacho falso de 28/08/2026 parecer legitimo.
_HEADLINE_NOISE: Final[frozenset] = frozenset(
    {
        "breaks", "silence", "officially", "exclusive", "reveals", "confirms",
        "first", "look", "trailer", "teaser", "season", "final", "series",
        "movie", "film", "show", "star", "stars", "actor", "director", "casting",
        "release", "date", "update", "report", "reportedly", "everything",
        "about", "what", "wants", "with", "from", "that", "this", "will",
        "after", "before", "into", "over", "more", "than", "just", "still",
        "original", "returns", "return", "joins", "sets", "gets", "adds",
        "netflix", "hbo", "max", "disney", "prime", "video", "apple", "hulu",
        "paramount", "peacock", "amazon", "marvel", "warner", "sony",
    }
)

#: Quantas palavras do inicio do corpo primario contam como LEAD. O lead de uma
#: noticia diz quem, o que e quando; o que ele repete da manchete e o assunto.
_LEAD_PALAVRAS: Final[int] = 30

#: Quanto do assunto da primaria a secundaria precisa cobrir.
#:
#: Contar tokens compartilhados NAO funciona, e a medicao contra texto real e
#: inequivoca: com o corpo inteiro da secundaria em maos (nao o slug), exigir
#: UM token distintivo aceitou 5 dos 6 cachos podres de 31/08/2026. Um artigo
#: de 12 mil caracteres quase sempre contem, em algum lugar, uma palavra da
#: manchete alheia — e duas materias que so citam a mesma franquia ("Halloween
#: Horror Nights **Stranger Things** House" x "Stephen King meets **Stranger
#: Things** in Flanagan's Carrie") compartilham o nome da franquia inteiro.
#:
#: Proporcao sobre as ANCORAS separa: nos 16 pares reais rotulados a mao,
#: contaminado nunca passou de 0,50 e legitimo nunca ficou abaixo de 0,80.
#: 0,65 e o meio dessa faixa. Qualquer valor entre 0,55 e 0,70 acerta os 16.
_MIN_COBERTURA_DO_ASSUNTO: Final[float] = float(
    os.getenv("MNSCR_CLUSTER_MIN_SUBJECT_COVERAGE", "0.65")
)


def _subject_tokens(texto: str) -> set:
    """Os tokens que IDENTIFICAM o assunto de uma manchete.

    Palavra de quatro letras ou mais, dobrada, fora da lista de ruido de
    manchete. Numero e pontuacao ficam de fora: "5" de "temporada 5" nao
    identifica nada.
    """
    from .cinerie.entity_resolve import fold

    return {
        token
        for token in re.findall(r"[^\W\d_]{4,}", fold(texto or ""), flags=re.UNICODE)
        if token not in _HEADLINE_NOISE
    }


def _subject_anchors(primaria: dict) -> set:
    """O assunto da primaria: o que a MANCHETE e o LEAD repetem.

    Manchete sozinha nao serve de assunto — ela carrega decoracao que nao
    identifica nada ("Fantastic", "Epic Effort", "Perfect") e, quando e isca de
    clique, pode nao nomear nada ("Prime Video's New 5-Book Mystery Crime
    Series Announces Perfect Casting"). Cruzar com o lead resolve as duas
    pontas: o que sobra sao os nomes que a materia repete porque e sobre eles.

    Nos pares reais medidos, as ancoras saem reconheciveis — ``{cullen,
    optimus, peter, voice}``, ``{badgley, everyone, family, killed}``.
    """
    titulo = _subject_tokens(primaria.get("title") or "")
    lead = _subject_tokens(" ".join((primaria.get("content") or "").split()[:_LEAD_PALAVRAS]))
    return titulo & lead


def subject_coverage(primaria: dict, secundaria: dict) -> float | None:
    """Quanto do assunto da primaria a secundaria cobre. ``None`` = sem medida."""
    ancoras = _subject_anchors(primaria)
    if not ancoras:
        return None
    texto_secundario = _subject_tokens(
        " ".join([secundaria.get("title") or "", secundaria.get("content") or ""])
    )
    return len(ancoras & texto_secundario) / len(ancoras)


def _talks_about_the_same_thing(primaria: dict, secundaria: dict) -> bool:
    """A secundaria fala do MESMO acontecimento que a primaria?

    O RSS Prime agrupa por similaridade e erra: em 28/08/2026 ele juntou
    "Kit Harington ... Industry" (Collider) com "Dan Stevens ... RoboCop"
    (ComicBook), e o MNScr mesclou 1.871 palavras de dois assuntos diferentes
    num texto so, publicando as duas na lista de fontes. O Editorial Gate ainda
    registrou `GATE_MULTI_SOURCE` como sinal de QUALIDADE — duas fontes soa
    melhor que uma.

    A checagem compara o ASSUNTO da primaria com o TEXTO INTEIRO da secundaria,
    e nao manchete com manchete: cobertura legitima do mesmo fato quase sempre
    repete o nome proprio no corpo, mesmo quando escolhe outro angulo para o
    titulo. O que mudou em 31/08/2026 foi o criterio — de "compartilha ao menos
    um token" para "cobre a maior parte das ancoras" —, porque contra o corpo
    real da secundaria o criterio antigo aceitava quase tudo. Ver
    `_MIN_COBERTURA_DO_ASSUNTO`.

    Sem ancoras a checagem se abstem e devolve `True`: recusar por falta de dado
    NOSSO descartaria fonte boa por defeito de extracao.
    """
    cobertura = subject_coverage(primaria, secundaria)
    if cobertura is None:
        return True
    return cobertura >= _MIN_COBERTURA_DO_ASSUNTO


def build_multi_source_payload(cluster: dict, extractor, min_chars: int = MIN_CHARS_DEFAULT) -> dict | None:
    """
    Extrai e qualifica fontes do cluster.
    Retorna payload com sources_used e sources_skipped, ou None se < 2 fontes OK.
    """
    urls = [cluster.get("url")] + list(cluster.get("additional_urls") or [])
    urls = [url for url in urls if url]
    if len(urls) > 1:
        urls = [urls[0], *urls[1 : MAX_SECONDARY_DOCS + 1]]

    seen_urls: set[str] = set()
    sources_used: list[dict] = []
    sources_skipped: list[dict] = []

    for index, url in enumerate(urls):
        canonical = canonicalize_url(url)
        if canonical in seen_urls:
            skipped = {
                "source_name": _source_name_for(cluster, url, index),
                "domain": _domain_from_url(url),
                "url": url,
                "title": "",
                "published_at": "",
                "status": "DUPLICATE",
                "chars": 0,
                "source_type": "media",
            }
            sources_skipped.append(skipped)
            continue
        seen_urls.add(canonical)

        doc = _extract_source(cluster, extractor, url, index, min_chars)
        if doc.get("status") != "ARTICLE_BODY_OK":
            sources_skipped.append(doc)
            continue

        # A PRIMEIRA fonte define o assunto; as demais precisam falar dele.
        if sources_used and not _talks_about_the_same_thing(sources_used[0], doc):
            doc = {**doc, "status": "OFF_TOPIC"}
            sources_skipped.append(doc)
            logger.warning(
                "[MULTI_SOURCE] fonte descartada por ASSUNTO: %s cobertura=%.2f "
                "minimo=%.2f (%r vs %r)",
                doc.get("domain"),
                subject_coverage(sources_used[0], doc) or 0.0,
                _MIN_COBERTURA_DO_ASSUNTO,
                (sources_used[0].get("title") or "")[:60],
                (doc.get("title") or "")[:60],
            )
            continue

        sources_used.append(doc)

    basis = "multi_source"
    if len(sources_used) < 2:
        basis = _single_source_basis(sources_used, declared_sources=len(urls))
        if basis is None:
            logger.info(
                "[MULTI_SOURCE] event_key=%s fontes_validas=%s fontes_descartadas=%s",
                cluster.get("event_key"),
                len(sources_used),
                len(sources_skipped),
            )
            return None
        logger.info(
            "[MULTI_SOURCE] event_key=%s %s=%s fontes_descartadas=%s",
            cluster.get("event_key"),
            basis,
            sources_used[0].get("domain"),
            len(sources_skipped),
        )

    return {
        "cluster_item": cluster,
        "cluster_docs": sources_used,
        "sources_used": build_sources_audit_payload(sources_used),
        "sources_skipped": build_sources_audit_payload(sources_skipped),
        "publication_basis": basis,
    }
