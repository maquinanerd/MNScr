"""Imagens de corpo: ingestao multipla, registro local e blocos ``image``.

O bloco ``image`` exige ``mediaRef`` de midia ja aprovada, e o ``mediaId`` so
existe DEPOIS de a materia existir — o mesmo ovo-e-galinha da capa. O caminho
que estes testes prendem e o que nao força revisao sintetica:

1. na publicacao, a ingestao sobe ate 3 imagens de corpo (alem da capa, sem
   repeti-la) e grava os ``mediaId`` em ``cinerie_media_assets`` por cluster;
2. numa revisao LEGITIMA posterior, os ids conhecidos viram ``media[]`` no
   pedido e ``attach_inline_images`` os promove a blocos ``image``.

Regra inegociavel, como sempre: falha de imagem nunca derruba materia.
"""

import hashlib
from types import SimpleNamespace

from app.cinerie.blocks import (
    BLOCK_HEADING,
    BLOCK_IMAGE,
    BLOCK_SOURCE_LIST,
    attach_inline_images,
    html_to_blocks,
)
from app.cinerie.contract import local_identity
from app.cinerie.media_selection import ingest_body_images
from app.cinerie.request import build_publication_request
from app.cinerie.validation import validate_request
from app.cinerie_store import CinerieStore
from app.editorial.models import DraftContent, DraftProvenance, EditorialDraft, SourceReference

_BODY = (
    "<p>O estudio confirmou nesta terca-feira a data de estreia da nova serie "
    "derivada, encerrando meses de especulacao entre os fas.</p>"
    "<h2>O que o trailer mostra</h2>"
    "<p>A equipe levou dois anos na producao, com filmagens em tres paises "
    "diferentes e um elenco mantido integralmente.</p>"
    "<h2>Elenco e producao</h2>"
    "<p>O comunicado oficial confirma o retorno de todos os protagonistas da "
    "temporada anterior, alem de tres novos nomes.</p>"
    "<h2>Data de estreia</h2>"
    "<p>A estreia esta marcada para o primeiro trimestre, simultaneamente em "
    "todos os territorios onde o servico opera.</p>"
)


