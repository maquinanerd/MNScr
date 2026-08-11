# MNScr no EasyPanel — preparação para deploy

Este documento é preparação, não migração. Nada foi movido para servidor;
ele existe para que a migração, quando acontecer, não repita os acidentes
da consolidação de worktrees locais (dois `.env` divergentes, um `app.db`
sobrescrito, uma flag desligada em silêncio).

## Por que isso importa: disco efêmero

Um container do EasyPanel tem disco efêmero por padrão. Se `data/app.db`
ficar dentro da imagem (ou em qualquer caminho que não seja um Volume
montado), **todo novo deploy apaga a memória do robô**. Na próxima
execução ele não vai saber o que já publicou — vai reprocessar o backlog
inteiro contra a IA (custo de tokens Gemini) e cada matéria vai levar um
409/conflito do lado do Cinerie ao tentar publicar de novo algo que já
está no ar. A idempotência do servidor evita a duplicata, mas não evita o
gasto nem o ruído no log.

## a) Pasta que precisa ser Volume persistente

Monte **`data/`** como Volume persistente. É onde vive:

- `data/app.db` — o banco operacional: fila de artigos, eventos RSS
  Prime, resultados do editorial gate, avaliação factual e,
  principalmente, `cinerie_publications` (o que já foi publicado — é
  essa tabela que impede republicação).
- `data/backups/` — se o processo de backup local continuar rodando no
  container.

Considere também, se quiser preservar histórico entre deploys:

- `artifacts/local-drafts/` — rascunhos gerados quando
  `MNSCR_OUTPUT_MODE=local`. Dispensável se o modo de saída no servidor
  for outro, mas hoje é o modo configurado.
- `logs/` — não afeta idempotência nem republicação; perder logs em um
  redeploy é aceitável, só atrapalha investigação de incidente passado.

O caminho do banco já não está mais fixo no código: `app/store.py`,
`app/cinerie_store.py`, `app/delivery_store.py`, `app/delivery_service.py`,
`app/event_store.py`, `app/factual_store.py`, `app/factual_service.py`,
`app/gate_store.py`, `app/gate_service.py` e `app/task_queue.py` agora lêem
o caminho de `app.config.DB_PATH`, que por sua vez lê a variável de
ambiente **`MNSCR_DB_PATH`** (default preservado: `data/app.db`, o mesmo
comportamento de sempre quando a variável não é definida). Isso permite
apontar o banco para o ponto de montagem do Volume sem editar código —
por exemplo `MNSCR_DB_PATH=/data/app.db` com o Volume montado em `/data`.

## b) Variáveis de ambiente que o serviço precisa

Lista completa dos NOMES (nunca os valores) presentes no `.env`
consolidado, 143 chaves. Marcadas com 🔒 as que são segredo — token,
chave de API ou credencial — e por isso NÃO devem ir como variável de
ambiente simples no EasyPanel (ver seção de segredos abaixo).

### Banco de dados
- `MNSCR_DB_PATH` — novo; aponte para o Volume (ex.: `/data/app.db`)

### Cinerie — publicação, catálogo, capa
- `MNSCR_DELIVERY_MODE`
- `MNSCR_ENVIRONMENT`
- `PAYLOAD_INTERNAL_SERVICE_URL`
- 🔒 `MNSCR_PAYLOAD_API_KEY`
- `MNSCR_PUBLIC_AUTHOR_ID`
- `MNSCR_ATTRIBUTION_MODE`
- `MNSCR_OWN_CMS_DOMAINS`
- `MNSCR_EDITORIAL_GATE_POLICY`
- `MNSCR_CONTRACT_PREFLIGHT_TTL_SECONDS`
- `MNSCR_CINERIE_TIMEOUT_SECONDS`
- `MNSCR_CINERIE_MAX_ATTEMPTS`
- `MNSCR_CINERIE_RETRY_BASE_SECONDS`
- `MNSCR_CINERIE_MEDIA_UPLOAD_ENABLED`
- 🔒 `MNSCR_CINERIE_MEDIA_API_KEY`
- `MNSCR_CINERIE_CATALOG_RESOLVE_URL`
- 🔒 `CINERIE_CATALOG_RESOLVE_API_KEYS`
- `CINERIE_PUBLIC_BASE_URL`
- `ALLOW_SINGLE_SOURCE`

