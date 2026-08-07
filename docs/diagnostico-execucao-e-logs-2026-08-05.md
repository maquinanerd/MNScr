# Diagnóstico de execução e análise de logs do MNScr

**Data da análise:** 5 de agosto de 2026  
**Execução observada:** 5 de agosto de 2026, 18:08:45 -03:00  
**Branch:** `claude/ms5-cinerie-autopublish-seo`  
**Escopo:** execução real de um ciclo, leitura de `logs/app.log`, inspeção somente diagnóstica do SQLite e rastreamento dos erros até o código.

> Este documento descreve o estado observado. Ele não aplica correções. Chaves, tokens, corpos de matérias e outros dados sensíveis não são reproduzidos.

---

## 1. Resumo executivo

O MNScr foi executado com o comando operacional documentado para um único ciclo:

```bash
env -u PYTHONPATH .venv/Scripts/python main.py --once
```

O processo terminou com código `0`, mas não processou nenhuma notícia. A execução encontrou 14 itens pendentes, iniciou uma thread de worker em segundo plano, recusou-se a iniciar uma nova ingestão por causa da fila já existente e encerrou o processo antes que o worker processasse os itens.

Estado confirmado no SQLite após a execução:

| Indicador | Resultado |
|---|---:|
| Itens em `article_queue` | 14 |
| Itens do Superfeed | 4 |
| Itens fallback | 10 |
| Itens associados a `seen_articles.status = NEW` | 14 |
| Artigos processados nesta execução | 0 |
| Chamadas ao Gemini nesta execução | 0 |
| Exceções nesta execução | 0 |
| Warnings nesta execução | 1 |
| Exit code | 0 |

O problema principal não é uma exceção Python. É um erro de coordenação entre o modo `--once`, o worker daemon e a guarda de fila. O programa declara sucesso sem realizar o trabalho pendente.

O log histórico também contém dois tipos de erro técnico e um bloqueio editorial:

1. `RssPrimeEventV1` não serializável ao entrar na fila — ainda existe no caminho atual do código;
2. `WORDPRESS_UPLOAD_URL_PRESENT` — falha histórica já corrigida;
3. `GATE_CRITICAL_FACT_CONFLICT` — bloqueio editorial intencional, não falha técnica.

---

## 2. Contexto do programa

### 2.1 O que é o MNScr

O MNScr é o motor editorial que transforma acontecimentos e fontes externas em drafts estruturados para o Cinerie. O fluxo pretendido é:

```text
RSS Prime e feeds externos
→ validação do contrato de entrada
→ persistência do evento e da revisão
→ fila operacional
→ extração das fontes
→ redação por IA
→ avaliação factual
→ gate editorial
→ draft local
→ entrega opcional ao CMS/Cinerie
```

O MNScr não deveria decidir sozinho o estado público final. Ele prepara e entrega um pedido; o Cinerie deve revalidar contrato, conteúdo, licença, autoria, mídia e regras de publicação.

### 2.2 Componentes envolvidos nesta análise

| Componente | Responsabilidade |
|---|---|
| `app/entrypoint.py` | Inicialização, configuração, banco e modos `--once`/contínuo |
| `app/pipeline.py` | Ingestão, worker, processamento editorial e entrega |
| `app/task_queue.py` | Fila persistente SQLite de artigos |
| `app/ingestion.py` | Validação e persistência de eventos/revisões RSS Prime |
| `app/event_store.py` | Eventos, revisões e cursores de ingestão |
| `app/store.py` | Estado operacional de artigos, drafts e falhas |
| `data/app.db` | Banco SQLite utilizado na execução |
| `logs/app.log` | Log consolidado das execuções |

### 2.3 Modo `--once`

O comando:

```bash
python main.py --once
```

deveria executar um ciclo finito e informar ao sistema operacional se o trabalho terminou corretamente. Esse modo é especialmente importante para:

- cron;
- systemd timers;
- containers;
- EasyPanel/CloudPanel;
- monitoramento;
- restart e alertas automáticos.

Para cumprir essa função, o processo principal precisa esperar a conclusão ou produzir um estado explícito de falha/incompletude. Encerrar com `0` significa convencionalmente que o trabalho solicitado foi concluído com sucesso.

### 2.4 Fila persistente

A fila vive na tabela SQLite `article_queue`. Cada item referencia uma linha de `seen_articles`. O worker deveria:

1. escolher um item;
2. reivindicá-lo atomicamente;
3. marcar o artigo como `PROCESSING`;
4. remover a entrada da fila;
5. executar extração, IA, factualidade, gate e persistência do draft;
6. atualizar o estado final.

A persistência em SQLite é positiva porque permite sobreviver a reinícios. Entretanto, persistir a fila não é suficiente se o modo de execução termina antes de consumi-la.

---

## 3. Como a execução foi realizada

### 3.1 Preparação

Foi confirmado que:

- o diretório era o repositório MNScr;
- o interpretador era `.venv/Scripts/python`;
- a versão do Python era 3.14.3;
- não existia outro processo MNScr ativo;
- `PYTHONPATH` externo foi removido para não carregar bibliotecas do ambiente Hermes.

O comando executado foi:

```bash
env -u PYTHONPATH .venv/Scripts/python main.py --once
```

### 3.2 Saída relevante

```text
Configuração crítica validada com sucesso.
Verificação do banco de dados concluída com sucesso.
Executando um único ciclo do pipeline (--once).
AI processor initialized lazily.
Pipeline worker thread started.
[QUEUE_STATS] pending_superfeed=4 pending_fallback=10
[CYCLE_GUARD] Ciclo ignorado: worker/fila ainda processando ciclo anterior.
Ciclo único finalizado.
```

### 3.3 Resultado objetivo

O processo:

- inicializou a configuração;
- verificou o schema SQLite;
- inicializou o cliente de IA, sem fazer chamada;
- iniciou o worker;
- encontrou fila pendente;
- ignorou a ingestão;
- encerrou imediatamente com código `0`.

Não houve:

- leitura de feed nesta execução;
- extração de artigo;
- chamada ao Gemini;
- avaliação factual;
- execução de gate para um novo draft;
- gravação de novo draft;
- consumo da fila.

---

## 4. Explicação dos logs da execução atual

### 4.1 `GATE_STARTED`

```text
[GATE_STARTED] policy_version=mnscr-editorial-gate-v1 rules=33 trusted_domains=12
```

**Significado:** a política editorial foi carregada, com 33 regras e 12 domínios confiáveis.

**Classificação:** funcionamento normal.

### 4.2 Configuração e banco validados

```text
Configuração crítica validada com sucesso.
Database initialized successfully.
```

**Significado:** as validações de startup não encontraram configuração crítica ausente e o schema SQLite pôde ser aberto/inicializado.

**Classificação:** funcionamento normal.

Isso não prova que o ciclo editorial foi executado. Prova somente que o programa conseguiu iniciar.

### 4.3 Cliente de IA inicializado

```text
AI processor initialized lazily.
AI CLIENT: Inicializado com 1 chaves de API
```

**Significado:** o objeto cliente foi criado e encontrou uma configuração de chave.

**Classificação:** funcionamento normal.

Não significa que a API foi chamada. Nesta execução não existe log de request HTTP, tokens ou resposta do Gemini.

### 4.4 Worker iniciado

```text
Pipeline worker thread started.
```

**Significado aparente:** um worker foi criado.

**Problema:** o worker é uma thread daemon (`app/pipeline.py:470-480`). Uma thread daemon não mantém o processo vivo. Quando a thread principal termina, o worker é encerrado junto, mesmo com trabalho pendente.

### 4.5 Estatísticas da fila

```text
[QUEUE_STATS] pending_superfeed=4 pending_fallback=10
```

**Significado:** a fila continha 14 itens persistidos.

A inspeção do banco confirmou:

- quatro itens `origin=superfeed`;
- dez itens `origin=fallback`;
- todos ligados a artigos com status `NEW`;
- itens mais antigos desde 4 de agosto de 2026.

### 4.6 Guarda de ciclo

```text
[CYCLE_GUARD] Ciclo ignorado: worker/fila ainda processando ciclo anterior.
```

**Significado pretendido:** evitar iniciar nova ingestão enquanto uma fila Superfeed anterior está sendo processada.

**Problema real:** não havia outro processo ativo nem worker de uma execução anterior. O próprio processo havia acabado de criar uma thread nova. Em seguida, a guarda encontrou quatro itens Superfeed e retornou de `run_pipeline_cycle()` em `app/pipeline.py:2241-2245`.

A mensagem é enganosa: os itens estavam pendentes, mas não havia evidência de que estivessem sendo processados.

