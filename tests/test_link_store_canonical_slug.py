"""O link interno tem de apontar para o endereco que o Cinerie publicou.

`887d562` fechou a porta certa: o link_store recusa o que nao for URL publica, e
em modo local nada e gravado. Mas a origem do valor continuou errada — o pipeline
oferecia `result.artifact_path or draft.draft_id`, e nenhum dos dois e endereco
de nada.

O resultado e um empate ruim: a barreira recusa, o link interno nunca existe, e o
unico sinal e um WARNING por artigo. As materias do Cinerie ficam sem link entre
si indefinidamente, e nada no sistema chama isso de defeito.

O endereco de verdade so aparece na RESPOSTA da publicacao, como `canonicalSlug`.
Ele ja e lido em `app/cinerie/outcomes.py` e ja e gravado no registro — so nunca
chegou ate aqui.
"""

from __future__ import annotations

import pytest

from app import pipeline


class TestPublicUrlFromPublication:
    """A montagem do endereco, isolada de todo o resto do pipeline."""

    def test_slug_mais_base_viram_url_publica(self):
        assert (
            pipeline._public_url_for_slug(
                "star-wars-personagens-inteligentes",
                base="https://cinerie.example/pt/",
            )
            == "https://cinerie.example/pt/star-wars-personagens-inteligentes"
        )

    def test_barra_sobrando_nao_duplica(self):
        assert (
            pipeline._public_url_for_slug(
                "/star-wars/", base="https://cinerie.example/pt"
            )
            == "https://cinerie.example/pt/star-wars"
        )

    @pytest.mark.parametrize(
        "slug, base",
        [
            ("", "https://cinerie.example/pt/"),
            ("   ", "https://cinerie.example/pt/"),
            (None, "https://cinerie.example/pt/"),
            ("star-wars", ""),
            ("star-wars", None),
            # Base sem esquema nao vira endereco: preferimos nao gravar a gravar
            # algo que o link_store recusaria de qualquer forma.
            ("star-wars", "cinerie.example"),
        ],
    )
    def test_sem_material_suficiente_devolve_nada(self, slug, base):
        """Faltando slug ou base, nao se inventa endereco.

        Gravar um palpite seria pior que nao gravar: o link_store alimenta o
        <a href> da proxima materia, entao um endereco errado vira link quebrado
        publicado.
        """
        assert pipeline._public_url_for_slug(slug, base=base) is None

    def test_o_que_sai_e_aceito_pelo_link_store(self):
        """A ponta final: o valor produzido aqui passa pela barreira de la."""
        from app.link_store import _is_public_url

        url = pipeline._public_url_for_slug(
            "star-wars", base="https://cinerie.example/pt/"
        )
        assert _is_public_url(url)
