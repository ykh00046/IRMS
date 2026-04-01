@echo off
setlocal

cd /d "%~dp0"

set "PYTHON_EXE=python"
if exist ".venv\Scripts\python.exe" (
  set "PYTHON_EXE=.venv\Scripts\python.exe"
)

set "LOCAL_IP="
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4"') do (
  for /f "tokens=1" %%b in ("%%a") do (
    echo %%b | findstr /b "192.168. 10." >nul && if not defined LOCAL_IP set "LOCAL_IP=%%b"
  )
)
if not defined LOCAL_IP set "LOCAL_IP=0.0.0.0"
echo [IRMS] Intranet server starting...
echo [IRMS] Local:   http://127.0.0.1:8000
echo [IRMS] Network: http://%LOCAL_IP%:8000
echo.
"%PYTHON_EXE%" -m uvicorn src.main:app --host 0.0.0.0 --port 8000

if errorlevel 1 (
  echo [IRMS] Server exited with code %errorlevel%.
  pause
)
