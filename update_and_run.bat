@echo off
setlocal

cd /d "%~dp0"

echo ============================================
echo  IRMS Update and Run
echo ============================================
echo.

:: ── 1. Git Pull ──
echo [1/3] Checking for updates...
git pull origin main
if errorlevel 1 (
  echo [ERROR] Git pull failed. Check network or credentials.
  pause
  exit /b 1
)
echo.

:: ── 2. Install dependencies ──
set "PYTHON_EXE=python"
if exist ".venv\Scripts\python.exe" (
  set "PYTHON_EXE=.venv\Scripts\python.exe"
)

echo [2/3] Installing dependencies...
"%PYTHON_EXE%" -m pip install -r requirements.txt --quiet
if errorlevel 1 (
  echo [ERROR] pip install failed.
  pause
  exit /b 1
)
echo.

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
echo  Local:   http://127.0.0.1:8000
echo  Network: http://%LOCAL_IP%:8000
echo ============================================
echo.
"%PYTHON_EXE%" -m uvicorn src.main:app --host 0.0.0.0 --port 8000

if errorlevel 1 (
  echo [IRMS] Server exited with code %errorlevel%.
  pause
)