### Editorial Gate / avaliação factual / taxonomia
- `MNSCR_EDITORIAL_GATE_ENABLED`
- `MNSCR_EDITORIAL_GATE_POLICY_DIR`
- `MNSCR_FACTUAL_ASSESSMENT_ENABLED`
- `MNSCR_FACTUAL_ASSESSMENT_VERSION`
- `MNSCR_FACTUAL_ASSESSMENT_MODE`
- `MNSCR_FACTUAL_PROMPT_VERSION`
- `MNSCR_FACTUAL_PROMPT_DIR`
- `MNSCR_MAX_CLAIMS_PER_DRAFT`
- `MNSCR_MAX_EVIDENCE_PER_CLAIM`
- `MNSCR_MAX_EVIDENCE_EXCERPT_CHARS`
- `MNSCR_MIN_FACTUAL_COVERAGE_RATIO`

### Entrada / saída editorial (RSS Prime, drafts)
- `MNSCR_OUTPUT_MODE`
- `MNSCR_LOCAL_DRAFT_DIR`
- `MNSCR_ACCEPT_LEGACY_FEED`
- `MNSCR_REQUIRED_INPUT_CONTRACT`
- `MNSCR_STORE_RAW_EVENT_PAYLOAD`
- `MNSCR_MAX_EVENT_PAYLOAD_BYTES`
- `MNSCR_USER_AGENT`
- `MNSCR_PROMPT_VERSION`

### Payload CMS (ms1 submitter + ms5 draft delivery — dois canais distintos)
- `PAYLOAD_ENABLED`
- `PAYLOAD_BASE_URL`
- 🔒 `PAYLOAD_API_TOKEN`
- `PAYLOAD_DRAFTS_COLLECTION`
- `PAYLOAD_CMS_ENABLED`
- `PAYLOAD_CMS_BASE_URL`
- `PAYLOAD_CMS_DRAFT_ENDPOINT`
- 🔒 `PAYLOAD_CMS_TOKEN`
- `PAYLOAD_CMS_COLLECTION`
- `PAYLOAD_CMS_TIMEOUT_SECONDS`
- `PAYLOAD_CMS_MAX_ATTEMPTS`
- `PAYLOAD_CMS_RETRY_BASE_SECONDS`

### Identidade editorial
- `PUBLISHER_NAME`
- `PUBLISHER_DOMAIN`
- `PUBLISHER_LOGO_URL`

### Gemini / IA
- 🔒 `GEMINI_KEY_1`
- `GEMINI_MODEL_ID`
- `AI_MODEL`
- `GEMINI_FALLBACK_MODELS`
- `AI_MAX_RETRIES`
- `AI_MIN_INTERVAL_S`
- `BACKOFF_BASE_S`
- `BACKOFF_MAX_S`
- `AI_VALIDATOR_ENABLED`
- `AI_VALIDATOR_MODEL`
- `AI_VALIDATOR_MAX_RETRIES`
- `AI_VALIDATOR_FAIL_OPEN`
- `AI_POST_WRITER_BUDGET_TOKENS`
- `AI_POST_WRITER_BUDGET_PER_1K_SOURCE`
- `AI_BUDGET_NEWS_TOKENS`
- `AI_BUDGET_LIST_TOKENS`
- `AI_BUDGET_GUIDE_TOKENS`
- `AI_BUDGET_ANALYSIS_TOKENS`
- `AI_BUDGET_SUPERFEED_TOKENS`
- `AI_BUDGET_FALLBACK_TOKENS`

