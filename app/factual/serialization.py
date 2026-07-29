"""Serialization helpers for the factual layer.

Reuses the canonical-JSON discipline of the rest of the project. Rendering for
humans is separate from hashing: the CLI shows truncated excerpts, and the hash
never sees prose that a person might reword.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from .hashing import canonical_json, stable_hash, to_plain
from .normalization import truncate_at_safe_boundary

#: How much of an excerpt the CLI shows by default. Reading the whole passage is
#: an explicit choice, not the default output.
CLI_EXCERPT_PREVIEW_CHARS = 120


def dumps_pretty(value: Any) -> str:
    return json.dumps(to_plain(value), ensure_ascii=False, sort_keys=True, indent=2)


def preview_excerpt(text: str, limit: int = CLI_EXCERPT_PREVIEW_CHARS) -> str:
    preview, _ = truncate_at_safe_boundary(text or "", limit)
    return preview


def assessment_summary(assessment: Any) -> Dict[str, Any]:
    """Compact view for logs and listings — counts only, never content."""
    coverage = assessment.coverage
    return {
        "assessment_id": assessment.assessment_id,
        "draft_id": assessment.draft_id,
        "event_key": assessment.event_key,
        "revision": assessment.revision,
        "assessment_hash": assessment.assessment_hash,
        "claim_count": len(assessment.claims),
        "material_claim_count": coverage.total_material_claims,
        "evidence_count": len(assessment.evidence),
        "link_count": len(assessment.links),
        "conflict_count": len(assessment.conflicts),
        "coverage_ratio": coverage.coverage_ratio,
        "source_diversity": coverage.source_diversity,
        "review_required": coverage.review_required,
    }


def format_assessment(assessment: Any) -> str:
    """Human-readable report for ``--show-factual-assessment``.

    Shows material claims and conflicts. Never prints the article, and excerpts
    appear only as short previews.
    """
    coverage = assessment.coverage
    lines: List[str] = [
        f"draft_id           : {assessment.draft_id}",
        f"event_key          : {assessment.event_key or '-'}",
        f"revision           : {assessment.revision}",
        f"assessment_id      : {assessment.assessment_id}",
        f"assessment_hash    : {assessment.assessment_hash}",
        f"version            : {assessment.version}",
        f"mode               : {assessment.mode}",
        f"coverage_ratio     : {coverage.coverage_ratio}",
        f"source_diversity   : {coverage.source_diversity}",
        f"primary_evidence   : {coverage.primary_evidence_count}",
        f"review_required    : {coverage.review_required}",
        "",
        "claims materiais por status:",
        f"  SUPPORTED           : {coverage.supported_material_claims}",
        f"  PARTIALLY_SUPPORTED : {coverage.partially_supported_material_claims}",
        f"  UNSUPPORTED         : {coverage.unsupported_material_claims}",
        f"  CONFLICTING         : {coverage.conflicting_material_claims}",
        f"  UNVERIFIED          : {coverage.unverified_material_claims}",
        f"  TOTAL               : {coverage.total_material_claims}",
        "",
        "claims materiais:",
    ]

    material = assessment.material_claims
    if not material:
        lines.append("  (nenhum)")
    for claim in material:
        locations = ", ".join(claim.draft_locations) or "-"
        lines.append(
            f"  [{claim.status:<20}] {claim.claim_type:<12} @{locations}: "
            f"{preview_excerpt(claim.display_text)}"
        )

    lines.extend(["", "conflitos:"])
    if not assessment.conflicts:
        lines.append("  (nenhum)")
    for conflict in assessment.conflicts:
        values = " | ".join(conflict.values) or "-"
        lines.append(
            f"  [{conflict.resolution_status}] {conflict.conflict_type} "
            f"subject={conflict.subject or '-'} values=[{values}]"
        )
        if conflict.description:
            lines.append(f"      {preview_excerpt(conflict.description)}")

    if assessment.warnings:
        lines.extend(["", "warnings:"])
        lines.extend(f"  {w}" for w in assessment.warnings)
    if assessment.blocking_errors:
        lines.extend(["", "erros:"])
        lines.extend(f"  {e}" for e in assessment.blocking_errors)
    return "\n".join(lines)


def format_unsupported_listing(rows: List[Dict[str, Any]]) -> str:
    header = f"{'DRAFT_ID':<40}  {'CLAIM_ID':<40}  {'LOCAL':<16}  {'STATUS':<20}  TEXTO"
    lines = ["Claims materiais sem suporte", header, "-" * len(header)]
    if not rows:
        lines.append("(nenhum claim nesta situacao)")
        return "\n".join(lines)
    for row in rows:
        lines.append(
            f"{str(row.get('draft_id') or '-'):<40}  "
            f"{str(row.get('claim_id') or '-'):<40}  "
            f"{str(row.get('location') or '-'):<16}  "
            f"{str(row.get('status') or '-'):<20}  "
            f"{preview_excerpt(str(row.get('display_text') or ''), 60)}"
        )
    return "\n".join(lines)


def format_conflict_listing(rows: List[Dict[str, Any]]) -> str:
    header = f"{'DRAFT_ID':<40}  {'CONFLICT_ID':<44}  {'TIPO':<22}  {'SUBJECT':<24}  REVISAO"
    lines = ["Conflitos factuais registrados", header, "-" * len(header)]
    if not rows:
        lines.append("(nenhum conflito registrado)")
        return "\n".join(lines)
    for row in rows:
        values = row.get("values") or []
        rendered = " | ".join(str(v) for v in values) if isinstance(values, list) else str(values)
        lines.append(
            f"{str(row.get('draft_id') or '-'):<40}  "
            f"{str(row.get('conflict_id') or '-'):<44}  "
            f"{str(row.get('conflict_type') or '-'):<22}  "
            f"{str(row.get('subject') or '-'):<24}  "
            f"{'sim' if row.get('requires_review') else 'nao'}"
        )
        if rendered:
            lines.append(f"      valores: {preview_excerpt(rendered, 100)}")
    return "\n".join(lines)


__all__ = [
    "CLI_EXCERPT_PREVIEW_CHARS",
    "assessment_summary",
    "canonical_json",
    "dumps_pretty",
    "format_assessment",
    "format_conflict_listing",
    "format_unsupported_listing",
    "preview_excerpt",
    "stable_hash",
    "to_plain",
]
