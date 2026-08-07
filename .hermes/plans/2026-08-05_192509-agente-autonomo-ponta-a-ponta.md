# Agente Autônomo de Desenvolvimento de Ponta a Ponta — MNScr + Screen/Cinerie

> **Para Hermes:** use desenvolvimento orientado por subagentes para implementar este plano tarefa por tarefa. Um Agente Autônomo Líder deve manter o estado, criar um implementador novo por tarefa e exigir revisão de especificação e revisão de qualidade independentes antes de avançar.

**Objetivo:** deixar o MNScr confiável para execução real, recuperação após falhas e entrega idempotente ao Screen/Cinerie, comprovando o fluxo completo com testes locais de falha e integração, sem publicar conteúdo real.

**Arquitetura:** o MNScr usará identidade por evento/revisão, trabalho durável reconciliável, execução `--once` finita, claims com lease, avaliação factual baseada em fontes canônicas e outbox de entrega. O Screen/Cinerie será a autoridade do efeito remoto, recebendo pedidos idempotentes e criando somente drafts sujeitos a revisão humana.

**Stack:** Python 3.14, SQLite, pytest, Ruff e uv no MNScr; Node 22, pnpm, TypeScript, Next.js, Prisma, PostgreSQL e Vitest no Screen.

---

## 1. Missão do Agente Autônomo Líder

Você é um **Agente Autônomo de Desenvolvimento de Ponta a Ponta**. Sua responsabilidade não termina ao escrever código. Você deve:

1. preservar os repositórios e trabalhos existentes;
2. criar ambientes de trabalho isolados;
3. estabelecer baseline verificável;
4. reproduzir cada defeito com teste que falha;
5. delegar uma tarefa pequena a um implementador novo;
6. verificar o resultado no repositório real;
7. chamar um revisor de conformidade com a especificação;
8. corrigir qualquer desvio;
9. chamar um revisor independente de qualidade e segurança;
10. corrigir problemas encontrados, no máximo em dois ciclos por tarefa;
11. executar testes focados, suíte completa, lint, build e verificações de segurança;
12. criar commits pequenos somente nas branches/worktrees de execução;
13. manter um diário de decisões, evidências e comandos;
14. avançar sozinho enquanto não houver bloqueio humano real;
15. entregar o programa funcionando, não apenas um relatório.

### Estado interno obrigatório

Mantenha uma tabela persistente com:

| Campo | Valores possíveis |
|---|---|
| tarefa | identificador único |
| fase | 0 a 6 |
| estado | `PENDING`, `RED`, `GREEN`, `SPEC_REVIEW`, `QUALITY_REVIEW`, `VERIFIED`, `BLOCKED` |
| implementador | id do subagente |
| spec reviewer | id do subagente |
| quality reviewer | id do subagente |
| testes focados | comando + resultado |
| suíte completa | comando + resultado |
| commit | SHA local ou `not-committed` |
| bloqueio | descrição objetiva |

Nunca marque `VERIFIED` sem saída real dos comandos.

---

## 2. Autonomia e limites

### O agente pode fazer sem nova autorização

- ler código, documentação, histórico e schemas;
- criar worktrees e branches locais isolados;
- editar código, testes, migrations e documentação nas worktrees;
- criar bancos SQLite/PostgreSQL temporários;
- instalar dependências declaradas em ambientes locais isolados;
- executar testes, lint, typecheck, build, auditorias e servidores locais;
- criar commits locais pequenos nas branches de trabalho;
- iniciar subagentes de implementação e revisão;
- corrigir automaticamente falhas encontradas por testes e revisores;
- usar servidores HTTP falsos e serviços locais descartáveis;
- atualizar lockfiles de forma reproduzível.

### O agente deve parar e pedir autorização somente para

