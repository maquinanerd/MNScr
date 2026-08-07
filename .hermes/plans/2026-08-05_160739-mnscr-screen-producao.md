# Prompt orquestrado — concluir MNScr + Screen/Cinerie com segurança

> **Para Hermes/Claude Code/Codex:** execute este trabalho como ORQUESTRADOR, com subagentes especializados, TDD, revisões independentes e entregas pequenas. Não faça uma refatoração gigante. Não declare sucesso sem executar e registrar os testes reais.

## PROMPT PARA COPIAR E EXECUTAR

Você é o engenheiro principal responsável por deixar a integração **MNScr → Screen/Cinerie** pronta para staging e, depois de aprovação humana, produção.

Trabalhe em dois repositórios locais:

- **MNScr (Python/SQLite):** `E:\Área de Trabalho 2\Portal The News\Nerd\MNScr`
- **Screen/Cinerie (TypeScript/Next/Prisma/PostgreSQL):** `E:\Área de Trabalho 2\Screnaa`

## Objetivo final

Entregar um fluxo comprovadamente confiável:

```text
RSS Prime
→ evento e revisão duráveis
→ fila recuperável
→ extração segura
→ redação por IA offline
→ avaliação factual real
→ gate editorial
→ outbox/entrega idempotente
→ Screen recebe e valida
→ Screen cria/atualiza somente um draft revisável
→ humano decide publicação
```

“`AUTO_PUBLISH`” no MNScr significa **pedido de publicação**. Ele NUNCA autoriza o MNScr a publicar diretamente. O Screen/Cinerie revalida o pedido e preserva sua regra mais forte: nenhum conteúdo fica `published` sem decisão humana explícita.

---

# 1. Regras inegociáveis

1. Leia antes de editar:
   - MNScr: `README.md`, `pyproject.toml`, `.github/workflows/ci.yml`, contratos e documentação Cinerie.
   - Screen: `CLAUDE.md`, `AGENTS.md`, `.claude/rules/*`, schema Prisma e regras de admin/editorial.
2. Não leia, imprima ou modifique `.env`, tokens ou credenciais.
3. Não chame Gemini real, APIs externas reais ou produção durante testes automáticos.
4. Não publique conteúdo real.
5. Não altere regras de licença, indexação ou publicação do Screen sem aprovação humana.
6. Preserve arquivos não rastreados e trabalho já existente. No estado observado:
   - MNScr possui `docs/correcoes-ciclo-05-08.md` não rastreado.
   - Screen possui muitos arquivos não rastreados e está na branch `feat/data-governance-hardening`.
7. Não trabalhe diretamente nessas árvores sujas. Crie worktrees/branches isolados para cada repositório e registre seus caminhos.
8. Não faça `push`, merge em `main`, deploy ou migration em banco real sem aprovação humana.
9. Cada correção deve seguir RED → GREEN → REFACTOR:
   - teste que reproduz a falha;
   - execução provando que falha;
   - implementação mínima;
   - execução provando que passa;
   - revisão independente.
10. Uma etapa só termina quando seus critérios de aceite estão comprovados por saída real de teste.
11. Se houver conflito entre documentação e código executável, registre a divergência; não escolha silenciosamente.
12. Não use contagem de testes como prova de confiabilidade. Teste explicitamente falhas, reinícios, concorrência e resultados ambíguos.

---

# 2. Organização dos agentes

Use um **orquestrador principal** e subagentes com contextos isolados.

## Agente A — Contrato e Screen/Cinerie

Responsável por:

- mapear o que o Screen já possui;
- reconciliar o JSON Schema do MNScr com `Article`, `ArticleTranslation`, revisão humana e render público;
- implementar a porta de entrada autenticada e idempotente no Screen;
- garantir que nenhum pedido publique diretamente;
- criar endpoint de consulta/reconciliação;
- testes Vitest/Prisma da fronteira.

## Agente B — Confiabilidade do MNScr

Responsável por:

- revisões `(event_key, revision)`;
- atomicidade cursor/inbox/outbox;
- fila recuperável, lease e retomada;
- `--once` com exit code correto;
- entrega idempotente e estados ambíguos;
- testes de queda/reinício/concorrência.

