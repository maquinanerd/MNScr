# MNScr

Motor editorial do **Cinerie**. O MNScr consome acontecimentos do RSS Prime, extrai e
qualifica as fontes, redige com IA e entrega um **draft editorial estruturado** para
revisão humana.

O MNScr **pede** publicação ao Cinerie; quem decide é o Cinerie. Ele **não aprova e
não indexa**, e não decide estado público: autor autorizado, QA, fontes, mídia
licenciada, SEO, tetos diários e kill switch são revalidados no servidor.

> **Mudança de postura (fase atual).** Até a MS-5 o MNScr só propunha rascunhos.
> Agora existe um caminho automático — e o verbo continua sendo *pedir*. Um pedido
> inválido não vira matéria publicada: vira `ROUTED_TO_REVIEW` ou `BLOCKED`.
> Ver [`docs/cinerie-autopublication.md`](docs/cinerie-autopublication.md).

## O que faz

- Ingere o Superfeed do RSS Prime e feeds de fallback sob um **contrato de entrada versionado**.
- Valida cada acontecimento antes de colocá-lo no pipeline; o inválido não avança e não move o cursor.
- Deduplica por `event_key`, assinatura de cluster, URL canônica e similaridade de título.
- Extrai e qualifica múltiplas fontes por acontecimento (limpadores por veículo).
- Redige em português brasileiro com Gemini, com política de tamanho proporcional à fonte.
- Aplica QA determinístico (título, meta, subtitle, CTA, entidades, estrutura).
- Monta um `EditorialDraft` com fontes, evidências, candidatos de mídia e proveniência.
- Estrutura o que o draft **afirma** e o que as fontes **sustentam** ou **contradizem**.
- Classifica cada draft no **Editorial Gate** versionado antes da gravação.
- Entrega o draft a um *submitter* local.
- Opcionalmente entrega o draft ao **Payload CMS**, sempre como `draft`.
- Opcionalmente **pede publicação** ao Cinerie sob o contrato versionado
  `editorial-publication-request-v1`, com preflight de contrato e SEO neutro.

## O que NÃO faz

Nenhuma destas ações existe no caminho canônico:

publicar em WordPress · criar post · criar categoria · criar tag · enviar imagem ·
produzir campos Yoast · disparar Google News · enviar IndexNow · pingar sitemap ·
validar News Sitemap · executar URL Inspection · agendar recrawl ·
decidir canonical, robots, JSON-LD ou indexabilidade · aprovar conteúdo ·
escrever no banco do Cinerie.

O destino final **não é WordPress**, e Yoast **não faz parte** de nenhum contrato:
o objeto do plugin foi substituído por campos neutros (`openGraphTitleSuggestion` e
companhia), que é o que o contrato do Cinerie aceita.

O modo de saída `wordpress` é rejeitado no startup com a mensagem
`WordPress output is disabled in MNScr.`

## CMS editorial

O CMS definido para o Cinerie é o **Payload CMS**, dentro do projeto Screen-App.

Existem **dois canais**, com contratos, endpoints e escopos diferentes — e a
separação é deliberada: transformar o endpoint de rascunho num endpoint de
publicação faria um pipeline que só sabia propor passar a publicar sem mudar uma
linha.

| Canal | Contrato | Publica? |
|---|---|---|
| rascunho (`MNSCR_DELIVERY_MODE=DRAFT`) | `mnscr-editorial-draft-v0` | não |
| publicação (`MNSCR_DELIVERY_MODE=AUTO_PUBLISH`) | `editorial-publication-request-v1` | pede |

O canal de publicação está **implementado e testado contra servidor local**;
ele ainda não foi validado contra uma instância real do Cinerie. Detalhes,
limitações e o que falta do outro lado:
[`docs/cinerie-autopublication.md`](docs/cinerie-autopublication.md).

O trecho abaixo descreve o `PayloadDraftSubmitter` **legado**, mantido por
compatibilidade e ainda desligado.

O `PayloadDraftSubmitter` já existe com configuração, validação e transformação de
`EditorialDraft` para um documento neutro do Payload — mas **não transmite nada**.
Com `PAYLOAD_ENABLED=false` ele não faz rede; com `PAYLOAD_ENABLED=true` ele falha com:

```
Payload submission is not enabled until the Cinerie Editorial contract is finalized.
```

