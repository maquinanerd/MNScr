# MNScr no Coolify

O MNScr roda 24/7 no Coolify (`https://vps.cinerie.com`), no build pack **Docker
Compose** com `/docker-compose.coolify.yml`, a partir da `main` deste repositório. Não
serve HTTP e não tem domínio: lê os superfeeds da Cinerie no RSS Prime e publica no CMS
da Cinerie.

## Onde mora o estado

Tudo que precisa sobreviver a um redeploy fica no volume `<app>_mnscr-data`, montado em
`/data`:

| Caminho no contêiner | O que é |
| --- | --- |
| `/data/app.db` | o banco — inclusive `cinerie_publications`, que impede republicar |
| `/data/.env` | as chaves (Gemini, Payload/Cinerie, TMDB…); `/app/.env` aponta para ele |
| `/data/logs/` | `app.log` e a contagem de tokens |
| `/data/drafts/`, `/data/debug/` | rascunhos locais e prompts que falharam |

O Coolify define só `MNSCR_DB_PATH=/data/app.db` e `MNSCR_LOCAL_DRAFT_DIR=/data/drafts`,
e isso vence o `.env` (`app/config.py` carrega com `override=False`). Nenhum segredo vai
para variável do painel nem para a imagem (`.dockerignore`).

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
