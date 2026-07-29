# MNScr

Motor editorial do **Cinerie**. O MNScr consome acontecimentos do RSS Prime, extrai e
qualifica as fontes, redige com IA e entrega um **draft editorial estruturado** para
revisão humana.

O MNScr **não publica, não aprova e não indexa**. A autoridade editorial final é humana
e vive no CMS.

## O que faz

- Ingere o Superfeed do RSS Prime e feeds de fallback sob um **contrato de entrada versionado**.
- Valida cada acontecimento antes de colocá-lo no pipeline; o inválido não avança e não move o cursor.
- Deduplica por `event_key`, assinatura de cluster, URL canônica e similaridade de título.
- Extrai e qualifica múltiplas fontes por acontecimento (limpadores por veículo).
- Redige em português brasileiro com Gemini, com política de tamanho proporcional à fonte.
- Aplica QA determinístico (título, meta, subtitle, CTA, entidades, estrutura).
- Monta um `EditorialDraft` com fontes, evidências, candidatos de mídia e proveniência.
- Entrega o draft a um *submitter*.

## O que NÃO faz

Nenhuma destas ações existe no caminho canônico:

publicar em WordPress · criar post · criar categoria · criar tag · enviar imagem ·
alterar Yoast · disparar Google News · enviar IndexNow · pingar sitemap ·
validar News Sitemap · executar URL Inspection · agendar recrawl ·
marcar conteúdo como publicado · aprovar conteúdo · escrever no banco do Cinerie.

O modo de saída `wordpress` é rejeitado no startup com a mensagem
`WordPress output is disabled in MNScr.`

## CMS editorial

O CMS definido para o Cinerie é o **Payload CMS**.

A integração efetiva **ainda está bloqueada** pelo contrato editorial: forma da collection,
campos obrigatórios, fluxo de revisão e política de mídia são decisões do Cinerie Editorial.

O `PayloadDraftSubmitter` já existe com configuração, validação e transformação de
`EditorialDraft` para um documento neutro do Payload — mas **não transmite nada**.
Com `PAYLOAD_ENABLED=false` ele não faz rede; com `PAYLOAD_ENABLED=true` ele falha com:

```
Payload submission is not enabled until the Cinerie Editorial contract is finalized.
```

## Modo operacional atual

`local` — o draft é gravado como JSON UTF-8 em `artifacts/local-drafts/{draft_id}.json`.

A escrita é atômica e idempotente:

| Situação | Resultado |
|---|---|
| arquivo não existe | `WRITTEN` |
| arquivo existe com o mesmo `output_hash` | `UNCHANGED` (sucesso, sem reescrita) |
| arquivo existe com `output_hash` diferente | `CONFLICT` — nada é sobrescrito; o draft recebido é preservado como `{draft_id}.conflict-{hash}.json` |

## Instalação

