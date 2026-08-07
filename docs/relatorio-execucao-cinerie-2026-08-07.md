# Relatorio de execucao - MNScr e Cinerie (2026-08-07)

## Resumo rapido
- Ambiente virtual validado e dependencias instaladas no .venv.
- Variavel de protecao contra loop de CMS configurada no .env.
- Preflight do Cinerie retornou compatibilidade de contrato.
- Execucao de 1 item com --once concluiu o ciclo, gerou rascunho local e bloqueou entrega ao Cinerie por regra de identidade legada nao mapeada.

## Ajustes aplicados
- Dependencias instaladas com pip editavel do projeto.
- Variavel adicionada no arquivo .env:
  - MNSCR_OWN_CMS_DOMAINS=cinerie.com,www.cinerie.com,cms.cinerie.com

## Saida completa - preflight
Comando executado:

~~~text
.venv\Scripts\python.exe -m app.main --cinerie-preflight
~~~

Saida:

~~~text
compatible         True
contract_name      editorial-publication-request-v1
remote_version     1.0.0
remote_hash        sha256:930243294465
build_identifier   unknown
from_cache         False
reason             None
~~~

## Saida completa - rodada unica de 1 item
Comando executado:

~~~text
.venv\Scripts\python.exe -m app.main --once --once-max-items 1
~~~

Log completo:

~~~text
================================================================================
Ativando o ambiente virtual...
================================================================================
OK - Ambiente virtual ativado com sucesso.