1. ler ou usar credenciais reais que não estejam prontas no ambiente;
2. chamar produção ou um serviço externo com efeito real;
3. publicar uma matéria;
4. executar migration em banco real;
5. fazer `push`, abrir PR, merge ou deploy;
6. escolher entre alternativas de produto irreversíveis sem regra existente;
7. alterar a regra do Screen que exige decisão humana antes de `published`;
8. descartar, sobrescrever ou incorporar trabalho não rastreado cuja origem seja desconhecida.

### Proibições

- não ler nem imprimir `.env`;
- não registrar tokens, chaves ou cabeçalhos de autenticação;
- não usar Gemini real em testes;
- não chamar URLs de produção;
- não usar `git reset --hard`, `git clean`, rebase destrutivo ou `git add -A`;
- não modificar as árvores sujas de origem;
- não confiar em logs simulados;
- não declarar “pronto” com testes faltando;
- não transformar pedido de publicação em publicação direta.

---

## 3. Repositórios e preservação do estado

### Repositórios

- MNScr: `E:\Área de Trabalho 2\Portal The News\Nerd\MNScr`
- Screen/Cinerie: `E:\Área de Trabalho 2\Screnaa`

### Estado que deve ser preservado

A árvore original do MNScr foi observada com arquivos staged/não rastreados, inclusive:

- `.hermes/plans/2026-08-05_160739-mnscr-screen-producao.md`;
- `docs/correcoes-ciclo-05-08.md`;
- `docs/diagnostico-execucao-e-logs-2026-08-05.md`;
- `teste.json`;
- nomes anômalos gerados aparentemente por comandos colados no shell.

Não assuma autoria e não remova esses arquivos. Use `git status --porcelain=v2` para registrar o estado, mas não tente “corrigi-lo”.

O Screen também foi observado com trabalho não rastreado. Leia `AGENTS.md`, `CLAUDE.md` e regras locais antes de qualquer edição.

### Preparação obrigatória

1. Registrar `git status`, branch, HEAD e remotes dos dois repositórios.
2. Criar uma worktree limpa por repositório a partir do HEAD escolhido:
   - MNScr: branch local `agent/mnscr-production-reliability`;
   - Screen: branch local `agent/screen-mnscr-ingestion`.
3. Não copiar `.env`, bancos reais, logs ou artefatos para as worktrees.
4. Registrar os caminhos absolutos das worktrees.
5. Executar todas as alterações apenas nelas.
6. Adicionar ao stage somente caminhos explícitos de cada tarefa.

Se uma branch já existir, use um sufixo seguro; não sobrescreva a branch existente.

---

## 4. Modelo de orquestração

## 4.1 Papéis

### Agente Autônomo Líder

- controla o plano e os gates;
- prepara worktrees;
- fornece contexto exato aos subagentes;
- verifica side effects e arquivos reais;
- integra commits;
- resolve dependências entre tarefas;
- executa a suíte completa;
- não implementa e revisa a mesma tarefa sozinho.

### Implementador de tarefa

Um contexto novo para cada tarefa. Recebe:

- objetivo único;
- arquivos permitidos;
- teste RED esperado;
- comandos de verificação;
- restrições do projeto;
- critérios de aceite.

O implementador não pode ampliar escopo.

### Revisor de especificação

Contexto novo, sem confiar no resumo do implementador. Deve:

- ler requisito e diff;
- executar ou inspecionar testes;
- apontar requisito ausente ou comportamento extra;
- retornar `APPROVED` ou lista objetiva de desvios.

### Revisor de qualidade e segurança

Outro contexto novo. Deve procurar:

- corrida, deadlock e abandono de lease;
- perda/duplicação;
- SQL não parametrizado;
- SSRF, redirect e limites de corpo;
- vazamento de segredo ou PII;
- testes frágeis/mockados demais;
- incompatibilidade de migração;
- regressão de publicação humana;
- erro de shutdown ou exit code.

### Verificador de integração

Contexto novo por fase transversal. Executa o programa e os dois sistemas locais, coleta evidência e não altera implementação.

## 4.2 Máquina de estados de cada tarefa

