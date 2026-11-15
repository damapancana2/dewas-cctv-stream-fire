@echo off
title DEWaS CCTV Fire & Smoke AI - Desktop Player
cd /d %~dp0

echo ===================================================================
echo   DEWaS CCTV Fire ^& Smoke AI Live Desktop Monitor
echo   Running with PyQt6 GUI and YOLOv5 Detection (best.pt)
echo ===================================================================

python desktop_stream.py

pause
