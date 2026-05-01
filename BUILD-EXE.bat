@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"
title Build rl-overlay.exe

echo.
echo  ===============================================
echo    BUILD rl-overlay.exe
echo  ===============================================
echo.
echo  Esto compila un unico .exe autosuficiente.
echo  Solo necesitas hacerlo UNA VEZ. Despues de eso,
echo  puedes copiar dist\rl-overlay.exe donde quieras
echo  y solo doble-click para correrlo.
echo.

REM ----- 1) Python -----
python --version >nul 2>&1
if errorlevel 1 (
    echo [setup] Python no encontrado. Instalando via winget...
    winget install -e --id Python.Python.3.12 --silent --accept-source-agreements --accept-package-agreements
    if errorlevel 1 (
        echo  X  No se pudo instalar Python automaticamente.
        echo     Descargalo desde https://python.org/downloads
        pause
        exit /b 1
    )
    for /f "tokens=2*" %%a in ('reg query "HKCU\Environment" /v PATH 2^>nul') do set "PATH=%%b;!PATH!"
)

REM ----- 2) venv + deps + pyinstaller -----
if not exist ".venv" (
    echo [setup] Creando entorno virtual...
    python -m venv .venv
)

echo [setup] Instalando dependencias y PyInstaller...
".venv\Scripts\python" -m pip install -q --upgrade pip
".venv\Scripts\pip" install -q -r requirements.txt
".venv\Scripts\pip" install -q pyinstaller

REM ----- 3) Compilar .exe -----
echo.
echo [build] Compilando rl-overlay.exe (puede tardar 1-2 min)...
echo.
".venv\Scripts\pyinstaller" --onefile --clean --noconfirm ^
    --name rl-overlay ^
    --add-data "static;static" ^
    --add-data "DefaultStatsAPI.ini;." ^
    --hidden-import uvicorn.lifespan.on ^
    --hidden-import uvicorn.lifespan.off ^
    --hidden-import uvicorn.protocols.websockets.auto ^
    --hidden-import uvicorn.protocols.websockets.websockets_impl ^
    --hidden-import uvicorn.protocols.websockets.wsproto_impl ^
    --hidden-import uvicorn.protocols.http.auto ^
    --hidden-import uvicorn.protocols.http.h11_impl ^
    --hidden-import uvicorn.protocols.http.httptools_impl ^
    --hidden-import uvicorn.loops.auto ^
    --hidden-import uvicorn.loops.asyncio ^
    --hidden-import uvicorn.logging ^
    --hidden-import websockets.legacy ^
    --hidden-import websockets.legacy.server ^
    app.py

if errorlevel 1 (
    echo.
    echo  X  La compilacion fallo. Revisa los errores arriba.
    pause
    exit /b 1
)

echo.
echo  ===============================================
echo    LISTO
echo  ===============================================
echo.
echo  Tu exe esta en:
echo    %CD%\dist\rl-overlay.exe
echo.
echo  Doble-click sobre ese archivo para correrlo.
echo  La primera vez instalara DefaultStatsAPI.ini en
echo  Rocket League automaticamente; reinicia RL una
echo  vez y listo.
echo.
pause
