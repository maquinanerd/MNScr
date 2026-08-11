"""Validacao de midia editorial, sem rede.

O contrato manda REFERENCIAR midia ja aprovada no CMS, nunca enviar bytes. O que
sobra para o MNScr e decidir quais candidatos merecem virar referencia — e
garantir que exista exatamente UMA imagem de destaque.
"""

import socket

import pytest

from app.media_validation import (
    ALT_MAX_CHARS,
    DELIVERABLE_EXTENSIONS,
    INTENDED_USES,
    MIN_HERO_EDGE_PX,
    normalize_alt,
    validate_candidate,
    validate_draft_media,
    validate_media_set,
)

BOA = "https://img.example.com/materia/superman-primeiro-trailer.jpg"


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _blocked(*args, **kwargs):
        raise AssertionError("A validacao de midia nao pode tocar a rede (SSRF)")

    monkeypatch.setattr(socket, "socket", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)


class Candidato:
    def __init__(self, url, alt=None, caption=None):
        self.source_url = url
        self.original_alt = alt
        self.original_caption = caption


# ===========================================================================
# Espelho do CMS
# ===========================================================================


def test_formatos_espelham_o_endpoint_de_midia_do_cms():
    assert DELIVERABLE_EXTENSIONS == {"jpg", "jpeg", "png", "webp", "avif"}


def test_usos_espelham_o_contrato():
    assert set(INTENDED_USES) == {"hero", "inline", "gallery", "social"}


# ===========================================================================
# Candidato isolado
# ===========================================================================


def test_candidato_bom_passa():
    v = validate_candidate(BOA, intended_use="hero", alt="Superman voando sobre Metropolis")
    assert v.usable
    assert v.alt_suggestion == "Superman voando sobre Metropolis"


def test_url_relativa_e_recusada():
    assert not validate_candidate("/wp-content/uploads/a.jpg").usable


def test_data_uri_e_recusada():
    """Bytes embutidos sao exatamente o que o contrato recusa."""
    v = validate_candidate("data:image/png;base64,iVBORw0KGgo=")
    assert not v.usable
    assert "MEDIA_URL_SCHEME_INVALID" in v.blocking_codes


def test_formato_nao_entregavel_e_recusado():
    v = validate_candidate("https://img.example.com/a.gif")
    assert "MEDIA_FORMAT_NOT_DELIVERABLE" in v.blocking_codes


def test_svg_e_recusado():
    assert not validate_candidate("https://img.example.com/a.svg").usable


def test_url_sem_extensao_passa_com_aviso():
    """CDN com transformacao e comum; nao e defeito, mas o formato fica incerto."""
    v = validate_candidate("https://cdn.example.com/image/abc123")
    assert v.usable
    assert "MEDIA_FORMAT_UNKNOWN" in v.warning_codes


@pytest.mark.parametrize(
    "url",
    [
        "https://a.example/pixel/track.png",
        "https://a.example/img/1x1.png",
        "https://a.example/assets/logo.png",
        "https://a.example/u/avatar.jpg",
        "https://a.example/social-icon.png",
        "https://a.example/placeholder.jpg",
    ],
)
def test_imagem_nao_editorial_e_recusada(url):
    v = validate_candidate(url)
    assert "MEDIA_NOT_EDITORIAL" in v.blocking_codes


def test_capa_pequena_declarada_na_url_e_recusada():
    v = validate_candidate("https://img.example.com/a-320x180.jpg", intended_use="hero")
    assert "MEDIA_HERO_TOO_SMALL" in v.blocking_codes


def test_imagem_pequena_serve_como_inline():
    """O limite de tamanho existe para a CAPA, que e o que aparece no card social."""
    v = validate_candidate("https://img.example.com/a-320x180.jpg", intended_use="inline")
    assert v.usable


def test_capa_grande_o_bastante_passa():
    tamanho = MIN_HERO_EDGE_PX + 100
    v = validate_candidate(
        f"https://img.example.com/a-{tamanho}x{tamanho}.jpg", intended_use="hero", alt="Capa"
    )
    assert v.usable


