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
    # requests/urllib3 의 선택적 의존성 함정 차단(훅 파일 주석 참조).
    runtime_hooks=[str(ROOT / "build" / "rthook_block_optional_imports.py")],
    # simplejson/brotlicffi/brotli/zstandard: requests·urllib3 가 optional import 하는
    # 패키지들. 빌드 환경에 (부분) 설치돼 있으면 PyInstaller 가 namespace 패키지로
    # 번들해 프리즈에서 `import X` 성공 후 속성 접근 크래시가 난다.
    # 실사고: simplejson(JSONDecodeError unknown location), brotlicffi(2026-08-03,
    # hermes venv py3.11 빌드 exe 가 urllib3 `brotli.error` AttributeError 로 기동 즉사).
    # excludes(수집 차단) + runtime hook(sys.modules[X]=None, import 차단) 이중 방어 —
    # 어느 python 으로 빌드해도 동일하게 동작한다.
    excludes=["unittest", "pydoc_data", "simplejson", "brotlicffi", "brotli", "zstandard", "backports.zstd", "compression.zstd"],
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
