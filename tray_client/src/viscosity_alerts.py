from __future__ import annotations

import datetime as dt
import logging
import threading
from typing import Any, Callable

import requests

from .attendance_popup import PopupPayload, build_viscosity_popup_payload

logger = logging.getLogger("irms_notice")

DEFAULT_INTERVAL_SECONDS = 60 * 60


def reminder_signature(items: list[dict[str, Any]]) -> str:
    codes = [str(item.get("code") or "").strip().upper() for item in items]
    return "|".join(sorted(code for code in codes if code))


class ViscosityAlertPoller:
    def __init__(
        self,
        config,
        present_alert: Callable[[PopupPayload], None],
        is_enabled_getter: Callable[[], bool],
        interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
        today_provider: Callable[[], str] | None = None,
    ) -> None:
        self._config = config
        self._present_alert = present_alert
        self._is_enabled_getter = is_enabled_getter
        self._interval = max(60, int(interval_seconds))
        self._today_provider = today_provider or (lambda: dt.date.today().isoformat())
        self._stop_event = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name="viscosity-alert",
            daemon=True,
        )
        self._session = requests.Session()
        self._last_signature: str | None = None
        self._last_signature_date: str | None = None

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread.is_alive():
            self._thread.join(timeout=5)

    def trigger_once(self) -> None:
        threading.Thread(
            target=self._poll_and_notify,
            kwargs={"force": True},
            daemon=True,
        ).start()

    def _run(self) -> None:
        while not self._stop_event.is_set():
            if self._is_enabled_getter():
                self._poll_and_notify()
            self._stop_event.wait(self._interval)

    def _poll_and_notify(self, force: bool = False) -> bool:
        today = self._today_provider()
        try:
            payload = self._poll_once(today)
        except requests.RequestException as exc:
            logger.warning("viscosity reminder poll failed: %s", exc)
            return False
        if not payload:
            return True

        items = list(payload.get("items") or [])
        if not items:
            self._last_signature = None
            self._last_signature_date = today
            return True

        signature = reminder_signature(items)
        if (
            not force
            and signature
            and signature == self._last_signature
            and today == self._last_signature_date
        ):
            logger.debug("viscosity reminder unchanged; duplicate popup suppressed")
            return True

        popup_payload = build_viscosity_popup_payload(payload)
        self._present_alert(popup_payload)
        self._last_signature = signature
        self._last_signature_date = today
        logger.info("viscosity popup raised: %s / %s", popup_payload.title, popup_payload.summary)
        return True

    def _poll_once(self, target_date: str) -> dict[str, Any] | None:
        # 알림 대상 반제품은 웹 점도 설정(remind_daily)이 소유한다. 트레이는 오늘
        # 밀린 대상만 서버에 물어보므로 로컬 품목 목록을 두지 않는다.
        url = f"{self._config.server_url.rstrip('/')}/api/public/viscosity-reminders/due"
        headers = {}
        token = getattr(self._config, "tray_api_token", "")
        if token:
            headers["X-IRMS-Tray-Token"] = token
        resp = self._session.get(
            url,
            params={"target_date": target_date},
            headers=headers or None,
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()
