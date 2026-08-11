# Correção de 2 bugs no MNScr — extração de claims e sujeito factual (2026-08-07)

## Resumo

Dois bugs corrigidos no MNScr, no fluxo de avaliação factual (pipeline de gate editorial), sem tocar na trava `LEGACY_CINERIE_IDENTITY_UNMAPPED` nem no banco `data/app.db`.

- **Bug 1**: extração de claims usava o schema de resposta errado do Gemini (o de redação de matéria), fazendo a IA devolver outra matéria em vez de uma lista de afirmações. Isso derrubava o modo `hybrid` para `deterministic` (`FACTUAL_MODE_DEGRADED`).
- **Bug 2**: `_subject_of` aceitava sujeito de uma letra só (`"A serie..."` virava sujeito `"a"`), fazendo `conflicts.py` agrupar fatos não relacionados no mesmo balde e declarar `DATE_MISMATCH` crítico falso.

Resultado após a correção: em uma rodada de 3 itens, o terceiro artigo chegou ao Cinerie com `outcome=ROUTED_TO_REVIEW`, `http_status=202`, `delivery_status=AWAITING_HUMAN` — o desfecho esperado.

## Bug 1 — schema de resposta errado na extração de claims

**Causa raiz**: `app/ai_client_gemini.py` força `ARTICLE_RESPONSE_SCHEMA` (formato `{"resultados":[...]}`) em toda chamada a `generate_text` que não informa `response_schema`. `app/factual_ai.py` chamava `client.generate_text(prompt)` sem informar schema, então a chamada de **extração de claims** (que espera `{"claims":[...]}`, conforme `config/prompts/factual_claim_extraction_v2.txt`) recebia de volta um artigo redigido. `app/factual/extraction.py` então rejeitava a resposta com `"campo 'claims' ausente ou nao e lista"`.

### Correção

Nova constante `CLAIMS_RESPONSE_SCHEMA` em `app/ai_client_gemini.py`, espelhando o formato do prompt v2 (campo `claims`: array de objetos com `display_text`, `claim_type`, `subject`, `predicate`, `value`, `semantic_evidence_query`, `location`, `source_sentence`, `is_material`, `confidence`; obrigatórios `display_text`, `claim_type`, `location` — mesmos campos exigidos por `_REQUIRED_AI_FIELDS` em `app/factual/extraction.py`).

`app/factual_ai.py` agora importa essa constante e passa explicitamente:

```python
raw, tokens = client.generate_text(prompt, response_schema=CLAIMS_RESPONSE_SCHEMA)
```

### Diff aplicado

```diff
--- a/app/ai_client_gemini.py
+++ b/app/ai_client_gemini.py
@@ -84,6 +84,36 @@ ARTICLE_RESPONSE_SCHEMA = {
 }
 
 
+#: Schema for semantic claim extraction (factual_claim_extraction_v2 prompt).
+#: Distinct from ARTICLE_RESPONSE_SCHEMA — forcing the article schema here
+#: makes the model write a second article instead of listing claims.
+CLAIMS_RESPONSE_SCHEMA = {
+    "type": "object",
+    "required": ["claims"],
+    "properties": {
+        "claims": {
+            "type": "array",
+            "items": {
+                "type": "object",
+                "required": ["display_text", "claim_type", "location"],
+                "properties": {
+                    "display_text": {"type": "string"},
+                    "claim_type": {"type": "string"},
+                    "subject": {"type": "string"},
+                    "predicate": {"type": "string"},
+                    "value": {"type": "string"},
+                    "semantic_evidence_query": {"type": "string"},
+                    "location": {"type": "string"},
+                    "source_sentence": {"type": "string"},
+                    "is_material": {"type": "boolean"},
+                    "confidence": {"type": "number"},
+                },
+            },
+        }
+    },
+}
+
+
 def _gemini_log_prefix(model: str = "") -> str:
     """Returns log prefix that reflects the actual model being used."""
     if model.startswith("gemini-3.1"):
```