================================================================================
Iniciando o programa...
================================================================================
2026-08-07 02:47:11 - INFO - policy - [GATE_STARTED] policy_version=mnscr-editorial-gate-v1 rules=33 trusted_domains=12
2026-08-07 02:47:11 - INFO - entrypoint - Configuração crítica validada com sucesso.
2026-08-07 02:47:11 - INFO - entrypoint - Verificando o esquema do banco de dados...
2026-08-07 02:47:11 - WARNING - store - [LEGACY_CINERIE_GUARD] 0 linha(s) legada(s) exigem reconciliacao de identidade antes de nova entrega ao Cinerie.
2026-08-07 02:47:11 - INFO - store - Initializing database tables if they don't exist...
2026-08-07 02:47:11 - WARNING - store - [LEGACY_CINERIE_GUARD] 0 linha(s) legada(s) exigem reconciliacao de identidade antes de nova entrega ao Cinerie.
2026-08-07 02:47:11 - INFO - store - Database initialized successfully.
2026-08-07 02:47:11 - INFO - store - Database connection closed.
2026-08-07 02:47:11 - INFO - entrypoint - Verificação do banco de dados concluída com sucesso.
2026-08-07 02:47:11 - INFO - entrypoint - Executando --once sincronamente.
2026-08-07 02:47:11 - WARNING - store - [LEGACY_CINERIE_GUARD] 0 linha(s) legada(s) exigem reconciliacao de identidade antes de nova entrega ao Cinerie.
2026-08-07 02:47:11 - INFO - store - Database connection closed.
2026-08-07 02:47:11 - WARNING - store - [LEGACY_CINERIE_GUARD] 0 linha(s) legada(s) exigem reconciliacao de identidade antes de nova entrega ao Cinerie.
2026-08-07 02:47:11 - INFO - pipeline - [EVENT_RECONCILIATION] created_tasks=41
2026-08-07 02:47:11 - INFO - store - Database connection closed.
2026-08-07 02:47:11 - INFO - task_queue - [TOKEN_TRACE] stage=pop db_id=4 token_generated='once:27236:26308:dca246175762' token_in_db_after_pop='once:27236:26308:dca246175762'
2026-08-07 02:47:11 - INFO - ai_processor - Initializing AIClient with 1 keys.
2026-08-07 02:47:11 - INFO - limiter - KEYPOOL: Inicializado com 1 chaves
2026-08-07 02:47:11 - INFO - ai_client_gemini - [GEMINI 3.1] AI CLIENT: Inicializado com 1 chaves de API | modelos=['gemini-3.1-flash-lite', 'gemini-2.5-flash-lite', 'gemini-2.5-flash']
2026-08-07 02:47:11 - INFO - pipeline - AI processor initialized lazily.
2026-08-07 02:47:11 - WARNING - store - [LEGACY_CINERIE_GUARD] 0 linha(s) legada(s) exigem reconciliacao de identidade antes de nova entrega ao Cinerie.
2026-08-07 02:47:11 - INFO - pipeline - Processing article: N/A (DB ID: 4) from rssprime_tv
2026-08-07 02:47:11 - INFO - pipeline -   Original URL: https://www.hollywoodreporter.com/tv/tv-news/disney-release-date-made-in-korea-1236663432
2026-08-07 02:47:11 - INFO - pipeline - [CLUSTER_ITEM] event_key=8733b4e3b8377c6c sources=['hollywoodreporter', 'screenrant']
2026-08-07 02:47:13 - INFO - extractor - [SUCCESS] Imagem encontrada com sucesso via Open Graph (og:image).
2026-08-07 02:47:13 - INFO - extractor - Found 1 unique YouTube videos.
2026-08-07 02:47:13 - INFO - extractor - INFO (HollywoodReporter): Iniciando limpeza de widgets, CTAs e blocos indesejados...
2026-08-07 02:47:13 - INFO - extractor - INFO (HollywoodReporter): Removendo legenda em inglês: 'Made in Korea' Season 2Disney/Hulu
2026-08-07 02:47:13 - INFO - extractor - INFO (HollywoodReporter): Limpeza concluída. Retornando HTML final.
2026-08-07 02:47:13 - INFO - extractor - INFO (hollywoodreporter.com): Usando limpador específico para HollywoodReporter
2026-08-07 02:47:13 - INFO - extractor - Successfully cleaned content for hollywoodreporter.com using specific extractor.
2026-08-07 02:47:13 - INFO - extractor - Could not find <article> tag, falling back to other selectors.
2026-08-07 02:47:14 - INFO - extractor - [SUCCESS] Imagem encontrada com sucesso via Open Graph (og:image).
2026-08-07 02:47:14 - INFO - extractor - INFO (ScreenRant): Iniciando limpeza de widgets e blocos indesejados...
2026-08-07 02:47:14 - INFO - extractor - INFO (ScreenRant): Removendo elemento com classe/id 'display-card video large no-badge' / ''
2026-08-07 02:47:14 - INFO - extractor - INFO (ScreenRant): Removendo elemento com classe/id 'display-card type-screen large' / '033e-4ae9-82daa8cb6290'
2026-08-07 02:47:14 - INFO - extractor - INFO (ScreenRant): Limpeza agressiva concluída. Retornando HTML final.
2026-08-07 02:47:14 - INFO - extractor - INFO (screenrant.com): Usando limpador específico para ScreenRant
2026-08-07 02:47:14 - INFO - extractor - Successfully cleaned content for screenrant.com using specific extractor.
2026-08-07 02:47:14 - INFO - extractor - Could not find <article> tag, falling back to other selectors.
2026-08-07 02:47:14 - INFO - policy_engine - [WORD_POLICY_CAP] source_words=852 min_calculado=681 min_aplicado=550 max_calculado=1022 max_aplicado=1000 db_id=4
2026-08-07 02:47:14 - INFO - pipeline - [CLUSTER_HEALTH] event_key=8733b4e3b8377c6c extraction_rate=2/2 (OK)
2026-08-07 02:47:14 - INFO - pipeline - [SOURCE_LENGTH] mode=cluster words=852 docs=2 images=2 db_id=4
2026-08-07 02:47:14 - INFO - pipeline - [TARGET_LENGTH] mode=cluster min_words=852 db_id=4
2026-08-07 02:47:14 - INFO - pipeline - [LINKS] writer candidates=0 db_id=4
2026-08-07 02:47:14 - INFO - pipeline - [CLUSTER_INPUT_AUDIT] event_key=8733b4e3b8377c6c source_count=2 prompt_sources_count=2 veredito=MULTI_FONTE_OK words=845 file=debug/prompt_input_disney-sets-release-date-for-season-2-of-hit-thriller-series-made-in-korea_20260807-024714.json
2026-08-07 02:47:14 - INFO - pipeline - [CLUSTER_MERGE] 2 docs mesclados para IA | event_key=8733b4e3b8377c6c
2026-08-07 02:47:14 - INFO - ai_processor - Sending batch of 1 articles to AI.
2026-08-07 02:47:14 - INFO - ai_processor - [TARGET_LENGTH] source_word_count=852 target_words=852 target_min_words=550 min_acceptable_words=550 max_recommended_words=1000
2026-08-07 02:47:14 - INFO - limiter - KEYPOOL: Usando chave ****NurQ (índice 0)
2026-08-07 02:47:14 - INFO - ai_client_gemini - [GEMINI 3.1] IA TENTATIVA 1: Chave ****NurQ | Modelo gemini-3.1-flash-lite
2026-08-07 02:47:15 - INFO - models - AFC is enabled with max remote calls: 10.
2026-08-07 02:47:22 - INFO - _client - HTTP Request: POST https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-lite:generateContent "HTTP/1.1 200 OK"
2026-08-07 02:47:22 - INFO - ai_client_gemini - TOKENS CAPTURADOS: Entrada=14470 | Saida=1416 | Total=15886 | Cache=0 | Modelo=gemini-3.1-flash-lite
2026-08-07 02:47:22 - INFO - ai_client_gemini - [GEMINI 3.1] IA OK: Tentativa 1 sucesso com chave ****NurQ | Modelo gemini-3.1-flash-lite
2026-08-07 02:47:22 - INFO - token_tracker - [GEMINI] Entrada: 14470 | Saída: 1416 | Total: 15886
2026-08-07 02:47:22 - INFO - ai_processor - BATCH OK: Processado com chave de API: ****NurQ
2026-08-07 02:47:22 - INFO - ai_processor - [AI_PARSE] parsed_on_first_try=True
2026-08-07 02:47:22 - INFO - ai_processor - Successfully processed batch of 1 articles with API key ****NurQ.
2026-08-07 02:47:22 - INFO - ai_processor - [FINAL_LENGTH] target_min_words=550 final_word_count=576 delta=26
2026-08-07 02:47:22 - WARNING - ai_processor - [EDITORIAL_QA] article=https://www.hollywoodreporter.com/tv/tv-news/disney-release-date-made-in-korea-1236663432 warnings=['vague_phrase'] counts={'vague_phrases': 1, 'generic_h2': 0, 'suspicious_lead': 0, 'missing_quotes': 0, 'missing_h2': 0}
2026-08-07 02:47:22 - INFO - ai_processor - [AI_CALLS] article_id=4 writer=1 validator=0 expansion=0 qa_semantic=0
2026-08-07 02:47:22 - INFO - policy_engine - [ARTICLE_TYPE] detected=news reason=signal_based source_words=852 db_id=4
2026-08-07 02:47:22 - INFO - policy_engine - [WORD_POLICY_CAP] source_words=852 min_calculado=681 min_aplicado=550 max_calculado=1022 max_aplicado=1000 db_id=4
2026-08-07 02:47:22 - INFO - policy_engine - [AI_VALIDATOR_DECISION] run=false reason=score_unavailable_but_deterministic_checks_passed db_id=4
2026-08-07 02:47:22 - INFO - policy_engine - [AI_BUDGET] stage=expansion allowed post_writer_tokens=0 budget=16000 writer_tokens=15886 db_id=4
2026-08-07 02:47:22 - INFO - ai_validator - [AI_WRITER] output_words=576 db_id=4
2026-08-07 02:47:22 - INFO - policy_engine - [EXPANSION_DECISION] needed=false reason=proportional_output_ok source_words=852 output_words=576 min_acceptable=550 db_id=4
2026-08-07 02:47:22 - WARNING - entity_validator - ENTITY_WARNING original="The Man Standing Next e Inside Men" canonico_proximo="The Man Standing Next and Inside Men" tipo=pessoa distancia=3
2026-08-07 02:47:22 - WARNING - entity_validator - ENTITY_WARNING original="Baek Kihyun" canonico_proximo="Baek Kitae" tipo=pessoa distancia=4
2026-08-07 02:47:22 - INFO - seo_title_optimizer - Título otimizado: 'Made in Korea ganha data de estreia para sua temporada final' → 'Made in Korea ganha data de estreia para sua temporada final'
2026-08-07 02:47:22 - INFO - seo_title_optimizer - Score: 100.0 → 100.0 (melhoria: +0.0)
2026-08-07 02:47:22 - INFO - pipeline - [QA] Made in Korea ganha data de estreia para sua tempo | score=35 | 580w | length=25(policy_min_met:580/550,target=852) | structure=10 | links=0/0 | h3=nao
2026-08-07 02:47:22 - INFO - pipeline - [QA_SCORE_BREAKDOWN] db_id=4 breakdown={'length': {'points': 25, 'reason': 'policy_min_met:580/550,target=852'}, 'structure': {'points': 10, 'has_h2': True, 'has_h3': False}, 'internal_links': {'points': 0, 'count': 0}}
2026-08-07 02:47:22 - INFO - pipeline - [META_DESC] status=OK chars=147
2026-08-07 02:47:22 - INFO - editorial_validator - [EDITORIAL_QA] type=news words=580 h2=2 ok=True status=WARNING_ONLY issues=['RAW_URL_OUTSIDE_CREDIT'] title=Made in Korea ganha data de estreia para sua temporada final
2026-08-07 02:47:22 - INFO - pipeline - [EDITORIAL_QA] ok=True status=WARNING_ONLY type=news words=580 issues=['RAW_URL_OUTSIDE_CREDIT']
2026-08-07 02:47:22 - INFO - policy_engine - [DRAFT_DECISION] allowed=true reason=processable_content score=35 db_id=4
2026-08-07 02:47:22 - INFO - html_utils - [FINAL_CLEANUP] start db_id=4
2026-08-07 02:47:22 - INFO - html_utils - [FINAL_CLEANUP] done captions_removed=0 captions_rewritten=0 entity_fixes=0 db_id=4
2026-08-07 02:47:22 - INFO - pipeline - [SLUG_SUGGESTION] slug=made-in-korea-temporada-final db_id=4
2026-08-07 02:47:22 - INFO - policy_engine - [AI_COST_ARTICLE] db_id=4 draft_id=draft-1eec0f423ce8d2c5f8e9a525d056c207 calls=2 input_tokens=14470 output_tokens=1416 total_tokens=18886 estimated_usd=0.0057 cost_model=gemini-3.1-flash-lite skipped=none draft_generated=True score=35
E:\Área de Trabalho 2\Portal The News\Nerd\MNScr\app\html_utils.py:18: MarkupResemblesLocatorWarning: The input passed in on this line looks more like a URL than HTML or XML.

