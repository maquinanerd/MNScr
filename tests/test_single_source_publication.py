"""Item de fonte unica: publica, e quando nao publica morre BARATO.

O defeito que estes testes travam custava dinheiro em duas etapas distintas, e
vale separar as duas porque a correcao de uma nao e a correcao da outra:

1. **O item nao era reconhecido.** ``is_superfeed_item`` exigia ``is_cluster``,
   que so e verdadeiro para multi-fonte. Item de fonte unica caia no caminho
   antigo — o de antes do Cinerie —, onde o ``art_data`` reconstruido nao carrega
   ``event_key``. Resultado: ``RequestBuildError`` em ``normalize_cluster_id``
   DEPOIS de a IA ter escrito a materia inteira.

2. **A recusa chegava tarde.** Mesmo com a politica dizendo nao, o item ja tinha
   consumido as tres chamadas de modelo. Recusar e legitimo; recusar depois de
   pagar nao e.

Por isso os dois caminhos de ``ALLOW_SINGLE_SOURCE`` sao provados aqui: ligado,
publica; desligado, e recusado ANTES da IA. Um interruptor que so funciona numa
posicao nao e reversibilidade.
"""

import hashlib
import socket
from types import SimpleNamespace

import pytest

from app.cinerie.contract import local_identity
from app.cinerie.errors import RequestBuildError
from app.cinerie.identity import build_identity
from app.cinerie.request import build_publication_request
from app.cluster_feed_adapter import is_superfeed_item, normalize_cluster_item
from app.early_reject import EarlyRejectLedger, evaluate_early_rejection
from app.editorial.models import DraftContent, DraftProvenance, EditorialDraft, SourceReference
from app.multi_source_builder import build_multi_source_payload


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _blocked(*args, **kwargs):
        raise AssertionError("Teste de fonte unica tentou acessar a rede")

    monkeypatch.setattr(socket, "socket", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)


@pytest.fixture(autouse=True)
def _flag_desligada_por_padrao(monkeypatch):
    """Nenhum teste aqui herda a decisao do ``.env`` da maquina.

    A suite ja passou por acidente de ambiente uma vez neste repositorio. Cada
    teste abaixo declara a posicao do interruptor que esta provando.
    """
    monkeypatch.delenv("ALLOW_SINGLE_SOURCE", raising=False)


def item_de_fonte_unica(**overrides) -> dict:
    """O item real: ``source_count=1``, ``origin=superfeed``, com ``event_key``."""
    item = {
        "db_id": 101,
        "id": "sf-101",
        "source_id": "rssprime_movies",
        "origin": "superfeed",
        "topic": "movies",
        "title": "Estudio confirma data de estreia da nova serie derivada",
        "url": "https://blogaleatorio.example/story",
        "urls": ["https://blogaleatorio.example/story"],
        "additional_urls": [],
        "event_key": "3b102773917e55e9",
        "is_cluster": False,
        "multi_source": False,
        "source_count": 1,
        "all_sources": ["Blog Aleatorio"],
        "primary_source": "Blog Aleatorio",
    }
    item.update(overrides)
    return item


# ===========================================================================
# 1. Reconhecimento — sem virar cacho
# ===========================================================================


def test_item_de_fonte_unica_do_superfeed_e_reconhecido():
    assert is_superfeed_item(item_de_fonte_unica()) is True


def test_reconhecimento_nao_transforma_fonte_unica_em_cacho():
    """O criterio mudou; o significado de ``is_cluster`` nao.

    ``is_cluster`` e lido como fato editorial por ``build_multi_source_payload``,
    pelo merge da IA e pelo CLUSTER_INPUT_AUDIT. Um item de fonte unica que
    saisse daqui marcado como cacho mentiria para os tres de uma vez.
    """
    normalizado = normalize_cluster_item(item_de_fonte_unica())

    assert normalizado["is_cluster"] is False
    assert normalizado["multi_source"] is False
    assert normalizado["event_key"] == "3b102773917e55e9"


