@echo off
REM IRMS 저울 에이전트 — 계량하는 현장 PC에서 실행 (창을 켜 두세요)
cd /d "%~dp0"
title IRMS 저울 에이전트
where python >nul 2>&1
if errorlevel 1 (
  echo [ERROR] python 이 없습니다. python 설치 후 pip install -r requirements.txt
  pause
  exit /b 1
)
python -c "import serial" 2>nul || python -m pip install -r requirements.txt
python agent.py
pause