If you meant to use Beautiful Soup to parse the web page found at a certain URL, then something has gone wrong. You should use an Python package like 'requests' to fetch the content behind the URL. Once you have the content as a string, you can feed that string into Beautiful Soup.

However, if you want to parse some data that happens to look like a URL, then nothing has gone wrong: you are using Beautiful Soup correctly, and this warning is spurious and can be filtered. To make this warning go away, run this code before calling the BeautifulSoup constructor:

    from bs4 import MarkupResemblesLocatorWarning
    import warnings

    warnings.filterwarnings("ignore", category=MarkupResemblesLocatorWarning)
    
  text = BeautifulSoup(value, "html.parser").get_text(separator=" ", strip=True)
2026-08-07 02:47:22 - INFO - factual_ai - [CLAIM_EXTRACTION_STARTED] draft_id=draft-1eec0f423ce8d2c5f8e9a525d056c207 prompt_version=factual-claim-extraction-v2 max_claims=100
2026-08-07 02:47:22 - INFO - limiter - KEYPOOL: Usando chave ****NurQ (índice 0)
2026-08-07 02:47:22 - INFO - ai_client_gemini - [GEMINI 3.1] IA TENTATIVA 1: Chave ****NurQ | Modelo gemini-3.1-flash-lite
2026-08-07 02:47:22 - INFO - models - AFC is enabled with max remote calls: 10.
2026-08-07 02:47:25 - INFO - _client - HTTP Request: POST https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-lite:generateContent "HTTP/1.1 200 OK"
2026-08-07 02:47:25 - INFO - ai_client_gemini - TOKENS CAPTURADOS: Entrada=1590 | Saida=520 | Total=2110 | Cache=0 | Modelo=gemini-3.1-flash-lite
2026-08-07 02:47:25 - INFO - ai_client_gemini - [GEMINI 3.1] IA OK: Tentativa 1 sucesso com chave ****NurQ | Modelo gemini-3.1-flash-lite
2026-08-07 02:47:25 - INFO - factual_ai - [CLAIM_EXTRACTION_COMPLETED] draft_id=draft-1eec0f423ce8d2c5f8e9a525d056c207 chars=1997 tokens=-
2026-08-07 02:47:25 - INFO - factual_builder - [FACTUAL_ASSESSMENT_STARTED] draft_id=draft-1eec0f423ce8d2c5f8e9a525d056c207 event_key=8733b4e3b8377c6c revision=1 mode=hybrid
2026-08-07 02:47:25 - WARNING - factual_builder - [CLAIMS_EXTRACTED] draft_id=draft-1eec0f423ce8d2c5f8e9a525d056c207 resposta de IA rejeitada: campo 'claims' ausente ou nao e lista
2026-08-07 02:47:25 - WARNING - factual_builder - [FACTUAL_MODE_DEGRADED] draft_id=draft-1eec0f423ce8d2c5f8e9a525d056c207 solicitado=hybrid efetivo=deterministic: a camada semantica nao entregou claim, o laudo e deterministico
2026-08-07 02:47:25 - INFO - factual_builder - [CLAIMS_EXTRACTED] draft_id=draft-1eec0f423ce8d2c5f8e9a525d056c207 claim_count=7
2026-08-07 02:47:25 - INFO - factual_builder - [EVIDENCE_EXTRACTED] evidence_count=23
2026-08-07 02:47:25 - INFO - matching - [CLAIM_EVIDENCE_LINKED] links=52 claims=7
2026-08-07 02:47:25 - INFO - conflicts - [FACTUAL_CONFLICT_DETECTED] type=DATE_MISMATCH subject=a values=4 critical=True
2026-08-07 02:47:25 - INFO - coverage - [FACTUAL_COVERAGE_CALCULATED] total=7 supported=0 partial=0 unsupported=1 conflicting=1 unverified=5 unmeasured=0 coverage_measured=True coverage_ratio=0.0 source_diversity=2
2026-08-07 02:47:25 - INFO - factual_builder - [FACTUAL_ASSESSMENT_COMPLETED] draft_id=draft-1eec0f423ce8d2c5f8e9a525d056c207 assessment_id=fa-a03bf265fec0ba486d106f4da90d5026 claim_count=7 evidence_count=23 conflict_count=1 coverage_ratio=0.0 assessment_hash=9e4a176b4781c671
2026-08-07 02:47:25 - INFO - policy - [GATE_STARTED] policy_version=mnscr-editorial-gate-v1 rules=33 trusted_domains=12
2026-08-07 02:47:25 - INFO - engine - [GATE_STARTED] draft_id=draft-1eec0f423ce8d2c5f8e9a525d056c207 event_key=8733b4e3b8377c6c revision=1 policy_version=mnscr-editorial-gate-v1
2026-08-07 02:47:25 - INFO - engine - [GATE_RULE_TRIGGERED] draft_id=draft-1eec0f423ce8d2c5f8e9a525d056c207 event_key=8733b4e3b8377c6c revision=1 policy_version=mnscr-editorial-gate-v1 rule_code=GATE_CRITICAL_FACT_CONFLICT severity=BLOCKING
2026-08-07 02:47:25 - INFO - engine - [GATE_RULE_TRIGGERED] draft_id=draft-1eec0f423ce8d2c5f8e9a525d056c207 event_key=8733b4e3b8377c6c revision=1 policy_version=mnscr-editorial-gate-v1 rule_code=GATE_LEGACY_INPUT_CONTRACT severity=WARNING
2026-08-07 02:47:25 - INFO - engine - [GATE_RULE_TRIGGERED] draft_id=draft-1eec0f423ce8d2c5f8e9a525d056c207 event_key=8733b4e3b8377c6c revision=1 policy_version=mnscr-editorial-gate-v1 rule_code=GATE_EVIDENCE_MISSING severity=WARNING
2026-08-07 02:47:25 - INFO - engine - [GATE_RULE_TRIGGERED] draft_id=draft-1eec0f423ce8d2c5f8e9a525d056c207 event_key=8733b4e3b8377c6c revision=1 policy_version=mnscr-editorial-gate-v1 rule_code=GATE_EVIDENCE_CONFLICT severity=WARNING
2026-08-07 02:47:25 - INFO - engine - [GATE_RULE_TRIGGERED] draft_id=draft-1eec0f423ce8d2c5f8e9a525d056c207 event_key=8733b4e3b8377c6c revision=1 policy_version=mnscr-editorial-gate-v1 rule_code=GATE_UNVERIFIED_MEDIA severity=WARNING
2026-08-07 02:47:25 - INFO - engine - [GATE_RULE_TRIGGERED] draft_id=draft-1eec0f423ce8d2c5f8e9a525d056c207 event_key=8733b4e3b8377c6c revision=1 policy_version=mnscr-editorial-gate-v1 rule_code=GATE_MULTI_SOURCE severity=INFO
2026-08-07 02:47:25 - INFO - engine - [GATE_RULE_TRIGGERED] draft_id=draft-1eec0f423ce8d2c5f8e9a525d056c207 event_key=8733b4e3b8377c6c revision=1 policy_version=mnscr-editorial-gate-v1 rule_code=GATE_LOCAL_OUTPUT_ONLY severity=INFO
2026-08-07 02:47:25 - INFO - engine - [GATE_RULE_TRIGGERED] draft_id=draft-1eec0f423ce8d2c5f8e9a525d056c207 event_key=8733b4e3b8377c6c revision=1 policy_version=mnscr-editorial-gate-v1 rule_code=GATE_FACTUAL_COVERAGE_LOW severity=WARNING
2026-08-07 02:47:25 - INFO - engine - [GATE_RULE_TRIGGERED] draft_id=draft-1eec0f423ce8d2c5f8e9a525d056c207 event_key=8733b4e3b8377c6c revision=1 policy_version=mnscr-editorial-gate-v1 rule_code=GATE_UNVERIFIED_CLAIMS_PRESENT severity=WARNING
2026-08-07 02:47:25 - INFO - engine - [GATE_COMPLETED] draft_id=draft-1eec0f423ce8d2c5f8e9a525d056c207 event_key=8733b4e3b8377c6c revision=1 policy_version=mnscr-editorial-gate-v1 outcome=GATE_BLOCKED blocking_count=1 warning_count=6 gate_hash=072cbe41591fe071
2026-08-07 02:47:25 - INFO - engine - [GATE_BLOCKED] draft_id=draft-1eec0f423ce8d2c5f8e9a525d056c207 regras=GATE_CRITICAL_FACT_CONFLICT,GATE_LEGACY_INPUT_CONTRACT,GATE_EVIDENCE_MISSING,GATE_EVIDENCE_CONFLICT,GATE_UNVERIFIED_MEDIA,GATE_MULTI_SOURCE,GATE_LOCAL_OUTPUT_ONLY,GATE_FACTUAL_COVERAGE_LOW,GATE_UNVERIFIED_CLAIMS_PRESENT
2026-08-07 02:47:25 - ERROR - pipeline - [GATE_BLOCKED] draft_id=draft-1eec0f423ce8d2c5f8e9a525d056c207 db_id=4 regras=GATE_CRITICAL_FACT_CONFLICT
2026-08-07 02:47:25 - INFO - local - [LOCAL_DRAFT] gravado draft_id=draft-1eec0f423ce8d2c5f8e9a525d056c207 path=artifacts\local-drafts\draft-1eec0f423ce8d2c5f8e9a525d056c207.json bytes=87976
2026-08-07 02:47:25 - INFO - pipeline - [DRAFT_SUBMIT] draft_id=draft-1eec0f423ce8d2c5f8e9a525d056c207 destino=local status=WRITTEN ok=True path=artifacts\local-drafts\draft-1eec0f423ce8d2c5f8e9a525d056c207.json
2026-08-07 02:47:25 - INFO - store - Draft registrado para artigo 4 (draft_id=draft-1eec0f423ce8d2c5f8e9a525d056c207 destino=local).
2026-08-07 02:47:25 - INFO - pipeline - DRAFT GERADO: draft-1eec0f423ce8d2c5f8e9a525d056c207 | Made in Korea ganha data de estreia para sua temporada final
2026-08-07 02:47:25 - INFO - pipeline - [CONFIG_FEATURE_SKIPPED] feature=draft_delivery variable=PAYLOAD_CMS_ENABLED state=disabled
2026-08-07 02:47:25 - ERROR - cinerie_service - [cinerie] evento legado 8733b4e3b8377c6c sem identidade remota mapeada; publicacao bloqueada para evitar segundo artigo
2026-08-07 02:47:25 - INFO - pipeline - [CINERIE_PUBLICATION] draft_id=draft-1eec0f423ce8d2c5f8e9a525d056c207 {'delivery_status': 'BLOCKED', 'request_id': None, 'outcome': None, 'article_id': None, 'reason_codes': [], 'next_eligible_at': None, 'attempts': 0, 'error_code': 'LEGACY_CINERIE_IDENTITY_UNMAPPED'}
2026-08-07 02:47:25 - INFO - pipeline - [CINERIE_QUOTA_DEFERRALS] deferred_total=0 dimensions=none
2026-08-07 02:47:25 - INFO - pipeline - [LINKS] link_store ignorado draft_id=draft-1eec0f423ce8d2c5f8e9a525d056c207 motivo=output_mode=local sem endereco publico para o draft
2026-08-07 02:47:25 - INFO - pipeline - [SUPERFEED_COVERAGE] URLs registradas apos draft event_key=8733b4e3b8377c6c seen_article_id=4 draft_id=draft-1eec0f423ce8d2c5f8e9a525d056c207
2026-08-07 02:47:25 - INFO - pipeline - Aguardando 30s para o proximo artigo...
2026-08-07 02:47:55 - INFO - store - Database connection closed.
2026-08-07 02:47:55 - WARNING - store - [LEGACY_CINERIE_GUARD] 0 linha(s) legada(s) exigem reconciliacao de identidade antes de nova entrega ao Cinerie.
2026-08-07 02:47:55 - INFO - store - Database connection closed.
2026-08-07 02:47:55 - INFO - pipeline - [ONCE_SUMMARY] initial=28 reconciled=41 ingested=0 claimed=1 completed=1 editorial_blocked=1 review_required=0 technical_failed=0 remaining=68 duration_ms=44202 stop_reason=item_limit outcome=incomplete exit_code=5
2026-08-07 02:47:55 - INFO - entrypoint - [ONCE_RESULT] {'initial': 28, 'reconciled': 41, 'ingested': 0, 'claimed': 1, 'completed': 1, 'editorial_blocked': 1, 'review_required': 0, 'technical_failed': 0, 'remaining': 68, 'stop_reason': 'item_limit', 'duration_ms': 44202, 'success': False, 'exit_code': 5}
2026-08-07 02:47:55 - INFO - entrypoint - Ciclo --once finalizado.

Command exited with code 1
~~~

## Arquivos de referencia
- Log bruto salvo pelo ambiente: c:\Users\pablo\AppData\Roaming\Code\copilot-terminal-output\copilot-terminal-output-80253ecb-5f19-4fb9-978a-dd36142fcc10.txt
- Documento deste relatorio: docs/relatorio-execucao-cinerie-2026-08-07.md