```text
PENDING
→ IMPLEMENTER_DISPATCHED
→ RED_CONFIRMED
→ GREEN_CONFIRMED
→ SPEC_REVIEW
→ correção se necessário
→ QUALITY_REVIEW
→ correção se necessário
→ FOCUSED_TESTS
→ FULL_SUITE
→ COMMIT_LOCAL
→ VERIFIED
```

Se a revisão falhar duas vezes após correções direcionadas, marque `BLOCKED`, preserve evidência e peça decisão humana. Não contorne o gate.

## 4.3 Regra de paralelismo

Pode paralelizar somente trabalho independente:

- investigação somente leitura do contrato Screen;
- inventário de fetches externos;
- auditoria de packaging/CI;
- desenho de fixtures contratuais.

Não paralelize edições sobre:

- `app/pipeline.py`;
- schema/migration SQLite;
- `packages/db/prisma/schema.prisma`;
- contrato canônico;
- migrations de idempotência;
- entrypoint/exit codes.

---

## 5. Baseline e comandos oficiais

### MNScr

Nunca use `python` solto. Use:

```bash
env -u PYTHONPATH uv run --isolated --all-extras python -m pytest -q
env -u PYTHONPATH uv run --isolated --all-extras ruff check .
env -u PYTHONPATH uv run --isolated --all-extras python -m compileall -q app main.py indexer.py
uv lock --check
```

Alternativa válida:

```bash
env -u PYTHONPATH .venv/Scripts/python -m pytest -q
```

Baseline histórico: 3.056 testes passaram antes das novas correções. A contagem pode crescer; compare falhas, não números decorativos.

### Screen

Primeiro leia `package.json`, `pnpm-workspace.yaml`, `AGENTS.md` e `CLAUDE.md`. Depois use os scripts declarados no repositório, tipicamente:

```bash
corepack pnpm install --frozen-lockfile
corepack pnpm test
corepack pnpm typecheck
corepack pnpm lint
corepack pnpm build
```

Não invente comandos se os scripts tiverem nomes diferentes.

### Baseline de runtime MNScr

Reproduzir em banco temporário, nunca depender de `data/app.db` pessoal:

```bash
env -u PYTHONPATH .venv/Scripts/python main.py --once
```

Resultado defeituoso já observado em banco real:

- 14 itens pendentes;
- zero processados;
- worker daemon encerrado com o processo;
- exit code `0`.

---

# 6. Fase 0 — Descoberta controlada e contrato real

## Objetivo

Eliminar suposições antes de criar a integração entre MNScr e Screen.

## Tarefa 0.1 — Baseline reproduzível

**Implementador:** agente de harness/testes.

**Arquivos prováveis:**

- `tests/test_once_and_event_queue.py`;
- fixtures em `tests/fixtures/`;
- scripts de teste apenas se o padrão do projeto exigir.

**Passos:**

1. Executar o teste existente `tests/test_once_and_event_queue.py` isoladamente.
2. Confirmar quais testes falham no HEAD limpo.
3. Não alterar testes que já representam o comportamento desejado.
4. Criar um harness com SQLite temporário e serviços externos falsos.
5. Registrar baseline em `artifacts/verification/phase-0/` ou caminho equivalente ignorado pelo Git.

**Gate:** testes falham pelos defeitos conhecidos, não por setup errado.

## Tarefa 0.2 — Matriz contratual MNScr ↔ Screen

**Implementador:** agente de contrato, somente leitura inicialmente.

**Ler no MNScr:**

- `contracts/cinerie/`;
- `app/cinerie/contract.py`;
- `app/cinerie/client.py`;
- `app/cinerie_service.py`;
- `app/delivery/payload_cms.py`;
- `docs/ms5-screen-app.md`, se existir.

**Ler no Screen:**

- `packages/db/prisma/schema.prisma`;
- `apps/admin/src/server/articles.ts`;
- `apps/admin/src/server/editorial-actions.ts`;
- `services/news-ingestion/README.md`;
- rotas e contratos existentes;
- histórico Git do commit indicado no `SOURCE.json`.

