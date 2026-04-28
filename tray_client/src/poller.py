"""Background thread that polls the IRMS notice endpoint.

Uses exponential backoff when the server is unreachable so the app does
not spam a down host. On the very first successful poll (``last_message_id``
is 0) the client snapshots the current ``latest_id`` so it does not replay
the entire notice history on install.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable

import requests

from .config import DEFAULT_POLL_INTERVAL, MAX_BACKOFF_SECONDS, Config

logger = logging.getLogger("irms_notice")

StatusCallback = Callable[[str], None]
MessageHandler = Callable[[dict], None]
STATUS_LABELS = {
    "connected": "연결됨",
    "offline": "오프라인",
}


class Poller:
    def __init__(
        self,
        config: Config,
        on_message: MessageHandler,
        on_status: StatusCallback | None = None,
    ) -> None:
        self._config = config
        self._on_message = on_message
        self._on_status = on_status or (lambda _s: None)
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._run, name="poller", daemon=True)
        self._session = requests.Session()

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread.is_alive():
            self._thread.join(timeout=5)

    def _run(self) -> None:
        interval = max(int(self._config.poll_interval_seconds or DEFAULT_POLL_INTERVAL), 2)
        backoff = interval
        while not self._stop_event.is_set():
            try:
                payload = self._poll_once()
                self._handle_success(payload)
            except requests.RequestException as exc:
                self._safe_status("offline")
                logger.warning("poll failed: %s", exc)
                self._stop_event.wait(backoff)
                backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)
                continue
            except Exception as exc:  # noqa: BLE001
                logger.exception("unexpected poll error: %s", exc)
                self._stop_event.wait(backoff)
                backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)
                continue

            backoff = interval
            self._stop_event.wait(interval)

    def _handle_success(self, payload: dict) -> None:
        self._safe_status("connected")
        is_initial_sync = self._config.last_message_id == 0

        raw_items = payload.get("items", [])
        if not isinstance(raw_items, list):
            logger.warning("poll payload items was not a list: %r", raw_items)
            raw_items = []

        for item in raw_items:
            if not isinstance(item, dict):
                logger.warning("poll payload item was not an object: %r", item)
                continue

            # Note: a TTS handler exception or full-queue drop is logged but
            # does not block last_message_id from advancing. The intent is
            # to never re-deliver the same message id (no replay storms),
            # at the cost of one missed playback when the queue is saturated.
            try:
                self._on_message(item)
            except Exception as exc:  # noqa: BLE001
                logger.exception("message handler failed: %s", exc)

            item_id = self._safe_int(item.get("id", 0), default=0)
            if item_id <= 0:
                logger.warning("poll payload item had invalid id: %r", item.get("id"))
                continue
            if item_id > self._config.last_message_id:
                self._config.last_message_id = item_id
                self._safe_save_config()

        if is_initial_sync:
            # Server returns at most one fresh message on initial sync. After
            # playing it (above), snapshot latest_id so older history stays
            # suppressed even if its id is greater than the fresh one's.
            snap = self._safe_int(payload.get("latest_id", 0))
            if snap > self._config.last_message_id:
                self._config.last_message_id = snap
                self._safe_save_config()
                logger.info(
                    "initial sync: snapshot latest_id=%d (replayed=%d)",
                    snap,
                    len(raw_items),
                )

    def _safe_status(self, status: str) -> None:
        try:
            self._on_status(STATUS_LABELS.get(status, status))
        except Exception as exc:  # noqa: BLE001
            logger.exception("status callback failed: %s", exc)

    def _safe_save_config(self) -> None:
        try:
            self._config.save()
        except Exception as exc:  # noqa: BLE001
            logger.exception("config save failed: %s", exc)

    @staticmethod
    def _safe_int(value, default: int = 0) -> int:
        try:
            return int(value or default)
        except (TypeError, ValueError):
            return default

    def _poll_once(self) -> dict:
        url = f"{self._config.server_url.rstrip('/')}/api/public/notice/poll"
        resp = self._session.get(
            url,
            params={"after_id": self._config.last_message_id, "limit": 20},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()
