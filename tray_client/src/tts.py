"""Text-to-speech + chime playback queue.

pyttsx3 is not thread-safe, so all utterances are serialized on a single
worker thread. A short chime plays before each message so field workers
hear the notification even with ambient machinery noise.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from pathlib import Path
from typing import Any

import pyttsx3

logger = logging.getLogger("irms_notice")

# Chime is played asynchronously (SND_ASYNC) so the worker proceeds while
# the WAV is still rendering. 0.2s gives the chime its audible "pluck"
# before the voice starts; a longer chime sample would overlap and should
# raise this constant. Tied to assets/ding.wav (~0.5s today).
CHIME_TO_TTS_DELAY_SECONDS = 0.2
DEFAULT_TTS_QUEUE_SIZE = 20

# Watchdog upper bound for a single message playback. 300-char Korean text
# at rate=180 fits comfortably under 30s; anything longer signals SAPI hang.
TTS_MAX_DURATION_SECONDS = 30
WATCHDOG_TICK_SECONDS = 5

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


def _pick_korean_voice(engine: Any) -> bool:
    """Select a Korean SAPI voice if available. Returns True on success.

    On Windows hosts that lack Heami/Seoyeon, SAPI silently falls back to the
    default English voice and Korean text is mispronounced. The caller logs
    the failure so an operator can install a Korean voice.
    """
    try:
        voices = engine.getProperty("voices") or []
    except Exception:  # noqa: BLE001 - SAPI can be flaky on some hosts
        return False
    preferred_hints = ("ko", "korean", "heami", "seoyeon")
    for voice in voices:
        identifier = (getattr(voice, "id", "") or "").lower()
        name = (getattr(voice, "name", "") or "").lower()
        if any(hint in identifier or hint in name for hint in preferred_hints):
            engine.setProperty("voice", voice.id)
            logger.info("tts using korean voice: %s", getattr(voice, "name", voice.id))
            return True
    available = [getattr(v, "name", "") or getattr(v, "id", "") for v in voices]
    logger.warning(
        "tts: no korean SAPI voice found (heami/seoyeon). "
        "korean notices will sound wrong. installed voices: %s",
        available,
    )
    return False


class TTSQueue:
    """Single-threaded speech + chime playback worker."""

    def __init__(
        self,
        chime_path: Path,
        rate: int = 180,
        muted: bool = False,
        volume: float = 1.0,
        max_queue_size: int = DEFAULT_TTS_QUEUE_SIZE,
    ):
        self._chime_path = chime_path
        self._rate = rate
        self._volume = max(0.0, min(float(volume), 1.0))
        self._queue: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=max_queue_size)
        self._muted = muted
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._run, name="tts-worker", daemon=True)
        self._engine: Any = None
        self._engine_lock = threading.Lock()
        self._engine_needs_reinit = False
        self._current_started_at: float | None = None

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._force_enqueue_stop()
        if self._thread.is_alive():
            self._thread.join(timeout=5)

    def set_muted(self, muted: bool) -> None:
        self._muted = muted

    def enqueue_message(self, msg: dict[str, Any]) -> None:
        self._enqueue(msg)

    def enqueue_raw(self, text: str) -> None:
        if text:
            self._enqueue({"message_text": text, "created_by_display_name": ""})

    def _enqueue(self, item: dict[str, Any]) -> None:
        try:
            self._queue.put_nowait(item)
            return
        except queue.Full:
            pass

        try:
            dropped = self._queue.get_nowait()
            logger.warning(
                "tts queue full; dropped oldest message id=%s",
                dropped.get("id") if isinstance(dropped, dict) else None,
            )
        except queue.Empty:
            pass

        try:
            self._queue.put_nowait(item)
        except queue.Full:
            logger.warning("tts queue full; dropped incoming message id=%s", item.get("id"))

    def _force_enqueue_stop(self) -> None:
        while True:
            try:
                self._queue.put_nowait(None)
                return
            except queue.Full:
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    return

    def _run(self) -> None:
        self._init_engine()
        threading.Thread(target=self._watchdog_loop, name="tts-watchdog", daemon=True).start()

        while not self._stop_event.is_set():
            item = self._queue.get()
            if item is None:
                return
            if self._muted:
                continue
            text = _format_message(item)
            if not text:
                continue

            if self._engine_needs_reinit:
                self._init_engine()
            if self._engine is None:
                continue

            try:
                if self._play_chime():
                    time.sleep(CHIME_TO_TTS_DELAY_SECONDS)
                self._current_started_at = time.monotonic()
                self._engine.say(text)
                self._engine.runAndWait()
            except Exception as exc:  # noqa: BLE001
                logger.error("tts playback failed: %s", exc)
                # Mark for re-init so the next message gets a fresh engine.
                # SAPI can wedge after one bad call; reusing the same engine
                # tends to cascade failures.
                self._engine_needs_reinit = True
            finally:
                self._current_started_at = None

    def _init_engine(self) -> None:
        with self._engine_lock:
            try:
                self._engine = pyttsx3.init("sapi5")
                _pick_korean_voice(self._engine)
                self._engine.setProperty("rate", self._rate)
                self._engine.setProperty("volume", self._volume)
                self._engine_needs_reinit = False
            except Exception as exc:  # noqa: BLE001
                logger.error("tts init failed: %s", exc)
                self._engine = None
                self._engine_needs_reinit = True

    def _watchdog_loop(self) -> None:
        # Polls the active TTS duration. If a single message exceeds
        # TTS_MAX_DURATION_SECONDS (typical SAPI hang), call engine.stop()
        # from this thread to unblock runAndWait() and force a re-init on
        # the next message.
        while not self._stop_event.is_set():
            self._stop_event.wait(WATCHDOG_TICK_SECONDS)
            started = self._current_started_at
            if started is None:
                continue
            elapsed = time.monotonic() - started
            if elapsed <= TTS_MAX_DURATION_SECONDS:
                continue
            logger.warning(
                "tts playback exceeded %.0fs (elapsed=%.1fs); forcing engine stop",
                TTS_MAX_DURATION_SECONDS,
                elapsed,
            )
            with self._engine_lock:
                if self._engine is not None:
                    try:
                        self._engine.stop()
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("tts engine stop failed: %s", exc)
            self._engine_needs_reinit = True

    def _play_chime(self) -> bool:
        if winsound is None or not self._chime_path.exists():
            return False
        try:
            winsound.PlaySound(
                str(self._chime_path),
                winsound.SND_FILENAME | winsound.SND_ASYNC,
            )
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("chime playback failed: %s", exc)
            return False
