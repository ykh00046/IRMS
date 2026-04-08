@echo off
setlocal

echo ============================================
echo  IRMS Server Setup (First Time)
echo ============================================
echo.

:: ── 1. Check Python ──
echo [1/4] Checking Python...
python --version >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Python not found.
  echo.
  echo  Please install Python 3.12:
  echo    https://www.python.org/downloads/
  echo.
  echo  IMPORTANT: Check "Add Python to PATH" during installation!
  echo  After installing, close this window and run setup_server.bat again.
  echo.
  pause
  exit /b 1
)
python --version
echo.

:: ── 2. Check Git ──
echo [2/4] Checking Git...
git --version >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Git not found.
  echo.
  echo  Please install Git:
  echo    https://git-scm.com/download/win
  echo.
  echo  After installing, close this window and run setup_server.bat again.
  echo.
  pause
  exit /b 1
)
git --version
echo.

:: ── 3. Clone or update project ──
echo [3/4] Setting up project...
set "REPO_URL=https://github.com/ykh00046/IRMS.git"
set "PROJECT_DIR=%~dp0IRMS"

if exist "%PROJECT_DIR%\.git" (
  echo [INFO] Project already exists. Updating...
  cd /d "%PROJECT_DIR%"
  git pull origin main
) else (
  echo [INFO] Cloning project...
  git clone %REPO_URL% "%PROJECT_DIR%"
  if errorlevel 1 (
    echo.
    echo [ERROR] Clone failed. Check network connection.
    pause
    exit /b 1
  )
  cd /d "%PROJECT_DIR%"
)
echo.

:: ── 4. Bootstrap (venv + dependencies) ──
echo [4/4] Setting up virtual environment and dependencies...
echo  (This may take a few minutes on first run)
echo.
python tools\bootstrap_irms.py --run-smoke
if errorlevel 1 (
  echo.
  echo [ERROR] Bootstrap failed. See error messages above.
  pause
  exit /b 1
)

:: Verify
echo.
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
echo  Project location: %PROJECT_DIR%
echo.
echo  To start the server:
echo    1. Open the IRMS folder
echo    2. Double-click update_and_run.bat
echo ============================================
echo.
pause