### Agendador / pipeline
- `CHECK_INTERVAL_MINUTES`
- `MAX_ARTICLES_PER_FEED`
- `MAX_PER_FEED_CYCLE`
- `MAX_PER_CYCLE`
- `ARTICLE_SLEEP_S`
- `BETWEEN_BATCH_DELAY_S`
- `BETWEEN_PUBLISH_DELAY_S`
- `FEED_STAGGER_S`
- `ARTICLE_WATCHDOG_TIMEOUT_S`
- `MAX_ARTICLE_FAIL_COUNT`
- `PER_ARTICLE_DELAY_SECONDS`
- `PER_FEED_DELAY_SECONDS`
- `CLEANUP_AFTER_HOURS`
- `IMAGES_MODE`

### Política de publicação / indexação
- `PUBLISH_ALL_PROCESSABLE`
- `ALLOW_LOW_SCORE_PUBLISH`
- `INDEX_MIN_SCORE`
- `NOINDEX_BELOW_SCORE`
- `ALLOW_LOW_SCORE_INDEX`
- `FORCE_INDEX_ALL_POSTS`
- `ALLOW_MANUAL_INDEX_OVERRIDE`
- `ENABLE_QA_LLM_BORDERLINE`

### TMDB / Movie Hub
- `TMDB_ENABLED`
- 🔒 `TMDB_API_KEY`
- 🔒 `TMDB_ACCESS_TOKEN`
- `TMDB_LANGUAGE`
- `TMDB_REGION`
- `TMDB_DB_PATH`
- `TMDB_SYNC_TRENDING`
- `TMDB_SYNC_UPCOMING`
- `TMDB_SYNC_WATCH_PROVIDERS`
- `TMDB_ARTICLE_ENRICHMENT`
- `TMDB_MAX_ARTICLE_BLOCKS`
- `TMDB_MIN_MATCH_CONFIDENCE`
- `TMDB_HUB_ENABLED`
- `TMDB_HUB_DRAFT_MODE`
- `TMDB_AUTO_PUBLISH_HUB`
- `TMDB_REQUEST_TIMEOUT`
- `TMDB_RATE_LIMIT_SLEEP`

### Indexador / Google Indexing / URL Inspection
- `INDEXER_NOTIFY_ON_PUBLISH`
- `INDEXER_NOTIFY_PROCESS_IMMEDIATELY`
- `INDEXER_INTERVAL_MINUTES`
- `INDEXER_POST_LIMIT`
- `INDEXER_TIMEOUT`
- `INDEXER_DB_PATH`
- `GOOGLE_INDEXING_ENABLED`
- 🔒 `GOOGLE_INDEXING_CREDENTIALS_FILE` (caminho para JSON de service account — o
  arquivo em si é o segredo, não uma string curta; ver seção de segredos)
- `GOOGLE_URL_INSPECTION_ENABLED`
- `GOOGLE_URL_INSPECTION_SITE_URL`
- `GOOGLE_URL_INSPECTION_LANGUAGE_CODE`

### Yoast News Sitemap / IndexNow / pings / recrawl / discovery
- `YOAST_NEWS_SITEMAP_ENABLED`
- `YOAST_NEWS_SITEMAP_URL`
- `YOAST_NEWS_SITEMAP_VALIDATE_RECENT_POSTS`
- `YOAST_NEWS_SITEMAP_TIMEOUT`
- `INDEXNOW_ENABLED`
- 🔒 `INDEXNOW_KEY`
- `INDEXNOW_KEY_LOCATION`
- `INDEXNOW_ENDPOINT`
- `SITEMAP_PING_ENABLED`
- `SITEMAP_PING_ENDPOINTS`
- `RSS_PING_ENABLED`
- `RSS_PING_XMLRPC_URL`
- `RECRAWL_ENABLED`
- `RECRAWL_DELAY_MINUTES`
- `RECRAWL_MAX_ATTEMPTS`
- `RECRAWL_INCLUDE_INITIAL_PUBLISH`
- `DISCOVERY_RELATED_LINKS_ENABLED`
- `DISCOVERY_RELATED_LINKS_LIMIT`

