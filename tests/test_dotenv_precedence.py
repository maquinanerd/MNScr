"""O ambiente real vence o `.env`, e nao o contrario.

`app/config.py` chamava `load_dotenv(override=True)`. Em desenvolvimento parece
conveniente; em producao inverte a hierarquia que todo orquestrador assume. O
EasyPanel, o Docker, o systemd e o CI passam configuracao por variavel de
ambiente — e com `override=True` um `.env` esquecido na imagem vence tudo isso.

O modo de falhar e mudo: nada acusa que a variavel injetada foi descartada. Uma
troca de chave, de banco ou de endpoint feita pelo painel simplesmente nao tem
efeito, e o log mostra o valor antigo como se fosse o pedido.

A propria suite ja convivia com isso: `tests/test_config_and_security.py` mantem
uma fixture que neutraliza `load_dotenv`, porque senao o arquivo vencia o
`monkeypatch.setenv` dos testes. Precisar desligar o carregador para conseguir
testar e o sintoma.

O teste roda em SUBPROCESSO porque a precedencia so acontece na importacao do
modulo, que dentro do pytest ja aconteceu. O `dotenv_path` e fixado no
subprocesso porque `find_dotenv` procura a partir do arquivo de `config.py`, nao
do diretorio de trabalho — sem isso o teste nao encontraria arquivo nenhum e
passaria por vacuidade, medindo nada.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SONDA = textwrap.dedent(
    """
    import os
    import dotenv

    _original = dotenv.load_dotenv

    def _com_caminho_fixo(*args, **kwargs):
        # Preserva o `override` que a config passa: e ele que esta sob teste.
        kwargs.setdefault("dotenv_path", os.path.join(os.getcwd(), ".env"))
        return _original(*args, **kwargs)

    dotenv.load_dotenv = _com_caminho_fixo

    from app import config

    print("VALOR=" + (config.PUBLISHER_DOMAIN or "<vazio>"))
    """
).strip()


def _sondar(tmp_path, *, no_arquivo, no_ambiente):
    (tmp_path / ".env").write_text(f"PUBLISHER_DOMAIN={no_arquivo}\n", encoding="utf-8")
    (tmp_path / "sonda.py").write_text(SONDA, encoding="utf-8")

    ambiente = {
        "PATH": os.environ.get("PATH", ""),
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
        "PYTHONPATH": REPO_ROOT,
        "PYTHONIOENCODING": "utf-8",
    }
    if no_ambiente is not None:
        ambiente["PUBLISHER_DOMAIN"] = no_ambiente

    resultado = subprocess.run(
        [sys.executable, str(tmp_path / "sonda.py")],
        cwd=str(tmp_path),
        env=ambiente,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert resultado.returncode == 0, f"a sonda falhou:\n{resultado.stdout}\n{resultado.stderr}"
    for linha in resultado.stdout.splitlines():
        if linha.startswith("VALOR="):
            return linha.split("=", 1)[1].strip()
    raise AssertionError(f"a sonda nao imprimiu VALOR=:\n{resultado.stdout}")


def test_variavel_de_ambiente_vence_o_arquivo(tmp_path):
    """O caso de producao: o painel injeta, e o `.env` da imagem nao manda nele."""
    assert _sondar(tmp_path, no_arquivo="arquivo.example", no_ambiente="ambiente.example") == (
        "ambiente.example"
    ), (
        "o `.env` sobrescreveu a variavel injetada pelo ambiente: uma troca de "
        "configuracao feita no painel nao teria efeito, e nada acusaria isso"
    )


def test_arquivo_preenche_o_que_o_ambiente_deixou_vazio(tmp_path):
    """O `.env` continua util: ele COMPLETA o ambiente, so nao manda nele."""
    assert _sondar(tmp_path, no_arquivo="arquivo.example", no_ambiente=None) == "arquivo.example"
