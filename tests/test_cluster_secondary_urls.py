"""O cacho reconstruido precisa chegar ao extrator com as fontes irmas.

Regressao de 31/08/2026, encontrada no log de um ciclo `--once` inteiro: as 15
materias sairam com `docs=1` e `fontes_descartadas=0`. Zero descartadas com uma
so usada e a assinatura de que nenhuma irma chegou a ser TENTADA.

`reconcile_rssprime_event_tasks` escrevia so `urls` na task; quem busca as
irmas (`build_multi_source_payload`) le so `additional_urls`. As URLs estavam
no dicionario, a uma chave de distancia, e o multi-fonte estava desligado em
silencio: sem erro, sem aviso, e com o Editorial Gate registrando
`GATE_TRUSTED_SINGLE_SOURCE` como se aquilo fosse a verdade da materia.

Os testes de `test_multi_source_builder.py` nao pegaram porque o cacho de
mentira deles sempre escreveu `additional_urls` — uma forma que a producao
nunca produziu.
"""

from __future__ import annotations

from app.cluster_feed_adapter import normalize_cluster_item, secondary_urls
from app.multi_source_builder import build_multi_source_payload

PRIMARIA = "https://variety.com/2026/08/nova-fase"
IRMA = "https://deadline.com/2026/08/nova-fase"


def _task_reconstruida() -> dict:
    """A forma exata que `reconcile_rssprime_event_tasks` poe na fila."""
    return {
        "id": "event:evt-1:1",
        "title": "Superman ganha nova fase",
        "url": PRIMARIA,
        "source_id": "rssprime_movies",
        "origin": "superfeed",
        "topic": "movies",
        "event_key": "evt-1",
        "event_revision": 1,
        "is_cluster": True,
        "multi_source": True,
        "source_count": 2,
        "all_sources": [{"url": PRIMARIA}, {"url": IRMA}],
        "urls": [PRIMARIA, IRMA],
        "fonte_nome": "Variety",
        "primary_source": PRIMARIA,
    }


def test_irma_vem_de_urls_quando_additional_urls_nao_existe():
    assert secondary_urls(_task_reconstruida()) == [IRMA]


def test_irma_vem_de_additional_urls_quando_urls_nao_existe():
    # A forma que `enrich_feed_item` produz no caminho normal do feed.
    assert secondary_urls({"url": PRIMARIA, "additional_urls": [IRMA]}) == [IRMA]


def test_as_duas_listas_se_somam_sem_repetir_e_sem_a_primaria():
    outra = "https://collider.com/nova-fase"
    entry = {
        "url": PRIMARIA,
        "urls": [PRIMARIA, IRMA],
        "additional_urls": [IRMA, outra],
    }
    assert secondary_urls(entry) == [IRMA, outra]


def test_item_de_fonte_unica_continua_de_fonte_unica():
    assert secondary_urls({"url": PRIMARIA, "urls": [PRIMARIA]}) == []
    assert secondary_urls({"url": PRIMARIA}) == []


def test_lixo_na_lista_nao_vira_url():
    entry = {"url": PRIMARIA, "urls": [PRIMARIA, None, "", "   ", 42, IRMA]}
    assert secondary_urls(entry) == [IRMA]


def test_normalize_entrega_a_irma_ao_pipeline():
    assert normalize_cluster_item(_task_reconstruida())["additional_urls"] == [IRMA]


class _ExtratorFalso:
    def __init__(self, paginas):
        self.paginas = paginas
        self.buscadas = []

    def _fetch_html(self, url):
        self.buscadas.append(url)
        return self.paginas.get(url)

    def extract(self, html, url=""):
        return self.paginas.get(url)


def _corpo(assunto: str) -> str:
    return f"{assunto} " + " ".join(["Metropolis"] * 120)


def test_cacho_reconstruido_e_extraido_como_multi_fonte():
    extrator = _ExtratorFalso(
        {
            PRIMARIA: {"title": "Superman ganha nova fase", "content": _corpo("Superman")},
            IRMA: {"title": "Superman: o que muda", "content": _corpo("Superman")},
        }
    )

    payload = build_multi_source_payload(normalize_cluster_item(_task_reconstruida()), extrator)

    assert payload is not None
    assert extrator.buscadas == [PRIMARIA, IRMA], "a irma tem de ser buscada"
    assert payload["publication_basis"] == "multi_source"
    assert [doc["url"] for doc in payload["cluster_docs"]] == [PRIMARIA, IRMA]


def test_a_irma_fora_de_assunto_continua_sendo_recusada():
    """O conserto liga o multi-fonte; nao afrouxa a checagem de assunto."""
    extrator = _ExtratorFalso(
        {
            PRIMARIA: {"title": "Superman ganha nova fase", "content": _corpo("Superman")},
            IRMA: {"title": "Batman muda de rumo", "content": _corpo("Gotham")},
        }
    )

    payload = build_multi_source_payload(normalize_cluster_item(_task_reconstruida()), extrator)

    assert payload is not None, "variety.com sustenta a materia sozinha"
    assert extrator.buscadas == [PRIMARIA, IRMA], "recusa por assunto, nao por falta de busca"
    assert payload["publication_basis"] == "single_source_trusted"
    assert [doc["url"] for doc in payload["cluster_docs"]] == [PRIMARIA]
    assert [d["status"] for d in payload["sources_skipped"]] == ["OFF_TOPIC"]


def test_cacho_degradado_de_dominio_nao_confiavel_passa_a_ser_recusado():
    """O efeito de segunda ordem do conserto, fixado de proposito.

    Enquanto as irmas nunca eram buscadas, `declared_sources` valia 1 e
    `_single_source_basis` julgava o item como "acontecimento de fonte unica",
    liberando-o por `ALLOW_SINGLE_SOURCE`. Era um julgamento sobre um fato
    falso: o evento declarava duas fontes.

    Agora que a irma e buscada de verdade, um cacho que DEGRADA (prometeu duas,
    entregou uma) e julgado como cacho degradado — e ai a exigencia de dominio
    confiavel vale integralmente, que e o que a politica sempre disse.

    Custo medido na fila de 31/08/2026: no pior caso 3 de 37 itens.
    """
    primaria = "https://screenrant.com/nova-fase"
    irma = "https://collider.com/outro-assunto"
    task = {
        **_task_reconstruida(),
        "url": primaria,
        "urls": [primaria, irma],
        "primary_source": primaria,
    }
    extrator = _ExtratorFalso(
        {
            primaria: {"title": "Superman ganha nova fase", "content": _corpo("Superman")},
            irma: {"title": "Batman muda de rumo", "content": _corpo("Gotham")},
        }
    )

    assert build_multi_source_payload(normalize_cluster_item(task), extrator) is None
    assert extrator.buscadas == [primaria, irma]
