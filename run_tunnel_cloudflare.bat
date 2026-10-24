@echo off
title DEWaS CCTV Cloudflare Tunnel
echo ===================================================
echo Memulai Tunneling Cloudflare untuk CCTV Stream...
echo Mengarahkan port lokal 8554 ke internet publik...
echo ===================================================
cloudflared tunnel --url http://localhost:8554
pause