**Entregáveis:**

- matriz campo a campo;
- fonte canônica do schema e fixtures;
- status HTTP e respostas;
- semântica de `Idempotency-Key`;
- endpoint de reconciliação;
- regra inequívoca: entrada cria draft/revisão, nunca `published`.

**Gate:** se o contrato afirma vir de código que não existe no Screen atual, registrar divergência e propor migração; não inventar compatibilidade silenciosa.

---

# 7. Fase 1 — Fazer o MNScr executar e não perder notícias

Esta fase é P0 e deve ficar verde antes das demais.

## Tarefa 1.1 — Referência durável de evento na fila

**Objetivo:** eliminar `RssPrimeEventV1 is not JSON serializable` sem perder identidade.

**Modificar:**

- `app/task_queue.py`;
- `app/pipeline.py:2105-2171`;
- possivelmente helper de contrato/referência.

**Testar:**

- `tests/test_once_and_event_queue.py::test_queue_persists_a_stable_event_reference_not_a_python_object`;
- `tests/test_once_and_event_queue.py::test_queue_rejects_an_unknown_event_reference_version`;
- replay e draft provenance.

**TDD:** confirmar RED, persistir apenas referência JSON versionada `{schema,event_key,revision}`, reidratar pelo `EventStore` quando necessário e rejeitar versões desconhecidas.

**Aceite:** nenhuma dataclass em JSON; restart recupera o mesmo evento/revisão.

## Tarefa 1.2 — Reconciliar evento aceito sem trabalho

**Objetivo:** garantir que evento persistido antes de uma queda ganhe exatamente uma tarefa.

**Modificar:**

- `app/store.py`;
- `app/event_store.py` se necessário;
- `app/pipeline.py`;
- migration SQLite idempotente.

**Testar:**

- `tests/test_once_and_event_queue.py::test_reconciliation_creates_one_task_for_an_accepted_event_revision`;
- revisão 1/revisão 2;
- reexecução do reconciliador;
- banco SQLite legado sem colunas novas.

**Aceite:** reconciliador é idempotente e preserva bancos existentes.

## Tarefa 1.3 — Runner finito para `--once`

**Objetivo:** `--once` processar uma quantidade limitada com deadline, sem daemon abandonado.

**Modificar:**

- `app/pipeline.py:470-480` e `app/pipeline.py:1697-1822`;
- `app/pipeline.py:2230-2400`;
- `app/entrypoint.py:129-140` e caminho CLI;
- `main.py`/`app/main.py` somente se a arquitetura exigir.

**Comportamento desejado:**

- modo contínuo pode manter worker de longa duração;
- modo `--once` usa runner finito e coordenado;
- runner reconcilia órfãos, recupera claims expirados, processa até `max_items` ou deadline e produz resultado estruturado;
- não chama nova ingestão se a política manda drenar backlog, mas realmente drena o backlog;
- não inicia daemon para encerrar imediatamente.

**Testar:**

- `tests/test_once_and_event_queue.py::test_once_reports_deadline_without_starting_a_daemon`;
- fila vazia;
- fila com 1 e 14 itens;
- deadline;
- exceção no artigo;
- shutdown durante sleep;
- worker inesperadamente morto.

## Tarefa 1.4 — Exit codes confiáveis

**Objetivo:** automação externa distinguir sucesso, deadline, falha e configuração inválida.

**Modificar:**

- `app/entrypoint.py`;
- testes CLI existentes;
- `Makefile` e README após comportamento estabilizado.

**Contrato mínimo:**

| Resultado | Exit code |
|---|---:|
| trabalho solicitado concluído | 0 |
| deadline com backlog | documentado e diferente de 0 |
| falha de configuração | diferente de 0 |
| exceção crítica | diferente de 0 |
| shutdown controlado sem backlog | 0 |

