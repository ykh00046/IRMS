@echo off
setlocal

cd /d "%~dp0"

echo ============================================
echo  IRMS Update and Run
echo ============================================
echo.

:: ── 0. Check setup ──
if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] Setup not completed. Run setup_server.bat first.
  pause
  exit /b 1
)

:: ── 1. Git Pull ──
echo [1/3] Checking for updates...
git pull origin main
if errorlevel 1 (
  echo.
  echo [ERROR] Git pull failed. Check network or credentials.
  pause
  exit /b 1
)
echo.

:: ── 2. Install dependencies ──
echo [2/3] Installing dependencies...
.venv\Scripts\python.exe -m pip install -r requirements.txt --quiet
if errorlevel 1 (
  echo.
  echo [ERROR] pip install failed.
  pause
  exit /b 1
)
echo.

:: ── 2.5. Check .env ──
if not exist ".env" (
  echo [WARN] .env file not found. Running with development defaults.
  echo        For production, copy .env.example to .env and set IRMS_ENV=production
  echo        and a unique IRMS_SESSION_SECRET.
  echo.
)

:: ── 2.7. Free port 9000 if already in use ──
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /r /c:":9000 .*LISTENING"') do (
  echo [INFO] Port 9000 is in use by PID %%p. Terminating...
  taskkill /PID %%p /F >nul 2>&1
)

:: ── 3. Start server ──
set "LOCAL_IP="
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4"') do (
  for /f "tokens=1" %%b in ("%%a") do (
    echo %%b | findstr /b "192.168. 10." >nul && if not defined LOCAL_IP set "LOCAL_IP=%%b"
  )
)
if not defined LOCAL_IP set "LOCAL_IP=0.0.0.0"

echo [3/3] Starting IRMS server...
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
