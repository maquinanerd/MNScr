"""A guarda de vazamento so funciona se a lista dela contiver HOSTS.

`MNSCR_OWN_CMS_DOMAINS` responde a uma pergunta unica: uma URL encontrada no
corpo do draft aponta para o NOSSO CMS ou para o site da fonte? A distincao
existe porque o legado do MNScr e boa parte das fontes que ele le (ScreenRant,
ComicBook, MovieWeb) rodam o mesmo WordPress e servem imagem pelo mesmo caminho.

A comparacao do outro lado ja e feita por host (`_is_own_host(_host_of(url))`).
Se a CONFIGURACAO guardar uma URL inteira, os dois lados nunca se encontram.

E o modo de falhar e o pior possivel: a lista fica NAO-VAZIA, entao o aviso de
startup — que existe exatamente para denunciar guarda desligada — nao dispara.
Uma lista com lixo e mais perigosa que uma lista vazia, porque ela silencia o
proprio alarme.
"""

from __future__ import annotations

import pytest

from app.config import _domain_list


@pytest.mark.parametrize(
    "entrada",
    [
        "https://www.casa-editorial.example",
        "http://www.casa-editorial.example",
        "https://casa-editorial.example/",
        "https://www.casa-editorial.example/wp-content/uploads/2026/01/x.jpg",
        "www.casa-editorial.example",
        "casa-editorial.example",
        "  CASA-EDITORIAL.EXAMPLE  ",
        ".casa-editorial.example",
    ],
)
def test_url_ou_host_viram_o_mesmo_host(entrada):
    """Todas estas formas descrevem o mesmo dominio e precisam convergir.

    A primeira e a que mais importa: e exatamente o valor que a pendencia manda
    copiar de `WORDPRESS_SITE_URL`, ou seja, a forma que uma pessoa realmente
    cola no `.env`.
    """
    assert _domain_list(entrada) == ["casa-editorial.example"], (
        f"{entrada!r} nao virou host utilizavel: {_domain_list(entrada)}"
    )


def test_lista_com_varias_formas_nao_duplica():
    """A mesma casa escrita de tres jeitos e uma casa so."""
    bruto = "https://www.casa-editorial.example, casa-editorial.example ; www.casa-editorial.example"
    assert _domain_list(bruto) == ["casa-editorial.example"]


def test_porta_e_credencial_nao_sobrevivem():
    """O que chega ao host precisa ser comparavel com o host de uma URL do corpo."""
    assert _domain_list("https://user:senha@www.casa-editorial.example:8443/x") == [
        "casa-editorial.example"
    ]


def test_entrada_sem_host_nao_vira_entrada_fantasma():
    """Lixo nao pode virar item: item nao-vazio silencia o aviso de startup."""
    assert _domain_list("https://") == []
    assert _domain_list("   ") == []
    assert _domain_list(",,,;") == []


def test_a_guarda_reconhece_a_url_do_corpo_com_a_config_em_forma_de_url():
    """A prova que liga as duas pontas.

    Este e o cenario real: alguem cola a URL do site no `.env` e a guarda passa a
    nao reconhecer nem a propria casa.
    """
    from app.editorial.models import own_cms_upload_urls

    corpo = (
        '<p>Nosso: <img src="https://www.casa-editorial.example/wp-content/uploads/a.jpg"></p>'
        '<p>Da fonte: <img src="https://screenrant.com/wp-content/uploads/b.jpg"></p>'
    )
    dominios = _domain_list("https://www.casa-editorial.example")

    encontrados = own_cms_upload_urls(corpo, own_domains=dominios)

    assert encontrados == ["https://www.casa-editorial.example/wp-content/uploads/a.jpg"], (
        "a guarda precisa pegar a nossa imagem e deixar a da fonte em paz; "
        f"veio {encontrados}"
    )