def test_cacho_continua_sendo_cacho_ao_normalizar():
    normalizado = normalize_cluster_item(
        item_de_fonte_unica(is_cluster=True, multi_source=True, source_count=3)
    )

    assert normalizado["is_cluster"] is True
    assert normalizado["multi_source"] is True


def test_item_sem_event_key_nao_e_item_de_superfeed():
    """Sem agrupamento de origem nao ha o que carregar ate o contrato."""
    assert is_superfeed_item(item_de_fonte_unica(event_key="")) is False


def test_item_legado_marcado_como_cacho_continua_reconhecido():
    """Linha antiga, sem ``origin`` preservado, nao pode sumir da fila."""
    legado = item_de_fonte_unica(is_cluster=True, source_count=2)
    legado.pop("origin")

    assert is_superfeed_item(legado) is True


# ===========================================================================
# 2. Qualificacao da extracao — o interruptor
# ===========================================================================


class _FakeExtractor:
    """Extrator local. Um documento so, que e exatamente o caso em teste."""

    def __init__(self, por_url: dict):
        self._por_url = por_url

    def _fetch_html(self, url):
        return "<html><body></body></html>"

    def extract(self, html, url=""):
        return self._por_url.get(url)


def _corpo(marcador: str) -> str:
    return "<p>" + (f"{marcador} texto suficiente para passar do minimo. " * 12) + "</p>"


def _cluster_de_uma_fonte(url: str) -> dict:
    return {
        "url": url,
        "additional_urls": [],
        "event_key": "3b102773917e55e9",
        "primary_source": "Blog Aleatorio",
        "all_sources": ["Blog Aleatorio"],
    }


def test_fonte_unica_declarada_publica_com_a_flag_ligada(monkeypatch):
    monkeypatch.setenv("ALLOW_SINGLE_SOURCE", "true")
    url = "https://blogaleatorio.example/story"
    extractor = _FakeExtractor({url: {"title": "A", "content": _corpo("fonte-a")}})

    payload = build_multi_source_payload(_cluster_de_uma_fonte(url), extractor, min_chars=50)

    assert payload is not None
    assert payload["publication_basis"] == "single_source_allowed"
    assert len(payload["cluster_docs"]) == 1
    assert payload["cluster_docs"][0]["status"] == "ARTICLE_BODY_OK"


def test_fonte_unica_declarada_e_recusada_com_a_flag_desligada(monkeypatch):
    monkeypatch.setenv("ALLOW_SINGLE_SOURCE", "false")
    url = "https://blogaleatorio.example/story"
    extractor = _FakeExtractor({url: {"title": "A", "content": _corpo("fonte-a")}})

    assert build_multi_source_payload(_cluster_de_uma_fonte(url), extractor, min_chars=50) is None


def test_cacho_degradado_para_uma_fonte_nao_e_afrouxado_pela_flag(monkeypatch):
    """A flag diz "fonte unica publica", nao "perder fontes tudo bem".

    Um item que DECLAROU varias fontes e chegou com uma perdeu justamente a
    corroboracao que prometia. Tratar essa perda como fonte unica legitima
    transformaria a flag num silenciador de degradacao — que e outra decisao, e
    ninguem a tomou.
    """
    monkeypatch.setenv("ALLOW_SINGLE_SOURCE", "true")
    urls = ["https://blogaleatorio.example/story", "https://outroblog.example/story"]
    extractor = _FakeExtractor(
        {
            urls[0]: {"title": "A", "content": _corpo("fonte-a")},
            urls[1]: {"title": "B", "content": "curto"},
        }
    )
    cluster = {**_cluster_de_uma_fonte(urls[0]), "additional_urls": [urls[1]]}

    assert build_multi_source_payload(cluster, extractor, min_chars=50) is None


def test_extracao_de_fonte_unica_produz_um_documento_utilizavel(monkeypatch):
    """Verificado, nao presumido: o caminho novo aguenta um documento so."""
    monkeypatch.setenv("ALLOW_SINGLE_SOURCE", "true")
    url = "https://blogaleatorio.example/story"
    extractor = _FakeExtractor({url: {"title": "Titulo da fonte", "content": _corpo("fonte-a")}})

    payload = build_multi_source_payload(_cluster_de_uma_fonte(url), extractor, min_chars=50)

    doc = payload["cluster_docs"][0]
    assert doc["url"] == url
    assert doc["title"] == "Titulo da fonte"
    assert doc["content"].strip()
    assert payload["sources_skipped"] == []


