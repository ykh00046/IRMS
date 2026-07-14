@echo off
REM =====================================================================
REM  IRMS server + auto-update, in a single window.
REM
REM  ASCII ONLY -- do not put Korean text in this file.
REM  cmd reads .bat bytes with the OEM codepage (cp949), so UTF-8 Korean
REM  in echo/title comes out mangled. Fixing it with `chcp 65001` is worse:
REM  cmd re-reads the batch by byte offset and starts parsing mid-line
REM  (verified: it tried to run a comment fragment as a command).
REM  All Korean output belongs in serve.py -- Python writes Unicode to the
REM  console via WriteConsoleW/SetConsoleTitleW, which ignores the codepage.
REM  The window title and the shutdown notice are printed by serve.py.
REM
REM  Behaviour: double-click and the server runs here with its log.
REM  Every IRMS_AUTO_INTERVAL seconds it checks origin/main; on a new commit
REM  it backs up the DB, pulls, installs deps and restarts -- same window.
REM  Autostart on boot: put a shortcut to this file in shell:startup.
REM =====================================================================
setlocal
cd /d "%~dp0"

REM -- settings (numbers only) --
set "IRMS_PORT=9000"
set "IRMS_AUTO_INTERVAL=600"
REM   IRMS_AUTO_INTERVAL = update check interval in seconds. 600 = 10 min.
REM   To turn auto-update off, uncomment the next line:
REM set "IRMS_AUTO_UPDATE=0"

if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" serve.py
) else (
  python serve.py
)

echo.
pause
