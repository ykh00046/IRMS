# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the IRMS scale agent (A&D GX-10202M).

Build from the ``scale_agent`` directory:

    pyinstaller irms_scale.spec --clean --noconfirm

Output: dist/IRMS-Scale/IRMS-Scale.exe (one-folder, 창 없는 트레이 앱 —
상태는 작업표시줄 아이콘 + %APPDATA%\\IRMS-Scale\\agent.log 로 확인).
"""

from pathlib import Path

ROOT = Path(SPECPATH).resolve()

a = Analysis(
    [str(ROOT / "agent.py")],
    pathex=[str(ROOT)],
    datas=[],
    hiddenimports=["serial", "serial.tools.list_ports", "pystray", "PIL", "winreg"],
    hookspath=[],
    runtime_hooks=[],
    # simplejson: requests.compat 함정과 동일 예방 차원(여긴 requests 없지만 무해)
    excludes=["unittest", "pydoc_data", "simplejson", "tkinter", "numpy"],
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=None)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="IRMS-Scale",
    debug=False,
    strip=False,
    upx=False,
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="IRMS-Scale",
)