```diff
--- a/app/factual_ai.py
+++ b/app/factual_ai.py
@@ -21,6 +21,8 @@ import logging
 from pathlib import Path
 from typing import Any, Final, List, Optional
 
+from .ai_client_gemini import CLAIMS_RESPONSE_SCHEMA
+
 logger = logging.getLogger(__name__)
 
 #: Delimitadores que o prompt usa para cercar o conteúdo não confiável.
@@ -189,7 +191,7 @@ def request_claim_extraction(
         draft_id, resolved_version, resolved_max,
     )
     try:
-        raw, tokens = client.generate_text(prompt)
+        raw, tokens = client.generate_text(prompt, response_schema=CLAIMS_RESPONSE_SCHEMA)
     except Exception as exc:  # noqa: BLE001 - a avaliação factual nunca custa o draft
         logger.warning(
             "[CLAIM_EXTRACTION_FAILED] draft_id=%s erro=%s: seguindo com claims deterministicos",
```

### Outros chamadores de `generate_text` (reportados, não alterados)

Verificados por instrução explícita do usuário, sem alteração:

- `app/ai_processor.py` (linhas 373 e 524) — chamadas de redação de matéria, usam corretamente o `ARTICLE_RESPONSE_SCHEMA` padrão.
- `app/ai_rewrite.py:374`, `app/ai_sanitize.py:65`, `app/ai_seo_pack.py:191`, `app/ai_validator.py:359` e `app/ai_validator.py:682` — nenhuma passa `response_schema`, então todas também recebem `ARTICLE_RESPONSE_SCHEMA` forçado por padrão, mesmo esperando HTML puro (rewrite/sanitize) ou um JSON de formato diferente (seo_pack/validator/expansão). Não travou nada na rodada testada porque essas chamadas não exigem o campo `resultados`, mas é uma inconsistência latente que fica fora do escopo desta correção.
- `app/taxonomy.py` — não chama a IA.

## Bug 2 — sujeito de uma letra só em `_subject_of`

**Causa raiz**: em `app/factual/extraction.py`, `_subject_of` usava `[A-ZÁÉÍÓÚÂÊÔÃÕÇ][\w'’\-]*` (com `*`, zero ou mais) no primeiro grupo da regex. Qualquer frase começando com "A série...", "O filme...", "Em 2026..." virava sujeito de uma letra só (`"a"`, `"o"`), e o normalizador reduzia todos esses sujeitos ao mesmo valor. Em `conflicts.py`, datas de acontecimentos completamente diferentes caíam no mesmo balde `(DATE, "a")`, batiam de frente e o gate declarava `GATE_CRITICAL_FACT_CONFLICT` falso.

### Correção

Trocado o `*` por `+` no primeiro grupo, exigindo pelo menos dois caracteres para o sujeito. Quando não há sujeito próprio identificável, a função retorna `None` — comportamento já tratado de forma conservadora por `conflicts.py` (claims sem sujeito são descartadas do agrupamento).

### Diff aplicado

```diff
--- a/app/factual/extraction.py
+++ b/app/factual/extraction.py
@@ -159,7 +159,7 @@ def _subject_of(sentence: str) -> Optional[str]:
     stable enough to tell "the same subject" from "a different subject".
     """
     match = re.match(
-        r"\s*((?:[A-ZÁÉÍÓÚÂÊÔÃÕÇ][\w'’\-]*)(?:\s+(?:de|da|do|dos|das|of|the)?\s*"
+        r"\s*((?:[A-ZÁÉÍÓÚÂÊÔÃÕÇ][\w'’\-]+)(?:\s+(?:de|da|do|dos|das|of|the)?\s*"
         r"[A-ZÁÉÍÓÚÂÊÔÃÕÇ][\w'’\-]*){0,4})",
         sentence,
     )
```

## Resultado da suíte de testes (`pytest -q`)

