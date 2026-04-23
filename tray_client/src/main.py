"""IRMS Notice tray application entrypoint.

Stays resident in the Windows notification area, polls the IRMS server for
new notice-room messages, and plays each new message aloud via TTS.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path

import pystray
from PIL import Image
from pystray import Menu, MenuItem

from .attendance_alerts import AttendanceAlertPoller, today_iso
from .config import Config, logs_dir
from .logger import setup_logger
from .poller import Poller
from .tts import TTSQueue

APP_TITLE = "IRMS 공지 수신기"


def asset_path(name: str) -> Path:
    """Locate a bundled asset for both dev and PyInstaller one-folder modes."""
    if getattr(sys, "frozen", False):
        base = Path(sys._MEIPASS)  # type: ignore[attr-defined]
        candidate = base / "assets" / name
        if candidate.exists():
            return candidate
    dev_base = Path(__file__).resolve().parent.parent / "assets"
    return dev_base / name


def load_icon_image() -> Image.Image:
    ico = asset_path("icon.ico")
    if ico.exists():
        return Image.open(ico)
    return Image.new("RGB", (64, 64), color=(30, 64, 175))


class TrayApp:
    def __init__(self) -> None:
        self.logger = setup_logger()
        self.config = Config.load()
        self.tts = TTSQueue(
            chime_path=asset_path("ding.wav"),
            rate=self.config.tts_rate,
            muted=self.config.muted,
        )
        self.poller = Poller(
            config=self.config,
            on_message=self._on_message,
            on_status=self._on_status,
        )
        self._status = "대기 중"
        self._icon: pystray.Icon | None = None
        self._alert_mute_date: str | None = None
        self.alert_poller = AttendanceAlertPoller(
            config=self.config,
            icon_getter=lambda: self._icon,
            is_enabled_getter=self._alerts_enabled_today,
        )

    def run(self) -> None:
        self.logger.info(
            "starting IRMS Notice tray (server=%s, poll=%ds)",
            self.config.server_url,
            self.config.poll_interval_seconds,
        )
        self.tts.start()
        self.poller.start()
        self.alert_poller.start()

        self._icon = pystray.Icon(
            "irms_notice",
            icon=load_icon_image(),
            title=APP_TITLE,
            menu=self._build_menu(),
        )
        try:
            self._icon.run()
        finally:
            self.logger.info("shutting down")
            self.alert_poller.stop()
            self.poller.stop()
            self.tts.stop()

    def _alerts_enabled_today(self) -> bool:
        if self._alert_mute_date is None:
            return True
        return self._alert_mute_date != today_iso()

    def _build_menu(self) -> Menu:
        return Menu(
            MenuItem(lambda _item: f"상태: {self._status}", None, enabled=False),
            MenuItem(
                lambda _item: "음소거 해제" if self.config.muted else "음소거",
                self._toggle_mute,
            ),
            MenuItem(
                lambda _item: (
                    "오늘 근태 알림 끄기"
                    if self._alerts_enabled_today()
                    else "오늘 근태 알림 켜기 (자정 자동 복귀)"
                ),
                self._toggle_alert_mute_today,
            ),
            MenuItem("테스트 재생", self._test_play),
            MenuItem("근태 알림 테스트", self._test_alert),
            MenuItem("로그 폴더 열기", self._open_logs),
            Menu.SEPARATOR,
            MenuItem("종료", self._quit),
        )

    def _on_message(self, msg: dict) -> None:
        preview = (msg.get("message_text") or "").replace("\n", " ")[:60]
        self.logger.info("notice received id=%s %s", msg.get("id"), preview)
        self.tts.enqueue_message(msg)

    def _on_status(self, status: str) -> None:
        if status != self._status:
            self.logger.info("status: %s", status)
        self._status = status
        if self._icon is not None:
            self._icon.update_menu()

    def _toggle_mute(self, _icon, _item) -> None:
        self.config.muted = not self.config.muted
        self.config.save()
        self.tts.set_muted(self.config.muted)
        self.logger.info("muted=%s", self.config.muted)
        if self._icon is not None:
            self._icon.update_menu()

    def _test_play(self, _icon, _item) -> None:
        self.tts.enqueue_raw("테스트 공지입니다. 정상적으로 들리면 설치가 완료된 것입니다.")

    def _toggle_alert_mute_today(self, _icon, _item) -> None:
        if self._alerts_enabled_today():
            self._alert_mute_date = today_iso()
            self.logger.info("attendance alerts muted for %s", self._alert_mute_date)
        else:
            self._alert_mute_date = None
            self.logger.info("attendance alerts re-enabled")
        if self._icon is not None:
            self._icon.update_menu()

    def _test_alert(self, _icon, _item) -> None:
        self.alert_poller.trigger_once()

    def _open_logs(self, _icon, _item) -> None:
        path = logs_dir()
        try:
            if sys.platform == "win32":
                os.startfile(str(path))  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("open logs failed: %s", exc)

    def _quit(self, icon, _item) -> None:
        def _stop() -> None:
            icon.stop()
        threading.Thread(target=_stop, daemon=True).start()


def main() -> None:
    TrayApp().run()


if __name__ == "__main__":
    main()
