@echo off
title Nonaktifkan Auto-Startup DEWaS CCTV Fire
cd /d "%~dp0"
echo ===================================================================
echo Menonaktifkan Auto-Startup DEWaS CCTV Fire Gateway...
echo ===================================================================

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" autostart.py --disable
) else (
    python autostart.py --disable
)

echo.
pause

