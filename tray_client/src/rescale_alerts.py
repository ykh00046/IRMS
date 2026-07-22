"""증량 사후 확인 알림 폴러 — 책임자 미확인 증량(rescale_unacked) 나그(nag).

근태·점도 알림이 정해진 시각(슬롯)당 1번만 뜨는 것과 달리, 증량 미확인은 책임자가
'배합 기록'에서 확인 처리할 때까지 남아 있는 상태이므로 완만한 주기(기본 10분)로
폴링해 count>0 인 동안 매 주기 반복 알림을 띄운다(사후 확인 독려). 서버 응답:

    GET /api/public/rescale-alerts  →  {count, items: [{id, product_name,
                                        product_lot, work_date, worker}]}
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable

import requests

from .attendance_popup import PopupPayload, build_rescale_popup_payload

logger = logging.getLogger("irms_notice")

# 완만한 주기 — 근태/점도(슬롯 1회)와 달리 미확인이 남는 동안 반복 알림.
DEFAULT_INTERVAL_SECONDS = 10 * 60


class RescaleAlertPoller:
    """미확인 증량이 남아 있는 동안 매 폴링 주기마다 반복 알림하는 폴러."""

    def __init__(
        self,
        config,
        present_alert: Callable[[PopupPayload], None],
        is_enabled_getter: Callable[[], bool],
        interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
    ) -> None:
        self._config = config
        self._present_alert = present_alert
        self._is_enabled_getter = is_enabled_getter
        self._interval = max(60, int(interval_seconds))
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._run, name="rescale-alert", daemon=True)
        self._session = requests.Session()

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread.is_alive():
            self._thread.join(timeout=5)

    def trigger_once(self) -> None:
        """수동 '바로 확인' — 한 번 폴링해 미확인이 있으면 알림(꺼져 있어도 강제)."""
        threading.Thread(
            target=self._poll_and_notify,
            kwargs={"force": True},
            daemon=True,
        ).start()

    def _run(self) -> None:
        # 첫 확인은 시작 직후가 아니라 한 주기 뒤에(부팅 직후 서버 미기동 회피).
        while not self._stop_event.wait(self._interval):
            if self._is_enabled_getter():
                self._poll_and_notify()

    def _poll_and_notify(self, force: bool = False) -> bool:
        if not force and not self._is_enabled_getter():
            return False
        try:
            payload = self._poll_once()
        except requests.RequestException as exc:
            logger.warning("rescale alert poll failed: %s", exc)
            return False
        except Exception as exc:  # noqa: BLE001
            logger.exception("rescale alert unexpected error: %s", exc)
            return False
        if not payload:
            return True

        count = int(payload.get("count") or len(payload.get("items") or []))
        if count <= 0:
            return True

        popup_payload = build_rescale_popup_payload(payload)
        self._present_alert(popup_payload)
        logger.info("rescale popup raised: %s (%d건)", popup_payload.title, count)
        return True

    def _poll_once(self) -> dict[str, Any] | None:
        url = f"{self._config.server_url.rstrip('/')}/api/public/rescale-alerts"
        headers = {}
        token = getattr(self._config, "tray_api_token", "")
        if token:
            headers["X-IRMS-Tray-Token"] = token
        resp = self._session.get(url, headers=headers or None, timeout=10)
        resp.raise_for_status()
        return resp.json()
