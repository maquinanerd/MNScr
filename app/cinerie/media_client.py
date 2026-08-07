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

A segunda metade — resubmeter a materia com ``media=[{mediaId,
intendedUse:"hero"}]`` para de fato apontar a capa — NAO esta implementada:
exigiria incrementar ``sourceRevision`` so para anexar a foto, e
``sourceRevision`` tambem alimenta a reconciliacao de eventos
(``app.event_store``, ``seen_articles.event_revision``). Inventar um
incremento artificial de revisao sem entender o efeito colateral la seria uma
decisao de arquitetura que este modulo nao toma sozinho. Ver o relatorio da
tarefa para a pergunta especifica que fica em aberto.
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


__all__ = [
    "INGESTIBLE_MIME_TYPES",
    "MAX_MEDIA_BYTES",
    "MAX_MEDIA_REQUEST_BYTES",
    "MEDIA_INGEST_PATH",
    "MediaIngestResult",
    "sniff_ingestible_mime",
]