# ===========================================================================
# 3. Recusa cedo — a parte que vale dinheiro
# ===========================================================================


def test_flag_desligada_recusa_o_item_antes_da_ia(monkeypatch):
    monkeypatch.setenv("ALLOW_SINGLE_SOURCE", "false")

    recusa = evaluate_early_rejection(
        item_de_fonte_unica(), publishes_to_cinerie=True, public_author_id="1"
    )

    assert recusa is not None
    codigo, motivo = recusa
    assert codigo == "EARLY_SUPERFEED_POLICY"
    assert "SINGLE_SOURCE_NOT_RELIABLE" in motivo


def test_flag_ligada_deixa_o_item_seguir(monkeypatch):
    monkeypatch.setenv("ALLOW_SINGLE_SOURCE", "true")

    assert evaluate_early_rejection(
        item_de_fonte_unica(), publishes_to_cinerie=True, public_author_id="1"
    ) is None


def test_event_key_ausente_e_recusado_antes_da_ia(monkeypatch):
    """O mesmo `no` que ``identity.py`` daria — so que de graca.

    Este e o caso medido: 30 itens processados, 1 publicado, e o resto morrendo
    em ``normalize_cluster_id`` com a materia ja escrita.
    """
    monkeypatch.setenv("ALLOW_SINGLE_SOURCE", "true")

    recusa = evaluate_early_rejection(
        item_de_fonte_unica(event_key="", origin="fallback"),
        publishes_to_cinerie=True,
        public_author_id="1",
    )

    assert recusa is not None
    assert recusa[0] == "EARLY_EVENT_KEY_AUSENTE"


def test_a_recusa_antecipada_usa_a_mesma_regra_do_contrato(monkeypatch):
    """Se o contrato aceita, o cheque antecipado nao pode recusar — e vice-versa.

    Duas implementacoes da mesma pergunta divergem com o tempo, e a que fica
    para tras e sempre a do cheque barato: e a que ninguem lembra de atualizar.
    """
    monkeypatch.setenv("ALLOW_SINGLE_SOURCE", "true")

    for event_key in ["3b102773917e55e9", "chave com espacos e acentuacao", "", None]:
        item = item_de_fonte_unica(event_key=event_key, origin="fallback")
        recusado_cedo = evaluate_early_rejection(
            item, publishes_to_cinerie=True, public_author_id="1"
        ) is not None
        try:
            build_identity(event_key=event_key, revision=1, payload_hash="h" * 64)
            contrato_recusa = False
        except RequestBuildError:
            contrato_recusa = True

        assert recusado_cedo == contrato_recusa, f"divergencia para event_key={event_key!r}"


def test_public_author_id_invalido_e_pego_antes_da_ia(monkeypatch):
    """Defeito de configuracao que reprovaria TODA materia do ciclo."""
    monkeypatch.setenv("ALLOW_SINGLE_SOURCE", "true")

    recusa = evaluate_early_rejection(
        item_de_fonte_unica(),
        publishes_to_cinerie=True,
        public_author_id="author-redacao-cinerie",
    )

    assert recusa is not None
    assert recusa[0] == "EARLY_PUBLIC_AUTHOR_ID_INVALIDO"


def test_fora_do_auto_publish_o_item_sem_event_key_segue_vivo(monkeypatch):
    """Fallback legitimamente nao tem ``event_key``.

    Sem pedido de publicacao ao Cinerie, exigir ``event_key`` nao economizaria
    nada: desligaria o caminho de fallback inteiro em nome de um custo que essa
    rodada nao ia pagar.
    """
    monkeypatch.setenv("ALLOW_SINGLE_SOURCE", "false")
    item = {
        "db_id": 7,
        "source_id": "screenrant_movie_news",
        "origin": "fallback",
        "url": "https://screenrant.com/story",
        "title": "Materia de fallback",
    }

    assert evaluate_early_rejection(
        item, publishes_to_cinerie=False, public_author_id="1"
    ) is None