Desde a MS-3 o submitter também recusa, **antes de qualquer outra verificação**,
qualquer draft cujo gate não seja `GATE_CLEAR` — inclusive um draft sem veredito,
porque a ausência de opinião não é aprovação.

A MS-5 acrescenta um caminho **separado** de entrega
(`PAYLOAD_CMS_ENABLED`), com contrato versionado, idempotência e retry. Esse
caminho também só aceita `GATE_CLEAR`, e continua criando apenas drafts.

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

## Estrutura factual

O MNScr não guarda apenas *referências* de fonte: ele mantém a estrutura que
permite perguntar o que o texto afirma e o que as fontes recebidas sustentam.

| Conceito | O que é |
|---|---|
| **claim** | uma afirmação do draft, com o lugar onde aparece (`title`, `body:p:3`, …) |
| **evidência** | um trecho curto de uma fonte recebida, com URL e hash |
| **link** | como a fonte se relaciona com a afirmação, com justificativa objetiva |
| **conflito** | onde as fontes discordam — registrado, nunca resolvido |
| **cobertura** | quanto das afirmações materiais está de fato sustentado |

### Status das afirmações

| Status | Significado |
|---|---|
| `SUPPORTED` | ao menos uma fonte sustenta, sem contradição material |
| `PARTIALLY_SUPPORTED` | parte relevante da afirmação segue sem confirmação |
| `UNSUPPORTED` | **nenhuma fonte recebida sustenta** — não significa que seja falso |
| `CONFLICTING` | alguma fonte contradiz o valor material |
| `UNVERIFIED` | as fontes citam o assunto, mas não confirmam nem negam |

Três distinções carregam o desenho:

- **`UNSUPPORTED` não é falsidade.** Ausência de evidência não é evidência de
  ausência: pode ser apenas que a fonte não chegou.
- **Menção não é suporte.** Uma fonte que fala do mesmo filme sem confirmar a
  data não confirma a data. A relação é `MENTIONS`, e ela nunca conta como
  confirmação.
- **`CONFLICTING` prevalece sobre `SUPPORTED`.** Uma afirmação contradita não é
  "sustentada com ressalva".

### Conflitos

Um conflito só é criado quando há o **mesmo sujeito**, o **mesmo predicado**,
**valores incompatíveis** e **evidência concreta**. Textos que apenas se leem
diferente não são conflito.

`resolution_status` é sempre `UNRESOLVED`. O MNScr **não** escolhe uma versão,
**não** conta maioria de fontes e **não** usa reputação como critério de
desempate. As duas versões ficam preservadas e a decisão é humana.

### Cobertura e independência editorial

```
coverage_ratio = (supported + 0,5 × partially_supported) ÷ total_material_claims
```

Sem afirmações materiais, a razão é 0 e a revisão é exigida — um draft que não
afirma nada verificável não está bem coberto, está inexaminado.

`source_diversity` conta **origens editoriais**, não URLs. Duas URLs do mesmo
veículo, uma edição regional ou uma republicação declarada valem uma origem:
contá-las duas vezes fabricaria a aparência de confirmação independente.

A cobertura é instrumento de revisão, **não** nota pública nem avaliação
editorial.

### Modos

| Modo | Comportamento |
|---|---|
| `deterministic` | apenas padrões (datas, números, moedas, citações, status). Não chama IA e registra a limitação. |
| `hybrid` | soma uma camada semântica cujo JSON é validado campo a campo |

A camada de IA só pode **apontar** para texto que já existe no draft: uma
afirmação cujo texto não está no rascunho é descartada, e uma resposta que não
seja JSON válido é rejeitada por inteiro em vez de interpretada com tolerância.
O prompt é versionado (`config/prompts/factual_claim_extraction_v1.txt`), trata
o conteúdo recebido como dado não confiável e não obedece a instruções
encontradas dentro dele.

### Integração com o gate

O gate passa a consumir a avaliação factual. Novas regras **bloqueantes**:
`GATE_MATERIAL_CLAIM_UNSUPPORTED` (afirmação material sem suporte em posição
crítica — manchete, subtítulo, meta, lead) e `GATE_CRITICAL_FACT_CONFLICT`.

Novos **warnings**: `GATE_FACTUAL_COVERAGE_LOW`, `GATE_PARTIAL_SUPPORT_PRESENT`,
`GATE_UNVERIFIED_CLAIMS_PRESENT`, `GATE_SOURCE_ORIGIN_DIVERSITY_LOW`.

