# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the IRMS Notice tray client.

Build from the ``tray_client`` directory:

    pyinstaller build/irms_notice.spec --clean --noconfirm

Outputs ``dist/IRMS-Notice/IRMS-Notice.exe`` + supporting DLLs (one-folder
mode, chosen for fast start-up and easier SmartScreen auditing).
"""

from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent
REPO_ROOT = ROOT.parent   # scale_agent 패키지를 임포트하기 위한 저장소 루트
ASSETS = ROOT / "assets"
SRC = ROOT / "src"

a = Analysis(
    [str(ROOT / "run.py")],
    # REPO_ROOT: 통합된 저울 로직(scale_agent 패키지)을 프리즈에 포함하기 위해 경로에 추가.
    pathex=[str(ROOT), str(REPO_ROOT)],
    datas=[
        (str(ASSETS / "icon.ico"), "assets"),
        (str(ASSETS / "ding.wav"), "assets"),
    ],
    hiddenimports=[
        "pywintypes",
        "pythoncom",
        "win32com",
        "win32com.client",
        # 저울 연동(통합): pyserial + 재사용하는 scale_agent 패키지 + 자동실행용 winreg
        "serial",
        "serial.tools.list_ports",
        "scale_agent",
        "scale_agent.agent",
        "winreg",
    ],
    hookspath=[],
    # requests + PyInstaller + simplejson 함정 차단(아래 excludes 주석 참조).
    runtime_hooks=[str(ROOT / "build" / "rthook_no_simplejson.py")],
    # simplejson: requests.compat imports it optionally. If the build venv has a
    # (partial) simplejson, PyInstaller bundles it as a namespace package and
    # `from simplejson import JSONDecodeError` fails at runtime with
    # "cannot import name 'JSONDecodeError' from 'simplejson' (unknown location)".
    # Excluding it forces requests to fall back to the stdlib json (fully supported).
    excludes=["unittest", "pydoc_data", "simplejson"],
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