### 4.7 Ciclo finalizado

```text
Ciclo único finalizado.
```

**Significado aparente:** o trabalho do ciclo terminou.

**Resultado real:** o processo encerrou sem processar qualquer item. Portanto, essa mensagem indica apenas que a função `run_once()` chegou ao bloco `finally`, não que o trabalho foi concluído.

---

## 5. Erros reais

## 5.1 Erro crítico: `--once` abandona a fila

### Evidências

- `app/pipeline.py:478`: worker criado com `daemon=True`;
- `app/pipeline.py:2241-2245`: a presença de item Superfeed faz o ciclo retornar;
- `app/entrypoint.py:129-140`: `run_once()` não espera o worker;
- `app/entrypoint.py:600-603`: o processo chama `run_once()` e termina;
- SQLite após a execução: 14 itens ainda na fila e 14 artigos ainda `NEW`.

### Sequência do defeito

```text
run_once
→ run_pipeline_cycle
→ cria worker daemon
→ detecta itens Superfeed pendentes
→ retorna sem ingerir
→ run_once finaliza
→ processo principal termina
→ worker daemon é morto
```

### Impacto

- notícias permanecem indefinidamente em `NEW`;
- novas execuções `--once` podem repetir o mesmo comportamento;
- cron ou container recebe falso sucesso;
- a fila pode crescer;
- nenhuma chamada editorial acontece.

### Severidade

**Crítica.** O caminho operacional documentado não cumpre sua função quando já existe backlog Superfeed.

---

## 5.2 Erro crítico: sucesso falso e exit code `0`

O processo terminou com código `0` apesar de não executar o trabalho solicitado.

Além disso, `run_once()` captura qualquer exceção do ciclo em `app/entrypoint.py:133-138`, registra `Erro crítico` e não relança a exceção nem retorna código de falha.

Existem, portanto, dois caminhos de sucesso falso:

1. a guarda retorna normalmente com fila pendente;
2. ocorre uma exceção, mas `run_once()` a captura e termina normalmente.

### Impacto

- monitoramento não detecta falha;
- systemd/cron não tentam novamente com base no exit code;
- health checks podem permanecer verdes;
- operador pode acreditar que o ciclo foi concluído.

### Severidade

**Crítica para produção e automação.**

---

## 5.3 Erro ativo: `RssPrimeEventV1` não é serializável

O log histórico registra duas ocorrências:

```text
Error processing feed rssprime_movies: Object of type RssPrimeEventV1 is not JSON serializable
Error processing feed rssprime_tv: Object of type RssPrimeEventV1 is not JSON serializable
```

Traceback observado:

```text
pipeline.run_pipeline_cycle
→ article_queue.push_many
→ ArticleQueue._serialize
→ json.dumps(item)
→ TypeError: RssPrimeEventV1 is not JSON serializable
when serializing dict item '_input_event'
```

### Causa

Após aceitar um evento, `app/pipeline.py:2165-2169` anexa o objeto tipado:

```python
enriched["_input_event"] = outcome.event
```

Depois, `app/task_queue.py:63-68` tenta serializar todo o dicionário:

```python
json.dumps(item, ensure_ascii=False)
```

Uma instância de `RssPrimeEventV1` não é JSON por padrão.

### Por que ainda é considerado ativo

O caminho atual ainda anexa `_input_event`, e `_serialize()` ainda usa `json.dumps()` sem converter a dataclass. Portanto, embora esta execução específica não tenha alcançado a ingestão, o defeito continua presente no código.

### Impacto

- a primeira tentativa de colocar um evento novo na fila pode falhar;
- evento e estado operacional podem ficar parcialmente persistidos;
- execução posterior pode precisar reconstruir/recolocar o item;
- o feed registra falha mesmo após aceitar eventos válidos.

### Severidade

**Alta/crítica para ingestão Superfeed.**

---

## 5.4 Problema de integração: contrato legado e ausência de cursor

Execuções anteriores registraram repetidamente:

```text
contract=legacy-feed-input-v0
LEGACY_CONTRACT
MISSING_CURSOR
MISSING_CLUSTER_CONFIDENCE
SOURCE_AUTHOR_UNKNOWN
SOURCE_DATE_UNKNOWN
```

### Significado

O feed identificado como RSS Prime não estava entregando o contrato versionado completo `rss-prime-event-v1`. O MNScr utilizou o adaptador legado.

### Impacto

