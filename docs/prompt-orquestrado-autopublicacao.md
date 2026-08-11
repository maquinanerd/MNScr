# Prompt orquestrado — MNScr: de draft local a publicação no Cinerie

> Documento executável. Cada fase tem **entrada**, **saída**, **critério de
> aceite** e **o que ela não pode fazer**. As fases A–C já foram executadas; as
> demais estão especificadas para execução.

---

## 0. Objetivo

Transformar o MNScr de um motor que grava drafts em disco num motor que **pede
publicação** ao Cinerie CMS, com taxonomia em três níveis, imagem de destaque
validada e avaliação factual que funciona de verdade.

**"Pedir publicação" e "publicar" são coisas diferentes, e a distinção é o eixo
de todo este documento.** O MNScr envia um pedido; quem decide o estado público
é o CMS. Um produtor que decide o próprio estado público torna o gate do CMS
decorativo.

---

## 1. Restrições invioláveis

Não são preferências de estilo. Cada uma vem do contrato
`editorial-publication-request-v1` (repo `maquinanerd/screena`,
`docs/contracts/mnscr-autopublication-delivery.md`) ou de um defeito já
observado neste repositório.

| # | Restrição | Origem |
|---|---|---|
| R1 | O MNScr **não cria taxonomia**. `contentType` é enum fechado; seção é sugestão; entidade exige `entityId` que já existe. | Contrato §3 "O que o produtor NÃO decide"; os únicos escopos possíveis são `draft_ingest`, `publication_projection`, `editorial_auto_publish`. |
| R2 | O MNScr **não envia bytes de imagem**. `media` referencia `mediaId` já aprovado no CMS. | Contrato: *"o pedido REFERENCIA, nunca envia bytes"*. |
| R3 | O MNScr **não baixa imagem de origem terceira**. | SSRF. O CMS criou `publication-media.ts` justamente para ninguém precisar. |
| R4 | `schemaHash` é **buscado** em `GET /api/internal/contracts` a cada execução, nunca fixado em literal. | Contrato §1: literal desatualizado transforma toda publicação em `422 BLOCKED`. |
| R5 | `202 ROUTED_TO_REVIEW` é **sucesso com espera**, não falha. Nunca reenviar. | Contrato §2. |
| R6 | `422 BLOCKED` **nunca** é reenviado sem corrigir o pedido. | Contrato §2: repetiria o mesmo defeito. |
| R7 | O Editorial Gate passa a ser o **único freio** antes do ar. Regra bloqueante = não envia. | Consequência direta de remover a revisão humana. |
| R8 | Nenhuma chave de API aparece em log sem máscara. | `test_api_logging.py`, `test_config_and_security.py`. |
| R9 | Nada que dependa de credencial roda no **import** de um teste. | Três arquivos faziam isso; um deles gastava cota da Gemini só de coletar a suíte. |
| R10 | Configuração ausente falha no **startup**, nunca em silêncio no runtime. | Regra já vigente para a política do gate; estendida ao prompt factual. |

---

## 2. Mapa da taxonomia — a tradução que o pedido usa

O protocolo pedido em português traduz assim, e **os três níveis são proposta**:

```
nível 1   Notícias  →  contentType: "news"        ← enum FECHADO do contrato
          Lista     →  contentType: "list"           news, feature, review,
          Review    →  contentType: "review"         guide, list, interview,
                                                     evergreen

nível 2   Cinema    →  seo.articleSectionSuggestion: "Cinema"
          Série     →  seo.articleSectionSuggestion: "Séries"
                       ↑ SUGESTÃO. O CMS decide.

nível 3   nome do filme / série / ator
                    →  entityLinks[]: { entityKind, entityId, relation, confidence }
                       ↑ entityId TEM que existir no CMS.
                         Sem resolução, a entidade não vira link — vira proposta
                         registrada. Id inventado liga a matéria à obra errada.
```

`entityKind` aceito: `movie, tv, season, episode, person, character, franchise`.

**O nível 2 é derivado do nível 3, não adivinhado do texto.** Filme → Cinema;
série/temporada/episódio → Séries. Pessoa, personagem e franquia não decidem
seção sozinhos: um ator faz filme e série. Empate ou ausência de obra → seção
indecisa, e indecisa é resultado legítimo.

---

## 3. Fases

### FASE A — Consertar a avaliação factual ✅ EXECUTADA

**Problema.** Dois defeitos silenciosos faziam a camada factual produzir sempre
o mesmo resultado vazio, com cobertura `0.0`, para todo artigo de fonte única.

**Entrega.**

- [app/pipeline.py](../app/pipeline.py) — `_source_material_for` lia
  `art_data["content"]`, chave que só existe no caminho de cluster. O texto de
  fonte única mora em `art_data["extracted"]["content"]`.