def test_o_registro_conta_a_economia_em_chamadas_de_ia():
    ledger = EarlyRejectLedger()
    ledger.record_rejection("EARLY_SUPERFEED_POLICY")
    ledger.record_rejection("EARLY_SUPERFEED_POLICY")
    ledger.record_rejection("EARLY_EVENT_KEY_AUSENTE")
    ledger.record_reached_ai(4)

    assert ledger.rejected == 3
    assert ledger.reached_ai == 4
    assert ledger.by_code["EARLY_SUPERFEED_POLICY"] == 2


# ===========================================================================
# 4. O contrato — externalSources com UMA entrada, papel primary
# ===========================================================================

CORPO = (
    "<p>O estudio confirmou nesta terca-feira a data de estreia da nova serie "
    "derivada, encerrando meses de especulacao entre os fas da franquia.</p>"
    "<h2>O que se sabe ate agora</h2>"
    "<p>O elenco principal foi mantido integralmente, segundo o comunicado "
    "oficial divulgado pelo estudio nesta manha.</p>"
)


def _draft_de_fonte_unica() -> EditorialDraft:
    draft = EditorialDraft(
        draft_id="draft-fonte-unica",
        article_id=101,
        event_key="3b102773917e55e9",
        revision=1,
        content=DraftContent(
            title="Estudio confirma data de estreia da nova serie derivada",
            subtitle="O anuncio veio acompanhado do primeiro trailer completo da temporada.",
            body_html=CORPO,
            summary=(
                "O estudio confirmou a data de estreia da nova serie derivada e "
                "divulgou o primeiro trailer completo, com o elenco ja confirmado."
            ),
            meta_description=(
                "O estudio confirmou a data de estreia da nova serie derivada e "
                "divulgou o primeiro trailer completo da temporada."
            ),
            focus_keyphrase="nova serie derivada",
            slug_suggestion="nova-serie-derivada-data-de-estreia",
            category_suggestions=["news"],
        ),
        provenance=DraftProvenance(
            input_hash=hashlib.sha256(b"entrada").hexdigest(),
            output_hash=hashlib.sha256(CORPO.encode("utf-8")).hexdigest(),
            input_event_key="3b102773917e55e9",
            input_revision=1,
            prompt_version="teste@1",
            created_at="2026-08-08T12:00:00+00:00",
        ),
        sources=[
            SourceReference(
                url="https://blogaleatorio.example/story",
                domain="blogaleatorio.example",
                name="Blog Aleatorio",
                is_primary=True,
            ),
        ],
    )
    draft.editorial_gate = SimpleNamespace(outcome="GATE_CLEAR", triggered_rules=[])
    draft.factual_assessment = SimpleNamespace(critical_conflicts=[])
    return draft


def _pedido(draft: EditorialDraft):
    return build_publication_request(
        draft,
        public_author_id="1",
        attribution_mode="newsroom",
        contract=local_identity(),
    )


def test_o_pedido_de_fonte_unica_e_montado_sem_erro_de_identidade():
    """O ``event_key`` chega ao contrato — que era exatamente o que faltava."""
    built = _pedido(_draft_de_fonte_unica())

    assert built.identity.source_cluster_id == "3b102773917e55e9"
    assert built.payload["sourceClusterId"] == "3b102773917e55e9"


def test_external_sources_sai_com_uma_entrada_de_papel_primario():
    """O gate ``ai_assisted_without_sources`` do Cinerie exige >= 1 fonte.

    Toda materia do MNScr e assistida por IA, entao esta lista nunca pode sair
    vazia — nem quando a materia tem uma fonte so.
    """
    payload = _pedido(_draft_de_fonte_unica()).payload

    assert len(payload["externalSources"]) == 1
    assert payload["externalSources"][0]["role"] == "primary"
    assert payload["externalSources"][0]["url"] == "https://blogaleatorio.example/story"


