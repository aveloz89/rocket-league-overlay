@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"
title RL Live Overlay

echo.
echo  ===============================================
echo    ROCKET LEAGUE - LIVE STATS OVERLAY
echo  ===============================================
echo.

REM ----- 1) Python -----
python --version >nul 2>&1
if errorlevel 1 (
    echo [setup] Python no encontrado. Instalando via winget...
    echo         ^(puede tardar ~1 min^)
    winget install -e --id Python.Python.3.12 --silent --accept-source-agreements --accept-package-agreements
    if errorlevel 1 (
        echo.
        echo  X  No se pudo instalar Python automaticamente.
        echo     Descargalo manualmente desde https://python.org/downloads
        echo     y vuelve a correr este archivo.
        echo.
        pause
        exit /b 1
    )
    REM Refresh PATH from registry so the new python is visible in this shell
    for /f "tokens=2*" %%a in ('reg query "HKCU\Environment" /v PATH 2^>nul') do set "PATH=%%b;!PATH!"
    for /f "tokens=2*" %%a in ('reg query "HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment" /v PATH 2^>nul') do set "PATH=!PATH!;%%b"
)

REM ----- 2) venv + deps -----
if not exist ".venv" (
    echo [setup] Creando entorno virtual...
    python -m venv .venv
    if errorlevel 1 (
        echo  X  Fallo creando venv. Borra la carpeta .venv y reintenta.
        pause
        exit /b 1
    )
    echo [setup] Instalando dependencias...
    ".venv\Scripts\python" -m pip install -q --upgrade pip
    ".venv\Scripts\pip" install -q -r requirements.txt
    if errorlevel 1 (
        echo  X  Fallo instalando dependencias.
        pause
        exit /b 1
    )
)

REM ----- 3) Activar Stats API de RL si todavia no lo esta -----
set "RL_CFG=%USERPROFILE%\Documents\My Games\Rocket League\TAGame\Config"
if not exist "%RL_CFG%\DefaultStatsAPI.ini" (
    echo [setup] Activando Stats API en Rocket League...
    if not exist "%RL_CFG%" mkdir "%RL_CFG%"
    copy /Y "DefaultStatsAPI.ini" "%RL_CFG%\DefaultStatsAPI.ini" >nul
    echo.
    echo  !  IMPORTANTE: Reinicia Rocket League una vez para que la API se active.
    echo.
    timeout /t 5 >nul
)

REM ----- 4) Lanzar overlay y abrir navegador -----
echo [run] Arrancando overlay en http://127.0.0.1:8080
echo       Cierra esta ventana para detener el overlay.
echo.
start "" http://127.0.0.1:8080
".venv\Scripts\python" app.py --port 8080
