# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the IRMS Notice tray client.

Build from the ``tray_client`` directory:

    pyinstaller build/irms_notice.spec --clean --noconfirm

Outputs ``dist/IRMS-Notice/IRMS-Notice.exe`` + supporting DLLs (one-folder
mode, chosen for fast start-up and easier SmartScreen auditing).
"""

from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent
ASSETS = ROOT / "assets"
SRC = ROOT / "src"

a = Analysis(
    [str(ROOT / "run.py")],
    pathex=[str(ROOT)],
    datas=[
        (str(ASSETS / "icon.ico"), "assets"),
        (str(ASSETS / "ding.wav"), "assets"),
    ],
    hiddenimports=[
        "pyttsx3.drivers.sapi5",
        "pywintypes",
        "pythoncom",
        "win32com",
        "win32com.client",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=["unittest", "pydoc_data"],
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=None)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="IRMS-Notice",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=str(ASSETS / "icon.ico"),
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="IRMS-Notice",
)