Requer Python 3.11 ou superior. A fonte canônica de dependências é o `pyproject.toml`.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
Copy-Item .env.example .env
```

Preencha no `.env` ao menos uma `GEMINI_KEY_*`. Credenciais de WordPress **não são mais
necessárias** e não são lidas.

## Operação

```powershell
python main.py --once   # um ciclo
python main.py          # execução contínua (a cada CHECK_INTERVAL_MINUTES)
```

`indexer.py` está desabilitado: ele apenas informa
`Legacy indexing is disabled in MNScr.` e sai com código 1.

## Contrato de entrada

O contrato canônico do RSS Prime é **`rss-prime-event-v1`**.

Três coisas são mantidas separadas de propósito:

| Conceito | Identidade | O que é |
|---|---|---|
| acontecimento | `event_key` | estável entre revisões |
| revisão | `revision` | uma versão numerada do acontecimento |
| payload | `payload_hash` | o que chegou naquela revisão |

O `payload_hash` é SHA-256 sobre o JSON canônico (UTF-8, chaves ordenadas,
separadores determinísticos, datas normalizadas para UTC, fontes ordenadas de
forma estável). O próprio hash e os campos de transporte — `cursor`,
`received_at`, `delivery_id` — ficam **fora** do conteúdo hasheado, então uma
reentrega ruidosa não vira uma revisão falsa.

Quando o RSS Prime envia `payload_hash`, o MNScr valida e registra
`payload_hash_origin = "rss-prime-verified"`. No modo legado ele calcula um hash
local e registra `payload_hash_origin = "mnscr-calculated-legacy"` — as duas
coisas nunca se confundem.

### Regras de revisão

| Situação | Resultado |
|---|---|
| `event_key` novo | aceito |
| mesma revisão, mesmo `payload_hash` | idempotente — nenhum draft novo |
| mesma revisão, `payload_hash` diferente | rejeitado — `REVISION_HASH_CONFLICT` |
| revisão maior | aceito — nova execução, nova revisão do draft |
| revisão menor que a última processada | rejeitado — `STALE_REVISION` |
| salto de revisão (2 → 5) | **aceito** com warning `REVISION_GAP`; as revisões ausentes são registradas, nunca inventadas |

A revisão 2 gera um `draft_id` determinístico diferente do da revisão 1, então
uma revisão nova nunca sobrescreve silenciosamente a anterior.

### Cursor

O cursor é **opaco**: o MNScr guarda e devolve o valor como veio, sem interpretar,
sem tratar como timestamp e sem ordenar por ele.

Ele só avança depois que o evento foi validado *e* persistido, na mesma
transação. Não avança em payload inválido, em conflito de revisão nem em revisão
antiga. Quando o feed não envia cursor, nenhum é inventado: a fonte fica marcada
com `cursor_mode = "unavailable"`.

### Compatibilidade legada

O superfeed anterior (`sf:*`) ainda é aceito como **`legacy-feed-input-v0`** por
um adaptador temporário, sempre com o warning `LEGACY_CONTRACT`.

O adaptador não finge que o RSS Prime enviou o que ele não envia: autor por
fonte, data por fonte, `cluster_confidence` e cursor permanecem `null`, e cada
ausência vira um warning estruturado. Campos que o MNScr não modela são
preservados em `metadata.legacy_unmapped` — desconhecido continua desconhecido.

Com `MNSCR_ACCEPT_LEGACY_FEED=false`, ou com
`MNSCR_REQUIRED_INPUT_CONTRACT=rss-prime-event-v1`, itens sem contrato são
rejeitados com `LEGACY_CONTRACT_DISABLED`.

## Replay

Replay reprocessa um acontecimento **lendo apenas o SQLite local**. Ele não
consulta o RSS Prime, não usa o feed, não altera o payload original, não muda a
revisão de entrada, não gera novo `event_key`, não avança o cursor e não publica.
O que ele cria é uma nova *tentativa operacional*, registrada no histórico.

```powershell
python main.py --list-event-revisions <event_key>
python main.py --replay-event <event_key>
python main.py --replay-event <event_key> --revision 2
```

`--list-event-revisions` mostra `revision`, `payload_hash`, `validation_status`,
`processing_status`, `draft_id` e `received_at`. O payload completo não é
exibido por padrão.

## Configuração

| Variável | Padrão | Papel |
|---|---|---|
| `MNSCR_OUTPUT_MODE` | `local` | `local` ou `payload`. `wordpress` é rejeitado. |
| `MNSCR_LOCAL_DRAFT_DIR` | `artifacts/local-drafts` | Onde os drafts são gravados. |
| `MNSCR_ACCEPT_LEGACY_FEED` | `true` | **Temporário.** Aceita o superfeed pré-contrato. |
| `MNSCR_REQUIRED_INPUT_CONTRACT` | — | Fixe em `rss-prime-event-v1` para rejeitar o legado. |
| `MNSCR_STORE_RAW_EVENT_PAYLOAD` | `true` | Guarda o payload bruto para replay fiel. |
| `MNSCR_MAX_EVENT_PAYLOAD_BYTES` | `1048576` | Teto validado antes de persistir. |
| `PAYLOAD_ENABLED` | `false` | Habilita o destino Payload (ainda bloqueado). |
| `PAYLOAD_BASE_URL` | — | URL do Payload CMS. |
| `PAYLOAD_API_TOKEN` | — | Token do Payload CMS. |
| `PAYLOAD_DRAFTS_COLLECTION` | `editorial-drafts` | Collection de drafts. |
| `GEMINI_KEY_*` | — | Uma ou mais chaves Gemini. |
| `PUBLISHER_DOMAIN` | — | Domínio usado só para sugerir links internos. |

O `.env.example` traz a lista completa.

## Estrutura

- `app/contracts/` — contrato de entrada versionado (`rssprime_event_v1`, `legacy_feed_v0`, `validation`, `hashing`, `revisions`, `states`, `errors`)
- `app/ingestion.py` — valida, decide a revisão e persiste o evento recebido
- `app/event_store.py` — eventos, cursores e histórico de tentativas (SQLite)
- `app/replay.py` — replay local a partir do payload persistido
- `app/editorial/` — modelo de domínio do draft (`models`, `states`, `serialization`, `builder`)
- `app/submitters/` — contrato de submissão (`base`), `LocalDraftSubmitter`, `PayloadDraftSubmitter`
- `app/pipeline.py` — orquestração do ciclo
- `app/store.py` — SQLite operacional (`data/app.db`)
- `tests/` — suíte automatizada

## Estados operacionais

| Estado | Significado |
|---|---|
| `NEW` / `QUEUED` | aguardando processamento |
| `PROCESSING` | reivindicado por um worker |
| `DRAFT_GENERATED` | **sucesso** — draft gerado e submetido |
| `DRAFT_FAILED` | falha na geração ou na submissão |
| `SUPERSEDED` | coberto por um item do Superfeed |
| `SKIPPED` | bloqueado por política editorial |

Estados de ingestão (contrato de entrada):

| Estado | Significado |
|---|---|
| `RECEIVED` | evento recebido, ainda não decidido |
| `VALIDATED` | passou no contrato — único estado que segue para o pipeline |
| `INVALID` | reprovado na validação |
| `DUPLICATE_DELIVERY` | reentrega idêntica; nada é regerado |
| `REVISION_HASH_CONFLICT` | mesma revisão com payload diferente |
| `STALE_REVISION` | revisão anterior à última processada |
| `REPLAY_REQUESTED` / `REPLAY_PROCESSING` / `REPLAY_COMPLETED` / `REPLAY_FAILED` | ciclo de vida de um replay |

Não existe estado `PUBLISHED`, `APPROVED` ou `PUBLICATION_READY` — tentar atribuí-los
levanta `ForbiddenStateError`.

## Testes

```powershell
python -m pytest
```

## Limitações conhecidas

- O RSS Prime **ainda não emite** `rss-prime-event-v1`. O contrato existe e é
  validado ponta a ponta no MNScr, mas até a origem passar a enviá-lo o caminho
  real continua sendo o adaptador legado, com hash calculado localmente, sem
  revisões reais e sem cursor.
- Sem cursor vindo da origem, não há retomada incremental: o modo legado opera
  com `cursor_mode = "unavailable"`.
- O envelope de transporte do v1 não está especificado pelo RSS Prime. O MNScr
  reconhece um documento JSON embutido no item, e deliberadamente não inventa um
  XSD nem um namespace XML para representar o domínio.
- Não há estrutura de conflito factual. Divergência entre fontes ainda é resolvida por
  instrução de prompt, sem registro.
- Autor e data da fonte não são extraídos no caminho ativo; ficam `null` na proveniência.
- `output_contract_version = "mnscr-editorial-draft-v0"` é **provisório** e será
  renegociado com o Cinerie Editorial.
- Não há Dockerfile, CI nem healthcheck. Execução ainda é manual.
- Candidatos de mídia são registrados com `license = null`, `credit = null` e
  `status = "unverified"`. Nenhuma imagem é baixada ou republicada.

## Segurança

Nunca versione `.env`, contas de serviço, bancos SQLite, logs ou drafts gerados.
O diretório `artifacts/local-drafts/` está no `.gitignore`.
