from __future__ import annotations

import os
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

try:
    from PIL import Image
except ModuleNotFoundError as exc:
    Image = None  # type: ignore[assignment]
    _PIL_IMPORT_ERROR = exc
else:
    _PIL_IMPORT_ERROR = None

try:
    import pystray
    from pystray import Menu, MenuItem
except ModuleNotFoundError as exc:
    pystray = None  # type: ignore[assignment]
    _PYSTRAY_IMPORT_ERROR = exc

    class Menu:  # type: ignore[no-redef]
        SEPARATOR = object()

        def __init__(self, *items):
            self.items = items

    class MenuItem:  # type: ignore[no-redef]
        def __init__(self, text, action, **kwargs):
            self.text = text
            self.action = action

else:
    _PYSTRAY_IMPORT_ERROR = None

from . import autostart
from .attendance_alerts import AttendanceAlertPoller, today_iso
from .attendance_popup import AttendanceAlertPopupManager, PopupPayload
from .config import Config, logs_dir
from .logger import setup_logger
from .scale_service import ScaleService
from .viscosity_alerts import ViscosityAlertPoller

# 통합 앱: 근태·점도 알림 + 저울 연동을 한 트레이에서. 각 기능은 메뉴에서 켜고 끌 수
# 있고 설정은 config.json 에 저장돼 재부팅해도 유지된다(기본: 알림만 켜짐).
APP_TITLE = "IRMS 현장 도우미"


def asset_path(name: str) -> Path:
    if getattr(sys, "frozen", False):
        base = Path(sys._MEIPASS)  # type: ignore[attr-defined]
        candidate = base / "assets" / name
        if candidate.exists():
            return candidate
    dev_base = Path(__file__).resolve().parent.parent / "assets"
    return dev_base / name


def load_icon_image() -> Image.Image:
    if Image is None:
        raise RuntimeError(
            "pillow is required to load the tray icon. "
            "Install test dependencies with `pip install -r requirements-dev.txt`."
        ) from _PIL_IMPORT_ERROR
    ico = asset_path("icon.ico")
    if ico.exists():
        return Image.open(ico)
    return Image.new("RGB", (64, 64), color=(30, 64, 175))


def attendance_page_url(server_url: str) -> str:
    return f"{server_url.rstrip('/')}/attendance"


def blend_page_url(server_url: str) -> str:
    return f"{server_url.rstrip('/')}/blend"


def viscosity_page_url(server_url: str) -> str:
    return f"{server_url.rstrip('/')}/viscosity"


def open_in_browser(url: str) -> None:
    webbrowser.open_new_tab(url)


