# Tipos de bloco do contrato: o que o MNScr emite, e por que nao emite o resto

`contracts/cinerie/editorial-publication-request-v1.schema.json` define dez
tipos em `blocks[].oneOf`. Ate 07/08/2026 o MNScr emitia dois. Este documento
registra o que passou a sair, com que regra, e — o mais importante — por que
quatro tipos continuam fora.

## Emitidos

| tipo | de onde vem | regra |
| --- | --- | --- |
| `paragraph` | `<p>`, `<li>`, texto solto | um bloco por paragrafo; um `<p>` com quebra dupla vira varios |
| `heading` | `<h1>`..`<h6>` | `h1` rebaixado para `h2` (o `h1` e o titulo da pagina) |
| `video` | paragrafo com URL nua do YouTube | depois do primeiro `h2`, ou no fim sem `h2`; **um** por materia |
| `quote` | `<blockquote>`, ou citacao com autor declarado dentro de um paragrafo | no maximo **duas** por materia |
| `sourceList` | `externalSources[]` do proprio pedido | **ultimo** bloco; substitui o paragrafo "Fonte: X" |
| `entityCard` | `POST /api/internal/entity-resolve` | so com `entityId` nao-nulo; no maximo **tres**; depois do paragrafo da primeira mencao |
| `divider` | `<hr>` | passa direto; o gerador de texto raramente produz `<hr>` |

### `video`

Quando o corpo chega ao conversor, o embed ja foi normalizado para
`<p>https://www.youtube.com/watch?v=ID</p>` por
`html_utils.strip_credits_and_normalize_youtube`. Ate agora esse paragrafo saia
como `paragraph` — uma URL crua no meio do texto.

O id e validado (`^[A-Za-z0-9_-]{11}$`) **antes** de virar bloco. Id fora dessa
forma e descartado com `VIDEO_DESCARTADO`, porque um `video` quebrado passa no
JSON Schema e so aparece na pagina publicada, como um player que nao carrega.

`externalId` fica de fora quando o id comeca por `-` ou `_`: `stableId` do
contrato exige primeiro caractere alfanumerico, e reprovar o pedido INTEIRO por
causa de um campo opcional seria trocar a materia pelo detalhe. A `url` carrega
o video sozinha.

### `quote`

Duas condicoes, as duas lidas do TEXTO — nada e inferido:

1. ha um trecho entre aspas com corpo de frase (40 caracteres ou mais), e
2. encostado nele ha um verbo de fala de uma lista fechada e um nome proprio.

Promover e **partir**, nao duplicar: o que vem antes e o que vem depois da fala
continuam como paragrafo. A clausula que APRESENTA a citacao (o pedaco entre o
ultimo ponto final e a aspa) e consumida, porque quem falou ja esta em
`attribution` — sem isso a materia 21 saiu publicada com o paragrafo
`Em comunicado oficial, a`, uma frase cortada no artigo.

Casos reais que NAO viram bloco, de proposito:

- `a frase traduzida e "Hair today, Gorn tomorrow"` — sem verbo de fala.
- `"...", afirmou o executivo` — sem nome. O nome esta na frase anterior, e ir
  busca-lo la seria inventar a atribuicao.

### `sourceList`

Referencia `externalSources[].id` por id. Cada ref e conferido contra a lista do
MESMO pedido: um `sourceRefs` orfao e um link quebrado publicado com 201, e o
contrato nao pega isso — ele conhece o FORMATO do id, nao a lista.

O paragrafo de credito (`html_utils.append_source_credit_block`) so sai quando o
bloco entra. Sem fonte referenciavel o texto fica: perder a atribuicao seria
pior do que exibi-la sem link, que e o estado que este bloco veio corrigir.

### `entityCard`

**Atualizado em 08/08/2026.** A secao abaixo, escrita em 07/08, terminava com
"para implementar seria preciso, do lado do Cinerie, um endpoint de resolucao
(nome + tipo -> id interno) que o MNScr pudesse consultar. Nao existe hoje."
**Esse endpoint existe agora**: `POST /api/internal/entity-resolve`, no
`screen-app` (nao no CMS), com escopo proprio `catalog_resolve` e credencial
separada. Tudo o que a secao antiga descreve sobre o PERIGO continua valendo
palavra por palavra — o que mudou e que o MNScr deixou de precisar adivinhar.

Regras de emissao, todas verificadas em `tests/test_cinerie_entity_cards.py`:

- **`entityId` nulo nao vira bloco.** Nem aproximacao, nem plano B. Isso inclui
  `no_canonical_slug`, o caso em que a entidade EXISTE e mesmo assim nao serve:
  sem slug canonico pt-BR ela nao tem pagina, e o card sumiria do corpo
  exatamente como sumiria um id inexistente;
- **uma chamada por MATERIA**, em lote (teto de 50 itens, cortado localmente
  ANTES do envio — acima disso a rota recusa o pedido inteiro, e nunca trunca);
- **limiar de confianca configuravel** (`MNSCR_CINERIE_ENTITY_MATCH_KINDS`). Os
  tres casamentos que a rota oferece sao exatos; toda ficha emitida por nome
  sobe no log como `[CINERIE_ENTITY_CARD] auditoria=exact_name`, com o nome de
  origem e o id resolvido;
- **no maximo tres por materia** (`MNSCR_CINERIE_ENTITY_CARD_MAX`), as de maior
  confianca, com desempate estavel por proeminencia e nome — uma materia
  picotada de fichas vira diretorio, e um desempate instavel faria a revisao
  seguinte trocar os blocos sem que nada tivesse mudado no texto;
- **posicao:** logo DEPOIS do paragrafo em que a entidade e mencionada pela
  primeira vez; quando o nome so aparece no titulo, depois do primeiro
  paragrafo. E o unico ponto DEDUZIVEL do texto — um indice fixo escolhido a mao
  seria desmentido pela proxima materia;
- **filme e serie so viajam com ano**, e so o ano que o texto declara colado ao
  titulo (`Titulo (2026)`). Sem ano a rota responde `title_requires_year`, e
  colar o primeiro ano do texto num titulo qualquer produziria
  `exact_title_year` — o casamento mais confiante da rota — ancorado num chute;
- **falha vira zero ficha**, nunca ficha aproximada e nunca materia perdida.
  `503 resolve_failed` existe justamente para que falha de leitura NAO chegue ao
  emissor como "nao existe".

O array `entityLinks[]` de topo **continua saindo vazio**, e de proposito: ver
"Relacao com `entityLinks[]`" abaixo.

## Nao emitidos

### `image` — falta um `mediaId` que possa ir no corpo

Exige `mediaRef` apontando para midia ja aprovada no CMS. O `mediaId` so nasce
depois de a materia existir, e o corpo e enviado antes — logo, so uma revisao
POSTERIOR do mesmo cluster poderia carregar o bloco.

**Continua fora, e o motivo mudou.** Desde 08/08 a capa e apontada de fato
(`PATCH /editorial-media/:mediaId/hero`), entao existe um `mediaId` real. Mas
ele e o da CAPA: reutiliza-lo como `image` no meio do texto publicaria a mesma
foto duas vezes na mesma pagina. Um bloco `image` util exigiria uma SEGUNDA
ingestao, com `intendedUse` proprio, e um lugar no banco local para guardar o id
entre revisoes — nenhum dos dois existe hoje. Forcar revisao sintetica para
antecipar isso ja foi decidido como nao: colide com a revisao legitima do RSS
Prime.

### `relatedContent` — falta o id INTERNO do catalogo

A secao abaixo foi escrita quando `entityCard` estava no mesmo balde. Para
`relatedContent` ela continua inteira: `articleRefs` pede id de materia do
proprio Cinerie, e nao ha rota que traduza isso.

Investigacao no repo do Cinerie (`origin/main`), 07/08/2026:

- **O que `entityId` espera.** O id interno do catalogo publico do Cinerie, e
  so ele. `packages/editorial-contracts/src/blocks.ts:76` diz literalmente
  "Id interno do Cinerie; o writer NAO cria entidade". A forma e cobrada em
  `apps/cms/src/publication-intake.ts:150` — `INTERNAL_ENTITY_ID = /^[1-9][0-9]*$/`
  — e o comentario ali explica: a coluna e `BigInt` e a sequencia comeca em 1,
  por isso `0` e `007` sao recusados. `tt0111161` e `tmdb:550` tambem
  (`apps/cms/src/__tests__/publication-intake.test.ts:168-174`).
  **Um id do TMDB nao serve.**

