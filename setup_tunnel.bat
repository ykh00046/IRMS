@echo off
setlocal
cd /d "%~dp0"

echo ============================================
echo  IRMS Cloudflare Tunnel - Initial Setup
echo ============================================
echo.

:: ── 1. cloudflared install check ──
where cloudflared >nul 2>&1
if errorlevel 1 (
  echo [1/5] cloudflared not found. Installing via winget...
  winget install --id Cloudflare.cloudflared --silent --accept-source-agreements --accept-package-agreements
  if errorlevel 1 (
    echo.
    echo [ERROR] winget install failed.
    echo         Install manually from:
    echo         https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/
    pause
    exit /b 1
  )
  echo [OK] cloudflared installed. You may need to reopen this terminal.
  echo      Then re-run setup_tunnel.bat.
  pause
  exit /b 0
) else (
  echo [1/5] cloudflared already installed.
)
echo.

:: ── 2. login (browser opens) ──
echo [2/5] Browser will open for Cloudflare login.
echo        Pick the domain you want to use for IRMS.
cloudflared tunnel login
if errorlevel 1 (
  echo [ERROR] Login failed or canceled.
  pause
  exit /b 1
)
echo.

:: ── 3. create tunnel ──
set /p TUNNEL_NAME=Enter tunnel name (default: irms):
if "%TUNNEL_NAME%"=="" set "TUNNEL_NAME=irms"
echo [3/5] Creating tunnel "%TUNNEL_NAME%"...
cloudflared tunnel create %TUNNEL_NAME%
if errorlevel 1 (
  echo [WARN] Tunnel may already exist. Continuing.
)
echo.

:: ── 4. DNS route ──
set /p HOSTNAME=Enter public hostname (e.g. irms.example.com):
if "%HOSTNAME%"=="" (
  echo [ERROR] Hostname is required.
  pause
  exit /b 1
)
echo [4/5] Routing DNS %HOSTNAME% -> tunnel %TUNNEL_NAME%...
cloudflared tunnel route dns %TUNNEL_NAME% %HOSTNAME%
echo.

:: ── 5. next steps ──
echo [5/5] Base setup complete.
echo.
echo NEXT STEPS:
echo  1. Copy cloudflared\config.example.yml to cloudflared\config.yml
echo  2. Replace ^<TUNNEL_UUID^> with the UUID shown above
echo  3. Replace ^<USER^> with %USERNAME% (or full path to .cloudflared dir)
echo  4. Replace hostname placeholders with %HOSTNAME%
echo  5. Test once (Ctrl+C to stop):   run_tunnel.bat
echo  6. Install as service:            cloudflared service install
echo  7. Verify Windows service:        sc query cloudflared
echo  8. Open in browser:               https://%HOSTNAME%/health
echo.
echo See docs\external-access.md for full guide.
pause
