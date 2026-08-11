"""Protocolo de categorias em tres niveis.

Os valores aceitos NAO sao escolha do MNScr: eles espelham
`editorial-publication-request-v1` do Cinerie. Estes testes existem para que uma
divergencia apareca aqui, e nao num `422 BLOCKED` em producao.
"""

import pytest

from app.taxonomy import (
    CONTENT_TYPES,
    ENTITY_KINDS,
    ENTITY_RELATIONS,
    SECTION_CINEMA,
    SECTION_SERIES,
    EntityProposal,
    build_taxonomy_proposal,
    infer_section,
    map_content_type,
    to_entity_links,
    validate_proposal,
)


def ent(name, kind, relation="mentioned", **kw):
    return EntityProposal(name=name, kind=kind, relation=relation, **kw)


# ===========================================================================
# Espelho do contrato
# ===========================================================================


def test_content_types_espelham_o_contrato():
    assert set(CONTENT_TYPES) == {
        "news", "feature", "review", "guide", "list", "interview", "evergreen"
    }


def test_entity_kinds_espelham_o_contrato():
    assert set(ENTITY_KINDS) == {
        "movie", "tv", "season", "episode", "person", "character", "franchise"
    }


def test_relations_espelham_o_contrato():
    assert set(ENTITY_RELATIONS) == {
        "primary_subject", "secondary_subject", "mentioned",
        "reviewed", "recommended", "compared",
    }


# ===========================================================================
# Nivel 1
# ===========================================================================


@pytest.mark.parametrize(
    "article_type,esperado",
    [("news", "news"), ("list", "list"), ("guide", "guide"), ("analysis", "feature")],
)
def test_tipos_do_mnscr_mapeiam_para_o_contrato(article_type, esperado):
    tipo, avisos = map_content_type(article_type)
    assert tipo == esperado
    assert avisos == []


def test_analysis_vira_feature_porque_analysis_nao_existe_no_contrato():
    assert map_content_type("analysis")[0] == "feature"


def test_tipo_desconhecido_cai_em_news_com_aviso():
    tipo, avisos = map_content_type("podcast")
    assert tipo == "news"
    assert any("TAXONOMY_UNKNOWN_ARTICLE_TYPE" in a for a in avisos)


def test_resenha_e_promovida_a_review_com_aviso_de_origem_terceira():
    tipo, avisos = map_content_type("analysis", title="Crítica de Duna: Parte Dois")
    assert tipo == "review"
    # O aviso e o ponto: a Cinerie assinaria resenha propria sobre texto de
    # terceiro, e isso precisa ficar visivel para um humano.
    assert "TAXONOMY_REVIEW_FROM_THIRD_PARTY_SOURCE" in avisos


def test_noticia_sobre_uma_critica_alheia_nao_vira_review():
    tipo, _ = map_content_type("news", title="Filme estreia em julho e diretor comenta")
    assert tipo == "news"


def test_lista_nunca_e_promovida_a_review():
    tipo, _ = map_content_type("list", title="10 melhores críticas de 2026")
    assert tipo == "list"


# ===========================================================================
# Nivel 2 — derivado do nivel 3, nunca adivinhado
# ===========================================================================


def test_secao_vem_da_entidade_principal():
    secao, _ = infer_section([ent("Duna", "movie", "primary_subject"), ent("Timothee", "person")])
    assert secao == SECTION_CINEMA


def test_serie_como_assunto_principal_da_secao_series():
    secao, _ = infer_section([ent("Severance", "tv", "primary_subject")])
    assert secao == SECTION_SERIES


def test_temporada_e_episodio_tambem_sao_series():
    assert infer_section([ent("T2", "season", "primary_subject")])[0] == SECTION_SERIES
    assert infer_section([ent("E5", "episode", "primary_subject")])[0] == SECTION_SERIES


def test_pessoa_como_assunto_principal_nao_decide_secao_sozinha():
    """Um ator faz filme e serie. Decidir pelo ator seria chute."""
    secao, _ = infer_section([ent("Alan Ritchson", "person", "primary_subject")])
    assert secao is None


