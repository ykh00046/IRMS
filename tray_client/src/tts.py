"""Text-to-speech + chime playback queue.

Uses SAPI 5 via ``comtypes`` directly instead of ``pyttsx3``. PyInstaller
bundles of pyttsx3 have a known issue where ``runAndWait()`` can return
without actually waiting for the speech to finish (the Windows message
pump on the worker thread fails to dispatch the EndStream event), so
TTS becomes silently silent while logs claim success.

Calling ``SAPI.SpVoice.Speak(text)`` directly blocks until playback is
complete and is much more reliable inside a PyInstaller exe.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("irms_notice")

# Small buffer between the synchronous chime returning and TTS starting,
# so the OS audio device finishes one transition before SAPI requests it.
# Empirically 100~200ms is enough on Windows 10/11.
CHIME_TO_TTS_DELAY_SECONDS = 0.2
DEFAULT_TTS_QUEUE_SIZE = 20

# Default SAPI rate is 0 (range -10..10). pyttsx3 used a 100~250 wpm-ish
# scale; map the existing config so users don't have to update config.json.
_PYTTSX_DEFAULT_RATE = 200


def _pyttsx_rate_to_sapi(pyttsx_rate: int) -> int:
    return max(-10, min(10, int((pyttsx_rate - _PYTTSX_DEFAULT_RATE) / 20)))


try:
    import comtypes.client  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - runtime target is Windows only
    comtypes = None  # type: ignore[assignment]

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


def _pick_korean_voice(voice_obj: Any) -> bool:
    """Select a Korean SAPI voice if available. Returns True on success."""
    try:
        tokens = voice_obj.GetVoices()
    except Exception:  # noqa: BLE001
        return False
    preferred_hints = ("ko", "korean", "heami", "seoyeon")
    available: list[str] = []
    try:
        token_count = tokens.Count
    except Exception:  # noqa: BLE001
        token_count = 0
    for index in range(token_count):
        token = tokens.Item(index)
        try:
            description = token.GetDescription() or ""
        except Exception:  # noqa: BLE001
            description = ""
        available.append(description)
        lowered = description.lower()
        if any(hint in lowered for hint in preferred_hints):
            voice_obj.Voice = token
            logger.info("tts using korean voice: %s", description)
            return True
    logger.warning(
        "tts: no korean SAPI voice found (heami/seoyeon). "
        "korean notices will sound wrong. installed voices: %s",
        available,
    )
    return False


def _create_sapi_voice(rate: int, volume: float) -> Any:
    """Create a SAPI.SpVoice configured for Korean notices."""
    if comtypes is None:
        raise RuntimeError("comtypes is unavailable (non-Windows runtime?)")
    voice = comtypes.client.CreateObject("SAPI.SpVoice")
    _pick_korean_voice(voice)
    voice.Rate = _pyttsx_rate_to_sapi(rate)
    voice.Volume = max(0, min(100, int(volume * 100)))
    return voice


class TTSQueue:
    """Single-threaded speech + chime playback worker."""

    def __init__(
        self,
        chime_path: Path,
        rate: int = _PYTTSX_DEFAULT_RATE,
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
        # SAPI is bound to the COM apartment of the thread that creates the
        # SpVoice object, so init + Speak must stay on this single worker
        # thread. Do NOT touch the voice from any other thread.
        try:
            voice = _create_sapi_voice(self._rate, self._volume)
            logger.info(
                "tts worker ready (rate=%d→sapi%d, volume=%.2f)",
                self._rate,
                _pyttsx_rate_to_sapi(self._rate),
                self._volume,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("tts init failed: %s", exc)
            voice = None

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
                if self._play_chime():
                    time.sleep(CHIME_TO_TTS_DELAY_SECONDS)
                if voice is None:
                    continue
                started_at = time.monotonic()
                logger.info("tts: speaking [%d chars] %s", len(text), text[:40])
                # Speak with SVSFDefault=0 - blocks until playback completes.
                voice.Speak(text, 0)
                duration = time.monotonic() - started_at
                logger.info("tts: speech completed in %.2fs", duration)
            except Exception as exc:  # noqa: BLE001
                logger.error("tts playback failed: %s", exc)

    def _play_chime(self) -> bool:
        # Synchronous playback (no SND_ASYNC) so the audio device is fully
        # released before TTS runs. The async version raced SAPI whenever
        # the chime tail was still rendering.
        if winsound is None or not self._chime_path.exists():
            return False
        try:
            winsound.PlaySound(
                str(self._chime_path),
                winsound.SND_FILENAME,
            )
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("chime playback failed: %s", exc)
            return False