- não há cursor real para avanço/reinício;
- não há revisão enviada pelo upstream;
- autoria e datas por fonte ficam desconhecidas;
- a confiança do cluster fica ausente;
- parte das garantias de versionamento depende de inferência local.

### Severidade

**Alta como incompatibilidade entre sistemas.** Não é necessariamente uma exceção do MNScr, mas impede que o fluxo moderno funcione conforme documentado.

---

## 6. Erros históricos que já foram corrigidos

## 6.1 `WORDPRESS_UPLOAD_URL_PRESENT`

O log contém três ocorrências:

```text
[DRAFT_INVALID] ... errors=['WORDPRESS_UPLOAD_URL_PRESENT']
```

### Causa histórica

Sites externos como ScreenRant e ComicBook usam URLs contendo `/wp-content/uploads/`. A validação confundia qualquer URL com esse caminho com vazamento do WordPress próprio.

Isso bloqueava imagens legítimas de fontes externas.

### Estado atual

O problema foi corrigido no commit:

```text
6a7a8e6 fix(editorial): a guarda de upload do WordPress passa a olhar o host
```

Há testes de regressão em `tests/test_editorial_draft_domain.py:165-219` para distinguir:

- upload do domínio próprio, que deve bloquear;
- upload de fonte externa, que deve passar;
- URL relativa de upload, que continua bloqueada por resolver contra o site próprio.

### Classificação

**Erro real histórico, já corrigido.** As linhas permanecem porque `logs/app.log` é acumulativo.

---

## 7. O que não é erro

## 7.1 `GATE_CRITICAL_FACT_CONFLICT` e `GATE_BLOCKED`

O log registra:

```text
GATE_CRITICAL_FACT_CONFLICT
GATE_BLOCKED
```

Nesse caso, o gate encontrou conflito factual material. O sistema:

- bloqueou o pedido de publicação;
- preservou o draft local;
- registrou o motivo;
- não perdeu o trabalho produzido.

### Classificação

**Comportamento de segurança esperado.** A matéria precisa de revisão/correção factual, mas o software fez o que deveria ao não liberá-la automaticamente.

---

## 7.2 `GATE_REVIEW_REQUIRED`

Warnings como:

```text
GATE_SINGLE_UNTRUSTED_SOURCE
GATE_UNVERIFIED_MEDIA
GATE_FACTUAL_COVERAGE_LOW
GATE_UNVERIFIED_CLAIMS_PRESENT
```

indicam que a matéria não está suficientemente segura para automação e deve ir para revisão.

### Classificação

**Decisão editorial esperada**, desde que as fontes e a avaliação factual tenham sido corretamente fornecidas ao gate.

---

## 7.3 Fallback de extração

Exemplos:

```text
Contêiner específico não encontrado
Usando fallback genérico
Fallback genérico escolhido
```

### Significado

O limpador especializado não encontrou conteúdo suficiente e o extractor genérico produziu resultado maior ou mais útil.

### Classificação

**Não é falha fatal.** É um mecanismo de tolerância. Deve ser monitorado porque mudanças frequentes no HTML de uma fonte podem reduzir qualidade.

---

## 7.4 Warnings de QA editorial

Exemplos:

```text
vague_phrase
suspicious_lead
missing_quotes
```

### Significado

São alertas de qualidade textual. Eles orientam validação, correção ou revisão humana.

### Classificação

**Não são exceções do programa.** Podem tornar a matéria inadequada para publicação automática, mas não significam que o processo quebrou.

---

## 7.5 Warnings de entidade

Exemplos observados envolvem nomes em português/inglês, possessivos e pequenas diferenças de grafia.

### Significado

O validador encontrou uma entidade semelhante, mas não conseguiu afirmar com segurança que deveria substituí-la.

### Classificação

**Não é erro fatal.** É proteção contra correção automática arriscada. Alguns casos podem revelar regra de normalização ruim e merecem revisão específica.

---

## 7.6 `CURSOR_NOT_ADVANCED` quando o contrato não possui cursor

```text
[CURSOR_NOT_ADVANCED] ... reason=no_cursor_in_payload
```

### Significado

O adaptador legado não inventa cursor. Isso é coerente com a regra de que desconhecido deve permanecer desconhecido.

### Classificação

**Comportamento correto diante de entrada incompleta**, mas também evidência de que a integração RSS Prime ainda não fornece o contrato moderno necessário.

