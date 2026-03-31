@echo off
setlocal

cd /d "%~dp0"

set "PYTHON_EXE=python"
if exist ".venv\Scripts\python.exe" (
  set "PYTHON_EXE=.venv\Scripts\python.exe"
)

echo [IRMS] Starting server on http://127.0.0.1:8000
"%PYTHON_EXE%" -m uvicorn src.main:app --reload

if errorlevel 1 (
  echo [IRMS] Server exited with code %errorlevel%.
  pause
)
