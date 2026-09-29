#!/bin/sh
# Entrada do contêiner do MNScr.
#
# O robô só trabalha com a memória dele: o banco que diz o que já foi publicado no
# Cinerie (`cinerie_publications`) e o `.env` com as chaves. Um volume vazio faria o
# pipeline tratar como novidade tudo o que já está no ar — o Cinerie recusa a
# duplicata, mas a cota de IA já foi gasta reescrevendo cada matéria. Então, até os
# dois chegarem pelo tools/coolify/enviar-estado.ps1, o contêiner só espera.
#
# Argumentos são repassados ao `python -m app.main` (sem nenhum, é o ciclo contínuo).
set -eu

mkdir -p /data/logs /data/drafts /data/debug

while [ ! -s /data/app.db ] || [ ! -s /data/.env ]; do
    echo "[MNScr] aguardando /data/app.db e /data/.env — envie com tools/coolify/enviar-estado.ps1"
    sleep 60
done

exec python -m app.main "$@"
