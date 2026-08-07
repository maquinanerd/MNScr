"""Validacao do pedido: varredura de campos, JSON Schema e refinamentos.

Ordem fixa, e a ordem e argumento:

1. **campos recusados** — a mensagem nomeia o campo e diz por que ele nao entra;
   ``additionalProperties: false`` recusaria o mesmo campo com um texto que nao
   ensina nada;
2. **JSON Schema** — a estrutura, com a biblioteca ``jsonschema``. Nao ha
   verificador proprio aqui: um segundo leitor do mesmo schema seria uma segunda
   interpretacao dele;
3. **refinamentos** — o que o JSON Schema nao expressa (ver ``refinements.py``).

Tudo isso roda **antes de qualquer socket**. Um pedido malformado nao custa
conexao, nao consome teto de publicacao e nao aparece na auditoria do Cinerie.

Em falha nada e enviado, o erro e classificado como PERMANENTE de construcao e
o que se registra sao os **caminhos** dos campos invalidos — nunca o corpo,
nunca o texto inedito.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Dict, List, Mapping, Sequence

from .contract import ContractIdentity, load_schema, local_identity
from .errors import ForbiddenFieldError, SchemaValidationError
from .forbidden import find_forbidden_key
from .refinements import ContractIssue, check_request, describe_value

logger = logging.getLogger(__name__)

#: Teto de problemas relatados. Um pedido estruturalmente errado pode produzir
#: centenas de issues, e as primeiras ja dizem o que houve.
MAX_REPORTED_ISSUES: int = 20


@lru_cache(maxsize=1)
def _validator() -> Any:
    from jsonschema import Draft202012Validator

    schema = load_schema()
    # `check_schema` roda uma vez por processo: um schema invalido e defeito do
    # pacote contratual, e descobri-lo no primeiro envio seria descobrir tarde.
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


@dataclass(frozen=True)
class ValidationReport:
    """O que a validacao encontrou, em forma segura para log."""

    ok: bool
    issues: List[ContractIssue]
    forbidden_key: str | None = None
    forbidden_group: str | None = None

    @property
    def paths(self) -> List[str]:
        return [issue.path for issue in self.issues]

    @property
    def details(self) -> List[str]:
        """Caminho, regra violada e a forma do valor recebido — um por problema."""
        return [str(issue) for issue in self.issues[:MAX_REPORTED_ISSUES]]

    def summary(self) -> str:
        if self.ok:
            return "pedido valido"
        return "; ".join(str(issue) for issue in self.issues[:MAX_REPORTED_ISSUES])


def _schema_issues(payload: Any) -> List[ContractIssue]:
    issues: List[ContractIssue] = []
    for error in sorted(_validator().iter_errors(payload), key=lambda item: list(item.absolute_path)):
        path = ".".join(str(part) for part in error.absolute_path) or "(raiz)"
        # `error.message` do jsonschema pode ecoar o valor recebido. Para
        # `additionalProperties` a mensagem nomeia a chave (util e seguro); para
        # os demais, ficamos com o validador que falhou.
        if error.validator == "additionalProperties":
            message = error.message
        else:
            message = f"{error.validator}: {error.validator_value!r}"
        # A FORMA do valor recusado acompanha o caminho. Sem ela,
        # `seo.focusKeyphrase: minLength: 1` nao distingue campo vazio de campo
        # nulo de campo so com espacos — tres causas com tres consertos
        # diferentes. `describe_value` limita o que sai (ver `refinements`).
        issues.append(
            ContractIssue(path=path, message=message, received=describe_value(error.instance))
        )
    return issues


def check_identity(payload: Any, identity: ContractIdentity) -> List[ContractIssue]:
    """Nome, versao e hash declarados batem com o contrato local?

    Sem esta checagem um pedido pode ser **estruturalmente perfeito** e ainda
    assim errado: a fixture ``incompatibleSchemaHash`` do proprio Cinerie e
    exatamente isso — o mesmo corpo, com o hash de outro schema. Ela atravessa o
    JSON Schema inteira, porque o formato ``sha256:<64 hex>`` esta correto; o que
    esta errado e a identidade. Esse e o caso mais perigoso justamente porque
    parece certo.
    """
    if not isinstance(payload, Mapping):
        return []
    issues: List[ContractIssue] = []
    expected = identity.as_request_fields()
    for field, value in expected.items():
        declared = payload.get(field)
        if declared != value:
            # O hash aparece truncado: 64 hexadecimais no log nao ajudam ninguem
            # e enchem a linha.
            shown = str(declared)[:19] if field == "schemaHash" else str(declared)[:60]
            issues.append(
                ContractIssue(
                    path=field,
                    message=(
                        f"{field} divergente do contrato local "
                        f"(declarado {shown}..., local {value[:19]}...)"
                        if field == "schemaHash"
                        else f"{field} divergente do contrato local (declarado {shown})"
                    ),
                )
            )
    return issues


def validate_request(
    payload: Any, *, identity: ContractIdentity | None = None
) -> ValidationReport:
    """Relatorio completo, sem levantar excecao.

    ``identity`` e opcional para permitir validar um payload de terceiro contra
    o schema puro — e o que o teste de compatibilidade faz com as fixtures do
    Cinerie. No caminho de envio ela e sempre passada.
    """
    finding = find_forbidden_key(payload)
    if finding is not None:
        return ValidationReport(
            ok=False,
            issues=[ContractIssue(path=finding.path, message=finding.reason)],
            forbidden_key=finding.key,
            forbidden_group=finding.group,
        )

    issues = _schema_issues(payload)
    issues.extend(check_request(payload))
    if identity is not None:
        issues.extend(check_identity(payload, identity))
    return ValidationReport(ok=not issues, issues=issues)


def ensure_valid(
    payload: Dict[str, Any], *, identity: ContractIdentity | None = None
) -> Dict[str, Any]:
    """Valida ou levanta. O ultimo portao antes do transporte."""
    report = validate_request(payload, identity=identity or local_identity())
    if report.ok:
        return payload

    if report.forbidden_key is not None:
        raise ForbiddenFieldError(
            f"campo recusado pelo contrato do Cinerie ({report.forbidden_group}): "
            f"{report.summary()}",
            paths=report.paths,
            details=report.details,
        )

    raise SchemaValidationError(
        "pedido invalido para editorial-publication-request-v1: " + report.summary(),
        paths=report.paths,
        details=report.details,
    )


def is_valid(payload: Any) -> bool:
    return validate_request(payload).ok


def describe_issues(issues: Sequence[ContractIssue]) -> List[str]:
    return [str(issue) for issue in issues[:MAX_REPORTED_ISSUES]]


__all__ = [
    "MAX_REPORTED_ISSUES",
    "ValidationReport",
    "check_identity",
    "describe_issues",
    "ensure_valid",
    "is_valid",
    "validate_request",
]