`GATE_EVIDENCE_MISSING` e `GATE_EVIDENCE_CONFLICT` foram adaptadas para ler a
avaliação factual, mantendo compatibilidade com drafts anteriores à MS-4.

Uma afirmação **não material** sem suporte nunca bloqueia sozinha.

### Persistência e reavaliação

Cinco tabelas: `factual_assessments`, `factual_claims`, `factual_evidence`,
`claim_evidence_links` e `factual_conflicts`. Avaliações são histórico
append-only: uma nova avaliação **não sobrescreve** a anterior.

```powershell
python main.py --show-factual-assessment <draft_id>
python main.py --list-unsupported-claims
python main.py --list-conflicting-claims
python main.py --reevaluate-factual <draft_id>
python main.py --reevaluate-factual <draft_id> --deterministic
```

A reavaliação lê apenas dados locais: não consulta o RSS Prime, não busca na
web, não chama IA no modo `deterministic`, não avança cursor e não altera o
`output_hash`. `assessment_hash` é separado de `output_hash` — mudar regra
factual não pode parecer reescrita do corpo.

`--show-factual-assessment` não exibe a matéria nem os excerpts completos.

## Editorial Gate

O gate avalia cada `EditorialDraft` **depois** da validação técnica e **antes**
da gravação do artefato local. Ele é determinístico, versionado e explicável.

O gate **não aprova e não publica**. Ele classifica tecnicamente:

| Outcome | Significado |
|---|---|
| `GATE_CLEAR` | Nenhum bloqueio determinístico encontrado. **Não é aprovação humana.** |
| `GATE_REVIEW_REQUIRED` | Há warnings ou incertezas que exigem decisão editorial humana. |
| `GATE_BLOCKED` | Há falha bloqueante que impede uma futura submissão ao CMS. |

Não existe `APPROVED`, `PUBLISHED` nem `PUBLICATION_READY` — atribuí-los levanta
`ForbiddenStateError`. A autoridade editorial continua sendo humana e vive no CMS.

### Gate não é aprovação

`GATE_CLEAR` significa apenas que nenhuma regra determinística objetou. Um
revisor humano continua sendo necessário, e o `PayloadDraftSubmitter` continua
desabilitado mesmo para drafts liberados.

### Cálculo do resultado

```
existe regra BLOCKING acionada  → GATE_BLOCKED
senão, existe WARNING acionada  → GATE_REVIEW_REQUIRED
senão                           → GATE_CLEAR
```

Regras `INFO` nunca alteram o resultado. Uma verificação interna impede que o
gate devolva `GATE_CLEAR` com qualquer bloqueio ou warning acionado.

### Regras bloqueantes

`GATE_MISSING_TITLE` · `GATE_MISSING_BODY` · `GATE_MISSING_SOURCES` ·
`GATE_NO_SUCCESSFUL_EXTRACTION` · `GATE_MISSING_PROVENANCE` ·
`GATE_FORBIDDEN_WORDPRESS_MARKUP` · `GATE_UNSAFE_HTML` ·
`GATE_UNSUPPORTED_TOPIC` · `GATE_BLOCKING_ERRORS_PRESENT` ·
`GATE_INVALID_REVISION_CONTEXT`

### Warnings (revisão humana)

`GATE_LEGACY_INPUT_CONTRACT` · `GATE_MISSING_PROMPT_VERSION` ·
`GATE_SINGLE_UNTRUSTED_SOURCE` · `GATE_SOURCE_DIVERSITY_LOW` ·
`GATE_EXTRACTION_WARNINGS` · `GATE_EVIDENCE_MISSING` ·
`GATE_EVIDENCE_CONFLICT` · `GATE_CONTENT_TOO_THIN` ·
`GATE_MISSING_META_DESCRIPTION` · `GATE_SUBTITLE_ISSUE` ·
`GATE_UNVERIFIED_MEDIA` · `GATE_PROMPT_INJECTION_SIGNAL` · `GATE_REVISION_GAP`

`GATE_PROMPT_INJECTION_SIGNAL` apenas **registra** que existe texto dirigido ao
modelo. O MNScr não obedece à instrução encontrada, não reescreve o corpo e não
bloqueia sozinho — uma matéria sobre prompt injection é jornalismo legítimo.