### Dashboard / logging
- 🔒 `SECRET_KEY`
- `LOG_LEVEL`
- 🔒 `DASHBOARD_TOKEN`

## c) Como levar o `app.db` consolidado para o Volume antes da primeira execução

1. Crie o Volume no EasyPanel e monte-o (ex.: em `/data`).
2. Copie o `data/app.db` consolidado — o mesmo que está hoje na raiz do
   repositório, com a tabela `cinerie_publications` completa — para dentro
   desse Volume, por fora do processo normal de deploy (upload manual,
   `scp`, ou o mecanismo de arquivos do próprio EasyPanel). Isso acontece
   **antes** de o serviço rodar pela primeira vez contra aquele Volume.
3. Configure `MNSCR_DB_PATH` para o caminho dentro do Volume (ex.:
   `/data/app.db`).
4. Só então inicie o serviço.

**Por que subir vazio custa dinheiro:** se o serviço subir com um Volume
vazio (sem o `app.db` consolidado dentro), `cinerie_publications` nasce
zerada. O robô não tem como saber que already existem ~29 matérias no ar
— ele vai tratá-las como novidade, gastar cota de Gemini reescrevendo
cada uma, e levar um 409/conflito do Cinerie ao tentar publicar. Nenhuma
duplicata chega a ficar visível (a idempotência do lado do servidor
segura isso), mas o custo de IA e o ruído no log já aconteceram — e por
sorte de desenho do outro lado, não por garantia deste.

## Segredo em variável de ambiente do EasyPanel: o aviso

Segredo passado como `--build-arg` (ou qualquer variável definida em
**tempo de build**) fica gravado no log de build e cristalizado em uma
camada da imagem Docker — mesmo que a variável não apareça no container
final rodando, ela continua recuperável de dentro da imagem publicada,
por qualquer um que tenha acesso a ela. Isso é diferente de uma env var
de **runtime** (definida só para o container em execução, não usada
durante `docker build`), que não fica gravada em camada nenhuma.

Ainda assim, o padrão já adotado no CMS (`maquinanerd/screena`) é usar
**File Mount** para segredo, não env var — mesmo em runtime. O mesmo vale
para o MNScr: prefira montar `GEMINI_KEY_1`, `MNSCR_PAYLOAD_API_KEY`,
`MNSCR_CINERIE_MEDIA_API_KEY`, `CINERIE_CATALOG_RESOLVE_API_KEYS`,
`SECRET_KEY`, `DASHBOARD_TOKEN`, `TMDB_API_KEY`, `TMDB_ACCESS_TOKEN`,
`INDEXNOW_KEY`, `PAYLOAD_API_TOKEN`, `PAYLOAD_CMS_TOKEN` e o JSON de
`GOOGLE_INDEXING_CREDENTIALS_FILE` como arquivo montado (por exemplo um
`.env` de runtime só com esses segredos, ou arquivos individuais),
em vez de colar o valor no painel de variáveis do EasyPanel. Variável de
ambiente de painel ainda aparece em qualquer inspeção do processo
(`/proc/<pid>/environ`, painéis de admin, integrações de log) — File
Mount reduz essa superfície.

## Dockerfile

Não existe Dockerfile (nem `docker-compose.yml`) em nenhum lugar deste
repositório hoje. O ponto de entrada atual é `python -m app.main`
(`iniciar.bat`) ou o script `mnscr` registrado em `pyproject.toml`
(`app.entrypoint:main`). Criar o Dockerfile fica fora do escopo desta
tarefa — é preparação, não migração — mas será necessário antes do
primeiro deploy no EasyPanel.
