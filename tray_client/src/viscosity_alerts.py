from __future__ import annotations

import datetime as _dt
import logging
import threading
from typing import Any, Callable

import requests

from .attendance_popup import PopupPayload, build_viscosity_popup_payload
from .schedule import current_slot_key, seconds_until_next_slot, stale_slot_key_on_startup

logger = logging.getLogger("irms_notice")

DEFAULT_INTERVAL_SECONDS = 60 * 60
SLOT_RETRY_SECONDS = 60


def reminder_signature(items: list[dict[str, Any]]) -> str:
    codes = [str(item.get("code") or "").strip().upper() for item in items]
    return "|".join(sorted(code for code in codes if code))


class ViscosityAlertPoller:
    """점도 리마인더 — 근태와 동일하게 정해진 시각(09/13/16시) 슬롯당 1번만 알린다.

    앱을 껐다 켜도 이미 지난 슬롯(30분 초과)은 다시 띄우지 않는다(schedule 공용 로직).
    """

    def __init__(
        self,
        config,
        present_alert: Callable[[PopupPayload], None],
        is_enabled_getter: Callable[[], bool],
        interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
        now_provider: Callable[[], _dt.datetime] | None = None,
    ) -> None:
        self._config = config
        self._present_alert = present_alert
        self._is_enabled_getter = is_enabled_getter
        self._interval = max(60, int(interval_seconds))
        self._now = now_provider or _dt.datetime.now
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._run, name="viscosity-alert", daemon=True)
        self._session = requests.Session()
        self._last_signature: str | None = None
        self._last_signature_slot: str | None = None
        self._last_processed_slot: str | None = None

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
        # 재시작 시 이미 지난 슬롯은 처리된 것으로 표시 → 켤 때마다 도로 뜨지 않음.
        self._last_processed_slot = stale_slot_key_on_startup(self._now())
        while not self._stop_event.is_set():
            now = self._now()
            slot_key = current_slot_key(now)
            if slot_key and slot_key != self._last_processed_slot:
                if self._is_enabled_getter():
                    if self._poll_and_notify(slot_key=slot_key):
                        self._last_processed_slot = slot_key
                        wait_seconds = min(seconds_until_next_slot(self._now()), self._interval)
                    else:
                        wait_seconds = min(SLOT_RETRY_SECONDS, self._interval)
                else:
                    # 꺼져 있으면 슬롯을 소비하지 않는다(다시 켜면 그 슬롯에 뜰 수 있게).
                    wait_seconds = min(SLOT_RETRY_SECONDS, self._interval)
                self._stop_event.wait(wait_seconds)
                continue
            wait_seconds = min(seconds_until_next_slot(now), self._interval)
            self._stop_event.wait(wait_seconds)

    def _poll_and_notify(self, force: bool = False, slot_key: str | None = None) -> bool:
        today = self._now().date().isoformat()
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
            self._last_signature_slot = slot_key
            return True

        signature = reminder_signature(items)
        if (
            not force
            and signature
            and signature == self._last_signature
            and slot_key == self._last_signature_slot
        ):
            logger.debug("viscosity reminder unchanged; duplicate popup suppressed")
            return True

        popup_payload = build_viscosity_popup_payload(payload)
        self._present_alert(popup_payload)
        self._last_signature = signature
        self._last_signature_slot = slot_key
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
