"""Publicacao governada no Cinerie (Payload CMS, dentro do Screen-App).

O contrato ``editorial-publication-request-v1`` vem do Screen-App e atravessa a
fronteira como **JSON Schema gerado + hash** — ver ``contracts/cinerie/``. Nada
aqui foi traduzido do TypeScript a mao: uma segunda copia do schema divergiria
da primeira no campo seguinte.

A separacao entre dominio e transporte e verificada por teste: apenas
``client.py`` importa ``requests``. Tudo o mais — contrato, politica, SEO,
blocos, identidade, refinamentos, validacao — e puro, e por isso o caminho
inteiro de construcao e de recusa pode ser exercitado sem abrir um socket.

O MNScr **pede** publicacao; quem decide e o Cinerie.
"""

from .contract import CONTRACT_NAME, ContractIdentity, local_identity
from .errors import (
    BlockedByPolicyError,
    CinerieError,
    ContractMismatchError,
    RequestBuildError,
    SchemaValidationError,
)
from .identity import is_public_author_id
from .outcomes import (
    BLOCKED,
    CONFLICT,
    DEFERRED,
    OPERATIONAL_ERROR,
    PUBLISHED,
    ROUTED_TO_REVIEW,
    PublicationResult,
)
from .policy import AUTO_PUBLISH_INELIGIBLE, BLOCKING, WARNING, seo_policy
from .request import BuiltRequest, build_publication_request
from .seo import SeoProposal, build_seo_proposal
from .validation import ValidationReport, ensure_valid, validate_request

__all__ = [
    "AUTO_PUBLISH_INELIGIBLE",
    "BLOCKED",
    "BLOCKING",
    "BlockedByPolicyError",
    "BuiltRequest",
    "CONFLICT",
    "CONTRACT_NAME",
    "CinerieError",
    "ContractIdentity",
    "ContractMismatchError",
    "DEFERRED",
    "OPERATIONAL_ERROR",
    "PUBLISHED",
    "PublicationResult",
    "ROUTED_TO_REVIEW",
    "RequestBuildError",
    "SchemaValidationError",
    "SeoProposal",
    "ValidationReport",
    "WARNING",
    "build_publication_request",
    "build_seo_proposal",
    "ensure_valid",
    "is_public_author_id",
    "local_identity",
    "seo_policy",
    "validate_request",
]
