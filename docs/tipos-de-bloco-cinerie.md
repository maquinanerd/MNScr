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

## Nao emitidos

### `image` — falta `mediaId`

Exige `mediaRef` apontando para midia ja aprovada no CMS. O `mediaId` so nasce
depois da materia existir; hoje a ingestao devolve um id real
(`[CINERIE_MEDIA] ingerida com sucesso`) mas a capa nao e reapontada na mesma
rodada. Fora de escopo enquanto a resubmissao com `media[]` nao existir.

### `entityCard` e `relatedContent` — falta o id INTERNO do catalogo

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

**Decisao: nao emitir.** O MNScr nao resolve entidade contra o catalogo do
Cinerie. `taxonomy.EntityProposal.entity_id` existe e e sempre `None` na
pratica (`TAXONOMY_ENTITIES_UNRESOLVED`), e a fase MS-5 proibe explicitamente
importar TMDB (`tests/test_no_publication_guard.py:285`). Chutar `entityId`
apontaria para outra obra, em silencio, com 201 na auditoria.

Para implementar seria preciso, do lado do Cinerie, um endpoint de resolucao
(nome + tipo -> id interno) que o MNScr pudesse consultar. Nao existe hoje.

### `factBox` — falta dado estruturado

Data de estreia, plataforma e temporada/episodio nao chegam ao draft como campos
separados: `DraftContent` nao os tem, e as afirmacoes factuais
(`factual_claims`) sao texto livre. Preencher a caixa por heuristica sobre o
corpo seria fato inventado com aparencia de dado conferido — exatamente o que a
matriz de `schemaTypeRecommendation` existe para impedir. Caixa vazia tambem nao
sai: `items` tem `minItems: 1`.
