@echo off
setlocal EnableExtensions
title MNScr - Motor editorial do Cinerie
cd /d "%~dp0"

set "VENV_PYTHON=%~dp0.venv\Scripts\python.exe"

:menu
cls
echo ===========================================================
echo    MNScr - Motor editorial do Cinerie
echo ===========================================================
echo    Pasta : %CD%
if exist "%VENV_PYTHON%" (
    echo    Python: .venv\Scripts\python.exe
) else (
    echo    Python: NAO ENCONTRADO - rode a opcao [I] primeiro
)
if exist "%~dp0.env" (
    echo    .env  : nesta pasta
) else (
    echo    .env  : ausente aqui; o app procura nos diretorios acima
)
echo -----------------------------------------------------------
echo    [1] Rodar em loop         - pipeline continuo
echo    [2] Rodar um ciclo        - --once
echo -----------------------------------------------------------
echo    [3] Preflight do contrato Cinerie - nao publica
echo    [4] Publicacoes pedidas ao Cinerie e seus desfechos
echo    [5] Drafts bloqueados no Editorial Gate
echo    [6] Drafts que exigem revisao no Editorial Gate
echo -----------------------------------------------------------
echo    [T] Rodar a suite de testes
echo    [L] Rodar o ruff
echo    [I] Instalar / atualizar o ambiente virtual
echo    [0] Sair
echo -----------------------------------------------------------
echo.
set "OPT="
set /p "OPT=Escolha e tecle Enter: "

if /i "%OPT%"=="1" goto run_loop
if /i "%OPT%"=="2" goto run_once
if /i "%OPT%"=="3" goto preflight
if /i "%OPT%"=="4" goto publications
if /i "%OPT%"=="5" goto gate_blocked
if /i "%OPT%"=="6" goto gate_review
if /i "%OPT%"=="T" goto tests
if /i "%OPT%"=="L" goto lint
if /i "%OPT%"=="I" goto install
if /i "%OPT%"=="0" goto fim
goto menu

:run_loop
call :require_venv
if errorlevel 1 goto menu
cls
echo Rodando o pipeline em loop. Ctrl+C encerra.
echo.
call :run -m app.main
goto menu

:run_once
call :require_venv
if errorlevel 1 goto menu
cls
echo Rodando um unico ciclo do pipeline.
echo.
call :run -m app.main --once
goto menu

:preflight
call :require_venv
if errorlevel 1 goto menu
cls
call :run -m app.main --cinerie-contract
echo.
call :run -m app.main --cinerie-preflight
goto menu

:publications
call :require_venv
if errorlevel 1 goto menu
cls
call :run -m app.main --list-cinerie-publications
goto menu

:gate_blocked
call :require_venv
if errorlevel 1 goto menu
cls
call :run -m app.main --list-gate-blocked
goto menu

:gate_review
call :require_venv
if errorlevel 1 goto menu
cls
call :run -m app.main --list-gate-review-required
goto menu

:tests
call :require_venv
if errorlevel 1 goto menu
cls
call :run -m pytest
goto menu

:lint
call :require_venv
if errorlevel 1 goto menu
cls
call :run -m ruff check .
goto menu

:install
cls
where python >nul 2>&1
if errorlevel 1 (
    echo [ERRO] Nenhum "python" no PATH. Instale o Python 3.11 ou superior.
    echo.
    pause
    goto menu
)
if not exist "%VENV_PYTHON%" (
    echo Criando o ambiente virtual em .venv ...
    python -m venv .venv
    if errorlevel 1 (
        echo [ERRO] Falha ao criar o ambiente virtual.
        echo.
        pause
        goto menu
    )
)
echo Instalando as dependencias ...
"%VENV_PYTHON%" -m pip install --upgrade pip
"%VENV_PYTHON%" -m pip install -e ".[dev]"
echo.
echo Terminado com codigo %ERRORLEVEL%.
pause
goto menu

:run
"%VENV_PYTHON%" %*
set "EXIT_CODE=%ERRORLEVEL%"
echo.
if "%EXIT_CODE%"=="0" (
    echo Terminado sem erro.
) else (
    echo Terminado com codigo de erro %EXIT_CODE%.
)
pause
exit /b %EXIT_CODE%

:require_venv
if exist "%VENV_PYTHON%" exit /b 0
echo.
echo [ERRO] Ambiente virtual nao encontrado em .venv\Scripts\python.exe
echo Use a opcao [I] deste menu para criar o ambiente.
echo.
pause
exit /b 1

:fim
endlocal
exit /b 0