**Aceite:** subprocess test observa o código real; não basta testar função mockada.

## Tarefa 1.5 — Claims, lease e recuperação

**Objetivo:** impedir duplicação entre workers e recuperar `PROCESSING` abandonado.

**Modificar:**

- `app/task_queue.py`;
- `app/store.py`;
- `app/pipeline.py`;
- migration SQLite.

**Testes obrigatórios:**

1. dois processos tentam claim do mesmo item;
2. somente um recebe;
3. lease vivo não é roubado;
4. lease expirado é recuperado;
5. draft já persistido não é refeito;
6. item permanentemente falho não entra em loop;
7. kill/restart em cada fronteira.

**Gate da Fase 1:** executar um banco temporário com 14 itens e provar contadores finais; depois suíte completa e duas revisões independentes.

---

# 8. Fase 2 — Factualidade real

## Tarefa 2.1 — DTO canônico de fonte

**Objetivo:** extração, factual, gate e reavaliação consumirem a mesma representação.

**Modificar:**

- `app/pipeline.py:1076-1092`;
- `app/pipeline.py:2027-2038`;
- `app/factual_builder.py`;
- modelos factuais/editoriais pertinentes.

**Testar:**

- conteúdo em `extracted.content` produz fonte;
- uma fonte simples;
- múltiplas fontes;
- fonte sem conteúdo;
- URL/título/excerto preservados sem invenção.

## Tarefa 2.2 — Modo `hybrid` realmente semântico

**Objetivo:** conectar o produtor de claims ao builder factual.

**Modificar:**

- `app/pipeline.py:1989`;
- `app/factual_builder.py:176-198`;
- `app/ai_client_gemini.py` ou camada correta de IA;
- configuração/modos.

**Testar sem rede:** cliente falso determinístico, semantic claims chamados exatamente no modo `hybrid`; modo degradado explicitamente rotulado; ausência de IA não pode fingir que executou hybrid completo.

## Tarefa 2.3 — Reavaliação com evidência persistida

**Objetivo:** reavaliar o mesmo conjunto de provas, sem reconstruir versão incompleta.

**Testar:** persistir avaliação, reiniciar processo, reavaliar e comparar hash/identidade das evidências.

**Gate:** matérias simples e multifuentes, incluindo conflito factual, produzem estados explicáveis e repetíveis.

---

# 9. Fase 3 — Entrega local confiável e outbox

## Tarefa 3.1 — Outbox persistente

Estados mínimos:

```text
PENDING
PROCESSING
SUCCEEDED
FAILED_RETRYABLE
FAILED_PERMANENT
UNKNOWN
```

Campos mínimos: operação, destino, idempotency key, payload hash, attempts, claimed_by, lease_until, next_attempt_at, remote id/status e timestamps.

## Tarefa 3.2 — Dispatcher retomável

No startup e periodicamente:

- retomar `PENDING` e retry vencido;
- recuperar lease expirado;
- não tocar em `SUCCEEDED`/`FAILED_PERMANENT`;
- não reenviar `UNKNOWN` antes de reconciliar;
- backoff limitado e observável.

## Tarefa 3.3 — Timeout ambíguo

Teste servidor local que:

1. recebe e aplica o pedido;
2. fecha/atrasa a resposta;
3. MNScr registra `UNKNOWN`;
4. reconciliador consulta a chave;
5. resultado encontrado vira `SUCCEEDED` sem POST novo;
6. não encontrado pode autorizar retry conforme contrato.

**Gate:** dois dispatchers concorrentes não enviam o mesmo pedido ao mesmo tempo.

---

# 10. Fase 4 — Implementar a fronteira no Screen/Cinerie

Esta fase começa somente após a matriz contratual da Fase 0.

## Tarefa 4.1 — Persistência idempotente

**Modificar:**

- `packages/db/prisma/schema.prisma`;
- nova migration Prisma;
- módulo de domínio de pedido editorial.

