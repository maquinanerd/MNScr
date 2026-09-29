# MNScr no Coolify

O MNScr roda 24/7 no Coolify (`https://vps.cinerie.com`), no build pack **Docker
Compose** com `/docker-compose.coolify.yml`, a partir da `main` deste repositório. São
dois serviços no mesmo volume:

- `mnscr`, o robô: lê os superfeeds da Cinerie no RSS Prime e publica no CMS da
  Cinerie. Não serve HTTP;
- `painel`, um site só de leitura, com login, para acompanhá-lo (seção [Painel](#painel)).

## Onde mora o estado

Tudo que precisa sobreviver a um redeploy fica no volume `<app>_mnscr-data`, montado em
`/data`:

| Caminho no contêiner | O que é |
| --- | --- |
| `/data/app.db` | o banco — inclusive `cinerie_publications`, que impede republicar |
| `/data/.env` | as chaves (Gemini, Payload/Cinerie, TMDB…); `/app/.env` aponta para ele |
| `/data/logs/` | `app.log` e a contagem de tokens |
| `/data/drafts/`, `/data/debug/` | rascunhos locais e prompts que falharam |
| `/data/painel.db`, `/data/painel.secret` | o acesso ao painel: administrador e a chave que assina o cookie |

O Coolify define só `MNSCR_DB_PATH=/data/app.db`, `MNSCR_LOCAL_DRAFT_DIR=/data/drafts` e
o teto `MNSCR_AI_DAILY_BUDGET_USD` (abaixo), e isso vence o `.env` (`app/config.py`
carrega com `override=False`). Nenhum segredo vai para variável do painel nem para a
imagem (`.dockerignore`).

## A virada (uma vez)

1. Primeiro deploy no Coolify. O contêiner sobe e **espera**, escrevendo a cada minuto
   `[MNScr] aguardando /data/app.db e /data/.env` (docker/entrypoint.sh): sem o banco,
   ele reescreveria tudo o que já está no ar.
2. Na máquina que rodava o MNScr, com ele **parado**, na pasta do repositório:

   ```powershell
   powershell -ExecutionPolicy Bypass -File tools\coolify\enviar-estado.ps1
   ```

   Consolida o WAL do `data\app.db`, confere o banco e manda `app.db` e `.env` para o
   volume por `ssh vps-mn` (pede a senha do root uma vez). Recusa se o MNScr estiver
   rodando na máquina, se o volume já tiver banco (use `-Substituir` só de propósito) ou
   se houver mais de um volume `*_mnscr-data`.
3. Em até um minuto o contêiner sai da espera e começa o ciclo. Conferir em *Logs* do
   recurso: `DRAFT GERADO`, publicações no Cinerie.

**Depois da virada o MNScr não roda mais na máquina local**: dois robôs publicariam no
Cinerie (o Cinerie recusa a duplicata, mas a IA já gastou a cota).

## Trocar uma chave

Corrigir o `.env` local e mandar só ele — o banco do servidor não é tocado:

```powershell
powershell -ExecutionPolicy Bypass -File tools\coolify\enviar-estado.ps1 -SomenteEnv
```

Depois, **Restart** do recurso no Coolify, para o processo ler o arquivo novo. Uma
variável definida no painel do Coolify também vale e vence o `.env`.

Nunca use `-Substituir` depois da virada sem querer isso: ele troca o banco do servidor
pelo local, mais velho, e apaga o que foi publicado desde então.

## Teto de gasto com IA

`MNSCR_AI_DAILY_BUDGET_USD` (padrão `1.00`; `0` desliga) limita, em dólares, o que o
MNScr gasta de Gemini por dia de São Paulo. O custo de toda resposta — redator,
validador, checagem factual — é somado na tabela `ai_spend_daily` do próprio banco,
com os preços de `app/ai_spend.py`. Batido o teto, o worker não pega matéria nova até
a meia-noite: a fila espera como está, sem gastar tentativa nem virar falha. A matéria
que já estava na IA termina, então o dia pode passar do teto pelo custo de uma matéria.

Até 29/09/2026 a média foi US$ 0,011 por matéria: US$ 1 rende umas 90 por dia.

- **Mudar o teto:** *Environment Variables* do recurso no Coolify, editar
  `MNSCR_AI_DAILY_BUDGET_USD`, depois **Redeploy**. Um valor inválido impede o
  robô de subir, com a mensagem no log.
- **Ver nos logs:** `[AI_SPEND]` a cada chamada (custo e total do dia) e
  `[AI_DAILY_BUDGET] teto atingido` uma vez por dia, quando trava.
- **Ver o gasto dos últimos dias** (*Terminal* do recurso):

  ```sh
  python -c "import sqlite3; c = sqlite3.connect('/data/app.db'); print(c.execute('select day, round(sum(usd), 4), sum(calls) from ai_spend_daily group by day order by day desc limit 7').fetchall())"
  ```

A tabela de preços é fixa no código. Se o Google mudar o preço, ou o `.env` passar a
usar um modelo fora dela, atualize `PRICES_USD_PER_MTOK`: modelo desconhecido conta
pelo preço mais caro da tabela.

## Painel

Site só de leitura (`app/painel`), no domínio que o Coolify gera para o serviço
`painel` (*Domains* do recurso; tem de estar em **https**, porque o cookie de sessão
exige HTTPS). Páginas:

| Página | O que mostra |
| --- | --- |
| Visão geral | último ciclo do robô (alerta se passar de 2,5 intervalos sem ciclo), gasto de IA de hoje contra o teto, publicadas hoje/ontem/7 dias, fila, últimas publicações |
| Publicações | as 100 últimas no Cinerie, com link para a matéria |
| Gasto de IA | 30 dias: gasto por modelo, chamadas, matérias e custo por matéria |
| Falhas | envios ao Cinerie que não terminaram publicados, erros ao escrever e descartes do filtro de entrada |

Ele não muda nada no robô: lê o `app.db` com `mode=ro`, não carrega nem abre o `.env` do
robô (`PYTHON_DOTENV_DISABLED=1`; a base dos links vem de `CINERIE_PUBLIC_BASE_URL` no
compose) e guarda o próprio acesso em `/data/painel.db`. Cair ou reiniciar o painel não
encosta no robô, e vice-versa.

**Risco assumido:** o painel roda com o mesmo usuário (uid 10001) e o mesmo volume do
robô, então o processo *poderia* ler `/data/.env`. Separar não funciona com o SQLite em
WAL: o leitor precisa criar `app.db-wal`/`-shm` na pasta do banco quando o robô está sem
conexão aberta, e com outro usuário ou montagem `:ro` o painel deixaria de ler
justamente quando o robô para. O que segura: nenhuma rota serve arquivo nem aceita
caminho, as consultas são fixas e só leitura, tudo fica atrás de login, e o CSP não
deixa rodar script.

- **Primeiro acesso:** abra o domínio do painel. Ele pede o código que imprime no log do
  contêiner do painel (*Logs* do recurso, contêiner `painel-…`, linha
  `[MNSCR_PAINEL] primeiro acesso: … código` seguido de 16 caracteres), um usuário e
  uma senha de pelo menos 10 caracteres. Existe um administrador só.
- **Esqueceu a senha:** no *Terminal* do recurso, contêiner do painel:

  ```sh
  python -m app.painel --novo-acesso
  ```

  Apaga o administrador, derruba as sessões abertas e imprime um código novo; o painel
  volta a pedir o primeiro acesso.
- **Segurança:** senha com scrypt, sessão assinada de 12 h, CSRF em todo formulário,
  5 tentativas de login a cada 5 minutos por IP, **Sair** (do administrador) invalida
  todo cookie emitido antes. Nenhum JavaScript; só links `http(s)`; cabeçalhos CSP,
  `X-Frame-Options: DENY` e `no-store`.
  `/health` é público e só responde `{"status": "ok"}`.
- **"Último ciclo: ainda não registrado"** até o primeiro ciclo depois deste deploy: o
  robô passa a gravar o início de cada ciclo (`pipeline_state.ultimo_ciclo_utc`).