---

## 8. Contagem do arquivo de log completo

O arquivo acumulado analisado continha:

| Severidade | Quantidade |
|---|---:|
| `ERROR` | 6 |
| `CRITICAL` | 0 |
| `WARNING` | 16 |

Distribuição dos seis `ERROR`:

| Mensagem | Quantidade | Estado |
|---|---:|---|
| `WORDPRESS_UPLOAD_URL_PRESENT` | 3 | histórico, corrigido |
| `RssPrimeEventV1 is not JSON serializable` | 2 | defeito ainda presente no caminho de código |
| `GATE_CRITICAL_FACT_CONFLICT` | 1 | bloqueio editorial esperado |

A execução atual das 18:08 acrescentou somente um `WARNING` de `CYCLE_GUARD`. Entretanto, esse warning representa o defeito operacional mais importante porque impediu todo o trabalho sem produzir exit code de erro.

---

## 9. Prioridade das correções

## Prioridade 0 — bloquear release/operação automatizada

1. Corrigir o modo `--once` para processar a fila ou esperar seu resultado.
2. Fazer execução incompleta/falha retornar exit code diferente de zero.
3. Coordenar shutdown do worker; não depender de thread daemon abandonada.
4. Serializar `_input_event` corretamente ou persistir apenas `(event_key, revision)` na fila.
5. Criar testes de regressão para esses dois caminhos.

## Prioridade 1 — confiabilidade da ingestão

6. Conciliar RSS Prime com `rss-prime-event-v1` real.
7. Garantir cursor e revisão fornecidos pelo upstream.
8. Recuperar automaticamente evento aceito que ficou sem item operacional.
9. Testar reinício depois de cada fronteira de commit.

## Prioridade 2 — qualidade e observabilidade

10. Separar no log `ciclo ignorado`, `fila pendente`, `worker ativo` e `worker inexistente`.
11. Registrar resumo final do `--once`: itens encontrados, processados, concluídos, falhos e restantes.
12. Alertar quando o backlog ultrapassar idade ou tamanho configurável.
13. Rotacionar `logs/app.log` para não misturar indefinidamente erros corrigidos com a execução atual.

---

## 10. Testes necessários para considerar a correção pronta

### Teste do `--once`

```text
Dado: fila com 4 itens Superfeed e 10 fallback
Quando: executar main.py --once
Então: o processo não encerra antes de produzir um resultado para a fila
E: o exit code representa sucesso/falha real
```

### Teste de serialização

```text
Dado: evento RssPrimeEventV1 aceito
Quando: o artigo enriquecido entra em ArticleQueue.push_many
Então: a fila persiste JSON válido
E: a identidade event_key/revision pode ser recuperada após restart
```

### Teste de queda e retomada

Simular encerramento:

1. antes de persistir o evento;
2. depois do evento e antes da fila;
3. depois da fila e antes do consumo;
4. durante `PROCESSING`;
5. depois do draft e antes do ack.

Após reinício, cada revisão deve terminar exatamente em um estado conhecido, sem desaparecer e sem duplicar draft.

### Teste de exit code

| Situação | Exit code esperado |
|---|---:|
| ciclo concluído com sucesso | 0 |
| configuração inválida | diferente de 0 |
| exceção crítica | diferente de 0 |
| worker termina inesperadamente | diferente de 0 |
| backlog não processado por timeout operacional | diferente de 0 ou estado explícito documentado |

---

## 11. Conclusão

O programa consegue inicializar configuração, banco, política editorial e cliente de IA. Os mecanismos de gate também demonstram comportamento defensivo adequado em conflitos factuais.

Entretanto, o caminho operacional `--once` não está confiável. Na execução analisada, o programa encontrou 14 notícias pendentes, não processou nenhuma e encerrou com sucesso. O principal defeito é a combinação de:

```text
worker daemon
+ guarda que retorna quando há backlog Superfeed
+ ausência de espera/join no --once
+ exit code sempre bem-sucedido
```

Além disso, o caminho de eventos novos ainda carrega uma dataclass `RssPrimeEventV1` para uma fila que aceita somente JSON, mantendo ativo o erro de serialização observado no log histórico.

Situação final:

> O startup funciona, mas a execução de ciclo único não comprova processamento. Antes de usar cron, systemd ou produção, é necessário corrigir a drenagem/coordenação da fila, o exit code e a serialização do evento de entrada.