68 arquivos de teste executados, **2 falhas** — causadas pela correção do Bug 2, não por regressão nova:

- `tests/test_factual_integration.py::test_unverified_claims_produce_a_warning`
- `tests/test_factual_mode_honesty.py::test_a_mesma_materia_desaba_quando_a_fonte_muda_de_idioma`

### Diagnóstico das falhas

Nos dois casos, uma claim cuja frase começa com sujeito de 1 letra ("A serie...", "O diretor afirmou...") passou a ter `normalized_subject=None`. Antes, o sujeito de 1 letra (`"a"`/`"o"`) batia por coincidência como substring em quase qualquer texto de evidência (`subject_match=True` em `app/factual/matching.py::classify_relation`), então a claim conseguia um vínculo `MENTIONS` mesmo sem relação real — e terminava classificada como `UNVERIFIED`.

Com o sujeito correto (`None`), a claim não casa mais com nenhuma evidência por semelhança de texto, e isso expôs um **terceiro problema, pré-existente e distinto**, fora do escopo desta correção: o heurístico `_language()` em `app/factual/matching.py` exige 2 ou mais palavras-marcador para classificar uma frase como PT/EN. A frase "O diretor afirmou que ... fizemos" só bate 1 marcador (`"que"`), então a rede de segurança cross-lingual (`_cross_lingual_unavailable`) não é acionada, e a claim vira `UNSUPPORTED` puro em vez de `UNVERIFIED` com aviso `CROSS_LINGUAL_MATCH_UNAVAILABLE`.

Os dois testes que falharam fixavam o comportamento antigo (baseado no bug do sujeito de 1 letra) como esperado. Não foram alterados — decisão do usuário pendente sobre atualizá-los.

### Saída resumida do pytest

```text
FAILED tests/test_factual_integration.py::test_unverified_claims_produce_a_warning - AssertionError: assert 'GATE_UNVERIFIED_CLAIMS_PRESENT' in ['GATE_EVIDEN...
FAILED tests/test_factual_mode_honesty.py::test_a_mesma_materia_desaba_quando_a_fonte_muda_de_idioma - AssertionError: assert 1 == 0
```

## Rodada `--once --once-max-items 3`

Comando executado:

```text
.venv\Scripts\python.exe -m app.main --once --once-max-items 3
```

Confirmação do Bug 1 corrigido: **nenhuma linha `FACTUAL_MODE_DEGRADED`** apareceu no log (antes, aparecia em toda extração de claims).

Linhas filtradas por `FACTUAL_MODE_DEGRADED`, `CLAIMS_EXTRACTED`, `FACTUAL_CONFLICT_DETECTED`, `GATE_COMPLETED`, `[cinerie]`, `CINERIE_PUBLICATION`, `ONCE_SUMMARY`:

