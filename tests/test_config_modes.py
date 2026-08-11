from __future__ import annotations

import logging

import pytest

from app import config, pipeline


def test_tabela_de_configuracao_recusa_autopublicacao_incompleta_em_producao():
    states = {
        "GEMINI_API_KEYS": True,
        "MNSCR_OWN_CMS_DOMAINS": True,
        "PAYLOAD_INTERNAL_SERVICE_URL": True,
        "MNSCR_PAYLOAD_API_KEY": False,
        "MNSCR_PUBLIC_AUTHOR_ID": True,
        "CINERIE_PUBLIC_BASE_URL": False,
        "MNSCR_DELIVERY_MODE": True,
    }

    with pytest.raises(ValueError) as exc:
        config.validate_operation_mode_config(
            mode="autopublish", environment="production", states=states
        )

    message = str(exc.value)
    assert "MNSCR_PAYLOAD_API_KEY" in message
    assert "CINERIE_PUBLIC_BASE_URL" in message


def test_tabela_declara_obrigatorias_por_modo():
    table = config.OPERATION_MODE_REQUIREMENTS

    assert "GEMINI_API_KEYS" in table["local"]
    assert "MNSCR_OWN_CMS_DOMAINS" in table["local"]
    assert "PAYLOAD_INTERNAL_SERVICE_URL" in table["cinerie_delivery"]
    assert "MNSCR_PAYLOAD_API_KEY" in table["cinerie_delivery"]
    assert "MNSCR_PUBLIC_AUTHOR_ID" in table["autopublish"]
    assert "CINERIE_PUBLIC_BASE_URL" in table["autopublish"]


def test_funcionalidade_opcional_pulada_emite_estado_sem_valor(caplog):
    segredo = "https://valor-secreto.example/nao-logar"

    with caplog.at_level(logging.INFO):
        assert pipeline._public_url_for_slug("materia", base="") is None
        assert pipeline._public_url_for_slug("materia", base=segredo.replace("https://", "ftp://")) is None

    assert "internal_link_skipped" in caplog.text
    assert "CINERIE_PUBLIC_BASE_URL" in caplog.text
    assert "state=empty" in caplog.text
    assert "state=invalid" in caplog.text
    assert "valor-secreto" not in caplog.text