def test_pessoa_principal_com_filme_citado_resolve_pela_maioria():
    secao, _ = infer_section(
        [ent("Alan Ritchson", "person", "primary_subject"), ent("Superman", "movie")]
    )
    assert secao == SECTION_CINEMA


def test_empate_entre_cinema_e_series_fica_indeciso():
    secao, avisos = infer_section([ent("Duna", "movie"), ent("Severance", "tv")])
    assert secao is None
    assert any("empate" in a for a in avisos)


def test_sem_entidades_a_secao_fica_indecisa():
    secao, avisos = infer_section([])
    assert secao is None
    assert any("sem_entidades" in a for a in avisos)


# ===========================================================================
# Nivel 3 — resolvido x nao resolvido
# ===========================================================================


def test_entidade_sem_id_nunca_vira_entity_link():
    """O contrato exige entityId real. Id inventado liga a materia a obra errada."""
    proposta = build_taxonomy_proposal(
        article_type="news", entities=[ent("Superman", "movie", "primary_subject")]
    )
    assert proposta.unresolved_entities
    assert to_entity_links(proposta) == []


def test_entidade_resolvida_vira_entity_link_no_formato_do_contrato():
    proposta = build_taxonomy_proposal(
        article_type="news",
        entities=[ent("Superman", "movie", "primary_subject", confidence=0.87, entity_id="mv-1")],
    )
    assert to_entity_links(proposta) == [
        {
            "entityKind": "movie",
            "entityId": "mv-1",
            "relation": "primary_subject",
            "confidence": 0.87,
        }
    ]


def test_entidades_nao_resolvidas_ficam_registradas_como_aviso():
    proposta = build_taxonomy_proposal(
        article_type="news", entities=[ent("Superman", "movie"), ent("Batman", "character")]
    )
    assert any("TAXONOMY_ENTITIES_UNRESOLVED:2" in a for a in proposta.warnings)


def test_kind_fora_do_contrato_e_descartado_com_aviso():
    proposta = build_taxonomy_proposal(
        article_type="news", entities=[ent("Algo", "videogame"), ent("Duna", "movie")]
    )
    assert [e.name for e in proposta.entities] == ["Duna"]
    assert any("TAXONOMY_ENTITY_KIND_INVALID" in a for a in proposta.warnings)


# ===========================================================================
# Proposta completa
# ===========================================================================


def test_caminho_legivel_dos_tres_niveis():
    proposta = build_taxonomy_proposal(
        article_type="news",
        title="Superman ganha data",
        entities=[ent("Superman", "movie", "primary_subject")],
    )
    assert proposta.label == "Notícias / Cinema / Superman"


def test_proposta_valida_nao_tem_problemas():
    proposta = build_taxonomy_proposal(
        article_type="list", entities=[ent("Severance", "tv", "primary_subject", entity_id="tv-9")]
    )
    assert validate_proposal(proposta) == []
    assert proposta.content_type == "list"
    assert proposta.section == SECTION_SERIES


def test_validacao_recusa_relation_fora_do_contrato():
    from app.taxonomy import TaxonomyProposal

    proposta = TaxonomyProposal(
        content_type="news", entities=(ent("X", "movie", "destaque_da_home"),)
    )
    assert any("relation" in issue for issue in validate_proposal(proposta))


def test_validacao_recusa_content_type_inventado():
    from app.taxonomy import TaxonomyProposal

    issues = validate_proposal(TaxonomyProposal(content_type="noticias"))
    assert any("nao existe no contrato" in issue for issue in issues)


def test_validacao_recusa_confianca_fora_da_faixa():
    from app.taxonomy import TaxonomyProposal

    proposta = TaxonomyProposal(content_type="news", entities=(ent("X", "movie", confidence=1.5),))
    assert any("confidence" in issue for issue in validate_proposal(proposta))
