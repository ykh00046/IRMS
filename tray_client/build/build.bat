@echo off
REM Build script for IRMS Notice tray client.
REM Run from the tray_client directory.

setlocal
cd /d "%~dp0\.."

echo [1/3] generating assets (icon.ico, ding.wav)
python src\assets_gen.py
if errorlevel 1 goto :error

echo [2/3] building executable with PyInstaller
pyinstaller build\irms_notice.spec --clean --noconfirm
if errorlevel 1 goto :error

echo [3/3] packaging installer with Inno Setup
where iscc >nul 2>&1
if errorlevel 1 (
    echo [WARN] iscc not found in PATH. Skipping installer build.
    echo        Install Inno Setup 6 from https://jrsoftware.org/isinfo.php
    echo        or add its install dir to PATH, then run: iscc build\installer.iss
    goto :done
)
iscc build\installer.iss
if errorlevel 1 goto :error

:done
echo.
echo Build complete.
echo   - EXE folder : dist\IRMS-Notice\
echo   - Installer  : Output\IRMS-Notice-Setup-1.0.0.exe
exit /b 0

:error
echo.
echo Build failed.
exit /b 1
