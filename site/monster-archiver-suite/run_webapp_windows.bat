@echo off
REM Double-click launcher for the Monster Archiver Suite web app (Windows).
cd /d "%~dp0webapp"

where node >nul 2>nul
if errorlevel 1 (
    echo Node.js was not found on PATH. Install the LTS version from https://nodejs.org
    echo and run this file again.
    pause
    exit /b 1
)

where python >nul 2>nul
if errorlevel 1 (
    echo Python was not found on PATH. Install Python 3.10+ from https://python.org
    echo ^(tick "Add python.exe to PATH" during install^) and run this file again.
    pause
    exit /b 1
)

if not exist node_modules (
    echo Installing dependencies for the first time — this can take a minute...
    call npm install
    if errorlevel 1 (
        echo npm install failed. See the errors above.
        pause
        exit /b 1
    )
)

call npm run dev
pause
