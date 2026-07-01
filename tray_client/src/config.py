"""Runtime configuration for the tray client.

Stored as JSON in ``%APPDATA%\\IRMS-Notice\\config.json``. The 2.0.0
release dropped TTS voice broadcasting, so the only thing the tray
still needs to remember between restarts is which IRMS server to ping.

점도 알림 대상 반제품 선택은 웹 점도 설정(remind_daily)이 소유한다. 트레이는
서버에 '오늘 밀린 알림 대상'을 물어보기만 하므로 품목 목록을 저장하지 않는다.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

APP_NAME = "IRMS-Notice"
DEFAULT_SERVER_URL = "http://192.168.11.194:9000"


def app_data_dir() -> Path:
    base = os.getenv("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    directory = Path(base) / APP_NAME
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def logs_dir() -> Path:
    directory = app_data_dir() / "logs"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def config_path() -> Path:
    return app_data_dir() / "config.json"


@dataclass
class Config:
    server_url: str = DEFAULT_SERVER_URL
    tray_api_token: str = ""

    @classmethod
    def load(cls) -> "Config":
        path = config_path()
        if not path.exists():
            config = cls()
            config.save()
            return config
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            backup = path.with_suffix(".json.bak")
            try:
                path.replace(backup)
            except OSError:
                pass
            config = cls()
            config.save()
            return config

        # Drop legacy fields (poll_interval_seconds, muted, last_message_id,
        # tts_rate, volume) silently - they were used by the removed voice
        # broadcaster and are no longer meaningful.
        allowed = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        cleaned = {k: v for k, v in raw.items() if k in allowed}
        return cls(**cleaned)

    def save(self) -> None:
        path = config_path()
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
