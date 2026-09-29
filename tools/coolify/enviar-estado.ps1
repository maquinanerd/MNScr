<#
Leva a memoria do MNScr desta maquina para o volume do conteiner no Coolify.

    powershell -ExecutionPolicy Bypass -File tools\coolify\enviar-estado.ps1
    powershell -ExecutionPolicy Bypass -File tools\coolify\enviar-estado.ps1 -SomenteEnv

Sem opcao: a virada. Copia `data\app.db` (o que ja foi publicado no Cinerie) e o `.env`
(as chaves) para o volume `<app>_mnscr-data`, que o conteiner monta em /data. Ate os
dois chegarem, o conteiner so espera (docker/entrypoint.sh): subir com o volume vazio
faria o robo reescrever tudo o que ja esta no ar.

-SomenteEnv: troca so o `.env` (uma chave nova). Nunca encosta no banco do servidor,
que depois da virada e o que vale. Reinicie o recurso no Coolify em seguida.

Pede a senha do root do servidor UMA vez (ssh vps-mn). Nada e impresso do .env.

Travas da virada:
  - recusa se o MNScr estiver rodando nesta maquina (o banco estaria mudando);
  - consolida o WAL do SQLite antes de copiar (o app.db sozinho fica completo);
  - recusa se o volume ja tiver um app.db. -Substituir passa por cima, de proposito,
    e apaga o que o servidor registrou depois da virada.
#>
param(
    [string]$Servidor = "vps-mn",
    [switch]$Substituir,
    [switch]$SomenteEnv
)

$ErrorActionPreference = "Stop"
$raiz = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$banco = Join-Path $raiz "data\app.db"
$arquivoEnv = Join-Path $raiz ".env"

if ($SomenteEnv) {
    Write-Host "MNScr -> Coolify: enviar so o .env para o volume" -ForegroundColor Cyan
    $arquivos = @(".env")
} else {
    Write-Host "MNScr -> Coolify: enviar banco e .env para o volume" -ForegroundColor Cyan
    $arquivos = @("app.db", ".env")
}
Write-Host "Pasta: $raiz"

if (-not (Test-Path $arquivoEnv)) { throw "Nao encontrei $arquivoEnv" }

if (-not $SomenteEnv) {
    if (-not (Test-Path $banco)) { throw "Nao encontrei $banco" }

    $rodando = Get-CimInstance Win32_Process -Filter "Name like 'python%'" |
        Where-Object { $_.CommandLine -match "app\.main|mnscr" }
    if ($rodando) {
        throw "O MNScr esta rodando nesta maquina (PID $($rodando.ProcessId -join ', ')). Feche-o antes: dois robos publicariam no Cinerie."
    }

    $python = Join-Path $raiz ".venv\Scripts\python.exe"
    if (-not (Test-Path $python)) { $python = "python" }

    # WAL consolidado: o app.db copiado sozinho passa a ter tudo.
    $consolida = "import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); c.execute('PRAGMA wal_checkpoint(TRUNCATE)'); ok=c.execute('PRAGMA quick_check').fetchone()[0]; n=c.execute('select count(*) from cinerie_publications').fetchone()[0]; c.close(); print(ok, n)"
    $saida = & $python -c $consolida $banco
    if ($LASTEXITCODE -ne 0) { throw "Falha ao consolidar o banco: $saida" }
    $ok, $publicacoes = "$saida".Trim().Split(" ")
    if ($ok -ne "ok") { throw "O banco nao passou no quick_check: $saida" }
    Write-Host "Banco consolidado: $publicacoes publicacoes no Cinerie registradas."
}

# O que roda no servidor, numa conexao so: acha o volume, recusa se ja ha banco (na
# virada), extrai, passa os arquivos para o usuario do conteiner (uid 10001) e esconde
# o .env de quem nao e ele.
$checaBanco = if ($SomenteEnv -or $Substituir) { "0" } else { "1" }
$remoto = @'
set -e
V=$(docker volume ls -q | grep '_mnscr-data$' || true)
[ -n "$V" ] || { echo 'ERRO: volume *_mnscr-data nao existe. Faca o primeiro deploy do MNScr no Coolify antes.'; exit 2; }
[ "$(echo "$V" | wc -l)" -eq 1 ] || { echo "ERRO: mais de um volume *_mnscr-data: $V"; exit 4; }
D=$(docker volume inspect -f '{{.Mountpoint}}' "$V")
if [ "__CHECA_BANCO__" = "1" ] && [ -s "$D/app.db" ]; then echo "ERRO: $D/app.db ja existe. Use -Substituir para passar por cima."; exit 3; fi
tar -xf - -C "$D"
cd "$D" && chown 10001:10001 __ARQUIVOS__
chmod 600 "$D/.env"
echo "OK: volume $V"
ls -la "$D"
'@
$remoto = ($remoto -replace "`r", "") -replace "__CHECA_BANCO__", $checaBanco -replace "__ARQUIVOS__", ($arquivos -join " ")
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($remoto))

$preparo = Join-Path ([IO.Path]::GetTempPath()) ("mnscr-estado-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $preparo | Out-Null
try {
    Copy-Item $arquivoEnv (Join-Path $preparo ".env")
    if (-not $SomenteEnv) { Copy-Item $banco (Join-Path $preparo "app.db") }

    # Um .cmd, e nao o pipe do PowerShell 5.1: aquele corromperia os bytes do tar.
    # Dentro das aspas, `|`, `>` e `&&` sao do comando remoto, nao do cmd.
    $lote = Join-Path $preparo "enviar.cmd"
    $linha = "tar --format ustar -cf - -C `"$preparo`" $($arquivos -join ' ') | ssh $Servidor `"echo $b64 | base64 -d > /tmp/mnscr-enviar.sh && sh /tmp/mnscr-enviar.sh; s=`$?; rm -f /tmp/mnscr-enviar.sh; exit `$s`""
    Set-Content -Path $lote -Value "@echo off`r`n$linha`r`n" -Encoding ASCII

    Write-Host "Conectando em $Servidor (a senha do root sera pedida uma vez)..."
    & cmd.exe /c $lote
    if ($LASTEXITCODE -ne 0) { throw "O envio falhou (codigo $LASTEXITCODE)." }
}
finally {
    Remove-Item -Recurse -Force $preparo
}

Write-Host ""
if ($SomenteEnv) {
    Write-Host "Pronto. Reinicie o recurso MNScr no Coolify para ele ler o .env novo." -ForegroundColor Green
} else {
    Write-Host "Pronto. Em ate 1 minuto o conteiner sai da espera e comeca o ciclo." -ForegroundColor Green
    Write-Host "Nao rode mais o MNScr nesta maquina: ele publicaria em dobro." -ForegroundColor Yellow
}
