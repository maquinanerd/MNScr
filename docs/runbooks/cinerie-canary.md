# Canário Cinerie — preparado, não executado

> **PRODUÇÃO:** este comando aponta para `cms.cinerie.com`. Não o execute sem
autorização humana no mesmo turno. O script não lê `.env` quando invocado com a
linha abaixo.

## Divergência registrada

`MNSCR_DELIVERY_MODE=DRAFT` é uma trava local do MNScr e é exigida. O contrato
`editorial-publication-request-v1`, porém, não carrega esse campo no payload: o
Cinerie também decide o roteamento pelo estado de `EDITORIAL_AUTO_PUBLISH_ENABLED`
no painel. Portanto, antes da execução, um humano precisa confirmar que o painel
está desligado. O script aborta se a primeira resposta for `PUBLISHED`, mas isso
não desfaz uma publicação que o destino já tenha feito.

O endpoint disponível não expõe um `GET` documentado por `idempotencyKey`.
Consequentemente, a consulta ao destino usa a semântica documentada do próprio
POST idempotente: repete byte a byte o pedido e exige a confirmação remota
`idempotent:true` ou `ALREADY_CONSUMED`. Não foi inventada uma URL de lookup.

## Estados que o humano deve confirmar

Responda apenas `vazia` ou `preenchida` para cada variável; não cole valores:

| Variável | Estado necessário |
|---|---|
| `PAYLOAD_INTERNAL_SERVICE_URL` | preenchida |
| `MNSCR_PAYLOAD_API_KEY` | preenchida |
| `MNSCR_PUBLIC_AUTHOR_ID` | preenchida |
| `MNSCR_DELIVERY_MODE` | preenchida com `DRAFT` |
| `MNSCR_CANARY_AUTHORIZED` | só definir como `YES` depois da autorização |

Também confirmar no painel: `EDITORIAL_AUTO_PUBLISH_ENABLED=desligado`.

## Comando único

Somente depois da autorização:

```bash
PYTHON_DOTENV_DISABLED=1 MNSCR_CANARY_AUTHORIZED=YES MNSCR_DELIVERY_MODE=DRAFT \
  uv run python -m app.cinerie_canary
```

O comando:

1. recusa iniciar sem autorização e modo `DRAFT` explícitos;
2. faz preflight do contrato antes do POST;
3. monta um único conteúdo sintético com o título `CANÁRIO SINTÉTICO MNSCR — NÃO PUBLICAR`;
4. envia o pedido uma vez e exige `ROUTED_TO_REVIEW`;
5. repete o mesmo dicionário, portanto o mesmo `requestId`, corpo e
   `idempotencyKey`;
6. exige `idempotent:true` ou `ALREADY_CONSUMED` e a mesma chave;
7. imprime JSON com desfecho, `requestId`, `idempotencyKey`, `articleId`, prova
   de reconciliação e `quotaRemaining` exatamente como informado pelo servidor
   (ausência fica `null`, nunca é convertida em zero).

Não há chamada a Gemini ou TMDB. São dois POSTs do mesmo pedido: um consumo e
uma repetição idempotente que não deve consumir teto adicional.

## Limpeza e reversão

1. Guardar o JSON de saída no registro operacional.
2. Localizar no painel pelo `articleId` e pelo marcador `CANÁRIO SINTÉTICO MNSCR`.
3. Excluir o rascunho ou movê-lo para a lixeira **pela UI do Cinerie**, conforme a
   política editorial. O MNScr não inventa um endpoint DELETE não documentado.
4. Se a resposta tiver sido `PUBLISHED`, despublicar imediatamente pela UI e
   escalar como incidente; o script já terá parado antes do segundo POST.
5. Remover `MNSCR_CANARY_AUTHORIZED` do ambiente da sessão.
6. Não reutilizar a chave do dia para outro conteúdo; conteúdo diferente exige
   nova revisão e nova autorização.

## Estado desta preparação

O script e seus testes usam cliente fake/injetado. Este runbook não executa e não
foi usado contra produção nesta rodada.
