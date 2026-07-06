"""Windows 부팅 시 자동 실행(HKCU Run) — 통합 현장 도우미용.

통합 앱은 부팅 후 저장된 토글(알림/저울)을 그대로 적용해야 하므로, 부팅 시 스스로
뜨도록 자동 실행을 등록한다. 최초 실행 때 켜지고, 트레이 메뉴에서 끌 수 있다.

구 개별 앱(IRMS-Scale 저울 에이전트)의 자동 실행 항목이 남아 있으면 이중 실행이
되므로 함께 제거한다.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

logger = logging.getLogger("irms_notice")

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
# 설치 프로그램(installer.iss)이 등록하는 자동 실행 이름과 반드시 일치해야 한다.
# (다르면 같은 exe 를 가리키는 항목이 둘 생겨 부팅 시 이중 실행된다.)
RUN_NAME = "IRMS-Notice"
# 통합되며 폐기된 구 개별 앱(저울 에이전트)의 자동 실행 항목(있으면 정리).
LEGACY_RUN_NAMES = ("IRMS-Scale",)


def _autostart_command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    # 개발 실행: run.py 를 파이썬으로 띄운다.
    entry = Path(__file__).resolve().parents[1] / "run.py"
    return f'"{sys.executable}" "{entry}"'


def is_enabled() -> bool:
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, RUN_NAME)
        return True
    except (OSError, ModuleNotFoundError):
        return False


def set_enabled(enabled: bool) -> None:
    try:
        import winreg
    except ModuleNotFoundError:  # 비-윈도우/테스트 환경
        return
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, RUN_NAME, 0, winreg.REG_SZ, _autostart_command())
        else:
            try:
                winreg.DeleteValue(key, RUN_NAME)
            except OSError:
                pass
    logger.info("autostart %s", "enabled" if enabled else "disabled")


def cleanup_legacy() -> None:
    """구 개별 앱들의 자동 실행 항목 제거(이중 부팅 방지)."""
    try:
        import winreg
    except ModuleNotFoundError:
        return
    for name in LEGACY_RUN_NAMES:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, name)
            logger.info("removed legacy autostart: %s", name)
        except (OSError, ModuleNotFoundError):
            pass


def ensure_default_on_first_run() -> None:
    """최초 실행 시 자동 실행을 기본으로 켠다(레지스트리 접근 불가 환경은 무시)."""
    try:
        cleanup_legacy()
        if not is_enabled():
            set_enabled(True)
    except Exception as exc:  # noqa: BLE001
        logger.warning("autostart setup skipped: %s", exc)
