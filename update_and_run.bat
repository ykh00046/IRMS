@echo off
setlocal

cd /d "%~dp0"

echo ============================================
echo  IRMS Update and Run
echo ============================================
echo.

:: ── 0. Check setup ──
if exist ".venv\Scripts\python.exe" goto setup_ok
echo [ERROR] Setup not completed. Run setup_server.bat first.
pause
exit /b 1
:setup_ok

:: ── 1. Backup DB before pull ──
echo [1/4] Backing up database...
if exist "data\irms.db" goto backup_db
echo [INFO] No existing DB to back up (first run).
goto after_backup
:backup_db
if not exist "backups" mkdir "backups"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ts=Get-Date -Format 'yyyyMMdd_HHmmss'; $dest='backups\irms_' + $ts + '.db'; Copy-Item -LiteralPath 'data\irms.db' -Destination $dest -Force; Write-Host ('[OK] Saved ' + $dest)"
if errorlevel 1 echo [WARN] DB backup failed, continuing anyway.
:after_backup
echo.

:: ── 2. Git Pull ──
echo [2/4] Checking for updates...
git pull origin main
if not errorlevel 1 goto git_ok
echo.
echo [ERROR] Git pull failed. Check network or credentials.
pause
exit /b 1
:git_ok
echo.

:: ── 3. Install dependencies ──
echo [3/4] Installing dependencies...
.venv\Scripts\python.exe -m pip install -r requirements.txt --quiet
if not errorlevel 1 goto pip_ok
echo.
echo [ERROR] pip install failed.
pause
exit /b 1
:pip_ok
echo.

:: ── 2.5. Check .env ──
if exist ".env" goto env_ok
  echo [WARN] .env file not found. Running with development defaults.
  echo        For production, copy .env.example to .env and set IRMS_ENV=production
  echo        and a unique IRMS_SESSION_SECRET.
  echo.
:env_ok

:: ── 2.7. Free port 9000 if already in use ──
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ports=Get-NetTCPConnection -LocalPort 9000 -State Listen -ErrorAction SilentlyContinue; foreach($p in $ports){ Write-Host ('[INFO] Port 9000 is in use by PID ' + $p.OwningProcess + '. Terminating...'); Stop-Process -Id $p.OwningProcess -Force -ErrorAction SilentlyContinue }"

:: ── 3. Start server ──
set "LOCAL_IP=0.0.0.0"

echo [4/4] Starting IRMS server...
echo ============================================
echo  Local:   http://127.0.0.1:9000
echo  Network: http://%LOCAL_IP%:9000
echo ============================================
echo.
echo  Press Ctrl+C to stop the server.
echo.
.venv\Scripts\python.exe -m uvicorn src.main:app --host 0.0.0.0 --port 9000

echo.
echo [IRMS] Server stopped.
pause
