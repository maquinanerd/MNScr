"""Identidade do contrato do Cinerie, lida dos artefatos copiados.

O MNScr nao pode importar o TypeScript do Screen-App. O que atravessa a
fronteira e o **JSON Schema gerado** mais um hash — e o hash e o que transforma
"acho que estamos na mesma versao" em uma checagem.

Duas decisoes carregam peso aqui:

* ``schemaHash`` e **recalculado** dos bytes do arquivo a cada carga, nunca
  fixado como literal. Um literal desatualizado transformaria toda publicacao em
  ``BLOCKED`` e ninguem saberia por que. O valor gravado em ``SOURCE.json``
  registra o que foi copiado; quem manda e o arquivo.
* o hash e calculado sobre os **bytes do schema**, e nao sobre uma
  re-serializacao em Python. O Screen-App gravou aqui exatamente a serializacao
  canonica que ele mesmo usa; reimplementar a canonicalizacao em Python criaria
  uma segunda maneira de ordenar chaves e as duas divergiriam em silencio.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Final, List, Optional

from .errors import ContractArtifactError

CONTRACT_NAME: Final[str] = "editorial-publication-request-v1"

#: Diretorio dos artefatos copiados do Screen-App. Relativo a raiz do repo.
CONTRACTS_DIR: Final[Path] = Path(__file__).resolve().parents[2] / "contracts" / "cinerie"

SCHEMA_FILE: Final[str] = f"{CONTRACT_NAME}.schema.json"
MANIFEST_FILE: Final[str] = "contract-manifest.json"
SOURCE_FILE: Final[str] = "SOURCE.json"
SEO_POLICY_FILE: Final[str] = "seo-policy.json"
FIXTURES_FILE: Final[str] = "fixtures/publication-fixtures.json"


def _read_bytes(name: str) -> bytes:
    path = CONTRACTS_DIR / name
    try:
        return path.read_bytes()
    except OSError as exc:
        raise ContractArtifactError(
            f"artefato contratual ausente ou ilegivel: contracts/cinerie/{name}"
        ) from exc


def _read_json(name: str) -> Any:
    raw = _read_bytes(name)
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractArtifactError(
            f"artefato contratual invalido: contracts/cinerie/{name}"
        ) from exc


def compute_schema_hash(raw: bytes) -> str:
    """``sha256:<hex>`` sobre os bytes do schema, no formato do Screen-App."""
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


@dataclass(frozen=True)
class ContractIdentity:
    """O que o MNScr declara e confere: nome, versao e hash do schema."""

    contract_name: str
    contract_version: str
    schema_hash: str
    compatibility: Optional[str] = None
    direction: Optional[str] = None
    source_commit: Optional[str] = None

    def as_request_fields(self) -> Dict[str, str]:
        """Os tres campos exatos que entram no corpo do pedido."""
        return {
            "contractName": self.contract_name,
            "contractVersion": self.contract_version,
            "schemaHash": self.schema_hash,
        }

    def truncated_hash(self) -> str:
        """Hash abreviado para log: identifica sem despejar 64 hexadecimais."""
        return self.schema_hash[:19]


@lru_cache(maxsize=1)
def load_schema() -> Dict[str, Any]:
    """O JSON Schema canonico, como objeto Python."""
    schema = _read_json(SCHEMA_FILE)
    if not isinstance(schema, dict):
        raise ContractArtifactError("JSON Schema do Cinerie nao e um objeto")
    return schema


@lru_cache(maxsize=1)
def schema_bytes() -> bytes:
    return _read_bytes(SCHEMA_FILE)


@lru_cache(maxsize=1)
def load_manifest() -> List[Dict[str, Any]]:
    manifest = _read_json(MANIFEST_FILE)
    contracts = manifest.get("contracts") if isinstance(manifest, dict) else None
    if not isinstance(contracts, list):
        raise ContractArtifactError("manifesto de contratos sem lista `contracts`")
    return contracts


@lru_cache(maxsize=1)
def load_source_record() -> Dict[str, Any]:
    record = _read_json(SOURCE_FILE)
    if not isinstance(record, dict):
        raise ContractArtifactError("SOURCE.json do contrato nao e um objeto")
    return record


def manifest_entry(contract_name: str = CONTRACT_NAME) -> Dict[str, Any]:
    for entry in load_manifest():
        if isinstance(entry, dict) and entry.get("contractName") == contract_name:
            return entry
    raise ContractArtifactError(f"contrato ausente no manifesto: {contract_name}")


@lru_cache(maxsize=1)
def local_identity() -> ContractIdentity:
    """Identidade LOCAL, verificada: hash do arquivo == hash do manifesto.

    A conferencia acontece na carga, e nao no envio, porque um artefato
    inconsistente e defeito de repositorio: descobri-lo com a materia pronta
    seria descobrir tarde.
    """
    entry = manifest_entry()
    computed = compute_schema_hash(schema_bytes())
    declared = str(entry.get("schemaHash") or "")

    if computed != declared:
        raise ContractArtifactError(
            "hash do JSON Schema local nao confere com o manifesto "
            f"(arquivo {computed[:19]}..., manifesto {declared[:19]}...)"
        )

    version = str(entry.get("contractVersion") or "")
    if not version:
        raise ContractArtifactError("manifesto sem contractVersion")

    source = load_source_record()
    declared_in_source = str(source.get("schemaHash") or "")
    if declared_in_source and declared_in_source != computed:
        raise ContractArtifactError(
            "SOURCE.json registra um schemaHash diferente do arquivo copiado; "
            "o pacote contratual esta inconsistente"
        )

    return ContractIdentity(
        contract_name=CONTRACT_NAME,
        contract_version=version,
        schema_hash=computed,
        compatibility=entry.get("compatibility"),
        direction=entry.get("direction"),
        source_commit=source.get("sourceCommitShort") or source.get("sourceCommit"),
    )


@lru_cache(maxsize=1)
def load_seo_policy() -> Dict[str, Any]:
    policy = _read_json(SEO_POLICY_FILE)
    if not isinstance(policy, dict):
        raise ContractArtifactError("politica de SEO do Cinerie nao e um objeto")
    return policy


def load_fixtures() -> Dict[str, Any]:
    fixtures = _read_json(FIXTURES_FILE)
    if not isinstance(fixtures, dict):
        raise ContractArtifactError("fixtures de publicacao nao sao um objeto")
    return fixtures


__all__ = [
    "CONTRACTS_DIR",
    "CONTRACT_NAME",
    "ContractIdentity",
    "compute_schema_hash",
    "load_fixtures",
    "load_manifest",
    "load_schema",
    "load_seo_policy",
    "load_source_record",
    "local_identity",
    "manifest_entry",
    "schema_bytes",
]
