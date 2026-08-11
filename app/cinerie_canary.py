"""Canário único e deliberadamente bloqueado para o Cinerie de produção.

Não é importado pelo runtime. A execução exige autorização humana explícita e
``MNSCR_DELIVERY_MODE=DRAFT``; ver ``docs/runbooks/cinerie-canary.md``.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Mapping

from app.cinerie import outcomes as O
from app.cinerie.client import CinerieClient, CinerieConfig
from app.cinerie.contract import local_identity
from app.cinerie.request import build_publication_request
from app.cinerie.validation import ensure_valid
from app.editorial import DraftContent, DraftProvenance, EditorialDraft, SourceReference

_REQUIRED_ENV = (
    "PAYLOAD_INTERNAL_SERVICE_URL",
    "MNSCR_PAYLOAD_API_KEY",
    "MNSCR_PUBLIC_AUTHOR_ID",
)


def validate_canary_environment(environ: Mapping[str, str]) -> None:
    if environ.get("MNSCR_CANARY_AUTHORIZED") != "YES":
        raise RuntimeError("MNSCR_CANARY_AUTHORIZED=YES ausente; canário não autorizado")
    if environ.get("MNSCR_DELIVERY_MODE") != "DRAFT":
        raise RuntimeError("canário exige MNSCR_DELIVERY_MODE=DRAFT explicitamente")
    missing = [name for name in _REQUIRED_ENV if not str(environ.get(name, "")).strip()]
    if missing:
        raise RuntimeError("variáveis obrigatórias vazias: " + ", ".join(missing))


def synthetic_payload(*, public_author_id: str, day: str | None = None) -> dict[str, Any]:
    resolved_day = day or datetime.now(timezone.utc).date().isoformat().replace("-", "")
    event_key = f"canary-mnscr-{resolved_day}"
    marker = "CANÁRIO SINTÉTICO MNSCR — NÃO PUBLICAR"
    body = (
        f"<h2>{marker}</h2>"
        "<p>Este conteúdo sintético verifica somente contrato e idempotência. "
        "Não relata acontecimento real e deve permanecer como rascunho para revisão.</p>"
        "<p>Marcador operacional repetido para tornar a limpeza inequívoca: "
        f"{marker}. Registro técnico sem valor editorial.</p>"
    )
    output_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
    draft = EditorialDraft(
        draft_id=f"draft-{event_key}",
        article_id=f"synthetic-{resolved_day}",
        event_key=event_key,
        revision=1,
        content=DraftContent(
            title=marker,
            subtitle="Teste controlado de contrato e idempotência; conteúdo sem valor editorial.",
            body_html=body,
            meta_description=(
                "Canário sintético do MNScr para validar contrato e idempotência, "
                "sem publicação e sem conteúdo editorial real."
            ),
            focus_keyphrase="canário sintético MNScr",
            related_keyphrases=["teste de idempotência"],
            slug_suggestion=f"canario-sintetico-mnscr-{resolved_day}",
            category_suggestions=["news"],
            tag_suggestions=["canário", "sintético"],
        ),
        provenance=DraftProvenance(
            input_hash=hashlib.sha256(event_key.encode("utf-8")).hexdigest(),
            output_hash=output_hash,
            input_event_key=event_key,
            input_revision=1,
            prompt_version="cinerie-canary@1",
            model_name="none-synthetic",
            created_at=f"{resolved_day[:4]}-{resolved_day[4:6]}-{resolved_day[6:]}T00:00:00+00:00",
        ),
        sources=[
            SourceReference(
                url="https://example.invalid/mnscr-canary",
                domain="example.invalid",
                is_primary=True,
                extraction_status="SYNTHETIC",
            )
        ],
    )
    # QA reprovado de propósito: mesmo com qualquer melhoria de cobertura factual,
    # o pedido declara que requer revisão. DRAFT continua sendo exigido à parte.
    draft.editorial_gate = SimpleNamespace(outcome="GATE_BLOCKED")
    draft.factual_assessment = SimpleNamespace(critical_conflicts=[])
    built = build_publication_request(
        draft,
        public_author_id=public_author_id,
        attribution_mode="newsroom",
        contract=local_identity(),
        pipeline_version="mnscr-cinerie-canary@1",
    )
    ensure_valid(built.payload, identity=built.contract)
    return built.payload


def execute_canary(client: Any, payload: Mapping[str, Any]) -> dict[str, Any]:
    first = client.submit_publication(payload)
    if first.outcome == O.PUBLISHED:
        raise RuntimeError("canário retornou PUBLISHED apesar de MNSCR_DELIVERY_MODE=DRAFT")
    if first.outcome != O.ROUTED_TO_REVIEW:
        raise RuntimeError(f"primeiro pedido não foi roteado à revisão: {first.outcome}")
    if not first.request_id:
        raise RuntimeError("primeiro pedido não retornou requestId")
    if not first.article_id:
        raise RuntimeError("primeiro pedido não retornou articleId")

    second = client.submit_publication(payload)
    already_consumed = "ALREADY_CONSUMED" in second.reason_codes
    if not (second.idempotent or already_consumed):
        raise RuntimeError("repetição não comprovou idempotent:true/ALREADY_CONSUMED")
    if second.idempotency_key and second.idempotency_key != payload.get("idempotencyKey"):
        raise RuntimeError("destino reconciliou chave idempotente diferente da enviada")
    if first.article_id != second.article_id:
        raise RuntimeError("replay idempotente retornou articleId diferente do primeiro pedido")
    expected_request_id = payload.get("requestId")
    for result in (first, second):
        if result.request_id and result.request_id != expected_request_id:
            raise RuntimeError("replay idempotente retornou requestId diferente do pedido")
        if result.idempotency_key and result.idempotency_key != payload.get("idempotencyKey"):
            raise RuntimeError("replay idempotente retornou idempotencyKey diferente do pedido")

    return {
        "outcome": first.outcome,
        "requestId": first.request_id or payload.get("requestId"),
        "idempotencyKey": first.idempotency_key or payload.get("idempotencyKey"),
        "articleId": first.article_id,
        "idempotent": bool(second.idempotent or already_consumed),
        "reconciliationProof": "destination-acknowledged-same-idempotency-key",
        "quotaRemaining": getattr(first, "quota_remaining", None),
    }


def main() -> int:
    validate_canary_environment(os.environ)
    payload = synthetic_payload(public_author_id=os.environ["MNSCR_PUBLIC_AUTHOR_ID"])
    client = CinerieClient(
        CinerieConfig(
            base_url=os.environ["PAYLOAD_INTERNAL_SERVICE_URL"],
            api_key=os.environ["MNSCR_PAYLOAD_API_KEY"],
        )
    )
    # Preflight explícito antes do primeiro POST. Divergência interrompe sem envio.
    manifest = client.fetch_contracts()
    identity = local_identity()
    compatible = any(
        item.get("contractName") == identity.contract_name
        and item.get("contractVersion") == identity.contract_version
        and item.get("schemaHash") == identity.schema_hash
        for item in manifest.get("contracts", [])
        if isinstance(item, Mapping)
    )
    if not compatible:
        raise RuntimeError("preflight de contrato incompatível; nenhum pedido enviado")
    print(json.dumps(execute_canary(client, payload), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
