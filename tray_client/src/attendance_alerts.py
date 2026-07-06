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

from .schedule import (  # 근태·점도 공통 스케줄
    SCHEDULED_ALERT_HOURS,
    SLOT_STALE_GRACE_MINUTES,
    current_slot_key,
    seconds_until_next_slot,
    stale_slot_key_on_startup,
)

logger = logging.getLogger("irms_notice")


DEFAULT_INTERVAL_SECONDS = 60 * 60
SLOT_RETRY_SECONDS = 60
# SCHEDULED_ALERT_HOURS, SLOT_STALE_GRACE_MINUTES 는 schedule 모듈에서 가져온다(하위호환 re-export).


class _FileLockedRetry(Exception):
    """Server reports the month file is temporarily locked; retry the slot soon."""


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
    """Background poller that checks month-wide anomalies on scheduled slots."""

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
        self._now_provider = now_provider or _dt.datetime.now
        self._stop_event = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name="attendance-alert",
            daemon=True,
        )
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
        # Skip slots whose grace period has already expired so a tray restart
        # mid-day doesn't re-fire popups the user already saw.
        self._last_processed_slot = self._stale_slot_key_on_startup(self._now_provider())

        while not self._stop_event.is_set():
            now = self._now_provider()
            slot_key = self._current_schedule_slot_key(now)
            if slot_key and slot_key != self._last_processed_slot:
                if self._is_enabled_getter():
                    completed = self._poll_and_notify(slot_key=slot_key)
                    if completed:
                        self._last_processed_slot = slot_key
                        wait_seconds = min(
                            self._seconds_until_next_schedule(self._now_provider()),
                            self._interval,
                        )
                    else:
                        wait_seconds = min(SLOT_RETRY_SECONDS, self._interval)
                else:
                    # Disabled: don't consume the slot. Re-enabling within the
                    # slot window should still fire a popup, so re-check soon.
                    wait_seconds = min(SLOT_RETRY_SECONDS, self._interval)
                self._stop_event.wait(wait_seconds)
                continue

            wait_seconds = min(self._seconds_until_next_schedule(now), self._interval)
            self._stop_event.wait(wait_seconds)

    def _stale_slot_key_on_startup(self, now: _dt.datetime) -> str | None:
        return stale_slot_key_on_startup(now)

    def _current_schedule_slot_key(self, now: _dt.datetime) -> str | None:
        return current_slot_key(now)

    def _seconds_until_next_schedule(self, now: _dt.datetime) -> int:
        return seconds_until_next_slot(now)

    def _poll_and_notify(self, force: bool = False, slot_key: str | None = None) -> bool:
        try:
            payload = self._poll_once()
        except _FileLockedRetry:
            return False
        except requests.RequestException as exc:
            logger.warning("attendance alert poll failed: %s", exc)
            return False
        except Exception as exc:  # noqa: BLE001
            logger.exception("attendance alert unexpected error: %s", exc)
            return False
        if not payload:
            return True

        items = payload.get("items") or []
        if not items:
            self._last_signature = None
            self._last_signature_slot = slot_key
            return True

        signature = anomaly_signature(items)
        if (
            not force
            and signature
            and signature == self._last_signature
            and slot_key == self._last_signature_slot
        ):
            logger.debug("attendance alert unchanged; duplicate popup suppressed")
            return True

        popup_payload = build_live_popup_payload(payload)
        self._present_alert(popup_payload)
        self._last_signature = signature
        self._last_signature_slot = slot_key
        logger.info("attendance popup raised: %s / %s", popup_payload.title, popup_payload.summary)
        return True

    def _poll_once(self) -> dict[str, Any] | None:
        url = f"{self._config.server_url.rstrip('/')}/api/public/attendance-alerts/month"
        headers = {}
        token = getattr(self._config, "tray_api_token", "")
        if token:
            headers["X-IRMS-Tray-Token"] = token
        resp = self._session.get(url, headers=headers or None, timeout=10)
        if resp.status_code == 404:
            logger.debug(
                "attendance alerts: 404 month file not ready (%s) - skipping slot",
                resp.text[:80],
            )
            return None
        if resp.status_code == 503:
            logger.info(
                "attendance alerts: 503 file locked (%s) - retrying slot in %ds",
                resp.text[:80],
                SLOT_RETRY_SECONDS,
            )
            raise _FileLockedRetry()
        resp.raise_for_status()
        return resp.json()


def today_iso() -> str:
    return _dt.date.today().isoformat()
