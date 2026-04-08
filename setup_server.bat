@echo off
setlocal

cd /d "%~dp0"

echo ============================================
echo  IRMS Server Setup (First Time)
echo ============================================
echo.

:: ── 1. Check Python ──
echo [1/4] Checking Python...
python --version >nul 2>&1
if errorlevel 1 (
  echo [INFO] Python not found. Installing...
  winget install Python.Python.3.12 --accept-package-agreements --accept-source-agreements
  if errorlevel 1 (
    echo [ERROR] Python install failed. Install manually: https://python.org
    pause
    exit /b 1
  )
  echo [INFO] Close this window and re-open to refresh PATH, then run setup_server.bat again.
  pause
  exit /b 0
)
python --version
echo.

:: ── 2. Check Git ──
echo [2/4] Checking Git...
git --version >nul 2>&1
if errorlevel 1 (
  echo [INFO] Git not found. Installing...
  winget install Git.Git --accept-package-agreements --accept-source-agreements
  if errorlevel 1 (
    echo [ERROR] Git install failed. Install manually: https://git-scm.com
    pause
    exit /b 1
  )
  echo [INFO] Close this window and re-open to refresh PATH, then run setup_server.bat again.
  pause
  exit /b 0
)
git --version
echo.

:: ── 3. Bootstrap (venv + dependencies) ──
echo [3/4] Setting up virtual environment and dependencies...
python tools\bootstrap_irms.py --run-smoke
if errorlevel 1 (
  echo [ERROR] Bootstrap failed.
  pause
  exit /b 1
)
echo.

:: ── 4. Verify ──
echo [4/4] Verifying setup...
if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] Virtual environment not found.
  pause
  exit /b 1
)
.venv\Scripts\python.exe -c "import uvicorn; print('[OK] uvicorn')"
.venv\Scripts\python.exe -c "import fastapi; print('[OK] fastapi')"
.venv\Scripts\python.exe -c "import jinja2; print('[OK] jinja2')"
echo.

echo ============================================
echo  Setup complete!
echo.
echo  Next: run update_and_run.bat to start IRMS
echo ============================================
pause
