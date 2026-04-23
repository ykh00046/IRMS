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
            except requests.RequestException as exc:
                self._on_status("오프라인")
                logger.warning("poll failed: %s", exc)
                self._stop_event.wait(backoff)
                backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)
                continue
            except Exception as exc:  # noqa: BLE001
                logger.exception("unexpected poll error: %s", exc)
                self._stop_event.wait(backoff)
                backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)
                continue

            self._on_status("연결됨")
            backoff = interval

            if self._config.last_message_id == 0:
                snap = int(payload.get("latest_id", 0))
                if snap > 0:
                    self._config.last_message_id = snap
                    self._config.save()
                    logger.info("initial sync: snapshot latest_id=%d", snap)
            else:
                for item in payload.get("items", []):
                    try:
                        self._on_message(item)
                    except Exception as exc:  # noqa: BLE001
                        logger.exception("message handler failed: %s", exc)
                    item_id = int(item.get("id", 0) or 0)
                    if item_id > self._config.last_message_id:
                        self._config.last_message_id = item_id
                        self._config.save()

            self._stop_event.wait(interval)

    def _poll_once(self) -> dict:
        url = f"{self._config.server_url.rstrip('/')}/api/public/notice/poll"
        resp = self._session.get(
            url,
            params={"after_id": self._config.last_message_id, "limit": 20},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()