### Informativas

`GATE_TRUSTED_SINGLE_SOURCE` · `GATE_MULTI_SOURCE` · `GATE_V1_INPUT_CONTRACT` ·
`GATE_LOCAL_OUTPUT_ONLY`

### Política versionada

`config/editorial_gate/mnscr-editorial-gate-v1.json` define quais regras rodam,
quais domínios são confiáveis para fonte única e os thresholds numéricos.

A allowlist de domínios foi **migrada** de `TRUSTED_SINGLE_SOURCES`; nenhum
veículo novo foi inventado. Uma política inexistente ou malformada **falha no
startup**, sem fallback silencioso.

### Draft bloqueado continua salvo

Um draft com `GATE_BLOCKED` **é gravado localmente** e mantém
`draft_status = DRAFT_GENERATED`: ele foi gerado corretamente. O bloqueio
impede uma submissão externa futura, nunca a preservação local que o operador
precisa para entender o que aconteceu. `DRAFT_FAILED` continua reservado a
falha real de geração ou serialização.

### Persistência e histórico

Os vereditos vivem em `editorial_gate_results` (`UNIQUE(gate_id)`), e o registro
operacional do draft guarda apenas ponteiros: `latest_gate_id`,
`latest_gate_status`, `latest_gate_policy_version`, `latest_gate_hash`.

Uma política nova **não sobrescreve** o veredito anterior: cada avaliação
distinta gera seu próprio registro.

### Reavaliação

```powershell
python main.py --show-gate <draft_id>
python main.py --reevaluate-gate <draft_id>
python main.py --reevaluate-gate <draft_id> --gate-policy mnscr-editorial-gate-v1
python main.py --list-gate-blocked
python main.py --list-gate-review-required
```

A reavaliação lê apenas o artefato local e o SQLite: não consulta o feed, não
chama IA, não acessa rede, não avança cursor e não altera o `output_hash`. É
idempotente quando draft e política são idênticos.

`--show-gate` não exibe o corpo da matéria.

## Entrega ao Payload CMS

O MNScr pode entregar um draft aprovado ao Payload CMS. Sempre como **draft**,
nunca publicado: `cms_status` é fixo em `"draft"` e não existe caminho de código
que peça outra coisa.

> **Escopo.** O Payload CMS vive no repositório **Screen-App** — o Cinerie faz
> parte desse projeto, não é um repositório separado. A MS-5 entregou o lado
> MNScr e o exercitou contra um **servidor local falso**. A integração **não foi
> validada contra uma instância real de Payload**. O que falta está em
> [docs/ms5-screen-app.md](docs/ms5-screen-app.md).

### Ordem

```
draft → validação técnica → avaliação factual → Editorial Gate
      → contrato de entrega → validação → DELIVERY_PENDING
      → tentativa → persistência do resultado
```

A entrega roda **depois** de o artefato local já estar gravado e o draft
registrado, então uma falha de entrega nunca custa a cópia local. Toda recusa
acontece antes de abrir socket, e o destino só é construído depois da validação —
o adapter não tem como contornar o gate.

### Contrato

`app/delivery/contract.py`, versionado (`schema_version = "1"`). Os hashes são
**transportados**, não recalculados: `content_hash` é o `output_hash` do draft,
`assessment_hash` vem da MS-4 e `gate_hash` da MS-3.

`entity_mentions` existe no contrato mas sai sempre **vazio**: o MNScr não extrai
menções e a MS-5 não cria uma segunda pipeline de IA só para preencher o campo.
Uma menção com `resolution_status` diferente de `UNRESOLVED` é rejeitada — seria
um fato inventado.

### Estados

| Estado | Significado |
|---|---|
| `DELIVERY_PENDING` | registrada, ainda não tentada |
| `DELIVERY_IN_PROGRESS` | tentativa em curso |
| `DELIVERED` | draft remoto criado — terminal |
| `DELIVERY_FAILED_RETRYABLE` | falha transitória; será repetida |
| `DELIVERY_FAILED_PERMANENT` | repetir não mudaria o resultado |
| `DELIVERY_BLOCKED` | recusada antes de qualquer rede |
| `DELIVERY_NEEDS_RECONCILIATION` | resultado desconhecido; **nunca** recriar às cegas |
| `DELIVERY_REQUIRES_MANUAL_REVIEW` | conteúdo mudou depois de uma entrega anterior |

