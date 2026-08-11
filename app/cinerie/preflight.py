"""Preflight contratual — perguntar antes de escrever.

O MNScr consulta ``GET /api/internal/contracts`` e compara **nome, versao e
hash** com o que ele tem. Os tres importam por razoes diferentes: nome errado e
outro contrato; versao errada e outra semantica; **hash errado e outro schema com
o mesmo rotulo** — o mais perigoso, porque parece certo.

Divergencia falha FECHADO: nenhum request e enviado ao endpoint de publicacao, o
codigo local e ``CONTRACT_MISMATCH`` e a mensagem carrega nome, versao e hashes
truncados. Publicar "quase compativel" e como se publica materia errada.

O cache existe por economia, nao por conveniencia: consultar o manifesto a cada
materia seria uma requisicao inutil por materia. Ele tem TTL configuravel, e
**invalida imediatamente** em erro de contrato — descobrir a incompatibilidade e
justamente quando nao se pode confiar no valor guardado.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional

from .contract import CONTRACT_NAME, ContractIdentity, local_identity
from .errors import ContractMismatchError
from .outcomes import OutcomeReason

logger = logging.getLogger(__name__)

DEFAULT_TTL_SECONDS: float = 300.0


@dataclass(frozen=True)
class PreflightResult:
    """O que o destino declarou, e se batemos com ele."""

    compatible: bool
    remote_version: Optional[str] = None
    remote_hash: Optional[str] = None
    remote_compatibility: Optional[str] = None
    remote_direction: Optional[str] = None
    build_identifier: Optional[str] = None
    reason: Optional[str] = None
    from_cache: bool = False

    def safe_log_fields(self) -> Dict[str, Any]:
        return {
            "compatible": self.compatible,
            "contract_name": CONTRACT_NAME,
            "remote_version": self.remote_version,
            # Hash truncado: 64 hexadecimais no log nao ajudam ninguem.
            "remote_hash": (self.remote_hash or "")[:19] or None,
            "build_identifier": self.build_identifier,
            "from_cache": self.from_cache,
            "reason": self.reason,
        }

    def as_reason(self) -> OutcomeReason:
        return OutcomeReason(code="CONTRACT_MISMATCH", detail=self.reason or "")


class ContractPreflight:
    """Consulta com cache por TTL, invalidacao em erro e checagem explicita."""

    def __init__(
        self,
        client: Any,
        *,
        identity: Optional[ContractIdentity] = None,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        clock: Optional[Any] = None,
    ) -> None:
        from app.delivery.retry import Clock

        self._client = client
        self._identity = identity or local_identity()
        self._ttl = max(0.0, float(ttl_seconds))
        self._clock = clock or Clock()
        self._cached: Optional[PreflightResult] = None
        self._cached_at: Optional[float] = None

    # -- cache -------------------------------------------------------------

    @property
    def identity(self) -> ContractIdentity:
        return self._identity

    def invalidate(self) -> None:
        """Esquece o que foi guardado. Chamado em qualquer erro de contrato."""
        self._cached = None
        self._cached_at = None

    def _fresh(self) -> bool:
        if self._cached is None or self._cached_at is None:
            return False
        if self._ttl <= 0:
            return False
        return (self._clock.now() - self._cached_at) < self._ttl

    # -- consulta ----------------------------------------------------------

    def check(self, *, force: bool = False) -> PreflightResult:
        """Resultado do preflight, do cache ou da rede."""
        if not force and self._fresh() and self._cached is not None:
            return PreflightResult(**{**self._cached.__dict__, "from_cache": True})

        manifest = self._client.fetch_contracts()
        result = self._compare(manifest)

        if result.compatible:
            self._cached = result
            self._cached_at = self._clock.now()
        else:
            self.invalidate()

        return result

    def ensure_compatible(self, *, force: bool = False) -> PreflightResult:
        """Compatível, ou levanta. O portao antes do primeiro envio."""
        result = self.check(force=force)
        if not result.compatible:
            raise ContractMismatchError(
                f"contrato incompativel com o Cinerie: {result.reason}"
            )
        return result

    # -- comparacao --------------------------------------------------------

    def _compare(self, manifest: Mapping[str, Any]) -> PreflightResult:
        entries = manifest.get("contracts")
        build = manifest.get("buildIdentifier")
        build_identifier = str(build)[:40] if isinstance(build, str) else None

        entry: Optional[Mapping[str, Any]] = None
        if isinstance(entries, list):
            for candidate in entries:
                if isinstance(candidate, Mapping) and candidate.get("contractName") == CONTRACT_NAME:
                    entry = candidate
                    break

        if entry is None:
            return PreflightResult(
                compatible=False,
                build_identifier=build_identifier,
                reason=f"o Cinerie nao publica o contrato {CONTRACT_NAME}",
            )

        remote_version = str(entry.get("contractVersion") or "")
        remote_hash = str(entry.get("schemaHash") or "")
        compatibility = entry.get("compatibility")
        direction = entry.get("direction")

        problems: List[str] = []
        if remote_version != self._identity.contract_version:
            problems.append(
                f"versao local {self._identity.contract_version} != remota {remote_version}"
            )
        if remote_hash != self._identity.schema_hash:
            problems.append(
                f"schemaHash local {self._identity.truncated_hash()}... "
                f"!= remoto {remote_hash[:19]}..."
            )
        if isinstance(direction, str) and direction != "inbound":
            # `inbound` = o MNScr produz para o Cinerie. Outra direcao significa
            # que estamos lendo o contrato errado.
            problems.append(f"direcao inesperada: {direction}")
        if isinstance(compatibility, str) and compatibility == "breaking":
            problems.append("o Cinerie declara este contrato como breaking")

        return PreflightResult(
            compatible=not problems,
            remote_version=remote_version or None,
            remote_hash=remote_hash or None,
            remote_compatibility=compatibility if isinstance(compatibility, str) else None,
            remote_direction=direction if isinstance(direction, str) else None,
            build_identifier=build_identifier,
            reason="; ".join(problems) or None,
        )


__all__ = ["ContractPreflight", "DEFAULT_TTL_SECONDS", "PreflightResult"]