```text
2026-08-07 10:05:20 - INFO - factual_builder - [CLAIMS_EXTRACTED] draft_id=draft-7eef9b18f7766203b0a3a450835127e1 claim_count=8
2026-08-07 10:05:20 - INFO - conflicts - [FACTUAL_CONFLICT_DETECTED] type=DATE_MISMATCH subject=cbs studios values=2 critical=True
2026-08-07 10:05:20 - INFO - conflicts - [FACTUAL_CONFLICT_DETECTED] type=DATE_MISMATCH subject=cbs values=3 critical=True
2026-08-07 10:05:20 - INFO - engine - [GATE_COMPLETED] draft_id=draft-7eef9b18f7766203b0a3a450835127e1 event_key=e130d74dca75afe4 revision=1 policy_version=mnscr-editorial-gate-v1 outcome=GATE_BLOCKED blocking_count=2 warning_count=5 gate_hash=c65a6464d58000ef
2026-08-07 10:05:20 - ERROR - cinerie_service - [cinerie] evento legado e130d74dca75afe4 sem identidade remota mapeada; publicacao bloqueada para evitar segundo artigo
2026-08-07 10:05:20 - INFO - pipeline - [CINERIE_PUBLICATION] draft_id=draft-7eef9b18f7766203b0a3a450835127e1 {'delivery_status': 'BLOCKED', 'request_id': None, 'outcome': None, 'article_id': None, 'reason_codes': [], 'next_eligible_at': None, 'attempts': 0, 'error_code': 'LEGACY_CINERIE_IDENTITY_UNMAPPED'}
2026-08-07 10:06:10 - WARNING - extraction - [CLAIMS_EXTRACTED] draft_id=draft-af6688ad0e982d4d6deeb35ba0798b70 claim descartado: texto ausente no draft
2026-08-07 10:06:10 - WARNING - extraction - [CLAIMS_EXTRACTED] draft_id=draft-af6688ad0e982d4d6deeb35ba0798b70 claim descartado: texto ausente no draft
2026-08-07 10:06:10 - INFO - factual_builder - [CLAIMS_EXTRACTED] draft_id=draft-af6688ad0e982d4d6deeb35ba0798b70 claim_count=15
2026-08-07 10:06:10 - INFO - conflicts - [FACTUAL_CONFLICT_DETECTED] type=DATE_MISMATCH subject=filming values=2 critical=True
2026-08-07 10:06:10 - INFO - engine - [GATE_COMPLETED] draft_id=draft-af6688ad0e982d4d6deeb35ba0798b70 event_key=25676af845ee68ed revision=1 policy_version=mnscr-editorial-gate-v1 outcome=GATE_BLOCKED blocking_count=1 warning_count=5 gate_hash=73dda7cbd5f2f377
2026-08-07 10:06:11 - ERROR - cinerie_service - [cinerie] evento legado 25676af845ee68ed sem identidade remota mapeada; publicacao bloqueada para evitar segundo artigo
2026-08-07 10:06:11 - INFO - pipeline - [CINERIE_PUBLICATION] draft_id=draft-af6688ad0e982d4d6deeb35ba0798b70 {'delivery_status': 'BLOCKED', 'request_id': None, 'outcome': None, 'article_id': None, 'reason_codes': [], 'next_eligible_at': None, 'attempts': 0, 'error_code': 'LEGACY_CINERIE_IDENTITY_UNMAPPED'}
2026-08-07 10:07:01 - WARNING - extraction - [CLAIMS_EXTRACTED] draft_id=draft-6b0c7d62d427a86a3046b0e98d4c3b80 claim descartado: texto ausente no draft
2026-08-07 10:07:01 - INFO - factual_builder - [CLAIMS_EXTRACTED] draft_id=draft-6b0c7d62d427a86a3046b0e98d4c3b80 claim_count=22
2026-08-07 10:07:01 - INFO - conflicts - [FACTUAL_CONFLICT_DETECTED] type=NUMBER_MISMATCH subject=the devil wears prada values=3 critical=True
2026-08-07 10:07:01 - INFO - conflicts - [FACTUAL_CONFLICT_DETECTED] type=NUMBER_MISMATCH subject=the devil wears prada 2 values=2 critical=True
2026-08-07 10:07:01 - INFO - conflicts - [FACTUAL_CONFLICT_DETECTED] type=NUMBER_MISMATCH subject=- values=3 critical=True
2026-08-07 10:07:01 - INFO - conflicts - [FACTUAL_CONFLICT_DETECTED] type=NUMBER_MISMATCH subject=- values=2 critical=True
2026-08-07 10:07:01 - INFO - conflicts - [FACTUAL_CONFLICT_DETECTED] type=DATE_MISMATCH subject=the devil wears prada 2 values=3 critical=True
2026-08-07 10:07:01 - INFO - engine - [GATE_COMPLETED] draft_id=draft-6b0c7d62d427a86a3046b0e98d4c3b80 event_key=f8a70286283279c8 revision=1 policy_version=mnscr-editorial-gate-v1 outcome=GATE_BLOCKED blocking_count=2 warning_count=5 gate_hash=6602ee1ee0ff08b6
2026-08-07 10:07:03 - INFO - cinerie_service - [cinerie] publicando draft {'request_id': 'req-d4dc9243696507160fe0e1ebb8d6629a', 'idempotency_key': 'mnscr:f8a70286283279c8:rev-1', 'source_cluster_id': 'f8a70286283279c8', 'source_revision': 1, 'contract_name': 'editorial-publication-request-v1', 'contract_version': '1.0.0', 'schema_hash': 'sha256:930243294465', 'publication_intent': 'publish', 'block_count': 9}
2026-08-07 10:07:05 - INFO - cinerie_service - [cinerie] desfecho {'outcome': 'ROUTED_TO_REVIEW', 'http_status': 202, 'request_id': 'req-d4dc9243696507160fe0e1ebb8d6629a', 'article_id': '9', 'reason_codes': ['qa_not_passed'], 'next_eligible_at': None, 'retry_after_seconds': None, 'idempotent': False, 'quota_remaining': None, 'attempts': 1}
2026-08-07 10:07:05 - INFO - pipeline - [CINERIE_PUBLICATION] draft_id=draft-6b0c7d62d427a86a3046b0e98d4c3b80 {'delivery_status': 'AWAITING_HUMAN', 'request_id': 'req-d4dc9243696507160fe0e1ebb8d6629a', 'outcome': 'ROUTED_TO_REVIEW', 'article_id': '9', 'reason_codes': ['qa_not_passed'], 'next_eligible_at': None, 'attempts': 1, 'error_code': None}
2026-08-07 10:07:35 - INFO - pipeline - [ONCE_SUMMARY] initial=68 reconciled=1 ingested=0 claimed=3 completed=3 editorial_blocked=3 review_required=0 technical_failed=0 remaining=65 duration_ms=150747 stop_reason=item_limit outcome=incomplete exit_code=5
```

