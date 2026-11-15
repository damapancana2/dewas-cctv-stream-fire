@echo off
title Aktifkan Auto-Startup DEWaS CCTV Fire
cd /d "%~dp0"
echo ===================================================================
echo Mengaktifkan Auto-Startup DEWaS CCTV Fire Gateway saat restart...
echo ===================================================================

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" autostart.py --enable
) else (
    python autostart.py --enable
)

echo.
pause