def test_bloco_de_lista_de_fontes_continua_saindo_com_fonte_unica():
    payload = _pedido(_draft_de_fonte_unica()).payload
    tipos = {bloco.get("type") for bloco in payload["blocks"]}
    ids_declarados = {fonte["id"] for fonte in payload["externalSources"]}

    listas = [b for b in payload["blocks"] if b.get("type") == "sourceList"]
    assert listas, f"nenhum bloco sourceList entre {sorted(tipos)}"
    for lista in listas:
        for item in lista.get("sources", []):
            assert item.get("sourceId") in ids_declarados


# ===========================================================================
# 5. O gate e conselheiro — aconselha, nao tranca
# ===========================================================================


def _gate_para(draft):
    """A politica REAL do MNScr, a mesma que a rodada usa.

    A avaliacao factual sai de cena durante a corrida: as regras factuais tem
    caminho proprio para "nao houve avaliacao", e o que se prova aqui e a regra
    de FONTE. Um duble de avaliacao factual so acrescentaria uma segunda coisa
    podendo falhar por motivo alheio ao teste.
    """
    from app.editorial_gate import EditorialGate

    anterior = getattr(draft, "factual_assessment", None)
    draft.factual_assessment = None
    try:
        return EditorialGate(policy_version="mnscr-editorial-gate-v2").evaluate(draft)
    finally:
        draft.factual_assessment = anterior


def test_o_aviso_de_fonte_unica_nao_bloqueia_e_chega_ao_pedido(monkeypatch):
    """Fonte unica fora da allowlist gera WARNING, e o WARNING atravessa.

    Rebaixar a severidade e depois colapsar o achado num rotulo unico
    reintroduziria, uma camada acima, o silenciamento que o rebaixamento desfez.
    """
    monkeypatch.setenv("ALLOW_SINGLE_SOURCE", "true")
    draft = _draft_de_fonte_unica()
    gate = _gate_para(draft)
    draft.editorial_gate = gate

    acionadas = {r.rule_code for r in gate.triggered_rules}
    assert "GATE_SINGLE_UNTRUSTED_SOURCE" in acionadas
    assert gate.blocking_count == 0

    payload = _pedido(draft).payload
    assert payload["qa"]["passed"] is True
    assert any("GATE_SINGLE_UNTRUSTED_SOURCE" in aviso for aviso in payload["qa"]["warnings"])
    assert "qa_not_passed" not in payload["qa"].get("reasonCodes", [])


def test_gate_trusted_single_source_nao_bloqueia_fonte_confiavel(monkeypatch):
    """A regra da allowlist observa; ela nao aprova nem tranca."""
    monkeypatch.setenv("ALLOW_SINGLE_SOURCE", "true")
    draft = _draft_de_fonte_unica()
    draft.sources = [
        SourceReference(
            url="https://deadline.com/2026/08/story",
            domain="deadline.com",
            name="Deadline",
            is_primary=True,
        )
    ]
    gate = _gate_para(draft)

    acionadas = {r.rule_code for r in gate.triggered_rules}
    assert "GATE_TRUSTED_SINGLE_SOURCE" in acionadas
    assert gate.blocking_count == 0


# ===========================================================================
# 6. O caminho inteiro, dentro de `process_batch`
# ===========================================================================
#
# As secoes acima provam as pecas. Esta prova a LIGACAO entre elas, que e onde
# o defeito realmente vivia: cada peca funcionava, e mesmo assim o `event_key`
# nao chegava ao contrato porque o item entrava pelo ramo errado.

