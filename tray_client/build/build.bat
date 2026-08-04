@echo off
REM Build script for IRMS Notice tray client.
REM Run from the tray_client directory.

setlocal
cd /d "%~dp0\.."

REM py -3.13 우선 — PATH 의 python 은 hermes venv(3.11, brotlicffi 보유)일 수 있어
REM 선택적 의존성이 namespace 로 번들되는 함정이 있다 (rthook 주석 참조).
set "PYTHON_CMD=py -3.13"
REM NOTE: ">nul" (Windows), not ">/dev/null" - the Unix form always fails here,
REM which silently fell back to PATH python (hermes venv, no PyInstaller).
%PYTHON_CMD% -c "" >nul 2>&1
if errorlevel 1 set "PYTHON_CMD=python"
set "ISCC_CMD="

echo [1/3] generating assets (icon.ico, ding.wav)
%PYTHON_CMD% src\assets_gen.py
if errorlevel 1 goto :error

echo [2/3] building executable with PyInstaller
%PYTHON_CMD% -m PyInstaller build\irms_notice.spec --clean --noconfirm
if errorlevel 1 goto :error

echo [3/3] packaging installer with Inno Setup
where iscc >nul 2>&1
if not errorlevel 1 (
    set "ISCC_CMD=iscc"
)
if not defined ISCC_CMD if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" (
    set "ISCC_CMD=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
)
if not defined ISCC_CMD if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" (
    set "ISCC_CMD=%ProgramFiles%\Inno Setup 6\ISCC.exe"
)
if not defined ISCC_CMD (
    echo [WARN] ISCC.exe not found in PATH or default Inno Setup folders. Skipping installer build.
    echo        Install Inno Setup 6 or add ISCC.exe to PATH, then rerun this script.
    goto :done
)
"%ISCC_CMD%" build\installer.iss
if errorlevel 1 goto :error

:done
echo.
echo Build complete.
echo   - EXE folder : dist\IRMS-Notice\
echo   - Installer  : Output\IRMS-Notice-Setup-<ver>.exe
exit /b 0

:error
echo.
echo Build failed.
exit /b 1