## Agente C — Factualidade e segurança

Responsável por:

- ligação das fontes à avaliação factual;
- modo `hybrid` realmente conectado;
- reavaliação factual/gate;
- SSRF, redirects e limites de resposta/descompressão;
- dependências vulneráveis, `.env`, HTTPS e empacotamento.

## Revisores independentes

Após cada fase, execute duas revisões separadas:

1. **Revisor de especificação:** confirma que todos os critérios de aceite foram cumpridos.
2. **Revisor de qualidade/segurança:** procura regressões, corridas, perda, duplicação, SSRF, vazamentos e testes frágeis.

Subagentes podem investigar em paralelo, mas **não podem editar o mesmo arquivo ou a mesma migration em paralelo**. O orquestrador integra e resolve conflitos.

---

# 3. Estado já comprovado — não redescobrir do zero

## MNScr

- A suíte isolada passou com **3.056 testes**.
- Ruff passou.
- Há vulnerabilidades conhecidas no lock:
  - `urllib3 2.5.0` → corrigir para versão segura compatível;
  - `cryptography 49.0.0` → corrigir para versão segura compatível.
- Problemas reproduzidos:
  1. revisão 1 é inserida, revisão 2 do mesmo `event_key` é descartada;
  2. `seen_articles` não possui revisão;
  3. cursor/evento são gravados antes do trabalho operacional existir na fila;
  4. `_source_material_for()` devolve `[]` para conteúdo em `extracted.content`;
  5. modo factual `hybrid` lê `_claim_response`, mas produção não o cria;
  6. `run_once()` captura erro crítico e retorna sucesso;
  7. wheel não inclui contratos, políticas, prompts nem entrypoint de console;
  8. fetches externos permitem SSRF e corpos/descompressão sem limites suficientes;
  9. entregas pendentes/diferidas não possuem dispatcher de retomada;
  10. timeouts ambíguos do Payload podem ser retentados e duplicar drafts;
  11. idempotência local não possui claim/lease atômico para múltiplos processos.

## Screen/Cinerie

- É monorepo Node 22/pnpm/TypeScript/Next/Prisma/PostgreSQL.
- Já possui `Article` e `ArticleTranslation` em `packages/db/prisma/schema.prisma`.
- Artigos nascem com defaults seguros: `reviewStatus=draft`, `indexStatus=noindex`, `displayAllowed=false`.
- Admin possui escrita humana limitada de `reviewStatus`/`indexStatus`.
- Páginas públicas leem PostgreSQL local; não há rede ou Gemini no render.
- No código rastreado não foi encontrado endpoint `editorial-publication-request-v1`, uso de `Idempotency-Key` ou implementação Cinerie compatível com o cliente MNScr.
- `services/news-ingestion/README.md` descreve ingestão futura/esperada, mas o serviço executável correspondente ainda não está implementado.
- O contrato presente no MNScr afirma vir do Screen-App, mas deve ser conferido contra o histórico Git e o estado atual do Screen antes de ser tratado como canônico.

---

# 4. Fase 0 — Conciliar o contrato antes de implementar

## Tarefas

1. Investigue o histórico Git de ambos os repositórios para localizar a origem real de:
   - `editorial-publication-request-v1`;
   - `mnscr-editorial-draft-v0`;
   - manifest, schema hash e fixtures;
   - endpoints e respostas esperadas.
2. Compare o schema do MNScr com os modelos reais `Article`/`ArticleTranslation` do Screen.
3. Produza uma matriz campo a campo:
   - campo do pedido;
   - campo no Screen;
   - obrigatório/opcional;
   - transformação;
   - validação;
   - informação sem destino;
   - regra de licença/revisão.
4. Defina uma única fonte canônica para contrato e fixtures. O consumidor deve validar cópia/hash sem manter versões divergentes manualmente.
5. Defina endpoints de fronteira com nomes baseados no contrato encontrado, não inventados. Eles precisam cobrir:
   - recebimento de pedido/draft;
   - idempotência;
   - conflito de mesma chave com corpo diferente;
   - consulta de status/reconciliação;
   - respostas bloqueado/revisão/draft aceito;
   - nunca publicar diretamente.