- **O que acontece com entidade inexistente.** Nada, no ato: ninguem recusa.
  `apps/cms/src/editorial-body-mapper.ts:271` copia `entityId` como string, sem
  checar existencia — e o CMS **nao consegue** checar, porque tem banco proprio
  e nao alcanca o catalogo publico (ADR 0015, travado por
  `tests/governance/cms-isolation.test.ts`). O pedido e aceito com 201. O
  defeito so aparece na pagina: `apps/web/src/lib/article-body-presenter.ts:350`
  procura `${kind}:${entityId}` no catalogo e, nao achando, **descarta o bloco
  inteiro em silencio**
  (`apps/web/src/lib/__tests__/article-body-presenter.test.ts:266`, "entidade que
  nao esta no catalogo NAO produz card falso"). Um id que por acaso EXISTA como id
  interno e pior: o card renderiza apontando para a obra errada, com toda a
  aparencia de estar certo.

- **Relacao com `entityLinks[]` e `ENTITY_LINK_NOT_REAPPLIED`.** Sao caminhos
  irmaos, com o MESMO formato de id e guardas diferentes. `entityLinks` passa
  por `resolveEntityReferences` (`publication-intake.ts:175`), que recusa id fora
  da forma interna com `ENTITY_LINK_ID_NOT_INTERNAL` e grava o resto sempre com
  `verified: false` — so aparece no site depois de confirmacao humana no admin
  (ADR 0018). `ENTITY_LINK_NOT_REAPPLIED`
  (`apps/cms/src/endpoints/editorial-publications.ts:533`) e o aviso de que, em
  `update`, os vinculos **nao** sao reaplicados: o campo carrega decisao humana
  (`verified`) que uma reescrita automatica apagaria. Ou seja: o `entityCard`
  do corpo nao tem nenhuma dessas duas protecoes — nem a checagem de forma, nem
  a confirmacao humana.

**Decisao de 07/08, hoje superada para `entityCard`:** naquela data o MNScr nao
tinha como resolver entidade contra o catalogo, `taxonomy.EntityProposal
.entity_id` era sempre `None` na pratica (`TAXONOMY_ENTITIES_UNRESOLVED`) e
chutar `entityId` apontaria para outra obra, em silencio, com 201 na auditoria.
A rota de resolucao fechou essa lacuna — mas note que ela nao afrouxou nenhuma
das regras acima: o que ela entrega e um id CONFERIDO, ou um `null`.

`relatedContent` continua sem rota equivalente, e por isso continua fora.

### Relacao com `entityLinks[]` e `ENTITY_LINK_NOT_REAPPLIED`

Os dois caminhos usam o MESMO formato de id e tem guardas diferentes, e a
diferenca decide por que o MNScr preenche um e nao o outro:

- **`entityCard` (corpo)** nao tem checagem de forma no CMS nem confirmacao
  humana. O corpo e remapeado inteiro a cada `update`, e isso e inofensivo:
  nenhuma decisao de gente mora la. E o bloco que vira link visivel na pagina.
- **`entityLinks[]` (topo)** passa por `resolveEntityReferences`, grava sempre
  com `verified: false` e **so aparece no site depois de confirmacao humana no
  admin** (ADR 0018). Em `update` o CMS **nao reaplica** o campo e avisa com
  `ENTITY_LINK_NOT_REAPPLIED`, porque `verified` carrega curadoria que uma
  reescrita automatica apagaria.

**Decisao: o MNScr continua enviando `entityLinks: []`.** Nao e timidez, sao
tres razoes somadas:

1. `relation` (`primary_subject` | `mentioned` | `reviewed` | ...) e
   **obrigatorio** e nao e resolvivel: a rota devolve QUAL entidade e, nunca o
   PAPEL dela na materia. Preencher exigiria a heuristica que o resto deste
   documento existe para recusar;
2. o ganho visivel hoje seria zero — a linha nasce `verified: false` e nao
   renderiza ate um humano confirmar —, enquanto o `entityCard` ja entrega o
   link real na pagina;
3. mandando `[]`, o array de curadoria simplesmente **nunca e tocado por robo**,
   e o aviso `ENTITY_LINK_NOT_REAPPLIED` nem chega a ser emitido. A garantia
   deixa de depender de o CMS continuar recusando a reaplicacao.

Travado por `test_the_top_level_entity_links_array_is_left_untouched_by_the_resolver`.

### `factBox` — falta dado estruturado

Data de estreia, plataforma e temporada/episodio nao chegam ao draft como campos
separados: `DraftContent` nao os tem, e as afirmacoes factuais
(`factual_claims`) sao texto livre. Preencher a caixa por heuristica sobre o
corpo seria fato inventado com aparencia de dado conferido — exatamente o que a
matriz de `schemaTypeRecommendation` existe para impedir. Caixa vazia tambem nao
sai: `items` tem `minItems: 1`.
