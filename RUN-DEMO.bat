@echo off
setlocal
cd /d "%~dp0"
title RL Live Overlay (DEMO)

if not exist ".venv" (
    echo Primero corre RUN.bat al menos una vez para instalar el entorno.
    pause
    exit /b 1
)

echo Arrancando overlay en modo DEMO ^(datos sinteticos, no necesita Rocket League^)...
start "" http://127.0.0.1:8080
".venv\Scripts\python" app.py --demo --port 8080
