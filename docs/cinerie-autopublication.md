# Publicação governada no Cinerie

O MNScr **pede** publicação. Quem decide é o Cinerie.

Esta é a mudança de postura desta fase, e ela é deliberada e limitada. Até aqui o
MNScr só propunha rascunhos. Agora existe um caminho automático — e o verbo
continua sendo *pedir*: autor autorizado, QA aprovado, fontes, mídia licenciada,
SEO válido, tetos diários e kill switch são **revalidados no servidor**. Um
pedido inválido não vira matéria publicada; vira `ROUTED_TO_REVIEW` ou `BLOCKED`.

## Fluxo

```
RSS Prime → MNScr → geração editorial → QA → SEO editorial
         → validação pelo JSON Schema → preflight contratual
         → POST /api/internal/editorial-publications
         → PUBLISHED | ROUTED_TO_REVIEW | CONFLICT | BLOCKED
```

Os três passos antes do `POST` acontecem **sem abrir socket**. Um pedido
malformado não custa conexão, não consome o teto de publicação do dia e não
aparece na auditoria do Cinerie como tentativa.

## Divisão de responsabilidade

| O MNScr produz | O Cinerie decide |
|---|---|
| redação, consolidação factual | canonical, robots, URL pública |
| SEO editorial (proposta) | JSON-LD final, publisher |
| fontes, evidências, proveniência | Sitemap, News Sitemap |
| sugestões de mídia e de links internos | `datePublished`, `dateModified` |
| identificação de entidades, QA | indexabilidade, projeção no screen-db |

`Review`, `ItemList` e `HowTo` podem ser **recomendações** editoriais. O MNScr
nunca presume qual JSON-LD final será renderizado — essa decisão fica no
Screen-App, que pode recusar a recomendação e aplicar fallback seguro.

## O contrato

| Item | Valor |
|---|---|
| Nome | `editorial-publication-request-v1` |
| Versão | `1.0.0` |
| Origem | Screen-App, commit `f4c49c4` |
| Artefatos | `contracts/cinerie/` |

O que atravessa a fronteira é o **JSON Schema gerado** (do próprio Zod do
Cinerie) mais um hash. Nada foi traduzido do TypeScript à mão: um schema escrito
a mão vira uma segunda fonte de verdade, e as duas divergem no primeiro campo
novo — com o agravante de que a divergência só aparece no consumidor.

O `schemaHash` é **recalculado dos bytes do arquivo** a cada carga, nunca fixado
como literal. Um literal desatualizado transformaria toda publicação em `BLOCKED`
e ninguém saberia por quê.

```bash
uv run python main.py --cinerie-contract
```

### O que o JSON Schema não carrega

`z.toJSONSchema(..., { unrepresentable: 'any' })` **descarta todo `superRefine`**.
Somem dali, entre outras: o mínimo de 15 caracteres de `seo.title` e de 70 de
`seo.metaDescription`, a exigência de fonte externa, `update` sem alvo, `qa`
reprovado sem erro declarado, alt para mídia ausente e ids de bloco únicos.

Essas regras são reimplementadas em `app/cinerie/refinements.py`, e o teste de
compatibilidade roda as **fixtures inválidas do próprio Cinerie** contra elas —
cinco dos dezessete contraexemplos passariam batido por um validador que fosse só
JSON Schema.

## Preflight

`GET /api/internal/contracts` compara nome, versão e hash. Os três importam por
razões diferentes: nome errado é outro contrato; versão errada é outra semântica;
**hash errado é outro schema com o mesmo rótulo** — o caso mais perigoso, porque
parece certo.

Divergência falha **fechado**: nada é enviado ao endpoint de publicação, o código
local é `CONTRACT_MISMATCH`, e a mensagem carrega os hashes truncados. A API key
não aparece.

O resultado é cacheado por TTL e **invalidado imediatamente** em erro de contrato
— descobrir a incompatibilidade é justamente quando não se pode confiar no valor
guardado.

```bash
uv run python main.py --cinerie-preflight
```

## Identidade do pedido

Quatro identificadores que **não** são a mesma coisa. Derivar todos de um único
valor custa caro nas duas direções: ou o retry de rede vira uma segunda
publicação, ou uma revisão nova é confundida com replay e nunca chega.

| Campo | Identifica | Estável entre |
|---|---|---|
| `sourceClusterId` | o agrupamento editorial | todas as revisões |
| `sourceRevision` | a ordem das revisões | — |
| `idempotencyKey` | **o trabalho** | retries do mesmo trabalho |
| `requestId` | **a tentativa** | retries de transporte |

`requestId` é derivado de `idempotencyKey + sourcePayloadHash`, nunca sorteado:
um valor aleatório a cada tentativa faria cada retry de rede consumir uma vaga do
teto diário, e o teto passaria a proteger contra a coisa errada.