def _draft_with(*, body=_BODY):
    output_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
    draft = EditorialDraft(
        draft_id="draft-teste-inline-media",
        article_id=1,
        event_key="evento-teste-inline-media",
        revision=2,
        content=DraftContent(
            title="Estudio confirma data de estreia da nova serie derivada",
            subtitle="O anuncio veio acompanhado do primeiro trailer completo.",
            body_html=body,
            summary=(
                "O estudio confirmou a data de estreia da nova serie derivada e "
                "divulgou o primeiro trailer completo, com o elenco confirmado."
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
            output_hash=output_hash,
            input_event_key="evento-teste-inline-media",
            input_revision=2,
            prompt_version="teste@1",
            created_at="2026-08-10T12:00:00+00:00",
        ),
        sources=[
            SourceReference(
                url="https://variety.com/2026/tv/news/exemplo-1234",
                domain="variety.com",
                name="Variety",
                is_primary=True,
            ),
        ],
    )
    draft.editorial_gate = SimpleNamespace(outcome="GATE_CLEAR")
    draft.factual_assessment = SimpleNamespace(critical_conflicts=[])
    return draft


class TestAttachInlineImages:
    def test_imagens_entram_apos_o_segundo_h2_em_diante(self):
        conversion = html_to_blocks(_BODY)
        ids = attach_inline_images(
            conversion,
            [
                {"mediaId": "77", "alt": "Elenco reunido no set"},
                {"mediaId": "78", "alt": "Bastidores da producao"},
            ],
        )
        assert ids == ["img1", "img2"]
        tipos = [b["type"] for b in conversion.blocks]
        # Nada de imagem antes do segundo h2: o primeiro trecho ja tem a capa.
        segundo_h2 = [i for i, b in enumerate(conversion.blocks)
                      if b["type"] == BLOCK_HEADING][1]
        assert BLOCK_IMAGE not in tipos[:segundo_h2]
        posicoes = [i for i, t in enumerate(tipos) if t == BLOCK_IMAGE]
        assert conversion.blocks[posicoes[0] - 1]["type"] == BLOCK_HEADING
        assert conversion.blocks[posicoes[1] - 1]["type"] == BLOCK_HEADING

    def test_sem_h2_suficiente_o_resto_vai_para_o_fim(self):
        conversion = html_to_blocks("<p>Paragrafo unico da materia, sem intertitulo nenhum no corpo inteiro.</p>")
        ids = attach_inline_images(conversion, [{"mediaId": "77", "alt": "Foto"}])
        assert ids == ["img1"]
        assert conversion.blocks[-1]["type"] == BLOCK_IMAGE

    def test_media_id_invalido_e_pulado(self):
        conversion = html_to_blocks(_BODY)
        ids = attach_inline_images(
            conversion,
            [{"mediaId": "", "alt": "x"}, {"mediaId": "-inv", "alt": "x"}],
        )
        assert ids == []
        assert all(b["type"] != BLOCK_IMAGE for b in conversion.blocks)


class TestRequestComMidiaConhecida:
    def test_media_inline_vira_bloco_image_e_valida_no_contrato(self):
        built = build_publication_request(
            _draft_with(),
            public_author_id="1",
            attribution_mode="newsroom",
            contract=local_identity(),
            media=[
                {"mediaId": "51", "intendedUse": "hero", "altSuggestion": "Capa"},
                {"mediaId": "52", "intendedUse": "inline", "altSuggestion": "Set"},
                {"mediaId": "53", "intendedUse": "inline", "altSuggestion": "Bastidores"},
            ],
        )
        report = validate_request(built.payload, identity=built.contract)
        assert report.ok, [f"{i.path}: {i.message}" for i in report.issues]

        image_blocks = [b for b in built.payload["blocks"] if b["type"] == BLOCK_IMAGE]
        assert [b["mediaRef"] for b in image_blocks] == ["52", "53"]
        # A capa NAO vira bloco de corpo; ela ja e a capa.
        assert all(b["mediaRef"] != "51" for b in image_blocks)
        media_ids = {m["mediaId"] for m in built.payload["media"]}
        assert {"51", "52", "53"} <= media_ids
        # `sourceList` continua fechando a materia.
        assert built.payload["blocks"][-1]["type"] == BLOCK_SOURCE_LIST

    def test_sem_midia_conhecida_o_pedido_sai_como_antes(self):
        built = build_publication_request(
            _draft_with(),
            public_author_id="1",
            attribution_mode="newsroom",
            contract=local_identity(),
        )
        assert all(b["type"] != BLOCK_IMAGE for b in built.payload["blocks"])
        assert built.payload["media"] == []


class TestMediaAssetsStore:
    def test_registro_e_leitura_capa_primeiro(self, tmp_path):
        store = CinerieStore(db_path=str(tmp_path / "app.db"))
        store.record_media_asset(
            source_cluster_id="ev1", article_id="34", media_id="60",
            source_url="https://cdn/a.jpg", alt="Corpo", role="inline",
            outcome="created",
        )
        store.record_media_asset(
            source_cluster_id="ev1", article_id="34", media_id="59",
            source_url="https://cdn/hero.jpg", alt="Capa", role="hero",
            outcome="created",
        )
        assets = store.list_media_assets("ev1")
        assert [a["media_id"] for a in assets] == ["59", "60"]
        assert assets[0]["role"] == "hero"
        store.close()

    def test_upsert_idempotente_nao_duplica(self, tmp_path):
        store = CinerieStore(db_path=str(tmp_path / "app.db"))
        for _ in range(3):
            store.record_media_asset(
                source_cluster_id="ev1", article_id="34", media_id="60",
                role="inline", outcome="unchanged",
            )
        assert len(store.list_media_assets("ev1")) == 1
        store.close()


class _FakeIngestClient:
    def __init__(self, fail_urls=()):
        self.fail_urls = set(fail_urls)
        self.calls = []

    def ingest_editorial_media(self, *, article_id, source_url, **kwargs):
        from app.cinerie.errors import MediaIngestError

        self.calls.append(source_url)
        if source_url in self.fail_urls:
            raise MediaIngestError("recusada", remote_code="unsupported_media_type")
        return SimpleNamespace(
            outcome="created",
            media_id=str(100 + len(self.calls)),
            content_hash="deadbeef",
            safe_log_fields=lambda: {},
        )


class TestIngestBodyImages:
    def _patch_download(self, monkeypatch):
        import app.safe_http as safe_http

        # Bytes DISTINTOS por URL: com bytes iguais o dedupe por hash colapsaria
        # tudo numa imagem so — que e o teste logo abaixo, nao estes.
        monkeypatch.setattr(
            safe_http, "safe_get",
            lambda url, max_bytes=0: SimpleNamespace(
                status_code=200, content=b"\xff\xd8\xff" + url.encode()
            ),
        )

    def test_exclui_a_capa_e_respeita_o_teto_de_3(self, monkeypatch):
        self._patch_download(monkeypatch)
        client = _FakeIngestClient()
        candidates = [
            {"url": f"https://cdn.example.com/img-{n}-1200x800.jpg", "alt": f"Foto {n}"}
            for n in range(6)
        ]
        result = ingest_body_images(
            article_id="34",
            image_candidates=candidates,
            exclude_urls=[candidates[0]["url"]],
            source_name="THR",
            client=client,
            media_api_key="chave",
        )
        assert len(result) == 3
        assert candidates[0]["url"] not in client.calls

    def test_falha_em_uma_imagem_nao_para_as_demais(self, monkeypatch):
        self._patch_download(monkeypatch)
        candidates = [
            {"url": f"https://cdn.example.com/img-{n}-1200x800.jpg", "alt": f"Foto {n}"}
            for n in range(4)
        ]
        client = _FakeIngestClient(fail_urls={candidates[1]["url"]})
        result = ingest_body_images(
            article_id="34",
            image_candidates=candidates,
            source_name="THR",
            client=client,
            media_api_key="chave",
        )
        assert len(result) == 3
        assert all(r["url"] != candidates[1]["url"] for r in result)

    def test_mesmos_bytes_da_capa_nao_reingerem(self, monkeypatch):
        # A fonte serve a MESMA foto em URLs diferentes (og:image vs corpo).
        # A exclusao por URL nao pega; a exclusao por hash dos bytes pega —
        # antes do upload. Foi exatamente o caso da materia 38.
        import hashlib

        import app.safe_http as safe_http

        payload = b"\xff\xd8\xffmesma-foto"
        monkeypatch.setattr(
            safe_http, "safe_get",
            lambda url, max_bytes=0: SimpleNamespace(status_code=200, content=payload),
        )
        client = _FakeIngestClient()
        result = ingest_body_images(
            article_id="38",
            image_candidates=[
                {"url": "https://cdn.example.com/foto-1200x800.jpg", "alt": "A"},
                {"url": "https://cdn.example.com/other-900x600.jpg", "alt": "B"},
            ],
            exclude_content_hashes=[f"sha256:{hashlib.sha256(payload).hexdigest()}"],
            source_name="THR",
            client=client,
            media_api_key="chave",
        )
        assert result == []
        assert client.calls == []

    def test_duas_urls_com_mesmos_bytes_ingerem_uma_vez(self, monkeypatch):
        import app.safe_http as safe_http

        payload = b"\xff\xd8\xffoutra-foto"
        monkeypatch.setattr(
            safe_http, "safe_get",
            lambda url, max_bytes=0: SimpleNamespace(status_code=200, content=payload),
        )
        client = _FakeIngestClient()
        result = ingest_body_images(
            article_id="38",
            image_candidates=[
                {"url": "https://cdn.example.com/foto-1200x800.jpg", "alt": "A"},
                {"url": "https://cdn.example.com/copia-900x600.jpg", "alt": "B"},
            ],
            source_name="THR",
            client=client,
            media_api_key="chave",
        )
        assert len(result) == 1

    def test_sem_credencial_nao_faz_nada(self):
        assert ingest_body_images(
            article_id="34",
            image_candidates=[{"url": "https://cdn.example.com/img-1200x800.jpg"}],
            source_name="THR",
            client=None,
            media_api_key="",
        ) == []