6. Registre a decisão em um ADR nos dois projetos, apontando um para o outro.

## Gate obrigatório

Não implemente integração até a matriz e o ADR demonstrarem como o contrato cabe no banco real. Se existir incompatibilidade que exija decisão humana de produto, pare somente nesse ponto e apresente opções concretas, impacto e recomendação.

## Critérios de aceite

- Contrato, hash, fixtures e semântica têm fonte canônica única.
- MNScr e Screen concordam sobre status HTTP, resposta, idempotência e reconciliação.
- Está explícito que pedido de publicação não equivale a `published`.
- Nenhum campo crítico é descartado silenciosamente.

---

# 5. Fase 1 — Não perder notícias

## 1A. Identidade por revisão

1. Escreva teste que reproduza:
   - revisão 1 aceita;
   - revisão 2 do mesmo evento aceita e processada separadamente;
   - reentrega da revisão 2 com mesmo hash idempotente;
   - mesma revisão com hash diferente rejeitada;
   - revisão antiga rejeitada.
2. Migre a identidade operacional para `(event_key, revision)`.
3. Propague a revisão por `seen_articles`, fila, draft, factual, gate e entrega.
4. Preserve migração de bancos SQLite existentes e rollback/backup documentado.

## 1B. Cursor e trabalho durável

Implemente inbox/outbox transacional ou mecanismo equivalente que garanta:

```text
trabalho durável criado/confirmado
ANTES
cursor avançar
```

Não aceite uma simples troca de ordem sem recuperação. Deve existir reconciliação de eventos que ficaram sem fila.

## 1C. Recuperação automática

No startup e periodicamente:

- recupere eventos aceitos sem trabalho operacional;
- recupere itens `processing` com lease expirado;
- não recupere itens concluídos;
- não duplique fila/draft/publicação;
- registre contadores e reason codes sem conteúdo sensível.

## Testes obrigatórios de falha

Simule interrupção em cada fronteira:

1. antes do commit do evento;
2. depois do evento e antes do trabalho;
3. depois do trabalho e antes do cursor;
4. depois do cursor e antes do consumo;
5. durante processamento;
6. depois do draft persistido e antes do ack;
7. reinício após cada ponto.

## Critério de aceite

Para cada versão aceita, ao final de reinício/retry existe exatamente um resultado operacional: pendente, processando, concluído ou falhou recuperável. Nenhuma versão desaparece e nenhum draft é duplicado.

---

# 6. Fase 2 — Fazer a factualidade funcionar de verdade

## 2A. Fonte correta

1. Crie teste de regressão para artigo simples com conteúdo em `extracted.content`.
2. Faça a avaliação receber a fonte real com URL, trecho, hash e proveniência.
3. Evite dicionários com formatos diferentes; introduza um DTO/tipo canônico de material-fonte.

## 2B. Hybrid real

1. Defina e implemente o produtor de claims semânticos offline.
2. Use adapter/fake determinístico nos testes; nunca Gemini real na suíte.
3. Se o semantic extractor estiver indisponível:
   - não anuncie silenciosamente `hybrid`;
   - marque modo efetivo e warning estruturado;
   - aplique política fail-closed definida pelo gate quando necessária.
4. Persista modo solicitado, modo efetivo, versão de prompt/modelo e hash de entrada/saída.

## 2C. Reavaliação

Reavaliação do gate deve carregar a `FactualAssessment` persistida correta de `(draft_id, event_key, revision)`. Ausência deve bloquear ou ser explicitamente reportada, nunca parecer aprovação.

## Testes obrigatórios

- artigo simples, uma fonte;
- cluster com várias fontes concordantes;
- fontes conflitantes;
- claim não suportada;
- resposta semântica inválida;
- modo hybrid sem adapter;
- reavaliação preservando resultado factual anterior.

---

# 7. Fase 3 — Não esquecer nem duplicar entregas

## 3A. Outbox e dispatcher

Crie dispatcher operacional para:

- `PENDING`;
- `FAILED_RETRYABLE`;
- `DEFERRED`;
- `IN_PROGRESS` com lease expirado;
- `NEEDS_RECONCILIATION`.

Use claim atômico/compare-and-swap com `claimed_by`, `lease_until`, tentativas e backoff. Dois processos não podem possuir o mesmo trabalho simultaneamente.

## 3B. Resultado ambíguo

Read timeout ou conexão perdida depois do envio é resultado desconhecido. Não retente cegamente. Transicione para `NEEDS_RECONCILIATION`, consulte o destino pela chave idempotente e só decida depois.

## 3C. Lado Screen/Cinerie

Implemente no Screen uma fronteira server-only que:

1. autentique com segredo server-side e comparação segura;
2. valide tamanho, JSON Schema, versão e schema hash;
3. exija `Idempotency-Key`/request ID;
4. grave pedido e resultado em transação;
5. para mesma chave + mesmo hash, devolva a resposta original;
6. para mesma chave + hash diferente, devolva conflito permanente;
7. transforme pedido válido em um único draft/revisão no modelo real;
8. aplique defaults seguros e encaminhe para revisão humana;
9. nunca marque `published` automaticamente;
10. exponha consulta de status por chave idempotente para reconciliação;
11. não exponha token, corpo completo ou dados pessoais nos logs.

Se o schema atual não puder representar todos os blocos/proveniência sem perda, armazene o pedido normalizado/versionado em modelo próprio e só materialize o artigo após validação. Não compacte dados silenciosamente em uma string sem ADR e testes.

## Testes obrigatórios

- mesma chave simultânea em duas requisições;
- mesma chave/mesmo hash;
- mesma chave/hash diferente;
- queda após commit antes da resposta;
- timeout do cliente após servidor criar draft;
- reconciliação encontra o draft existente;
- lease expira e outro worker assume;
- item concluído nunca é reenviado;
- pedido inválido/bloqueado não cria artigo;
- pedido aceito nasce em revisão humana, não `published`.

---

# 8. Fase 4 — Operação, segurança e empacotamento

## 4A. Processo e configuração

- Faça `--once` sair diferente de zero em falha crítica.
- `load_dotenv` não pode sobrescrever env real por padrão.
- Exija HTTPS para endpoints externos em produção; permita HTTP apenas para loopback/teste explícito.
- Domínios próprios obrigatórios quando o modo de pedido de publicação estiver habilitado.
- Corrija warnings de datetime/SQLite e testes que retornam valores em vez de usar `assert`.

## 4B. Cliente HTTP seguro

Centralize fetch de feeds/páginas com:

- somente HTTP/HTTPS;
- sem credenciais em URL;
- bloqueio de loopback, link-local, multicast, redes privadas e IPv6 local;
- resolução e validação do destino;
- validação de cada redirect;
- timeouts de conexão/leitura;
- streaming com teto de bytes;
- teto separado para conteúdo comprimido e descomprimido;
- limites de quantidade/profundidade de sitemaps;
- parser XML defensivo (`defusedxml` ou equivalente comprovado).

Teste IPv4, IPv6, DNS/redirect para IP privado, corpo sem `Content-Length`, gzip bomb e corpo acima do limite.

## 4C. Dependências e wheel

- Atualize `urllib3` e `cryptography` para versões sem os advisories encontrados.
- Regenere `uv.lock` sem remover compatibilidade necessária.
- Faça o wheel incluir contratos, políticas e prompts.
- Adicione `[project.scripts]` para CLI.
- Instale o wheel em diretório/venv temporário fora do checkout e execute contrato, CLI e testes smoke.

## 4D. CI

MNScr deve executar, em ordem imutável:

```bash
uv lock --check
uv sync --all-extras --frozen
uv run python -m ruff check .
uv run python -m pytest -q
uv build
# instalar/testar wheel fora do checkout
# pip-audit equivalente no lock/export
# git diff --exit-code
```

Screen deve manter verdes:

```bash
corepack pnpm typecheck
corepack pnpm lint
corepack pnpm test
corepack pnpm audit:invariants
corepack pnpm audit:render
corepack pnpm build
```

