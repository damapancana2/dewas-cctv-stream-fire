@echo off
title DEWaS CCTV Fire & Smoke AI Edge Gateway (Port 8555)
cd /d "%~dp0"

echo ===================================================================
echo   DEWaS CCTV Fire ^& Smoke AI Streaming Gateway
echo   Running YOLOv5 Detection (best.pt) on Port 8555
echo ===================================================================

python -c "import cv2, torch" >nul 2>&1
if %ERRORLEVEL% equ 0 (
    python app.py
    goto check_pause
)

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" app.py
) else (
    python app.py
)

:check_pause

if "%1"=="--no-pause" goto end
if not "%CI%"=="" goto end
pause
:end