## SEO: transporte não é autopublicação

Duas faixas diferentes, de propósito. A faixa larga existe para o CMS **receber**
material imperfeito destinado a revisão; publicar automaticamente só porque
passou no transporte seria confundir "cabe no campo" com "está bom o suficiente
para ir ao ar sozinho".

| Campo | Transporte | Autopublica | Preferência da redação |
|---|---|---|---|
| `seo.title` | 15–120 | 15–65 | — |
| `seo.metaDescription` | 70–320 | 120–160 | 140–155 |

Fora da faixa de autopublicação e dentro da de transporte → o pedido segue e
tende a `ROUTED_TO_REVIEW`. Fora da de transporte → não sai daqui.

Os números vivem em `contracts/cinerie/seo-policy.json`, exportado do mesmo
`SEO_POLICY` que valida no servidor. Antes desta fase havia três: prompt pedindo
65, otimizador aceitando 70, sanitizador aceitando 90 — nenhum errado
isoladamente, e nada que explicasse a diferença.

Os achados têm três severidades: `BLOCKING` (não sai), `AUTO_PUBLISH_INELIGIBLE`
(sai, tende a revisão) e `WARNING` (só registra).

## Legado WordPress/Yoast

`yoast_meta` saiu da saída da IA. Os **valores** não se perderam:
`_yoast_wpseo_opengraph-title` virou `openGraphTitleSuggestion`, e assim por
diante. O que se perde é a *semântica* do outro CMS — controle de canonical, de
robots, de index — que nunca deveria ter saído daqui.

O Editorial Gate varre **chaves** de CMS de terceiro em qualquer profundidade do
draft, e o contrato varre de novo antes do envio. A varredura é por chave, e não
pelo `str()` do dicionário: a versão anterior achava "yoast" também dentro de um
*valor*, e uma matéria que citasse o plugin no próprio texto era bloqueada por
falar sobre ele.

## Modos de entrega

| Modo | Contrato | Publica? |
|---|---|---|
| `DRAFT` | `mnscr-editorial-draft-v0` (fase anterior) | não |
| `AUTO_PUBLISH` | `editorial-publication-request-v1` | pede |

Em produção, `MNSCR_DELIVERY_MODE` ausente ou inválido **bloqueia**. É a decisão
mais consequente do arquivo de configuração e não pode ter default do código.

## `publicAuthorId` — o campo em que o schema e o runtime discordam

`MNSCR_PUBLIC_AUTHOR_ID` é o **id numérico** da entidade `Author` no Cinerie
(`"12"`), não o slug e não o nome.

O contrato declara o campo como `stableId`, e um slug como
`author-redacao-cinerie` passa no schema sem uma reclamação. O runtime do Cinerie
é outra história: ele testa `/^\d+$/` antes de procurar o autor e, falhando,
responde `author_not_found` → `422 BLOCKED`. Um valor errado aqui reprova **toda**
matéria do ciclo, uma por uma, gastando um round-trip para descobrir de novo a
mesma coisa — por isso a recusa acontece no startup (`get_cinerie_config_issues`)
e de novo ao montar o pedido (`is_public_author_id`).

A armadilha tem nome: a fixture canônica do próprio Cinerie usa um slug nesse
campo. Copiá-la é o caminho curto para o erro.

## Os desfechos

| Desfecho | HTTP | Estado local | Reenviar? |
|---|---|---|---|
| `PUBLISHED` | 201 | `COMPLETED` | não |
| `ROUTED_TO_REVIEW` | 202 | `AWAITING_HUMAN` | **não** — aguarde |
| `DEFERRED` | 429 | `DEFERRED` | **sim, depois de `nextEligibleAt`** |
| `CONFLICT` | 409 | `NEEDS_RECONCILIATION` | só após reconciliar |
| `BLOCKED` | 422 | `FAILED_PERMANENT` | não — repete o defeito |
| `OPERATIONAL_ERROR` | 503 | `FAILED_RETRYABLE` | sim, com backoff |

**`ROUTED_TO_REVIEW` não é falha.** O conteúdo passou por todas as validações de
forma; falta julgamento humano. O texto foi guardado.

**`DEFERRED` também não é falha, e não é limitação de taxa.** É o único 429 que
não vem da borda da rede: o teto diário da redação acabou. Nada foi persistido e
nenhum contador foi consumido — o mesmo corpo passa depois da virada sem uma
linha alterada. Por isso ele sai do cliente como **desfecho**, e não como erro de
transporte: retentar dentro do ciclo queimaria as tentativas contra uma parede
que só cai à meia-noite do fuso da redação, e perderia qual dos quatro tetos
estourou (`global`, `content_type`, `section`, `author`).

