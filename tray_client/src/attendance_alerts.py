"""Attendance anomaly alert poller for the tray client.

Polls ``/api/public/attendance-alerts/today`` every 30 minutes; if any
workers have a missing check-in/out, tardy, or early-leave today, the
tray icon raises a silent Windows toast listing their names.

"Resolved" is implicit: the server re-reads the Excel file every
request, so once a late worker's check-in is filled in or the date
rolls over at midnight, the names drop out of the response on their
own. The tray app never manages state — it just trusts today's
snapshot from the server.
"""

from __future__ import annotations

import datetime as _dt
import logging
import threading
from typing import Any, Callable

import requests

logger = logging.getLogger("irms_notice")


DEFAULT_INTERVAL_SECONDS = 30 * 60
MAX_NAMES_SHOWN = 3


def format_notification(items: list[dict[str, Any]]) -> tuple[str, str]:
    """Build (title, body) for a toast listing anomaly owners by name."""
    total = len(items)
    title = f"근태 이상 {total}건"
    names = [str(item.get("name") or item.get("emp_id") or "").strip() for item in items]
    names = [n for n in names if n]
    if not names:
        return title, f"확인이 필요한 근태 이상 {total}건이 있습니다."
    if total <= MAX_NAMES_SHOWN:
        body = " · ".join(names)
    else:
        body = " · ".join(names[:MAX_NAMES_SHOWN]) + f" 외 {total - MAX_NAMES_SHOWN}명"
    return title, body


class AttendanceAlertPoller:
    """Background polling thread that pings the alert endpoint."""

    def __init__(
        self,
        config,
        icon_getter: Callable[[], Any],
        is_enabled_getter: Callable[[], bool],
        interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
    ) -> None:
        self._config = config
        self._icon_getter = icon_getter
        self._is_enabled_getter = is_enabled_getter
        self._interval = max(60, int(interval_seconds))
        self._stop_event = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name="attendance-alert", daemon=True
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
        """Fire a single poll on a worker thread (used by test menu)."""
        threading.Thread(target=self._poll_and_notify, daemon=True).start()

    def _run(self) -> None:
        # Wait one interval on startup so the app launches quickly and the
        # first notification does not fire before the user sees the icon.
        self._stop_event.wait(min(self._interval, 60))
        while not self._stop_event.is_set():
            if self._is_enabled_getter():
                self._poll_and_notify()
            self._stop_event.wait(self._interval)

    def _poll_and_notify(self) -> None:
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
            # Reset the dedup signature so a fresh burst of anomalies
            # tomorrow (same names) will still pop up.
            self._last_signature = None
            return

        title, body = format_notification(items)
        icon = self._icon_getter()
        if icon is None:
            logger.info("anomalies pending, icon not ready yet: %s", body)
            return
        try:
            icon.notify(body, title)
            logger.info("attendance toast raised: %s / %s", title, body)
        except Exception as exc:  # noqa: BLE001
            logger.warning("attendance toast failed: %s", exc)

    def _poll_once(self) -> dict[str, Any] | None:
        url = (
            f"{self._config.server_url.rstrip('/')}"
            f"/api/public/attendance-alerts/today"
        )
        resp = self._session.get(url, timeout=10)
        if resp.status_code in (404, 503):
            logger.debug(
                "attendance alerts: %s %s — skipping",
                resp.status_code,
                resp.text[:80],
            )
            return None
        resp.raise_for_status()
        return resp.json()


def today_iso() -> str:
    return _dt.date.today().isoformat()
