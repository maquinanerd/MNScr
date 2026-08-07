@echo off
setlocal

cd /d "%~dp0"

set "VENV_PYTHON=%~dp0.venv\Scripts\python.exe"

if not exist "%VENV_PYTHON%" (
    echo [ERRO] Ambiente virtual nao encontrado em .venv\Scripts\python.exe
    echo Execute "make install" ou crie o venv antes de iniciar.
    pause
    exit /b 1
)

echo Iniciando MNScr...
"%VENV_PYTHON%" -m app.main
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
    echo.
    echo O programa terminou com codigo de erro %EXIT_CODE%.
    pause
)

endlocal
exit /b %EXIT_CODE%