# Corpo longo de proposito: a qualificacao de fonte exige um minimo de
# caracteres visiveis (``MIN_CHARS_DEFAULT``), e um texto curto demais seria
# descartado como TOO_SHORT antes de o teste chegar ao que ele quer provar.
ARTIGO_HTML = """
<html><head>
  <meta property="og:title" content="Estudio confirma nova temporada da serie">
  <meta name="description" content="O estudio confirmou a nova temporada.">
</head><body>
  <article>
    <p>O estudio confirmou nesta segunda-feira a producao da nova temporada da
       serie, com estreia prevista para 2027 e retorno do elenco principal em
       todos os papeis centrais da trama original, segundo o comunicado.</p>
    <h2>O que muda na nova fase</h2>
    <p>A producao sera retomada em Vancouver com o mesmo showrunner, segundo o
       comunicado oficial divulgado pela distribuidora nesta manha, depois de
       quase um ano de negociacao com o sindicato de roteiristas e com a equipe
       tecnica que assinou as duas temporadas anteriores da franquia.</p>
    <p>A distribuidora nao informou o numero de episodios nem o orcamento
       previsto para a temporada que vem por ai, e tambem nao comentou os
       rumores sobre uma serie derivada centrada nos personagens secundarios
       apresentados no final da temporada passada.</p>
    <p>O anuncio encerra meses de especulacao entre os fas, que acompanhavam a
       renovacao desde a exibicao do ultimo episodio. A emissora manteve o
       horario original na grade e prometeu divulgar o primeiro teaser antes do
       fim do ano, junto com a data exata de estreia no servico de streaming.</p>
  </article>
</body></html>
"""

RESPOSTA_DA_IA = {
    "titulo_final": "Estudio confirma nova temporada da serie para 2027",
    "conteudo_final": (
        "<p>O estudio confirmou a producao da nova temporada, com estreia "
        "prevista para 2027 e o elenco principal de volta.</p>"
        "<h2>O que muda na nova fase</h2>"
        "<p>A producao sera retomada em Vancouver com o mesmo showrunner, "
        "segundo o comunicado oficial da distribuidora.</p>"
    ),
    "meta_description": (
        "O estudio confirmou a nova temporada da serie com estreia prevista "
        "para 2027 e retorno do elenco principal."
    ),
    "subtitle": (
        "A producao sera retomada em Vancouver com o mesmo showrunner e o "
        "elenco principal confirmado para 2027."
    ),
    "focus_keyphrase": "nova temporada",
    "related_keyphrases": ["estreia 2027"],
    "slug": "nova-temporada-2027",
    "categorias": [{"nome": "Filmes"}],
    "tags_sugeridas": ["nova temporada"],
    "yoast_meta": {},
}


class _ClienteDeIAFalso:
    def get_last_used_model(self):
        return "gemini-3.1-flash-lite"

    def get_last_used_key(self):
        return "****1234"

    def generate_text(self, *args, **kwargs):
        raise AssertionError("Chamada de IA inesperada")


class _ProcessadorFalso:
    """Conta as chamadas. E o contador que prova a economia."""

    def __init__(self):
        self._ai_client = _ClienteDeIAFalso()
        self.chamadas = 0

    def rewrite_batch(self, batch_data):
        self.chamadas += 1
        return [(dict(RESPOSTA_DA_IA), None) for _ in batch_data]


