"""Formato e regras da ingestao de midia editorial — sem transporte.

``POST /api/internal/editorial-media`` — rota SEPARADA de
``/api/internal/editorial-publications``. Documentada em
``docs/operations/editorial-media-ingest.md`` no repositorio do Cinerie (PR
#136) e implementada em ``apps/cms/src/endpoints/editorial-media.ts`` +
``apps/cms/src/media-intake.ts``. Este modulo espelha o CODIGO real, nao uma
suposicao de formato — inclusive onde os dois divergem (ver nota abaixo).

DIVERGENCIA ENCONTRADA E RESOLVIDA A FAVOR DO CODIGO: o desenho original
presumido para esta tarefa era "envia a foto, recebe mediaId, referencia
mediaId no PRIMEIRO envio da materia". O contrato real exige ``articleId``
numerico no corpo — ou seja, o artigo PRECISA JA EXISTIR no Cinerie antes de
qualquer ingestao de midia. Isso so e possivel depois que
``editorial-publications`` respondeu com um ``article_id`` (o que so acontece
DEPOIS do primeiro envio da materia, texto puro). A rota tambem declara,
explicitamente, que ela **nao aponta a capa** — apontar a capa exige uma nova
submissao da materia (``intent=update``) referenciando o ``mediaId`` obtido.

Este pacote cobre a INGESTAO (upload dos bytes, recebe mediaId) via
``CinerieClient.ingest_editorial_media`` em ``client.py`` — o UNICO modulo do
pacote autorizado a importar ``requests`` (ver
``tests/test_no_publication_guard.py::test_only_the_cinerie_client_imports_requests``).
Este arquivo fica so com o que e puro: constantes, sniff de bytes e o formato
do resultado.

A SEGUNDA METADE, ANTES EM ABERTO, FECHOU. Ate 07/08/2026 a foto entrava no
acervo com um ``mediaId`` real e a capa continuava vazia, porque as duas saidas
conhecidas eram ruins: reenviar a materia com ``media[]`` bate em
``staleRevision`` (a revisao nao mudou), e inventar um incremento de
``sourceRevision`` so por causa da foto mexeria na reconciliacao de eventos
(``app.event_store``, ``seen_articles.event_revision``).

O Cinerie publicou a terceira chamada, que nao precisa de nenhuma das duas:
``PATCH /api/internal/editorial-media/:mediaId/hero`` com ``{articleId}``
(``docs/operations/editorial-media-hero.md`` no repositorio do Cinerie). Ela
NAO le nem escreve ``sourceRevision``, nao passa por ``staleRevision``, nao
muda ``workflowStatus`` e nao consome cota de autopublicacao. O escopo e o
MESMO da ingestao de bytes — ``editorial_media_ingest`` —, entao nao ha
credencial nova: quem ja pode POR a foto no acervo daquela materia nao ganha
poder novo ao dizer qual delas e a capa.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Final, Optional, Tuple

MEDIA_INGEST_PATH: Final[str] = "/api/internal/editorial-media"

#: Espelha `MAX_MEDIA_REQUEST_BYTES` de `apps/cms/src/media-intake.ts`.
MAX_MEDIA_REQUEST_BYTES: Final[int] = 21 * 1024 * 1024
#: Espelha `MAX_MEDIA_BYTES` (bytes DECODIFICADOS) do mesmo arquivo.
MAX_MEDIA_BYTES: Final[int] = 15 * 1024 * 1024

#: Espelha `INGESTIBLE_MIME_TYPES`. AVIF fica de fora nesta rota de proposito
#: — ver o comentario do lado do Cinerie: as dimensoes do AVIF vivem numa caixa
#: de deslocamento variavel que o sniff nao le.
INGESTIBLE_MIME_TYPES: Final[Tuple[str, ...]] = ("image/jpeg", "image/png", "image/webp")


def sniff_ingestible_mime(content: bytes) -> Optional[str]:
    """Que formato os BYTES dizem ser — mesma assinatura que o Cinerie confere.

    Enviar um `contentType` que nao bate com a assinatura real e recusado do
    outro lado como `bytes_mismatch`; sniffar aqui evita gastar o upload inteiro
    so para ouvir isso.
    """
    if content[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if content[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if len(content) >= 12 and content[0:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    return None


@dataclass(frozen=True)
class MediaIngestResult:
    """O que a rota devolveu para um upload aceito."""

    outcome: str  # created | unchanged | replaced
    media_id: str
    content_hash: str

    def safe_log_fields(self) -> Dict[str, Any]:
        return {"outcome": self.outcome, "media_id": self.media_id, "content_hash": self.content_hash}


# ===========================================================================
# Passo 3: apontar a capa
# ===========================================================================


def media_hero_path(media_id: str) -> str:
    """``mediaId`` vem do CAMINHO; ``articleId``, do corpo.

    A assimetria e do contrato e e deliberada: a foto e o recurso ALTERADO (por
    isso identifica a URL) e a materia e a afirmacao que sera CONFERIDA contra
    ``media.ingestedForArticle``. Montar o caminho aqui — e nao no cliente —
    mantem o formato do lado puro, junto do resto do que esta rota promete.
    """
    return f"/api/internal/editorial-media/{str(media_id).strip()}/hero"


#: Os tres desfechos de sucesso. ``unchanged`` NAO escreve do outro lado, e isso
#: nao e otimizacao: cada ``update`` cria uma linha de versao, e um pipeline que
#: reenvia a cada revisao encheria o historico da materia com versoes iguais.
HERO_OUTCOMES: Final[Tuple[str, ...]] = ("linked", "unchanged", "replaced")

#: Os quatro `409`. Todos sao de ESTADO — a capa nao pode ser apontada agora —, e
#: nenhum deles significa "o mediaId estava errado". A distincao importa no log:
#: quem le `article_not_automation_draft` e conclui "id errado" vai procurar
#: defeito num id que estava certo. A ordem em que o Cinerie os avalia aponta a
#: causa mais corrigivel primeiro: pertencimento -> licenca -> estado da materia
#: -> proveniencia da capa atual.
HERO_STATE_CONFLICTS: Final[Dict[str, str]] = {
    "media_not_ingested_for_article": (
        "a foto nao foi ingerida PARA esta materia; o vinculo de pertencimento e "
        "que recusou, nao o id"
    ),
    "media_not_hero_eligible": "licenca editorial revogada ou allowedForHero desligado",
    "article_not_automation_draft": (
        "a materia saiu do alcance da automacao; dali em diante a capa e assunto de "
        "quem esta editando"
    ),
    "hero_not_owned_by_automation": "a capa atual foi escolhida por gente, e robo nao reescreve",
}


@dataclass(frozen=True)
class MediaHeroResult:
    """O que a rota devolveu quando a capa pode ser apontada."""

    outcome: str  # linked | unchanged | replaced
    article_id: str
    media_id: str
    previous_media_id: Optional[str] = None

    @property
    def wrote(self) -> bool:
        """Houve escrita do outro lado? ``unchanged`` nao escreve nada."""
        return self.outcome != "unchanged"

    def safe_log_fields(self) -> Dict[str, Any]:
        return {
            "outcome": self.outcome,
            "article_id": self.article_id,
            "media_id": self.media_id,
            "previous_media_id": self.previous_media_id,
        }


__all__ = [
    "HERO_OUTCOMES",
    "HERO_STATE_CONFLICTS",
    "INGESTIBLE_MIME_TYPES",
    "MAX_MEDIA_BYTES",
    "MAX_MEDIA_REQUEST_BYTES",
    "MEDIA_INGEST_PATH",
    "MediaHeroResult",
    "MediaIngestResult",
    "media_hero_path",
    "sniff_ingestible_mime",
]
