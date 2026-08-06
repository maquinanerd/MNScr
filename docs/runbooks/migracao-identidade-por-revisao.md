# Migração: identidade por revisão e posse de entrega

Duas mudanças desta rodada tocam bancos SQLite que já existem em produção. As
duas são compatíveis com dados antigos, mas uma delas muda o **significado** de
uma coluna, e é isso que este documento existe para explicar antes de você subir.

Nenhuma das duas exige parar o serviço para converter dados. O que muda é
aplicado na abertura do banco, e o que não converte continua funcionando.

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

---

## O risco real, e ele é um só

Linhas antigas de `seen_articles` podem ter `event_revision` **nulo** — foram
gravadas antes de a coluna existir. Para elas o sistema não sabe *qual* versão do
evento já foi processada.

Se o RSS Prime reemitir esse mesmo evento agora, declarando `revision: 2`, o
dedup versionado procura o par `(event_key, 2)`, não encontra, e trata como
trabalho novo. **Resultado: a matéria pode ser reprocessada uma vez.**

Isso não é defeito da mudança — é o custo de passar a versionar algo que antes
não era versionado. "Revisão desconhecida" e "revisão 2" são genuinamente
diferentes, e qualquer mecanismo honesto trataria assim.

Para medir a exposição antes de subir:

```bash
sqlite3 data/app.db "SELECT COUNT(*) FROM seen_articles WHERE event_key IS NOT NULL AND event_revision IS NULL;"
```

Zero: nenhuma exposição. Diferente de zero: esse é o número máximo de matérias
que podem ser reprocessadas uma vez, e só se o RSS Prime reemitir cada uma delas.

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
texto diferente para ele.

O único efeito de voltar é o comportamento antigo voltar junto — revisões novas
passam a ser descartadas outra vez pelo dedup. Não há corrupção, e não há passo
de conversão para desfazer.

```bash
# 1. parar o serviço
# 2. voltar o código para o commit anterior
# 3. subir
```

Restaurar o backup só é necessário se você quiser apagar também as matérias
processadas depois da subida:

```bash
sqlite3 data/app.db.bak-antes-da-revisao ".restore 'data/app.db'"
```

Isso descarta trabalho legítimo feito no intervalo. Só faz sentido se a subida
tiver produzido conteúdo que você quer eliminar.

---

## Verificação depois de subir

```bash
# 1. as colunas de posse existem
sqlite3 data/app.db "PRAGMA table_info(cinerie_publications);" | grep -E "claimed_by|lease_until"

# 2. nenhuma entrega ficou com posse presa (esperado: 0 nas primeiras horas)
sqlite3 data/app.db "SELECT COUNT(*) FROM cinerie_publications WHERE claimed_by IS NOT NULL AND lease_until < datetime('now');"

# 3. revisões novas estão entrando com o formato esperado
sqlite3 data/app.db "SELECT external_id, event_revision FROM seen_articles WHERE external_id LIKE '%#rev%' ORDER BY id DESC LIMIT 5;"
```

O item 2 merece atenção continuada: uma contagem que **cresce e não volta a
zero** significa worker morrendo antes de concluir entrega. O lease expira e o
trabalho é reivindicado de novo, então nada se perde — mas a causa da morte
precisa ser investigada.
