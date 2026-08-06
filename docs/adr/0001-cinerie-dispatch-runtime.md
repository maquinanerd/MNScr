# ADR 0001 — Dispatcher Cinerie acoplado ao ciclo, assíncrono e com lock

- **Estado:** aceito
- **Data:** 2026-08-06
- **Decisão:** opção A

## Contexto

`dispatch_pending` já preservava `nextEligibleAt`, claims por item e leases, mas não havia chamador no runtime. Um `DEFERRED` só voltava depois de reiniciar o processo.

## Decisão

Cada `run_pipeline_cycle` chama `schedule_cinerie_dispatch`. A passada:

1. só é agendada em `AUTO_PUBLISH`;
2. roda em executor de uma thread, portanto não bloqueia ingestão;
3. processa no máximo `MNSCR_CINERIE_DISPATCH_LIMIT` itens (freio de backlog, não cota);
4. usa lock local para não acumular futures no processo;
5. usa lease SQLite `cinerie-dispatch` para impedir sobreposição entre processos/containers;
6. mantém os claims/leasing existentes por item como segunda camada de proteção;
7. registra `deferred_total` e contagem por cada dimensão `*_DAILY_LIMIT_REACHED` recebida do servidor.

Não existe contador/teto diário local. O Cinerie continua sendo a única fonte de verdade para as cinco dimensões configuráveis no painel.

## Alternativas rejeitadas

- **B, serviço separado:** aumenta operação e exige comprovar supervisão/agendamento que este repositório não controla.
- **C, cron externo:** cria dependência do EasyPanel e pode deixar a fila parada quando o cron não estiver provisionado.

## Consequências

A ingestão dispara a retomada automaticamente sem esperar a rede. Um processo caído libera o lock após o lease; itens só ficam elegíveis conforme o estado e `nextEligibleAt` persistidos. O intervalo de retomada é o intervalo real dos ciclos do pipeline.
