"""Attendance anomaly alert poller for the tray client."""

from __future__ import annotations

import datetime as _dt
import logging
import threading
from typing import Any, Callable

import requests

from .attendance_popup import (
    PopupPayload,
    build_live_popup_payload,
    build_test_popup_payload,
)

logger = logging.getLogger("irms_notice")


DEFAULT_INTERVAL_SECONDS = 60 * 60


def anomaly_signature(items: list[dict[str, Any]]) -> str:
    """Return a stable signature for suppressing duplicate poll results."""
    parts: list[str] = []
    for item in items:
        emp = str(item.get("emp_id") or item.get("name") or "").strip()
        issues = item.get("issues") or []
        issue_text = ",".join(str(issue).strip() for issue in issues if str(issue).strip())
        parts.append(f"{emp}:{issue_text}")
    return "|".join(sorted(part for part in parts if part))


class AttendanceAlertPoller:
    """Background polling thread that pings the alert endpoint."""

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
        self._thread = threading.Thread(
            target=self._run,
            name="attendance-alert",
            daemon=True,
        )
        self._session = requests.Session()
        self._last_signature: str | None = None

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread.is_alive():
            self._thread.join(timeout=5)

    def trigger_once(self) -> None:
        """Fire a single poll on a worker thread."""
        threading.Thread(
            target=self._poll_and_notify,
            kwargs={"force": True},
            daemon=True,
        ).start()

    def show_test_notification(self) -> None:
        """Show the persistent popup in test mode."""
        payload = build_test_popup_payload()
        self._present_alert(payload)
        logger.info("attendance test popup raised: %s", payload.title)

    def _run(self) -> None:
        self._stop_event.wait(min(self._interval, 60))
        while not self._stop_event.is_set():
            if self._is_enabled_getter():
                self._poll_and_notify()
            self._stop_event.wait(self._interval)

    def _poll_and_notify(self, force: bool = False) -> None:
        try:
            payload = self._poll_once()
        except requests.RequestException as exc:
            logger.warning("attendance alert poll failed: %s", exc)
            return
        except Exception as exc:  # noqa: BLE001
            logger.exception("attendance alert unexpected error: %s", exc)
            return
        if not payload:
            return

        items = payload.get("items") or []
        if not items:
            self._last_signature = None
            return

        signature = anomaly_signature(items)
        if not force and signature and signature == self._last_signature:
            logger.debug("attendance alert unchanged; duplicate popup suppressed")
            return

        popup_payload = build_live_popup_payload(payload)
        self._present_alert(popup_payload)
        self._last_signature = signature
        logger.info("attendance popup raised: %s / %s", popup_payload.title, popup_payload.summary)

    def _poll_once(self) -> dict[str, Any] | None:
        url = f"{self._config.server_url.rstrip('/')}/api/public/attendance-alerts/today"
        resp = self._session.get(url, timeout=10)
        if resp.status_code in (404, 503):
            logger.debug(
                "attendance alerts: %s %s - skipping",
                resp.status_code,
                resp.text[:80],
            )
            return None
        resp.raise_for_status()
        return resp.json()


def today_iso() -> str:
    return _dt.date.today().isoformat()
