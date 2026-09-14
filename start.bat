@echo off
REM One-click launcher for the youtube-ready-ai web UI (Windows).
REM Double-click this file in File Explorer to run it.

cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo Python 3.11+ is required but was not found on this machine.
    echo Install it from https://www.python.org/downloads/ and run this script again.
    pause
    exit /b 1
)

if not exist ".venv" (
    echo Setting up youtube-ready-ai for the first time (this only happens once)...
    python -m venv .venv
)

call .venv\Scripts\activate.bat

python -c "import fastapi, uvicorn" >nul 2>nul
if errorlevel 1 (
    echo Installing dependencies...
    pip install --quiet --upgrade pip
    pip install --quiet -e ".[web]"
)

echo Starting youtube-ready-ai...
youtube-ready-web
pause
