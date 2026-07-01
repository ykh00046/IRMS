@echo off
REM =====================================================================
REM  IRMS 서버 + 자동 업데이트 (창 하나)
REM  - 더블클릭하면 이 창에서 서버가 뜨고 로그가 흐릅니다.
REM  - IRMS_AUTO_INTERVAL 초마다 origin/main 확인 → 새 커밋 있으면
REM    DB 백업 → git pull → pip → 서버 재시작 (같은 창에서).
REM  - 재부팅 자동 시작: 이 파일 바로가기를 shell:startup 폴더에 넣기.
REM =====================================================================
setlocal
cd /d "%~dp0"

REM ── 설정 (필요하면 숫자만 바꾸세요) ──
set "IRMS_PORT=9000"
set "IRMS_AUTO_INTERVAL=600"
REM   IRMS_AUTO_INTERVAL = 업데이트 확인 주기(초). 600 = 10분.
REM   자동 업데이트를 끄려면 아래 주석을 해제:
REM set "IRMS_AUTO_UPDATE=0"

title IRMS 서버 + 자동 업데이트 (포트 %IRMS_PORT%)

if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" serve.py
) else (
  python serve.py
)

echo.
echo [IRMS] 종료되었습니다.
pause