@pytest.fixture
def bancada(tmp_path, monkeypatch):
    """Pipeline inteiro em disco temporario, sem rede, sem modelo, sem Cinerie."""
    from app import pipeline
    from app.store import Database
    from app.submitters import LocalDraftSubmitter

    db_path = tmp_path / "app.db"
    drafts_dir = tmp_path / "drafts"
    monkeypatch.chdir(tmp_path)

    processador = _ProcessadorFalso()
    monkeypatch.setattr(pipeline, "article_queue", pipeline.ArticleQueue(db_path=str(db_path)))
    monkeypatch.setattr(pipeline, "Database", lambda *a, **k: Database(str(db_path)))
    monkeypatch.setattr(pipeline, "get_ai_processor", lambda: processador)
    monkeypatch.setattr(pipeline, "USE_3PHASE_AI", False)
    monkeypatch.setattr(
        pipeline, "build_submitter", lambda *a, **k: LocalDraftSubmitter(output_dir=drafts_dir)
    )
    monkeypatch.setattr(pipeline.time, "sleep", lambda *_: None)
    # A entrega ao Cinerie tem suite propria, contra servidor de loopback. Aqui
    # o que esta em teste e o que o MNScr MONTA, nao o que a rede responde.
    monkeypatch.setattr(pipeline, "MNSCR_DELIVERY_MODE", "DRAFT")
    monkeypatch.setattr(pipeline, "ls_save_article", lambda **kwargs: None)
    monkeypatch.setattr(pipeline, "ls_get_link_map", lambda: {"posts": []})
    monkeypatch.setattr(
        pipeline.ContentExtractor, "_fetch_html", lambda self, url: ARTIGO_HTML
    )
    pipeline.EARLY_REJECT_LEDGER.reset()

    db = Database(str(db_path))
    db.initialize()
    cursor = db.conn.execute(
        """
        INSERT INTO seen_articles (
            source_id, external_id, url, canonical_url, origin, topic,
            event_key, is_cluster, multi_source, source_count, status, fail_count
        )
        VALUES (?, ?, ?, ?, 'superfeed', 'movies', ?, 0, 0, 1, 'NEW', 0)
        """,
        (
            "rssprime_movies",
            "sf-fonte-unica",
            "https://blogaleatorio.example/story",
            "https://blogaleatorio.example/story",
            "3b102773917e55e9",
        ),
    )
    db.conn.commit()
    article_id = int(cursor.lastrowid)
    db.close()

    item = item_de_fonte_unica(db_id=article_id, id="sf-fonte-unica")
    pipeline.article_queue.push_many([item])

    return SimpleNamespace(
        pipeline=pipeline,
        db_path=db_path,
        drafts_dir=drafts_dir,
        processador=processador,
        article_id=article_id,
    )


def _artefato(bancada):
    import json
    from pathlib import Path

    arquivos = list(Path(bancada.drafts_dir).glob("*.json"))
    assert len(arquivos) == 1, f"esperava 1 artefato, achei {len(arquivos)}"
    return json.loads(arquivos[0].read_text(encoding="utf-8"))


def test_com_a_flag_ligada_o_item_gera_draft_carregando_o_event_key(bancada, monkeypatch):
    """O desfecho que faltava: fonte unica vira draft, com agrupamento de origem.

    Sem o ``event_key`` aqui, o pedido de publicacao morre em
    ``normalize_cluster_id`` — que e exatamente como 29 de 30 itens morriam,
    depois de pagos.
    """
    monkeypatch.setenv("ALLOW_SINGLE_SOURCE", "true")
    reivindicado = bancada.pipeline.article_queue.pop_claimed("teste-worker")
    assert reivindicado is not None

    bancada.pipeline.process_batch([reivindicado], link_map={"posts": []})

    artefato = _artefato(bancada)
    assert artefato["event_key"] == "3b102773917e55e9"
    assert bancada.processador.chamadas == 1


def test_o_draft_de_fonte_unica_sai_com_exatamente_uma_fonte(bancada, monkeypatch):
    monkeypatch.setenv("ALLOW_SINGLE_SOURCE", "true")
    reivindicado = bancada.pipeline.article_queue.pop_claimed("teste-worker")
    bancada.pipeline.process_batch([reivindicado], link_map={"posts": []})

    fontes = _artefato(bancada)["sources"]
    assert len(fontes) == 1
    assert fontes[0]["url"] == "https://blogaleatorio.example/story"
    assert fontes[0]["is_primary"] is True


def test_com_a_flag_desligada_nenhuma_chamada_de_ia_e_feita(bancada, monkeypatch):
    """A reversibilidade que interessa: recusar, sim — mas de graca.

    Antes desta correcao o mesmo `nao` chegava tarde demais para valer alguma
    coisa: o texto ja estava escrito e pago quando alguem reparava.
    """
    monkeypatch.setenv("ALLOW_SINGLE_SOURCE", "false")
    reivindicado = bancada.pipeline.article_queue.pop_claimed("teste-worker")

    bancada.pipeline.process_batch([reivindicado], link_map={"posts": []})

    assert bancada.processador.chamadas == 0
    assert not list(bancada.drafts_dir.glob("*.json"))
    assert bancada.pipeline.EARLY_REJECT_LEDGER.rejected == 1
    assert bancada.pipeline.EARLY_REJECT_LEDGER.by_code["EARLY_SUPERFEED_POLICY"] == 1