Inclua testes do contrato e integração local entre os dois lados sem rede pública.

---

# 9. Fase 5 — Testar como será usado de verdade

## 5A. Harness local completo

Suba Screen/PostgreSQL descartáveis e execute MNScr contra essa instância local, usando credenciais falsas de teste. Prove:

1. versão 1 cria um draft;
2. reentrega idêntica não duplica;
3. versão 2 cria atualização/revisão distinta conforme ADR;
4. timeout após commit é reconciliado;
5. dois workers não duplicam;
6. reinício recupera inbox/outbox;
7. pedido bloqueado não cria conteúdo publicável;
8. nada fica `published` automaticamente.

## 5B. Staging real — somente com aprovação e credenciais fornecidas externamente

Antes de chamar staging, pare e peça confirmação humana. Não leia `.env` para descobrir credenciais.

Execute apenas com:

- URL explícita de staging;
- token de staging injetado pelo operador;
- conteúdo sintético claramente marcado;
- kill switch ligado;
- limite de um pedido;
- limpeza/reversão documentada.

Valide criação, idempotência, reconciliação e visualização no admin. Não teste produção.

## 5C. GitHub e PRs

Somente após todos os gates locais:

1. apresente `git status`, diffs e lista de commits dos dois worktrees;
2. execute revisão independente final;
3. peça autorização antes de push;
4. abra PRs separados e vinculados, com ordem de merge explícita;
5. espere CI verde;
6. não faça merge sem aprovação humana.

---

# 10. Estratégia de commits

Faça commits pequenos e reversíveis por etapa, por exemplo:

1. `test(ingestion): reproduce revision loss and cursor gap`
2. `fix(ingestion): persist operational work per event revision`
3. `feat(queue): recover abandoned and missing work`
4. `fix(factual): pass extracted sources into assessment`
5. `feat(factual): connect semantic hybrid extraction`
6. `test(delivery): reproduce ambiguous timeout duplication`
7. `feat(delivery): add atomic outbox claims and reconciliation`
8. `feat(screen): accept idempotent editorial requests as review drafts`
9. `fix(security): harden outbound fetches and response limits`
10. `fix(runtime): propagate once-mode failures`
11. `fix(packaging): ship runtime artifacts and CLI`
12. `ci: enforce frozen installs and cross-repo contract tests`

Não force exatamente esses commits se a investigação mostrar fronteiras melhores, mas preserve mudanças pequenas e revisáveis.

---

# 11. Relatório obrigatório após cada fase

Entregue uma tabela:

| Item | Estado | Evidência | Teste executado | Commit |
|---|---|---|---|---|
| requisito | pronto/parcial/bloqueado | arquivo:linha | comando + resultado real | SHA local |

Inclua também:

- migrations criadas;
- compatibilidade com bancos existentes;
- riscos restantes;
- decisões humanas pendentes;
- arquivos alterados fora do escopo (deve ser nenhum);
- status das duas árvores Git.

---

# 12. Definição final de “pronto”

Não diga “pronto” apenas porque a suíte antiga passa. O sistema só está pronto para staging quando todas estas propriedades forem demonstradas:

- cada `(event_key, revision)` aceita produz trabalho exatamente uma vez;
- cursor nunca causa perda após crash;
- trabalhos abandonados são recuperados;
- factual usa fontes reais e registra modo efetivo;
- reavaliação usa evidência persistida;
- duas instâncias não enviam o mesmo trabalho;
- timeout ambíguo reconcilia antes de retry;
- Screen é idempotente e nunca publica automaticamente;
- SSRF e respostas gigantes são bloqueados;
- `--once` informa falha ao sistema operacional;
- dependências não têm os advisories conhecidos;
- wheel funciona fora do checkout;
- CI dos dois repositórios está verde;
- integração local completa passa;
- staging real passa com autorização humana.

Se algum item não puder ser provado, marque como **NÃO PRONTO**, explique o bloqueio e forneça o próximo passo exato. Nunca fabrique resultados.

## FIM DO PROMPT
