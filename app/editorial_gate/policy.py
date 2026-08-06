"""Versioned Editorial Gate policy.

The policy is data, not code: which rules run, which domains are trusted for a
single source, and the numeric thresholds. It is versioned so a draft evaluated
last month can be told apart from the same draft evaluated under new rules.

There is deliberately no silent fallback. A missing or malformed policy raises
at startup — evaluating drafts under a policy nobody chose would make every
stored verdict unexplainable.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from dataclasses import field as dc_field
from pathlib import Path
from typing import Any, Dict, Final, List, Optional

from .errors import InvalidPolicyError, PolicyNotFoundError
from .models import POLICY_VERSION_V1

logger = logging.getLogger(__name__)

DEFAULT_POLICY_DIR: Final[str] = "config/editorial_gate"

#: Threshold keys the engine understands. Anything else in the file is kept but
#: never consulted, so an unknown key is a typo the operator can still see.
KNOWN_THRESHOLDS: Final[frozenset[str]] = frozenset(
    {
        "min_body_words",
        "min_meta_description_chars",
        "max_meta_description_chars",
        "min_distinct_source_domains",
        "prompt_injection_min_signals",
        "block_on_unverified_media",
        # MS-4: limiares factuais.
        "minimumFactualCoverageRatio",
        "minimumIndependentSourceOrigins",
        "blockUnsupportedMaterialClaimsInCriticalLocations",
        "blockCriticalFactConflicts",
    }
)


@dataclass
class EditorialGatePolicy:
    """A loaded, validated policy version."""

    policy_version: str
    enabled_rules: List[str] = dc_field(default_factory=list)
    trusted_single_source_domains: List[str] = dc_field(default_factory=list)
    thresholds: Dict[str, Any] = dc_field(default_factory=dict)
    source_path: Optional[str] = None

    def __post_init__(self) -> None:
        self.policy_version = str(self.policy_version or "").strip()
        if not self.policy_version:
            raise InvalidPolicyError("<sem versao>", "policyVersion ausente")
        self.enabled_rules = [str(r).strip() for r in self.enabled_rules if str(r).strip()]
        self.trusted_single_source_domains = [
            str(d).strip().lower() for d in self.trusted_single_source_domains if str(d).strip()
        ]

    def is_enabled(self, rule_code: str) -> bool:
        return rule_code in self.enabled_rules

    def threshold(self, name: str, default: Any = None) -> Any:
        return self.thresholds.get(name, default)

    def is_trusted_domain(self, domain: Optional[str]) -> bool:
        """Substring match, mirroring how the existing allowlist is applied.

        ``app.multi_source_builder`` already matches by substring so that
        ``www.variety.com`` and ``variety.com/x`` both count; keeping the same
        semantics avoids two allowlists that disagree.
        """
        if not domain:
            return False
        normalized = str(domain).strip().lower()
        return any(trusted in normalized for trusted in self.trusted_single_source_domains)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "policyVersion": self.policy_version,
            "enabledRules": list(self.enabled_rules),
            "trustedSingleSourceDomains": list(self.trusted_single_source_domains),
            "thresholds": dict(self.thresholds),
        }


#: Raiz de instalação: a mesma que `app/cinerie/contract.py` usa para achar
#: `contracts/`. No repositório é a raiz do checkout; num wheel instalado é a
#: raiz do `site-packages`.
_INSTALL_ROOT: Final[Path] = Path(__file__).resolve().parents[2]


def policy_path(policy_version: str, policy_dir: Optional[str] = None) -> Path:
    """Caminho do arquivo de política, procurado em dois lugares.

    `DEFAULT_POLICY_DIR` é relativo ao DIRETÓRIO DE TRABALHO, o que funciona
    quando se roda de dentro do checkout e falha em qualquer outro lugar — um
    wheel instalado carregava o contrato e morria aqui, porque `config/` não fica
    sob o cwd de quem executa.

    O caminho relativo continua vencendo: é ele que permite a um operador apontar
    para uma política própria sem reinstalar nada. A raiz de instalação entra só
    como segunda tentativa, quando o relativo não existe.
    """
    base = Path(policy_dir or DEFAULT_POLICY_DIR)
    candidato = base / f"{policy_version}.json"
    if candidato.exists() or base.is_absolute():
        return candidato

    instalado = _INSTALL_ROOT / base / f"{policy_version}.json"
    return instalado if instalado.exists() else candidato


def load_policy(
    policy_version: Optional[str] = None,
    policy_dir: Optional[str] = None,
) -> EditorialGatePolicy:
    """Load a policy by version. Raises rather than guessing."""
    from app import config

    version = (policy_version or getattr(config, "EDITORIAL_GATE_POLICY", POLICY_VERSION_V1)).strip()
    directory = policy_dir or getattr(config, "EDITORIAL_GATE_POLICY_DIR", DEFAULT_POLICY_DIR)

    path = policy_path(version, directory)
    if not path.is_file():
        raise PolicyNotFoundError(version, str(directory))

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise InvalidPolicyError(version, f"nao foi possivel ler o JSON ({exc})") from exc

    if not isinstance(raw, dict):
        raise InvalidPolicyError(version, "o arquivo precisa conter um objeto JSON")

    declared = str(raw.get("policyVersion") or "").strip()
    if declared != version:
        raise InvalidPolicyError(
            version,
            f"policyVersion declarada '{declared}' nao corresponde ao nome do arquivo",
        )

    enabled = raw.get("enabledRules")
    if not isinstance(enabled, list) or not enabled:
        raise InvalidPolicyError(version, "enabledRules precisa ser uma lista nao vazia")

    trusted = raw.get("trustedSingleSourceDomains") or []
    if not isinstance(trusted, list):
        raise InvalidPolicyError(version, "trustedSingleSourceDomains precisa ser uma lista")

    thresholds = raw.get("thresholds") or {}
    if not isinstance(thresholds, dict):
        raise InvalidPolicyError(version, "thresholds precisa ser um objeto")

    unknown = sorted(set(thresholds) - KNOWN_THRESHOLDS)
    if unknown:
        # Not fatal: an unrecognized threshold is inert, but the operator must
        # be able to see that it is doing nothing.
        logger.warning(
            "[GATE_STARTED] policy_version=%s thresholds_ignorados=%s",
            version, ",".join(unknown),
        )

    policy = EditorialGatePolicy(
        policy_version=version,
        enabled_rules=enabled,
        trusted_single_source_domains=trusted,
        thresholds=thresholds,
        source_path=str(path),
    )
    logger.info(
        "[GATE_STARTED] policy_version=%s rules=%s trusted_domains=%s",
        policy.policy_version, len(policy.enabled_rules),
        len(policy.trusted_single_source_domains),
    )
    return policy


def available_policies(policy_dir: Optional[str] = None) -> List[str]:
    """Versions with a file on disk, for CLI error messages."""
    base = Path(policy_dir or DEFAULT_POLICY_DIR)
    if not base.is_dir():
        return []
    return sorted(p.stem for p in base.glob("*.json"))


__all__ = [
    "DEFAULT_POLICY_DIR",
    "KNOWN_THRESHOLDS",
    "EditorialGatePolicy",
    "available_policies",
    "load_policy",
    "policy_path",
]