Transições inválidas são recusadas. `DELIVERED` é terminal.

### Três conceitos distintos

Fáceis de confundir, e o código os trata separadamente:

| Conceito | Onde | Chave |
|---|---|---|
| **deduplicação editorial** | MS-2 | `event_key` + `payload_hash` |
| **idempotência de entrega** | MS-5 | `draft_id` + `content_hash` + `destination` |
| **atualização de draft remoto** | — | **não implementada** |

`draft_id` já deriva de `event_key + revision`, então a identidade do evento
viaja sem duplicar campos. Mesmo evento, mesma revisão e mesmo conteúdo → uma
única entrega, e a reexecução devolve o resultado gravado sem nova chamada.

### Conteúdo alterado

Se o conteúdo muda depois de uma entrega bem-sucedida, o MNScr **não** sobrescreve
o draft remoto. Ele registra uma nova entrega versionada, preserva a anterior no
histórico e marca `DELIVERY_REQUIRES_MANUAL_REVIEW` — sobrescrever um draft que um
revisor já pode ter editado destruiria trabalho humano.

### Bloqueio

A entrega é recusada, sem rede, quando: falta avaliação factual · falta gate ·
gate diferente de `GATE_CLEAR` · conflito factual crítico · draft com erros
bloqueantes · payload inválido · categoria sem correspondência canônica ·
configuração ausente · transição inválida.

### Retry e garantia

Retryable: timeout de leitura, conexão, 408, 429, 500, 502, 503, 504.
Permanente: 400, 401, 403, 404, 409, 422 e payload inválido.

Backoff exponencial com jitter determinístico; `Retry-After` do destino sempre
vence a estimativa local; `sleeper` e `clock` são injetáveis, então nenhum teste
espera de verdade.

Um *connect* timeout é tratado como falha de conexão — a requisição não chegou,
nada foi criado. Só um *read* timeout deixa o resultado ambíguo, e esse vai para
`DELIVERY_NEEDS_RECONCILIATION`.

**A garantia é `at-least-once` com deduplicação best-effort.** O MNScr envia
`Idempotency-Key`, mas não pode provar que o Payload o honra; a deduplicação que
funciona hoje é local. **Não é `exactly-once`** e não é descrita como tal.

### Taxonomia

Só categorias já existentes em `EDITORIAL_CATEGORIES` são enviadas. O MNScr
**nunca cria taxonomia remota**: um tópico sem correspondência bloqueia a entrega
com motivo explícito, em vez de mandar um valor inventado.

### Mídia

Apenas metadados: URL de origem, veículo, crédito, `license_status` e alt. Nada é
baixado, re-hospedado ou enviado; nenhuma licença é inventada e nenhum crédito é
removido. Ausência de mídia **não** bloqueia a matéria.

### Segurança

O token nunca aparece em log, exceção, `repr` ou teste. URLs em mensagens de erro
são reduzidas a esquema, host e caminho — uma query string pode carregar
credencial. O corpo da matéria não é logado.

### Persistência

`editorial_deliveries` (única por `idempotency_key`) e `delivery_attempts`
(append-only). Migrations aditivas e idempotentes; nada de MS-1..MS-4 é alterado.

## Configuração

