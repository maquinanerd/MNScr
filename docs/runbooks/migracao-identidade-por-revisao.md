# Migração: identidade por revisão e posse de entrega

Três mudanças desta rodada tocam bancos SQLite que já existem em produção. Todas
são compatíveis com dados antigos, mas duas mudam o **significado operacional**
de identidade, e é isso que este documento existe para explicar antes de você
subir.

Nenhuma delas exige parar o serviço para converter dados. O que muda é aplicado
na abertura do banco, e o que não converte continua funcionando.

---

## O que muda

### 1. `seen_articles.external_id` passa a carregar a revisão

**Antes:** `external_id` era o id do item no feed, ou o hash da URL quando o feed
não mandava id. Estável entre revisões do mesmo evento.

**Depois:** quando o item traz `event_key` **e** `event_revision`, o valor vira
`<id-anterior>#rev<N>`.

**Por quê:** a tabela declara `UNIQUE(source_id, external_id)` — a identidade de
antes de existir revisão. Com o valor repetido, a revisão 2 era recusada no
`INSERT` mesmo depois de todo o dedup ter liberado, e a `IntegrityError`
derrubava o lote inteiro daquela rodada.

**Não há `ALTER TABLE`.** Linhas antigas mantêm o `external_id` que sempre
tiveram; só linhas novas usam o formato com revisão.

### 2. `cinerie_publications` ganha `claimed_by` e `lease_until`

Duas colunas novas, aplicadas por `ALTER TABLE` na abertura do banco, mais um
índice `(delivery_status, lease_until)`. Ambas nascem `NULL`, que é exatamente
"sem dono" — o estado correto para tudo que já estava lá.

`ALTER TABLE` em vez de recriar a tabela porque **há entrega em voo lá dentro**:
recriar exigiria copiar linhas com publicação pendente, e uma cópia interrompida
perderia o rastro de pedidos que podem ter chegado ao Cinerie.

### 3. legado de revisão ganha backfill conservador e guarda do Cinerie

Quando `seen_articles` ainda não tem `event_revision`, a abertura do banco agora:

1. adiciona `event_revision` e `legacy_cinerie_identity_unmapped`;
2. marca toda linha preexistente com `event_key` e revisão nula;
3. preenche a revisão **somente** quando `rssprime_events` tem exatamente uma
   revisão validada para aquele `event_key` e existe uma única linha legada para
   a chave;
4. mantém a guarda mesmo após o backfill.

O backfill recupera a identidade histórica sem adivinhar. A guarda resolve outro
problema: essas linhas podem representar matérias que já existem no Cinerie, mas
o banco local não tem `payload_article_id`. Sem esse id, uma rev2 seria mapeada
como `publicationIntent=publish`, não `update`, e poderia criar uma segunda
matéria. Enquanto a guarda existir e não houver mapeamento local para o artigo
remoto, a entrega falha fechada com
`LEGACY_CINERIE_IDENTITY_UNMAPPED`, antes de preflight ou socket.

A revisão continua podendo virar tarefa/draft; o bloqueio fica na fronteira do
Cinerie, onde o risco de duplicação realmente existe.

---

## Exposição legada e decisão C5

Para medir a exposição antes de subir, sem abrir o banco pelo runtime novo:

```bash
sqlite3 data/app.db "SELECT COUNT(*) FROM seen_articles WHERE event_key IS NOT NULL;"
sqlite3 data/app.db "SELECT COUNT(DISTINCT event_key) FROM seen_articles WHERE event_key IS NOT NULL;"
```

Quando `event_revision` já existir:

```bash
sqlite3 data/app.db "SELECT COUNT(*) FROM seen_articles WHERE event_key IS NOT NULL AND event_revision IS NULL;"
```

A migração não atribui "rev1" por convenção. Ausência de histórico, mais de uma
revisão validada ou mais de uma linha legada para a chave deixam
`event_revision` nula e a guarda ativa. É preferível reter uma entrega para
reconciliação humana a declarar uma publicação nova sem saber qual artigo
atualizar.

