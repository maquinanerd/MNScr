"""Selecao da imagem de destaque e orquestracao best-effort da ingestao.

Duas responsabilidades, deliberadamente separadas do transporte HTTP (que vive
SO em ``CinerieClient.ingest_editorial_media``, em ``client.py``):

1. **Escolher** o melhor candidato entre as imagens que o extrator ja achou
   nas fontes do cluster — decisao do dono do produto: sempre postar imagem,
   nunca falhar por causa dela, e priorizar a de melhor qualidade quando a
   dimensao esta disponivel na URL.
2. **Orquestrar** download seguro (``app.safe_http``) + envio
   (``CinerieClient.ingest_editorial_media``) atras de uma unica funcao que
   NUNCA levanta: toda falha vira WARNING no log e ``None`` no retorno, e a
   materia segue sem foto.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Final, Mapping, Optional, Sequence

from .errors import MediaHeroError, MediaIngestError
from .media_client import MAX_MEDIA_BYTES, MediaHeroResult, MediaIngestResult

if TYPE_CHECKING:
    from .client import CinerieClient

logger = logging.getLogger(__name__)


def _candidate_url_and_text(candidate: Any) -> tuple[str, Optional[str], Optional[str]]:
    if isinstance(candidate, str):
        return candidate.strip(), None, None
    if isinstance(candidate, Mapping):
        url = str(candidate.get("url") or candidate.get("src") or "").strip()
        alt = candidate.get("alt") or candidate.get("alt_text")
        caption = candidate.get("caption") or candidate.get("figcaption")
        return url, alt, caption
    # `MediaCandidate` (app.editorial.models) e outros objetos com os mesmos
    # atributos: duck-typed de proposito para nao criar um import circular com
    # `app.editorial` so por causa de um tipo.
    url = getattr(candidate, "source_url", None) or getattr(candidate, "url", None)
    if url:
        alt = getattr(candidate, "original_alt", None) or getattr(candidate, "alt", None)
        caption = getattr(candidate, "original_caption", None) or getattr(candidate, "caption", None)
        return str(url).strip(), alt, caption
    return "", None, None


def rank_usable_candidates(
    image_candidates: Sequence[Any],
    *,
    intended_use: str = "hero",
    alt_fallback: Optional[str] = None,
    caption_fallback: Optional[str] = None,
) -> list[Mapping[str, str]]:
    """Candidatos utilizaveis, do melhor para o pior. Nunca levanta.

    Reusa ``app.media_validation.validate_candidate`` — a mesma regra que ja
    filtra pixel de rastreio, avatar, logo e formato nao entregavel — para nao
    duplicar criterio. Entre os utilizaveis, o de maior dimensao DECLARADA NA
    URL vence; sem dimensao para nenhum candidato, o primeiro da lista vence
    (a ordem do cluster ja e prioridade editorial: fonte primaria primeiro).
    URLs repetidas contam uma vez — a mesma foto em duas fontes e uma foto.
    """
    from app.media_validation import _DIMENSION_IN_URL, validate_candidate

    scored: list[tuple[int, int, str, Optional[str]]] = []
    seen: set[str] = set()
    for order, candidate in enumerate(image_candidates or []):
        url, alt, caption = _candidate_url_and_text(candidate)
        if not url or url in seen:
            continue
        seen.add(url)
        validation = validate_candidate(
            url,
            intended_use=intended_use,
            alt=alt or alt_fallback,
            caption=caption or caption_fallback,
        )
        if not validation.usable:
            continue
        dimension_match = _DIMENSION_IN_URL.search(url)
        area = 0
        if dimension_match:
            area = int(dimension_match.group(1)) * int(dimension_match.group(2))
        scored.append((area, -order, url, validation.alt_suggestion))

    # Maior area primeiro; empate resolvido pela ordem editorial do cluster
    # (fonte primaria primeiro), que o ``-order`` preserva no sort decrescente.
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [
        {"url": url, "alt": alt or alt_fallback or ""}
        for _, _, url, alt in scored
    ]


def select_hero_candidate(
    image_candidates: Sequence[Any],
    *,
    alt_fallback: Optional[str] = None,
    caption_fallback: Optional[str] = None,
) -> Optional[Mapping[str, str]]:
    """O melhor candidato a capa, ou ``None`` se nenhum for utilizavel."""
    ranked = rank_usable_candidates(
        image_candidates,
        intended_use="hero",
        alt_fallback=alt_fallback,
        caption_fallback=caption_fallback,
    )
    return ranked[0] if ranked else None


def ingest_hero_image(
    *,
    article_id: str,
    image_candidates: Sequence[Any],
    source_name: str,
    client: Optional["CinerieClient"],
    media_api_key: str,
    alt_fallback: Optional[str] = None,
) -> Optional[MediaIngestResult]:
    """Baixa e ingere a melhor imagem do cluster. Nunca levanta.

    ``client`` e o MESMO ``CinerieClient`` ja usado para publicar o texto —
    reaproveitado de proposito, para nao abrir uma segunda configuracao de
    base_url so por causa da foto. ``media_api_key`` e a credencial SEPARADA
    (escopo ``editorial_media_ingest``), passada explicitamente a cada
    chamada em vez de armazenada no client, para nao se misturar com a
    credencial de publicacao.

    ``source_name`` vira ``sourceName``/``rightsHolder``/base do ``credit`` no
    corpo da ingestao — a mesma proveniencia que ``_sources_from_draft`` ja
    resolveu para a fonte primaria. A licenca em si (``approved`` /
    ``allowedForHero``) e decidida do lado do Cinerie, nunca aqui.
    """
    if client is None or not media_api_key:
        logger.info(
            "[CINERIE_MEDIA] ingestao pulada: credencial de midia nao configurada "
            "(MNSCR_CINERIE_MEDIA_API_KEY ausente)"
        )
        return None

    candidate = select_hero_candidate(image_candidates, alt_fallback=alt_fallback)
    if candidate is None:
        logger.warning(
            "[CINERIE_MEDIA] nenhuma imagem utilizavel entre os candidatos do cluster; "
            "publicando sem hero"
        )
        return None

    try:
        from app.safe_http import safe_get

        fetched = safe_get(candidate["url"], max_bytes=MAX_MEDIA_BYTES)
        if fetched.status_code != 200:
            raise MediaIngestError(
                f"download da imagem retornou HTTP {fetched.status_code}",
                remote_code="download_failed",
            )

        display_name = source_name or "Fonte externa"
        result = client.ingest_editorial_media(
            article_id=article_id,
            source_url=candidate["url"],
            source_name=display_name,
            rights_holder=display_name,
            credit=f"Divulgacao/{display_name}",
            alt=candidate.get("alt") or alt_fallback or "Imagem da materia",
            content_bytes=fetched.content,
            api_key=media_api_key,
        )
        logger.info("[CINERIE_MEDIA] ingerida com sucesso %s", result.safe_log_fields())
        return result
    except Exception as exc:  # noqa: BLE001 - imagem nunca derruba a materia
        logger.warning(
            "[CINERIE_MEDIA] ingestao falhou (%s): %s; publicando sem hero",
            type(exc).__name__, exc,
        )
        return None


#: Teto de imagens de corpo por materia, alem da capa. Tres e o suficiente
#: para ilustrar sem transformar a materia em galeria.
MAX_BODY_IMAGES: Final = 3


def ingest_body_images(
    *,
    article_id: str,
    image_candidates: Sequence[Any],
    exclude_urls: Sequence[str] = (),
    exclude_content_hashes: Sequence[str] = (),
    source_name: str,
    client: Optional["CinerieClient"],
    media_api_key: str,
    alt_fallback: Optional[str] = None,
    maximum: int = MAX_BODY_IMAGES,
) -> list[Mapping[str, str]]:
    """Ingere ate ``maximum`` imagens de CORPO. Nunca levanta, nunca repete a capa.

    A rota de midia aceita varias fotos por ``articleId``; o que faltava era
    alguem chama-la mais de uma vez. Cada foto aceita devolve um ``mediaId``
    real — e um ``mediaId`` conhecido e o que permite a uma revisao legitima
    posterior carregar blocos ``image`` que o contrato aceita.

    Duas exclusoes, porque a mesma foto chega por dois caminhos diferentes:

    - ``exclude_urls``: a URL que ja virou capa.
    - ``exclude_content_hashes``: o hash dos BYTES da capa. A fonte serve a
      mesma imagem em URLs distintas (og:image e corpo, tamanhos na query), e
      so a URL nao pega isso — a materia 38 ingeriu a capa de novo como
      "imagem de corpo" exatamente assim. O hash e comparado ANTES do upload,
      sobre os bytes baixados, entao a duplicata nem consome a rota.

    Falha em uma foto nao interrompe as demais — cada uma e best-effort por
    conta propria.
    """
    if client is None or not media_api_key or maximum <= 0:
        return []

    excluded = {str(url).strip() for url in exclude_urls or [] if str(url).strip()}
    ranked = [
        candidate
        for candidate in rank_usable_candidates(
            image_candidates, intended_use="inline", alt_fallback=alt_fallback
        )
        if candidate["url"] not in excluded
    ]
    if not ranked:
        return []

    import hashlib

    from app.safe_http import safe_get

    seen_hashes = {
        str(item).strip().removeprefix("sha256:")
        for item in exclude_content_hashes or []
        if str(item).strip()
    }

    display_name = source_name or "Fonte externa"
    ingested: list[Mapping[str, str]] = []
    for candidate in ranked:
        if len(ingested) >= maximum:
            break
        try:
            fetched = safe_get(candidate["url"], max_bytes=MAX_MEDIA_BYTES)
            if fetched.status_code != 200:
                raise MediaIngestError(
                    f"download da imagem retornou HTTP {fetched.status_code}",
                    remote_code="download_failed",
                )
            content_hash = hashlib.sha256(fetched.content).hexdigest()
            if content_hash in seen_hashes:
                logger.info(
                    "[CINERIE_MEDIA] imagem de corpo pulada: mesmos bytes de "
                    "imagem ja usada (hash %s...) url=%s",
                    content_hash[:12], candidate["url"][:120],
                )
                continue
            result = client.ingest_editorial_media(
                article_id=article_id,
                source_url=candidate["url"],
                source_name=display_name,
                rights_holder=display_name,
                credit=f"Divulgacao/{display_name}",
                alt=candidate.get("alt") or alt_fallback or "Imagem da materia",
                content_bytes=fetched.content,
                api_key=media_api_key,
            )
        except Exception as exc:  # noqa: BLE001 - imagem nunca derruba materia
            logger.warning(
                "[CINERIE_MEDIA] imagem de corpo falhou (%s) url=%s: %s; seguindo",
                type(exc).__name__, candidate["url"][:120], exc,
            )
            continue
        seen_hashes.add(content_hash)
        logger.info(
            "[CINERIE_MEDIA] imagem de corpo ingerida %s", result.safe_log_fields()
        )
        ingested.append(
            {
                "media_id": result.media_id,
                "url": candidate["url"],
                "alt": candidate.get("alt") or alt_fallback or "Imagem da materia",
                "outcome": result.outcome,
            }
        )
    return ingested


def point_hero_media(
    *,
    article_id: str,
    media_id: str,
    client: Optional["CinerieClient"],
    media_api_key: str,
) -> Optional[MediaHeroResult]:
    """Passo 3: aponta a foto ja ingerida como capa da materia. Nunca levanta.

    Mesma promessa de ``ingest_hero_image``, e pelo mesmo motivo: a esta altura
    a materia JA foi aceita pelo Cinerie. Deixar uma excecao escapar daqui faria
    a camada de cima ler "a publicacao falhou" depois de o `article_id` real ja
    existir — e o id seria descartado. Toda falha vira WARNING e ``None``.

    O log separa duas coisas que um `HTTP 4xx` generico confundiria:

    - **conflito de estado** (os quatro ``409``): o ``mediaId`` estava CERTO e a
      capa e que nao pode ser apontada agora. Depois que a materia sai de
      ``automation_draft`` este passo passa a recusar sempre, e isso e o
      desenho: dali em diante a capa e assunto de quem esta editando;
    - **qualquer outra recusa**: credencial, formato, foto ou materia
      inexistente — ai sim ha algo a corrigir do lado de ca.
    """
    if client is None or not media_api_key:
        logger.info(
            "[CINERIE_MEDIA] capa nao apontada: credencial de midia nao configurada "
            "(MNSCR_CINERIE_MEDIA_API_KEY ausente)"
        )
        return None

    try:
        result = client.point_media_as_hero(
            media_id=str(media_id), article_id=str(article_id), api_key=media_api_key
        )
    except MediaHeroError as exc:
        if exc.is_state_conflict:
            logger.warning(
                "[CINERIE_MEDIA] capa NAO apontada por conflito de ESTADO "
                "(mediaId=%s continua valido) article_id=%s code=%s: %s",
                media_id, article_id, exc.remote_code or "sem_codigo", exc,
            )
        else:
            logger.warning(
                "[CINERIE_MEDIA] capa NAO apontada article_id=%s mediaId=%s code=%s: %s; "
                "a materia publicada segue valida, sem capa",
                article_id, media_id, exc.remote_code or "sem_codigo", exc,
            )
        return None
    except Exception as exc:  # noqa: BLE001 - a capa nunca custa a materia publicada
        logger.warning(
            "[CINERIE_MEDIA] capa NAO apontada (%s) article_id=%s mediaId=%s: %s",
            type(exc).__name__, article_id, media_id, exc,
        )
        return None

    logger.info("[CINERIE_MEDIA] capa apontada %s", result.safe_log_fields())
    return result


__all__ = [
    "MAX_BODY_IMAGES",
    "ingest_body_images",
    "ingest_hero_image",
    "point_hero_media",
    "rank_usable_candidates",
    "select_hero_candidate",
]