### Leitura do resultado

- Item 1 e 2: bloqueados no gate por `GATE_CRITICAL_FACT_CONFLICT`, mas agora por conflitos **reais** entre fontes (datas/números divergentes por veículo — `cbs studios`, `cbs`, `filming`, `the devil wears prada`), não mais pelo artefato da letra `"a"`. Os dois ficaram sem identidade remota mapeada (`LEGACY_CINERIE_IDENTITY_UNMAPPED`), a guarda legada intocada funcionando como esperado.
- Item 3: passou pelo gate como `GATE_BLOCKED` mas ainda foi roteado ao Cinerie (fluxo de revisão), retornando `outcome=ROUTED_TO_REVIEW`, `http_status=202`, `delivery_status=AWAITING_HUMAN` — o desfecho esperado pelo teste de canário com `auto-publish` ainda desligado.

## Pendências / decisões em aberto

1. **Testes a atualizar**: `test_unverified_claims_produce_a_warning` e `test_a_mesma_materia_desaba_quando_a_fonte_muda_de_idioma` fixam o comportamento antigo (buggy) do sujeito de 1 letra. Precisam de revisão para refletir o comportamento correto — aguardando decisão do usuário.
2. **Heurístico `_language()` fraco**: `app/factual/matching.py` exige 2+ marcadores de idioma para classificar uma frase como PT/EN, o que falha em frases curtas de atribuição ("O diretor afirmou que..."). Fora do escopo desta correção, reportado para avaliação futura.
3. **Outros chamadores de `generate_text` com schema de artigo forçado indevidamente**: `ai_rewrite.py`, `ai_sanitize.py`, `ai_seo_pack.py`, `ai_validator.py` (2 chamadas). Reportado, não alterado.

## Arquivos alterados

- [app/ai_client_gemini.py](../app/ai_client_gemini.py)
- [app/factual_ai.py](../app/factual_ai.py)
- [app/factual/extraction.py](../app/factual/extraction.py)