- [app/factual_ai.py](../app/factual_ai.py) — módulo novo. Era a peça ausente
  entre `config/prompts/` e `parse_claim_response`: `_claim_response` era lido
  do `art_data` e **nunca escrito por ninguém**, então `hybrid` sempre caía em
  `NO_SEMANTIC_CLAIM_RESPONSE`.
- [app/config.py](../app/config.py) — `MNSCR_FACTUAL_PROMPT_DIR`; `hybrid` com
  prompt inexistente agora falha no startup (R10).

**Aceite.** `tests/test_factual_ai_wiring.py`, 20 testes, sem rede.

**Não pode.** Interpretar a resposta do modelo dentro de `factual_ai`. Quem
valida é `parse_claim_response`, que confere cada afirmação contra o texto do
rascunho — duas regras de aceitação seriam duas respostas para "isto vale?".

---

### FASE B — Protocolo de categorias ✅ EXECUTADA

**Entrega.**

- [app/taxonomy.py](../app/taxonomy.py) — os três níveis, espelhando os enums do
  contrato. `to_entity_links` emite **somente** entidades resolvidas.
- [config/prompts/taxonomy_classification_v1.txt](../config/prompts/taxonomy_classification_v1.txt)
  — classificação de entidades, com o mesmo endurecimento contra injeção do
  prompt factual: conteúdo é dado, não instrução.

**Aceite.** `tests/test_taxonomy_protocol.py`, 28 testes.

**Ponto editorial registrado no código.** Promover um texto a
`contentType: review` significa que a Cinerie assina resenha **própria** sobre
material reescrito de terceiro. O mapeamento acontece, com o aviso
`TAXONOMY_REVIEW_FROM_THIRD_PARTY_SOURCE` viajando junto. O próprio contrato
recusa `schemaTypeRecommendation: Review` pela mesma razão — *"schema falso sem
review própria"*.

---

### FASE C — Validação de mídia ✅ EXECUTADA

**Entrega.** [app/media_validation.py](../app/media_validation.py).

Valida a **forma** do candidato sem tocar a rede: esquema da URL, formato
entregável (`jpg/jpeg/png/webp/avif`, espelhando `DELIVERABLE_MIME_TYPES` do
CMS), pixel de rastreio, logo, avatar, placeholder, e dimensão declarada na URL
para a capa.

A regra central é do **conjunto**: existe exatamente uma `hero`. Zero é matéria
sem capa; duas é ausência de escolha, e aí o CMS escolhe sozinho.

**Aceite.** `tests/test_media_validation.py`, 32 testes.

**Não pode.** Adivinhar licença ou crédito (a MS-0 encontrou o pipeline antigo
deduzindo detentor de direitos por palavra-chave), promover o primeiro candidato
a capa por posição na lista, ou resolver `mediaId` — sem catálogo não há
resolução, e id inventado publica a imagem errada.

---

### FASE D — Resolver mídia e entidades contra o CMS ⏳ BLOQUEADA

**Bloqueio.** Precisa de `CINERIE_BASE_URL` e de uma API key com escopo
`editorial_auto_publish`. Nenhum dos dois existe hoje.

**Entrada.** Candidatos validados na Fase C; propostas de entidade da Fase B.

**Saída.** `mediaId` canônico por candidato; `entityId` por entidade.

**Aceite.**
1. Uma proposta sem correspondência no catálogo permanece **não resolvida** —
   nunca vira link ou referência aproximada.
2. A resolução é cacheada por execução: o mesmo ator em cinco matérias do ciclo
   consulta o catálogo uma vez.
3. Falha de resolução **não derruba o draft**: ele segue sem aquele link.

**Não pode.** Criar entidade, categoria ou mídia no CMS (R1, R2).

---

### FASE E — Cliente de publicação ⏳ ESPECIFICADA

**Entrada.** `EditorialDraft` + `TaxonomyProposal` + mídia resolvida.

**Saída.** `POST /api/internal/editorial-publications` com o contrato
`editorial-publication-request-v1`.

**Sub-fase E1 — conversor de corpo.** O contrato **não aceita HTML**. `blocks` é
união discriminada por `type`:

```
paragraph  heading(level 2|3|4)  image  video  quote
entityCard  factBox  relatedContent  sourceList  divider
```

O MNScr produz `body_html`. O conversor é mecânico e **testável offline** — é a
peça mais isolada da Fase E e deve vir primeiro.

