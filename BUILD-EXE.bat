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

REM ----- 0) Aviso si el proyecto esta dentro de OneDrive -----
echo %CD% | findstr /I "OneDrive" >nul
if not errorlevel 1 (
    echo.
    echo  !!  ADVERTENCIA  !!
    echo  La carpeta del proyecto esta dentro de OneDrive:
    echo    %CD%
    echo.
    echo  OneDrive bloquea archivos mientras los sincroniza y
    echo  PyInstaller suele fallar con "Access is denied" al
    echo  limpiar la carpeta build\.
    echo.
    echo  RECOMENDADO: mueve la carpeta fuera de OneDrive,
    echo  por ejemplo a C:\Dev\rocket-league-overlay\
    echo.
    set /p ONEDRIVE_OK="Continuar de todas formas? [s/N]: "
    if /I not "!ONEDRIVE_OK!"=="s" (
        echo Cancelado.
        pause
        exit /b 1
    )
)

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

REM ----- 2) Matar procesos previos que puedan bloquear archivos -----
taskkill /IM rl-overlay.exe /F >nul 2>&1

REM ----- 3) Validar .venv: debe haber sido creado en ESTA carpeta -----
if exist ".venv\Scripts\activate.bat" (
    findstr /I /C:"%CD%\.venv" ".venv\Scripts\activate.bat" >nul
    if errorlevel 1 (
        echo [setup] .venv apunta a otra ubicacion ^(probablemente lo copiaste^). Recreando...
        call :force_rmdir .venv
        if errorlevel 1 exit /b 1
    )
) else (
    if exist ".venv" (
        echo [setup] .venv esta incompleto. Recreando...
        call :force_rmdir .venv
        if errorlevel 1 exit /b 1
    )
)

REM ----- 4) Crear venv si no existe -----
if not exist ".venv" (
    echo [setup] Creando entorno virtual...
    python -m venv .venv
    if errorlevel 1 (
        echo  X  No se pudo crear el venv.
        pause
        exit /b 1
    )
)

REM ----- 5) Instalar deps + pyinstaller -----
echo [setup] Instalando dependencias y PyInstaller...
".venv\Scripts\python" -m pip install -q --upgrade pip
".venv\Scripts\pip" install -q -r requirements.txt
".venv\Scripts\pip" install -q pyinstaller

REM ----- 6) Limpiar build\ y dist\ previos con reintentos -----
call :force_rmdir build
if errorlevel 1 exit /b 1
call :force_rmdir dist
if errorlevel 1 exit /b 1

REM ----- 7) Compilar .exe -----
echo.
echo [build] Compilando rl-overlay.exe (puede tardar 1-2 min)...
echo.
".venv\Scripts\pyinstaller" --onefile --noconfirm ^
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
exit /b 0

REM ===================== subrutinas =====================

:force_rmdir
REM Borra un directorio con reintentos. %1 = ruta relativa o absoluta.
set "TARGET=%~1"
if not exist "%TARGET%" exit /b 0
echo [clean] Borrando %TARGET%\ ...
set /a TRIES=0
:force_rmdir_retry
set /a TRIES+=1
rmdir /s /q "%TARGET%" 2>nul
if not exist "%TARGET%" exit /b 0
if !TRIES! geq 5 (
    echo  X  No se pudo borrar %TARGET%\ despues de varios intentos.
    echo     Causas comunes: OneDrive sincronizando, antivirus escaneando,
    echo     o el Explorador abierto dentro de %TARGET%\.
    echo     Cierra esos programas y vuelve a correr este script.
    pause
    exit /b 1
)
timeout /t 1 /nobreak >nul
goto force_rmdir_retry