def test_uso_fora_do_contrato_e_recusado():
    assert "MEDIA_INTENDED_USE_INVALID" in validate_candidate(BOA, intended_use="destaque").blocking_codes


def test_alt_ausente_e_aviso_nao_bloqueio():
    v = validate_candidate(BOA)
    assert v.usable
    assert "MEDIA_ALT_MISSING" in v.warning_codes


def test_legenda_serve_de_alt_quando_nao_ha_alt():
    v = validate_candidate(BOA, caption="Cena do filme divulgada pelo estudio")
    assert v.alt_suggestion == "Cena do filme divulgada pelo estudio"


def test_alt_e_truncado_ao_limite_do_contrato():
    assert len(normalize_alt("x" * 500)) == ALT_MAX_CHARS


def test_alt_curto_demais_e_ignorado():
    assert normalize_alt("ab", None) is None


def test_alt_nunca_e_inventado():
    assert normalize_alt(None, "") is None


# ===========================================================================
# Conjunto — a regra da capa
# ===========================================================================


def test_conjunto_sem_capa_e_apontado():
    v = [validate_candidate(BOA, intended_use="inline", alt="Uma cena")]
    assert "MEDIA_HERO_MISSING" in [i.code for i in validate_media_set(v)]


def test_duas_capas_e_ausencia_de_escolha():
    v = [
        validate_candidate("https://a.example/1.jpg", intended_use="hero", alt="Primeira"),
        validate_candidate("https://a.example/2.jpg", intended_use="hero", alt="Segunda"),
    ]
    assert "MEDIA_HERO_AMBIGUOUS" in [i.code for i in validate_media_set(v)]


def test_uma_capa_e_o_estado_correto():
    v = [
        validate_candidate("https://a.example/1.jpg", intended_use="hero", alt="Capa"),
        validate_candidate("https://a.example/2.jpg", intended_use="inline", alt="Apoio"),
    ]
    assert validate_media_set(v) == []


def test_candidato_inutilizavel_nao_conta_como_capa():
    v = [validate_candidate("https://a.example/logo.png", intended_use="hero")]
    assert "MEDIA_HERO_MISSING" in [i.code for i in validate_media_set(v)]


# ===========================================================================
# Draft inteiro
# ===========================================================================


def test_featured_url_marca_a_capa():
    capa = "https://a.example/capa.jpg"
    candidatos = [Candidato(capa, alt="A capa"), Candidato("https://a.example/b.jpg", alt="Outra")]
    validacoes, problemas = validate_draft_media(candidatos, featured_url=capa)
    heroes = [v for v in validacoes if v.intended_use == "hero"]
    assert len(heroes) == 1
    assert heroes[0].source_url == capa
    assert problemas == []


def test_capa_fora_da_lista_e_acrescentada_e_nao_promove_a_primeira():
    """Promover a primeira da lista seria decisao editorial por ordenacao."""
    candidatos = [Candidato("https://a.example/b.jpg", alt="Uma imagem qualquer")]
    validacoes, _ = validate_draft_media(candidatos, featured_url="https://a.example/capa.jpg")
    heroes = [v for v in validacoes if v.intended_use == "hero"]
    assert len(heroes) == 1
    assert heroes[0].source_url == "https://a.example/capa.jpg"


def test_draft_sem_capa_nenhuma_e_apontado():
    _, problemas = validate_draft_media([Candidato(BOA, alt="Só apoio")], featured_url=None)
    assert "MEDIA_HERO_MISSING" in [i.code for i in problemas]


def test_draft_sem_midia_alguma_nao_quebra():
    validacoes, problemas = validate_draft_media([], featured_url=None)
    assert validacoes == []
    assert "MEDIA_HERO_MISSING" in [i.code for i in problemas]


def test_require_hero_desligado_nao_reclama_de_capa():
    _, problemas = validate_draft_media([], featured_url=None, require_hero=False)
    assert problemas == []