**Sub-fase E2 — montagem do pedido.** Campos obrigatórios: `contractName`,
`contractVersion`, `schemaHash` (R4), `requestId`, `idempotencyKey`,
`sourceClusterId`, `sourceRevision`, `sourcePayloadHash`, `publicationIntent`,
`publicAuthorId`, `attributionMode`, `contentType`, `language`, `title`,
`summary`, `blocks`, `seo`, `provenance`, `qa`, `generatedAt`, `pipelineVersion`.

Campos **recusados** — o schema é `.strict()`, e enviá-los é `422`:
`_status`, `workflowStatus`, `publishedAt`, `bypassReview`, `canonical`,
`robots`, `jsonLd`, `articleId`, `serviceAccountId`, `actor`, e SEO duplicado no
nível superior (`metaDescription`, `slugSuggestion`, `focusKeyphrase`…, que são
os campos reais **dentro** de `seo`).

Duas regras do próprio schema que o pipeline precisa respeitar antes de enviar:

- `externalSources` **não pode ser vazio** — *"publicação automática sem fonte
  externa é paráfrase sem lastro"*.
- `qa.passed = false` exige ao menos um `blockingError` declarado.

**Sub-fase E3 — desfechos e idempotência.**

| HTTP | Desfecho | Ação |
|---|---|---|
| 201 | `PUBLISHED` | registrar id, encerrar |
| 202 | `ROUTED_TO_REVIEW` | **sucesso**; não reenviar (R5) |
| 409 | `CONFLICT` | não reenviar sem mudar algo |
| 422 | `BLOCKED` | não reenviar; corrigir (R6) |

`requestId` identifica **a tentativa**; `idempotencyKey` identifica **o
trabalho**. Retry de rede repete o mesmo `requestId` — um `requestId` novo conta
como publicação nova e consome cota diária de novo.

**Aceite.** Servidor local falso no molde de `tests/fake_payload_server.py`,
ligado só a `127.0.0.1`, cobrindo os quatro desfechos, timeout, retry e os 12
contraexemplos de `publication-fixtures.ts`.

---

### FASE F — Virada operacional ⏳ ESPECIFICADA

**Ordem obrigatória, e ela não é burocracia:**

1. `CINERIE_PUBLICATION_ENABLED=true` **contra o canário**, nunca produção
   (contrato §9.2).
2. Confirmar que a service account tem `editorial_auto_publish` **e só ele**.
3. Confirmar que o `publicAuthorId` aceita automação **para aquele
   `contentType`** — um autor pode aceitar `news` e recusar `review`.
4. Rodar um ciclo com `MAX_PER_CYCLE=1` e conferir o desfecho manualmente.
5. Só então apontar para produção.

**Aceite.** Um ciclo completo em canário terminando em `201` ou `202`, com o
veredito do gate e o desfecho do CMS registrados no SQLite.

**Não pode.** Ligar em produção sem passar pelo canário. O primeiro ciclo
publica no site ao vivo, sem revisão humana — e o item 3 acima é a única coisa
entre um bug de classificação e uma resenha falsa assinada por um autor real.

---

## 4. Dívida que esta rodada expôs e já pagou

Três arquivos de `tests/` eram **scripts**, não testes, e faziam trabalho no
import — ou seja, na coleta do pytest:

| Arquivo | O que fazia | Consequência |
|---|---|---|
| `test_api_logging.py` | `AIProcessor()` real + `rewrite_batch()` | **rodar `pytest` gastava cota da Gemini** |
| `test_key_rotation.py` | `AIClient` com as chaves do `.env` | suíte não coletava sem credencial |
| `test_logging.py` | lia `logs/app.log` sem `encoding` | suíte quebrava depois da primeira execução real do app, com `UnicodeDecodeError` apontando para o arquivo errado |

E `tests/test_config_and_security.py` passava por acaso: `importlib.reload(config)`
reexecuta `load_dotenv(override=True)`, que **sobrescreve o `monkeypatch.setenv`
do próprio teste** com o valor do arquivo. Enquanto houvesse um `.env` com
`GEMINI_KEY_1` real ao alcance, a asserção era satisfeita pelo arquivo, não pelo
código.

Resultado: **2513 testes passando com `.env` sem nenhuma credencial**, e a suíte
caiu de 61s para 40s — o tempo a mais era trabalho real disfarçado de teste.

---

## 5. O que continua fora de escopo

Não porque seja difícil, mas porque pertence ao outro lado da fronteira
(ADR 0015 do `screena`):

- criar categoria, seção ou entidade no CMS;
- decidir estado público, canonical, robots ou JSON-LD;
- upload de mídia;
- alterar autoria de matéria já publicada (o CMS responde
  `AUTHOR_CHANGE_REQUIRES_HUMAN` e **não aplica nada** do update — nem o corpo);
- resolver conflito factual: ele é registrado, nunca resolvido.