**Modelo mínimo conceitual:** chave idempotente única, hash do corpo, versão/schema hash, event key/revision, status, article id, resposta/reason code e timestamps.

**Garantias:** mesma chave+mesmo hash retorna o mesmo efeito; mesma chave+hash diferente retorna conflito; concorrência produz uma linha e no máximo um draft.

## Tarefa 4.2 — Endpoint autenticado de ingestão

**Modificar:** rota/service conforme arquitetura existente; não inventar localização antes de ler regras do monorepo.

**Validar:** tamanho antes de parse completo quando possível, auth fail-closed, JSON Schema + refinements, HTML sanitizado, campos desconhecidos, licença/mídia e reason codes.

**Resultado:** criar/associar somente `Article`/`ArticleTranslation` revisável com defaults seguros:

```text
reviewStatus = draft/needs_review
indexStatus = noindex
displayAllowed = false
```

Nunca `published`.

## Tarefa 4.3 — Endpoint de status/reconciliação

Consultar por idempotency key/external request id e devolver resposta estável suficiente para o MNScr resolver `UNKNOWN`.

## Tarefa 4.4 — Testes contratuais dos dois lados

As mesmas fixtures positivas e negativas devem rodar em Python e TypeScript. Divergência bloqueia merge.

**Gate:** nenhuma publicação direta; idempotência transacional comprovada com PostgreSQL real temporário.

---

# 11. Fase 5 — Segurança, operação, packaging e CI

## Tarefa 5.1 — Cliente HTTP seguro

Centralizar política de fetch externo:

- somente `http/https` quando permitido;
- HTTPS obrigatório em produção;
- resolver DNS e bloquear loopback/private/link-local/reserved/metadata;
- validar cada redirect;
- prevenir DNS rebinding na conexão;
- limites de bytes comprimidos e descomprimidos;
- timeouts separados;
- tipos permitidos;
- XML seguro.

Aplicar a feeds, sitemaps, páginas e mídia remota pertinente.

## Tarefa 5.2 — Configuração

- ambiente do servidor vence `.env`;
- `.env` não reduz segurança em produção;
- logs não expõem segredos;
- configurações de produção inválidas falham no startup.

## Tarefa 5.3 — Dependências

Atualizar para versões seguras compatíveis, incluindo vulnerabilidades observadas em `urllib3` e `cryptography`; atualizar lock; executar `pip-audit`, `pip check`, testes e Ruff.

## Tarefa 5.4 — Wheel/CLI

Garantir que wheel inclui contratos, políticas, prompts e resources; adicionar/verificar entrypoint `mnscr`; instalar wheel em ambiente temporário e executar `mnscr --help` e teste de startup offline.

## Tarefa 5.5 — CI reproduzível

CI deve instalar com lock frozen, executar testes, lint, build/wheel, auditoria e fixtures contratuais. Não declarar CI aprovado sem execução remota; até autorização para push, validar workflow localmente quando possível.

---

# 12. Fase 6 — Prova ponta a ponta

## Ambiente local obrigatório

- MNScr com SQLite temporário;
- Screen com PostgreSQL descartável;
- endpoint local autenticado com credencial efêmera;
- Gemini falso;
- fontes HTTP locais controladas;
- nenhum acesso à internet necessário.

## Cenários obrigatórios

1. evento revisão 1 cria exatamente um draft revisável;
2. reentrega idêntica não duplica;
3. revisão 2 preserva revisão 1 e produz efeito contratualmente definido;
4. queda após evento e antes da fila é reconciliada;
5. queda depois da fila é retomada;
6. dois MNScr não enviam simultaneamente o mesmo pedido;
7. dois requests no Screen produzem um efeito;
8. timeout após commit remoto é reconciliado sem novo POST;
9. conflito de mesma chave/hash diferente é bloqueado;
10. reavaliação usa evidência persistida;
11. redirect público para IP privado é bloqueado;
12. resposta/gzip acima do limite é interrompida;
13. `--once` retorna código correto;
14. nenhum registro fica público sem ação humana.

