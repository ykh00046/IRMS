@echo off
setlocal
cd /d "%~dp0"

if not exist "cloudflared\config.yml" (
  echo [ERROR] cloudflared\config.yml not found.
  echo         Run setup_tunnel.bat first, then copy config.example.yml
  echo         to config.yml and replace placeholders.
  pause
  exit /b 1
)

where cloudflared >nul 2>&1
if errorlevel 1 (
  echo [ERROR] cloudflared not on PATH. Run setup_tunnel.bat.
  pause
  exit /b 1
)

echo ============================================
echo  IRMS Cloudflare Tunnel - Debug Run
echo ============================================
echo  Press Ctrl+C to stop.
echo  For permanent install:   cloudflared service install
echo ============================================
echo.

cloudflared tunnel --config cloudflared\config.yml run
pause
