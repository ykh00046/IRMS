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
# 서버 이전 전 옛 기본값. 이 값 그대로 저장돼 있던 기존 설치는 새 기본값으로 자동 이관한다.
_OLD_DEFAULT_SERVER_URL = "http://192.168.11.147:9000"


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
    # 통합 앱(현장 도우미)의 기능 토글 — 한 번 켜고/끄면 재부팅해도 유지된다(설정 창에서 변경).
    # 기본: 근태·점도 알림 켜짐, 저울은 저울이 연결된 현장 PC에서만 켜서 쓴다.
    attendance_alerts_enabled: bool = True
    viscosity_alerts_enabled: bool = True
    scale_enabled: bool = False

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
        # 레거시 이관: 옛 단일 토글 alerts_enabled → 근태/점도 개별 토글.
        if "alerts_enabled" in raw:
            master = bool(raw.get("alerts_enabled"))
            cleaned.setdefault("attendance_alerts_enabled", master)
            cleaned.setdefault("viscosity_alerts_enabled", master)

        # 서버 이전 이관: 옛 기본값(.147)이 그대로 저장돼 있으면 새 기본값(.194)으로 갱신.
        # (사용자가 직접 지정한 다른 주소는 건드리지 않는다 — 정확히 옛 기본값일 때만.)
        migrated_server = False
        if cleaned.get("server_url") == _OLD_DEFAULT_SERVER_URL:
            cleaned["server_url"] = DEFAULT_SERVER_URL
            migrated_server = True

        config = cls(**cleaned)
        if migrated_server:
            try:
                config.save()
            except OSError:
                pass
        return config

    def save(self) -> None:
        path = config_path()
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
