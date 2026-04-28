"""IRMS Notice tray application entrypoint."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

import pystray
from PIL import Image
from pystray import Menu, MenuItem

from .attendance_alerts import AttendanceAlertPoller, today_iso
from .attendance_popup import AttendanceAlertPopupManager
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


def attendance_page_url(server_url: str) -> str:
    """Return the attendance page URL rooted at the configured server."""
    return f"{server_url.rstrip('/')}/attendance"


def open_in_browser(url: str) -> None:
    """Open a URL in the default browser."""
    webbrowser.open_new_tab(url)


class TrayApp:
    def __init__(self) -> None:
        self.logger = setup_logger()
        self.config = Config.load()
        self.tts = TTSQueue(
            chime_path=asset_path("ding.wav"),
            rate=self.config.tts_rate,
            muted=self.config.muted,
            volume=self.config.volume,
        )
        self.poller = Poller(
            config=self.config,
            on_message=self._on_message,
            on_status=self._on_status,
        )
        self._status = "대기 중"
        self._icon: pystray.Icon | None = None
        self._alert_mute_date: str | None = None
        self.alert_popup = AttendanceAlertPopupManager(
            on_confirm=self._open_attendance,
            on_dismiss_today=self._mute_alerts_for_today,
        )
        self.alert_poller = AttendanceAlertPoller(
            config=self.config,
            present_alert=self.alert_popup.show,
            is_enabled_getter=self._alerts_enabled_today,
        )

    def run(self) -> None:
        self.logger.info(
            "starting IRMS Notice tray (server=%s, poll=%ds)",
            self.config.server_url,
            self.config.poll_interval_seconds,
        )
        self.alert_popup.start()
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
            self.alert_popup.stop()
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
                    "근태 알림 오늘만 끄기"
                    if self._alerts_enabled_today()
                    else "근태 알림 오늘만 켜기 (자정 자동 복귀)"
                ),
                self._toggle_alert_mute_today,
            ),
            MenuItem("테스트 재생", self._test_play),
            MenuItem("근태 알림 테스트", self._test_alert),
            MenuItem("근태 확인 열기", self._open_attendance_menu),
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
        self.tts.enqueue_raw(
            "테스트 공지입니다. 정상적으로 소리와 음성 안내가 나오면 설정이 완료된 것입니다."
        )

    def _mute_alerts_for_today(self) -> None:
        self._alert_mute_date = today_iso()
        self.logger.info("attendance alerts muted for %s", self._alert_mute_date)
        if self._icon is not None:
            self._icon.update_menu()

    def _enable_alerts(self) -> None:
        self._alert_mute_date = None
        self.logger.info("attendance alerts re-enabled")
        if self._icon is not None:
            self._icon.update_menu()

    def _toggle_alert_mute_today(self, _icon, _item) -> None:
        if self._alerts_enabled_today():
            self._mute_alerts_for_today()
        else:
            self._enable_alerts()

    def _test_alert(self, _icon, _item) -> None:
        self.alert_poller.show_test_notification()

    def _open_attendance(self) -> None:
        url = attendance_page_url(self.config.server_url)
        try:
            open_in_browser(url)
            self.logger.info("attendance page opened: %s", url)
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("open attendance failed: %s", exc)

    def _open_attendance_menu(self, _icon, _item) -> None:
        self._open_attendance()

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
