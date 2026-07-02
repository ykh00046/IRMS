# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the IRMS scale agent (A&D GX-10202M).

Build from the ``scale_agent`` directory:

    pyinstaller irms_scale.spec --clean --noconfirm

Output: dist/IRMS-Scale/IRMS-Scale.exe (one-folder, console app —
콘솔 창이 곧 상태 표시(연결 포트/오류)이므로 console=True 유지).
"""

from pathlib import Path

ROOT = Path(SPECPATH).resolve()

a = Analysis(
    [str(ROOT / "agent.py")],
    pathex=[str(ROOT)],
    datas=[],
    hiddenimports=["serial", "serial.tools.list_ports"],
    hookspath=[],
    runtime_hooks=[],
    # simplejson: requests.compat 함정과 동일 예방 차원(여긴 requests 없지만 무해)
    excludes=["unittest", "pydoc_data", "simplejson", "tkinter", "numpy", "PIL"],
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
    console=True,
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
