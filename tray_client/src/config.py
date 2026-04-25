"""Runtime configuration for the tray client.

Stored as JSON in ``%APPDATA%\\IRMS-Notice\\config.json``. The file is created
on first launch with default values and updated in place as the user toggles
mute, a new message is received, etc.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

APP_NAME = "IRMS-Notice"
DEFAULT_SERVER_URL = "http://192.168.11.147:9000"
DEFAULT_POLL_INTERVAL = 10
LEGACY_DEFAULT_POLL_INTERVAL = 5
MAX_BACKOFF_SECONDS = 60


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
    poll_interval_seconds: int = DEFAULT_POLL_INTERVAL
    muted: bool = False
    last_message_id: int = 0
    tts_rate: int = 180
    volume: float = 1.0

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

        allowed = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        cleaned = {k: v for k, v in raw.items() if k in allowed}
        config = cls(**cleaned)

        if cleaned.get("poll_interval_seconds") == LEGACY_DEFAULT_POLL_INTERVAL:
            config.poll_interval_seconds = DEFAULT_POLL_INTERVAL
            config.save()

        return config

    def save(self) -> None:
        path = config_path()
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