Não remova a guarda apenas porque o backfill preencheu a revisão: revisão de
origem e id remoto do Cinerie são identidades diferentes. Ela só pode ser limpa
depois de existir uma associação auditável do agrupamento a um
`payload_article_id` remoto.

---

## Backup

Com o serviço **parado**, para o arquivo ficar consistente:

```bash
sqlite3 data/app.db ".backup 'data/app.db.bak-antes-da-revisao'"
```

`.backup` e não `cp`: ele lida com o WAL. Copiar o arquivo com o serviço no ar
pode capturar um estado intermediário.

Confirme que o backup abre antes de prosseguir:

```bash
sqlite3 data/app.db.bak-antes-da-revisao "PRAGMA integrity_check;"
```

---

## Rollback

O código antigo **lê o banco novo sem alteração nenhuma**: as colunas novas são
ignoradas por quem não as conhece, e um `external_id` com sufixo `#rev` é só um
texto diferente para ele. Porém, ele também ignora a guarda C5. Portanto um
rollback apenas de código deve manter a entrega ao Cinerie desabilitada até o
runtime protegido voltar; caso contrário, a rev2 volta a poder ser enviada como
`publish`.

Rollback operacional seguro:

```bash
# 1. parar o serviço
# 2. desabilitar a entrega ao Cinerie
# 3. voltar o código para o commit anterior
# 4. subir somente o pipeline sem entrega ao Cinerie
```

Não execute `UPDATE ... SET event_revision = NULL` para "desfazer" o backfill:
isso perde uma associação histórica que veio de `rssprime_events` e não remove
as colunas. O SQLite legado não precisa que as colunas sejam removidas.

Restaurar o backup é necessário se o objetivo for voltar **schema e dados** ao
instante exato anterior à migração:

```bash
# com o serviço parado
sqlite3 data/app.db.bak-antes-da-revisao ".restore 'data/app.db'"
sqlite3 data/app.db "PRAGMA integrity_check;"
```

Isso também descarta trabalho legítimo feito no intervalo. Só faça a restauração
se esse descarte tiver sido aceito explicitamente.

---

## Verificação depois de subir

```bash
# 1. as colunas de posse existem
sqlite3 data/app.db "PRAGMA table_info(cinerie_publications);" | grep -E "claimed_by|lease_until"

# 2. nenhuma entrega ficou com posse presa (esperado: 0 nas primeiras horas)
sqlite3 data/app.db "SELECT COUNT(*) FROM cinerie_publications WHERE claimed_by IS NOT NULL AND lease_until < datetime('now');"

# 3. backfill/guarda C5 (na exposição medida: esperado 6 / 6 / 0)
sqlite3 data/app.db "SELECT COUNT(*) AS guardadas, SUM(event_revision IS NOT NULL) AS com_backfill, SUM(event_revision IS NULL) AS ambiguas FROM seen_articles WHERE legacy_cinerie_identity_unmapped = 1;"

# 4. nenhuma entrega foi criada para evento guardado sem id remoto (esperado: 0)
sqlite3 data/app.db "SELECT COUNT(*) FROM cinerie_publications cp JOIN seen_articles sa ON sa.event_key = cp.source_cluster_id WHERE sa.legacy_cinerie_identity_unmapped = 1 AND cp.payload_article_id IS NULL;"

# 5. revisões novas não guardadas estão entrando com o formato esperado
sqlite3 data/app.db "SELECT external_id, event_revision FROM seen_articles WHERE external_id LIKE '%#rev%' ORDER BY id DESC LIMIT 5;"
```

O item 2 merece atenção continuada: uma contagem que **cresce e não volta a
zero** significa worker morrendo antes de concluir entrega. O lease expira e o
trabalho é reivindicado de novo, então nada se perde — mas a causa da morte
precisa ser investigada.

No item 3, `ambiguas > 0` não é falha de migração: significa que o histórico não
permite backfill honesto. Essas linhas continuam guardadas e exigem reconciliação
manual. Qualquer `LEGACY_CINERIE_IDENTITY_UNMAPPED` no log confirma que a proteção
reteve uma entrega potencialmente duplicada; não é autorização para apagar o
marcador sem localizar o artigo remoto correspondente.
