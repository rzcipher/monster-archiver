@echo off
REM Double-click launcher for Monster Archiver on Windows.
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo Python was not found on PATH. Install Python 3.10+ from https://python.org
    echo ^(tick "Add python.exe to PATH" during install^) and run this file again.
    pause
    exit /b 1
)

python rezakir.py
pause
