@echo off
setlocal

cd /d "%~dp0"

set "PYTHON_EXE=python"
if exist ".venv\Scripts\python.exe" (
  set "PYTHON_EXE=.venv\Scripts\python.exe"
)

echo [IRMS] Starting intranet server on http://0.0.0.0:8000
"%PYTHON_EXE%" -m uvicorn src.main:app --host 0.0.0.0 --port 8000

if errorlevel 1 (
  echo [IRMS] Server exited with code %errorlevel%.
  pause
)
