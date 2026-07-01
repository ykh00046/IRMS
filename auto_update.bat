@echo off
REM =====================================================================
REM  IRMS 자동 업데이트 감시자 (auto_update.bat)
REM  - 이 창은 켜 두세요(닫지 마세요). INTERVAL 초마다 origin/main 을 확인해서:
REM      * 서버가 꺼져 있으면 'IRMS Server' 창을 띄우고,
REM      * 새 커밋이 있으면 DB 백업 -> git pull -> pip -> 서버 재시작.
REM  - 서버는 별도의 'IRMS Server' 창에서 돕니다(로그/상태 확인용, 계속 보임).
REM  - 최초 1회: 이 파일을 더블클릭. 재부팅 후 자동 시작을 원하면 이 파일의
REM    바로가기를 시작프로그램 폴더(shell:startup)에 넣으세요.
REM =====================================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"

REM ── 설정 ─────────────────────────────────────────────────────
set "PORT=9000"
set "INTERVAL=180"
REM   INTERVAL = 확인 주기(초). 180 = 3분.

if not exist "logs" mkdir "logs"
set "LOG=logs\auto_update.log"

if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] .venv 가 없습니다. 먼저 setup_server.bat 을 실행하세요.
  pause
  exit /b 1
)

title IRMS 자동 업데이트 (%INTERVAL%초마다 확인)
echo ============================================
echo  IRMS 자동 업데이트 감시 시작 (포트 %PORT%, %INTERVAL%초 주기)
echo  이 창을 닫지 마세요. 서버는 'IRMS Server' 창에서 돕니다.
echo ============================================

:loop
  REM 1) 서버 살아있는지 확인, 꺼져 있으면 기동(재부팅/크래시 복구)
  call :is_running RUN
  if "!RUN!"=="0" (
    echo [%date% %time%] 서버가 꺼져 있어 시작합니다.
    echo [%date% %time%] server start >> "%LOG%"
    call :start_server
    goto sleep
  )

  REM 2) 업데이트 확인 (origin/main vs 로컬 HEAD)
  git fetch origin main 1>nul 2>>"%LOG%"
  for /f %%i in ('git rev-parse HEAD 2^>nul') do set "LOCAL=%%i"
  for /f %%i in ('git rev-parse origin/main 2^>nul') do set "REMOTE=%%i"
  if "!LOCAL!"=="!REMOTE!" (
    echo [%date% %time%] 변경 없음.
    goto sleep
  )

  echo [%date% %time%] 새 업데이트 발견: !LOCAL:~0,7! -^> !REMOTE:~0,7! . 반영합니다...
  echo [%date% %time%] update !LOCAL! to !REMOTE! >> "%LOG%"

  REM 2-1) DB 백업 (덮어쓰기 전 안전장치)
  if exist "data\irms.db" (
    if not exist "backups" mkdir "backups"
    powershell -NoProfile -Command "Copy-Item 'data\irms.db' ('backups\irms_' + (Get-Date -Format 'yyyyMMdd_HHmmss') + '.db') -Force" 2>>"%LOG%"
  )

  REM 2-2) 코드/의존성 반영
  git pull origin main >> "%LOG%" 2>&1
  .venv\Scripts\python.exe -m pip install -r requirements.txt --quiet >> "%LOG%" 2>&1

  REM 2-3) 서버 재시작
  call :stop_server
  call :start_server
  echo [%date% %time%] 재시작 완료.
  echo [%date% %time%] restarted >> "%LOG%"

:sleep
  timeout /t %INTERVAL% /nobreak >nul
  goto loop

REM ── 서브루틴 ─────────────────────────────────────────────────
:is_running
  set "%1=0"
  for /f %%p in ('powershell -NoProfile -Command "(Get-NetTCPConnection -LocalPort %PORT% -State Listen -ErrorAction SilentlyContinue ^| Measure-Object).Count"') do set "%1=%%p"
  exit /b 0

:stop_server
  taskkill /FI "WINDOWTITLE eq IRMS Server*" /T /F 1>nul 2>nul
  powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort %PORT% -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }" 2>nul
  exit /b 0

:start_server
  REM 새 콘솔(제목 'IRMS Server')에서 uvicorn 실행. 시작 디렉터리는 이 창에서 상속됨.
  start "IRMS Server" cmd /k ".venv\Scripts\python.exe -m uvicorn src.main:app --host 0.0.0.0 --port %PORT%"
  exit /b 0
