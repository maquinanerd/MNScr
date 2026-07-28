# MNScr

Motor editorial do **Cinerie**. O MNScr consome acontecimentos do RSS Prime, extrai e
qualifica as fontes, redige com IA e entrega um **draft editorial estruturado** para
revisão humana.

O MNScr **não publica, não aprova e não indexa**. A autoridade editorial final é humana
e vive no CMS.

## O que faz

- Ingere o Superfeed do RSS Prime e feeds de fallback.
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

## Configuração

| Variável | Padrão | Papel |
|---|---|---|
| `MNSCR_OUTPUT_MODE` | `local` | `local` ou `payload`. `wordpress` é rejeitado. |
| `MNSCR_LOCAL_DRAFT_DIR` | `artifacts/local-drafts` | Onde os drafts são gravados. |
| `PAYLOAD_ENABLED` | `false` | Habilita o destino Payload (ainda bloqueado). |
| `PAYLOAD_BASE_URL` | — | URL do Payload CMS. |
| `PAYLOAD_API_TOKEN` | — | Token do Payload CMS. |
| `PAYLOAD_DRAFTS_COLLECTION` | `editorial-drafts` | Collection de drafts. |
| `GEMINI_KEY_*` | — | Uma ou mais chaves Gemini. |
| `PUBLISHER_DOMAIN` | — | Domínio usado só para sugerir links internos. |

O `.env.example` traz a lista completa.

## Estrutura

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

Não existe estado `PUBLISHED`, `APPROVED` ou `PUBLICATION_READY` — tentar atribuí-los
levanta `ForbiddenStateError`.

## Testes

```powershell
python -m pytest
```

## Limitações conhecidas

- O contrato de entrada do RSS Prime ainda não é versionado. O MNScr aceita dois
  namespaces `sf:*` alternativos e usa `input_contract_version = "legacy-feed-input-v0"`
  como marcador **temporário**.
- Não há cursor, número de revisão de acontecimento nem replay: um `event_key` já visto
  é descartado, mesmo que o cluster tenha ganhado fontes.
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