class TrayApp:
    def __init__(self) -> None:
        self.logger = setup_logger()
        self.config = Config.load()
        self._icon: pystray.Icon | None = None
        self._alert_mute_date: str | None = None
        self.alert_popup = AttendanceAlertPopupManager(
            on_confirm=self._open_popup_target,
            on_dismiss_today=self._mute_alerts_for_today,
        )
        # 알림 폴러는 항상 떠 있고, 아래 _alerts_active() 게이트로 on/off 된다.
        # (config.alerts_enabled 토글 + '오늘만 끄기' 뮤트 두 조건을 모두 만족해야 발송)
        self.alert_poller = AttendanceAlertPoller(
            config=self.config,
            present_alert=self.alert_popup.show,
            is_enabled_getter=self._alerts_active,
        )
        self.viscosity_poller = ViscosityAlertPoller(
            config=self.config,
            present_alert=self.alert_popup.show,
            is_enabled_getter=self._alerts_active,
        )
        # 저울은 포트·HTTP 서버를 잡으므로 토글 시 실제 start/stop 한다.
        self.scale = ScaleService(self.logger)

    def run(self) -> None:
        if pystray is None:
            raise RuntimeError(
                "pystray is required to run the tray client. "
                "Install test dependencies with `pip install -r requirements-dev.txt`."
            ) from _PYSTRAY_IMPORT_ERROR
        self.logger.info(
            "starting %s (server=%s, alerts=%s, scale=%s)",
            APP_TITLE, self.config.server_url, self.config.alerts_enabled, self.config.scale_enabled,
        )
        autostart.ensure_default_on_first_run()

        self.alert_popup.start()
        self.alert_poller.start()
        self.viscosity_poller.start()
        if self.config.scale_enabled:
            self.scale.start()

        self._icon = pystray.Icon(
            "irms_field",
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
            self.viscosity_poller.stop()
            self.scale.stop()

    # ── 알림 on/off 상태 ─────────────────────────────────────────
    def _alerts_enabled_today(self) -> bool:
        """'오늘만 끄기' 뮤트 기준(자정 지나면 자동 재활성)."""
        if self._alert_mute_date is None:
            return True
        return self._alert_mute_date != today_iso()

    def _alerts_active(self) -> bool:
        """실제 발송 여부 = 영구 토글(config) AND 오늘 뮤트 안 됨."""
        return self.config.alerts_enabled and self._alerts_enabled_today()

    def _save_config(self) -> None:
        try:
            self.config.save()
        except OSError as exc:
            self.logger.warning("config save failed: %s", exc)

    def _build_menu(self) -> Menu:
        return Menu(
            MenuItem(
                "알림 사용",
                self._toggle_alerts,
                checked=lambda _item: self.config.alerts_enabled,
            ),
            MenuItem(
                "저울 연동 사용",
                self._toggle_scale,
                checked=lambda _item: self.config.scale_enabled,
            ),
            MenuItem(lambda _item: self.scale.status_line(), None, enabled=False),
            Menu.SEPARATOR,
            MenuItem(
                lambda _item: (
                    "현장 알림 오늘만 끄기" if self._alerts_enabled_today() else "현장 알림 다시 켜기"
                ),
                self._toggle_alert_mute_today,
                enabled=lambda _item: self.config.alerts_enabled,
            ),
            MenuItem("근태 알림 바로 확인", self._show_attendance_anomalies),
            MenuItem("점도 알림 바로 확인", self._show_viscosity_reminders),
            MenuItem("저울 다시 연결", self._reconnect_scale, enabled=lambda _item: self.config.scale_enabled),
            Menu.SEPARATOR,
            MenuItem("근태 확인", self._open_attendance_menu),
            MenuItem("반제품 제조 관리", self._open_blend_menu),
            MenuItem("점도 등록", self._open_viscosity_menu),
            Menu.SEPARATOR,
            MenuItem(
                "부팅 시 자동 실행",
                self._toggle_autostart,
                checked=lambda _item: autostart.is_enabled(),
            ),
            MenuItem("로그 폴더 열기", self._open_logs),
            Menu.SEPARATOR,
            MenuItem("종료", self._quit),
        )

    # ── 기능 토글 ────────────────────────────────────────────────
    def _toggle_alerts(self, _icon, _item) -> None:
        self.config.alerts_enabled = not self.config.alerts_enabled
        self._save_config()
        self.logger.info("alerts %s", "enabled" if self.config.alerts_enabled else "disabled")
        if self._icon is not None:
            self._icon.update_menu()

    def _toggle_scale(self, _icon, _item) -> None:
        self.config.scale_enabled = not self.config.scale_enabled
        self._save_config()
        if self.config.scale_enabled:
            started = self.scale.start()
            self.logger.info("scale %s", "started" if started else "enabled (start failed — will retry on next launch)")
        else:
            self.scale.stop()
            self.logger.info("scale stopped")
        if self._icon is not None:
            self._icon.update_menu()

    def _reconnect_scale(self, _icon, _item) -> None:
        if self.config.scale_enabled:
            self.scale.reconnect()

    def _mute_alerts_for_today(self) -> None:
        self._alert_mute_date = today_iso()
        self.logger.info("field alerts muted for %s", self._alert_mute_date)
        if self._icon is not None:
            self._icon.update_menu()

    def _enable_alerts_today(self) -> None:
        self._alert_mute_date = None
        self.logger.info("field alerts re-enabled")
        if self._icon is not None:
            self._icon.update_menu()

    def _toggle_alert_mute_today(self, _icon, _item) -> None:
        if self._alerts_enabled_today():
            self._mute_alerts_for_today()
        else:
            self._enable_alerts_today()

    def _toggle_autostart(self, icon, _item) -> None:
        autostart.set_enabled(not autostart.is_enabled())
        icon.update_menu()

    # ── 액션 ─────────────────────────────────────────────────────
    def _show_attendance_anomalies(self, _icon, _item) -> None:
        self.logger.info("manual attendance anomaly check requested")
        self.alert_poller.trigger_once()

    def _show_viscosity_reminders(self, _icon, _item) -> None:
        self.logger.info("manual viscosity reminder check requested")
        self.viscosity_poller.trigger_once()

    def _open_popup_target(self, payload: PopupPayload) -> None:
        if payload.action_key == "viscosity":
            self._open_viscosity()
            return
        self._open_attendance()

    def _open_attendance(self) -> None:
        url = attendance_page_url(self.config.server_url)
        try:
            open_in_browser(url)
            self.logger.info("attendance page opened: %s", url)
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("open attendance failed: %s", exc)

    def _open_blend(self) -> None:
        url = blend_page_url(self.config.server_url)
        try:
            open_in_browser(url)
            self.logger.info("blend page opened: %s", url)
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("open blend failed: %s", exc)

    def _open_viscosity(self) -> None:
        url = viscosity_page_url(self.config.server_url)
        try:
            open_in_browser(url)
            self.logger.info("viscosity page opened: %s", url)
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("open viscosity failed: %s", exc)

    def _open_attendance_menu(self, _icon, _item) -> None:
        self._open_attendance()

    def _open_blend_menu(self, _icon, _item) -> None:
        self._open_blend()

    def _open_viscosity_menu(self, _icon, _item) -> None:
        self._open_viscosity()

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
