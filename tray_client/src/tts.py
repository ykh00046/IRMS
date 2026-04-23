"""Text-to-speech + chime playback queue.

pyttsx3 is not thread-safe, so all utterances are serialized on a single
worker thread. A short chime plays before each message so field workers
hear the notification even with ambient machinery noise.
"""

from __future__ import annotations

import logging
import queue
import threading
from pathlib import Path
from typing import Any

import pyttsx3

logger = logging.getLogger("irms_notice")

try:
    import winsound
except ImportError:  # pragma: no cover - runtime target is Windows only
    winsound = None  # type: ignore[assignment]


def _format_message(msg: dict[str, Any]) -> str:
    text = (msg.get("message_text") or "").strip()
    speaker = (msg.get("created_by_display_name") or msg.get("created_by_username") or "").strip()
    if not text:
        return ""
    if speaker:
        return f"{speaker}님: {text}"
    return text


def _pick_korean_voice(engine: Any) -> None:
    try:
        voices = engine.getProperty("voices") or []
    except Exception:  # noqa: BLE001 - SAPI can be flaky on some hosts
        return
    preferred_hints = ("ko", "korean", "heami", "seoyeon")
    for voice in voices:
        identifier = (getattr(voice, "id", "") or "").lower()
        name = (getattr(voice, "name", "") or "").lower()
        if any(hint in identifier or hint in name for hint in preferred_hints):
            engine.setProperty("voice", voice.id)
            return


class TTSQueue:
    """Single-threaded speech + chime playback worker."""

    def __init__(self, chime_path: Path, rate: int = 180, muted: bool = False):
        self._chime_path = chime_path
        self._rate = rate
        self._queue: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self._muted = muted
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._run, name="tts-worker", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._queue.put(None)
        if self._thread.is_alive():
            self._thread.join(timeout=5)

    def set_muted(self, muted: bool) -> None:
        self._muted = muted

    def enqueue_message(self, msg: dict[str, Any]) -> None:
        self._queue.put(msg)

    def enqueue_raw(self, text: str) -> None:
        if text:
            self._queue.put({"message_text": text, "created_by_display_name": ""})

    def _run(self) -> None:
        try:
            engine = pyttsx3.init("sapi5")
            _pick_korean_voice(engine)
            engine.setProperty("rate", self._rate)
        except Exception as exc:  # noqa: BLE001
            logger.error("tts init failed: %s", exc)
            engine = None

        while not self._stop_event.is_set():
            item = self._queue.get()
            if item is None:
                return
            if self._muted:
                continue
            text = _format_message(item)
            if not text:
                continue
            try:
                self._play_chime()
                if engine is None:
                    continue
                engine.say(text)
                engine.runAndWait()
            except Exception as exc:  # noqa: BLE001
                logger.error("tts playback failed: %s", exc)

    def _play_chime(self) -> None:
        if winsound is None or not self._chime_path.exists():
            return
        try:
            winsound.PlaySound(
                str(self._chime_path),
                winsound.SND_FILENAME | winsound.SND_ASYNC,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("chime playback failed: %s", exc)