| Variável | Padrão | Papel |
|---|---|---|
| `MNSCR_OUTPUT_MODE` | `local` | `local` ou `payload`. `wordpress` é rejeitado. |
| `MNSCR_LOCAL_DRAFT_DIR` | `artifacts/local-drafts` | Onde os drafts são gravados. |
| `MNSCR_ACCEPT_LEGACY_FEED` | `true` | **Temporário.** Aceita o superfeed pré-contrato. |
| `MNSCR_REQUIRED_INPUT_CONTRACT` | — | Fixe em `rss-prime-event-v1` para rejeitar o legado. |
| `MNSCR_STORE_RAW_EVENT_PAYLOAD` | `true` | Guarda o payload bruto para replay fiel. |
| `MNSCR_MAX_EVENT_PAYLOAD_BYTES` | `1048576` | Teto validado antes de persistir. |
| `MNSCR_EDITORIAL_GATE_ENABLED` | `true` | Desabilitar registra `GATE_DISABLED` e nunca libera submissão. |
| `MNSCR_EDITORIAL_GATE_POLICY` | `mnscr-editorial-gate-v1` | Política avaliada. Inexistente falha no startup. |
| `MNSCR_EDITORIAL_GATE_POLICY_DIR` | `config/editorial_gate` | Onde as políticas vivem. |
| `MNSCR_FACTUAL_ASSESSMENT_ENABLED` | `true` | Liga a estrutura factual. |
| `MNSCR_FACTUAL_ASSESSMENT_MODE` | `hybrid` | `deterministic` (sem IA) ou `hybrid`. |
| `MNSCR_MAX_CLAIMS_PER_DRAFT` | `100` | Teto de afirmações por draft. |
| `MNSCR_MAX_EVIDENCE_PER_CLAIM` | `10` | Teto de evidências por afirmação. |
| `MNSCR_MAX_EVIDENCE_EXCERPT_CHARS` | `500` | Evidência é ponteiro, não cópia. |
| `MNSCR_MIN_FACTUAL_COVERAGE_RATIO` | `0.75` | Limiar técnico inicial, ajustável pelo Cinerie Editorial. |
| `PAYLOAD_CMS_ENABLED` | `false` | Liga a entrega ao CMS. Desligada por padrão. |
| `PAYLOAD_CMS_BASE_URL` | — | URL do Payload. |
| `PAYLOAD_CMS_TOKEN` | — | Token. Nunca commitado, nunca logado. |
| `PAYLOAD_CMS_COLLECTION` | `editorial-drafts` | Collection de destino. |
| `PAYLOAD_CMS_TIMEOUT_SECONDS` | `15` | Timeout explícito por requisição. |
| `PAYLOAD_CMS_MAX_ATTEMPTS` | `3` | Teto de tentativas. |
| `PAYLOAD_CMS_RETRY_BASE_SECONDS` | `2` | Base do backoff exponencial. |
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
- `app/editorial_gate/` — gate versionado (`models`, `policy`, `rules`, `engine`, `serialization`, `errors`)
- `app/gate_store.py` — vereditos e histórico do gate (SQLite)
- `app/gate_service.py` — avaliação, reavaliação e consultas
- `config/editorial_gate/` — políticas versionadas em JSON
- `app/factual/` — domínio factual (`models`, `states`, `normalization`, `extraction`, `matching`, `conflicts`, `coverage`)
- `app/factual_builder.py` — monta a avaliação a partir do draft e das fontes recebidas
- `app/factual_store.py` — claims, evidências, links e conflitos (SQLite)
- `app/factual_service.py` — construção, reavaliação e consultas
- `config/prompts/` — prompts versionados
- `app/delivery/` — contrato, estados, categorias, retry e adapter do CMS
- `app/delivery_store.py` — entregas e tentativas (SQLite)
- `app/delivery_service.py` — orquestra contrato → validação → envio → persistência
- `docs/ms5-screen-app.md` — o que ainda falta no Screen-App
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
- O Editorial Gate usa apenas o que já existe no `EditorialDraft`. O modelo
  definitivo de fatos, claims, evidências e resolução de conflitos fica para a
  MS-4 — `GATE_EVIDENCE_MISSING` e `GATE_EVIDENCE_CONFLICT` registram a lacuna
  em vez de fabricar evidência.
- Não há aprovação humana, tela de revisão, RBAC nem workflow de CMS. O gate
  classifica; quem decide é uma pessoa, fora do MNScr.
- Não há entity linking definitivo nem desambiguação de pessoas, obras e
  empresas: o sujeito de uma afirmação é uma heurística rasa de nome próprio.
- Não há Knowledge Graph, embeddings, banco vetorial, RAG nem busca externa. A
  verificação usa exclusivamente as fontes já recebidas.
- Conflitos não são resolvidos e não existe score editorial definitivo.
- A integração com o Payload CMS foi implementada e testada **apenas contra um
  servidor local falso**. Ela não foi validada contra uma instância real, e o
  Screen-App ainda precisa confirmar a forma da collection, a autenticação, o
  suporte a `Idempotency-Key` e a capacidade de lookup por `delivery_id`.
- Não há atualização de draft remoto nem upload de mídia.
- Entity linking, Knowledge Graph, embeddings e busca externa seguem fora de
  escopo.
- A MS-6 ainda não foi implementada.
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