O reenvio de um `DEFERRED` repete o **mesmo `requestId`**. Um id novo declara ao
Cinerie "conte isto como publicação nova" e consumiria uma vaga do teto do dia
seguinte.

`nextEligibleAt` em **qualquer outro** desfecho é informativo, não agendamento:
transformá-lo em reenvio mandaria de novo uma matéria que já está na fila de
alguém. Quem faz essa distinção é `outcomes.resend_not_before()`.

O teto vizinho é a exceção: reescrever a **mesma matéria** tem limite próprio,
e ele responde `BLOCKED` 422 (`AUTO_PUBLISH_ARTICLE_UPDATE_LIMIT_REACHED`), sem
`Retry-After` e sem horário prometido. O contador de fato reabre à meia-noite,
mas prometer isso seria autorizar reescrever a mesma matéria todo dia — que é o
comportamento que o teto existe para conter.

`AUTHOR_CHANGE_REQUIRES_HUMAN` volta como `ROUTED_TO_REVIEW` e **nada do update é
aplicado** — nem o corpo. Aplicar metade deixaria a matéria com texto novo e
assinatura antiga, sem ninguém ter decidido isso.

## Retry

Retentável **dentro do ciclo**: timeout, conexão recusada, 408, 500, 502, 504, e
503 **marcado** `retryable: true`. Sem a marca não há promessa de que nada foi
persistido, e retentar às cegas poderia duplicar.

Um 429 **sem `outcome` no corpo** continua nessa lista: é limitador de taxa na
borda, não o portão. O 429 do portão traz `outcome: "DEFERRED"` e sai como
desfecho.

Nunca retentado dentro do ciclo: 201, 202, 409 sem reconciliação, 422, 429 do
portão, contrato divergente, JSON Schema inválido, autor ausente, configuração
inválida.

Backoff exponencial com jitter determinístico; `Retry-After` do destino sempre
vence a estimativa local; `sleeper` e `clock` injetáveis — nenhum teste espera de
verdade.

Um timeout de **leitura** esgotado é ambíguo (o pedido pode ter sido aplicado) e
vai para `NEEDS_RECONCILIATION`. Um timeout ao **conectar** significa que a
requisição nunca chegou, e o retry é limpo.

## Segurança

A API key nunca aparece em log, exceção, `repr`, teste, relatório ou banco. URLs
em mensagens de erro são reduzidas a esquema+host+path — uma querystring pode
carregar credencial. O corpo da matéria não é logado, e erros de validação
registram apenas os **caminhos** dos campos inválidos.

Um teste injeta uma chave sentinela e prova que ela não aparece em nenhum desses
lugares.

## Execução local

```bash
uv run python main.py --cinerie-contract          # identidade local, sem rede
uv run python main.py --cinerie-preflight         # compara com o destino
uv run python main.py --list-cinerie-publications # desfechos registrados
```

```bash
uv run python -m pytest tests/test_cinerie_contract.py tests/test_cinerie_seo.py tests/test_cinerie_delivery.py
```

Os testes de transporte usam um servidor HTTP local (`127.0.0.1`, porta efêmera)
em vez de um mock da função interna — um mock da própria função que se quer
testar prova apenas que ela foi chamada. Por isso eles **não** usam a fixture
`_no_network`; a garantia no lugar dela é `assert_loopback_only()`.

## Limitações

- **A integração não foi validada contra uma instância real do Cinerie.** O
  canário ponta a ponta não pôde ser executado: `embedded-postgres` não está
  instalado no checkout do Screen-App, e instalá-lo significaria escrever nesse
  repositório.
- `media` sai **vazio**. O contrato exige `mediaId` de mídia já aprovada no CMS, e
  o MNScr conhece a URL que a fonte usou, não o id do item aprovado no Cinerie.
  Em consequência, imagens do corpo são descartadas (com aviso) e
  `imageAltSuggestions` sai vazio — a conversão existe e é testada, mas não tem a
  que se ligar.
- `entityLinks` sai vazio enquanto não houver id verificável do catálogo. O MNScr
  não cria entidade.
- Não existe bloco de lista nem de passos no contrato, então `HowTo` e `ItemList`
  nunca são recomendados: seria structured data falso.

## O que falta no Screen-App

Ver `docs/ms5-screen-app.md` para o pacote da fase anterior. Para esta:

1. uma service account com escopo `editorial_auto_publish` **e só ele**;
2. uma entidade `Author` com `automationPublishingAllowed`, os tipos de conteúdo
   e o modo de assinatura que ela aceita;
3. autopublicação habilitada no ambiente de canário, com fuso `America/Sao_Paulo`;
4. confirmação de que o `outbox` recebe o `publication-event` e o worker projeta
   no screen-db.