## Canary de staging

Somente com autorização humana e credenciais `[REDACTED]`:

- usar endpoint de staging, nunca produção;
- enviar fixture marcada como teste;
- confirmar draft no admin;
- confirmar `noindex`, `displayAllowed=false` e não publicado;
- repetir chave e comprovar idempotência;
- simular timeout apenas se o ambiente permitir com segurança;
- apagar dado de teste conforme política do staging.

---

# 13. Gates de revisão e commits

Após cada tarefa:

1. teste RED executado e falha esperada registrada;
2. implementação mínima;
3. teste GREEN;
4. revisor de especificação;
5. correção de desvios;
6. revisor de qualidade/segurança;
7. no máximo dois ciclos de correção;
8. testes focados;
9. suíte do projeto afetado;
10. `git diff --check`;
11. scan de segredos/dangerous APIs nas linhas adicionadas;
12. commit local com paths explícitos.

Mensagens sugeridas:

```text
fix(queue): persist durable event references
fix(runtime): make once runner bounded and observable
fix(ingestion): reconcile accepted event revisions
feat(factual): connect semantic hybrid assessment
feat(delivery): add leased outbox dispatcher
feat(cinerie): accept idempotent editorial requests
fix(security): enforce safe bounded external fetches
fix(packaging): ship runtime resources and cli
```

Não fazer push sem autorização.

---

# 14. Definition of Done

O Agente Autônomo Líder só pode declarar conclusão quando:

- [ ] cada revisão aceita tem identidade e resultado operacional próprio;
- [ ] queda entre evento, fila, cursor, processamento e draft não perde item;
- [ ] `--once` processa/termina de forma coordenada e retorna exit code verdadeiro;
- [ ] `RssPrimeEventV1` não entra bruto em JSON;
- [ ] claims concorrentes e leases foram testados com múltiplos processos;
- [ ] factual simples e multifuente recebem fontes reais;
- [ ] hybrid chama IA semântica ou declara degradação explícita;
- [ ] reavaliação usa evidência persistida;
- [ ] outbox retoma e não reenvia efeito remoto ambíguo sem consulta;
- [ ] Screen oferece idempotência e reconciliação transacionais;
- [ ] pedidos criam somente drafts não públicos;
- [ ] SSRF, redirects e corpos grandes estão limitados;
- [ ] dependências auditadas e lock reproduzível;
- [ ] wheel instalado funciona fora do checkout;
- [ ] testes Python, TypeScript, contrato e E2E estão verdes;
- [ ] revisores independentes aprovaram cada fase;
- [ ] CI local/reproduzível está verde;
- [ ] staging, se autorizado, comprovou o contrato;
- [ ] relatório final contém comandos, resultados, commits locais, riscos restantes e passos humanos.

Se staging, push ou deploy não forem autorizados, o resultado correto é:

```text
IMPLEMENTAÇÃO LOCAL E INTEGRAÇÃO DESCARTÁVEL CONCLUÍDAS
BLOQUEIO EXTERNO: aguardando autorização para staging/push/deploy
```

Isso não deve ser confundido com falha de implementação.

---

# 15. Relatório final obrigatório

Entregar:

1. resumo simples do que foi corrigido;
2. matriz requisito → teste → resultado → commit;
3. migrations criadas e estratégia de rollback/backup;
4. contrato e fixtures canônicas;
5. resultados completos de testes/lint/build/auditoria;
6. prova de concorrência, restart e timeout ambíguo;
7. inventário de endpoints e estados;
8. riscos residuais;
9. ações que exigem autorização humana;
10. declaração explícita de que nenhuma publicação real ocorreu.

O trabalho não termina em diagnóstico, código parcial ou testes unitários isolados. Termina com o fluxo local ponta a ponta funcionando e comprovado, ou com um bloqueio externo específico, verificável e impossível de resolver sem autorização humana.
