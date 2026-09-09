"""AI 도우미 설정 — 켬/끔, 모델, API 키.

값은 기존 ``app_settings`` 테이블(settings_service)에 담는다. 새 테이블을 만들지
않는 이유는 저울 전용 입력 모드와 같은 성격의 전역 스위치이기 때문이다.

키 우선순위는 **DB → 환경변수** 다. DB 에 넣으면 화면에서 바꿀 수 있고, 환경변수는
운영 PC 에 붙박이로 두는 용도다. 어느 쪽이든 값 자체는 응답·로그에 싣지 않고
가린 형태(앞 4자 + … + 뒤 4자)만 내보낸다.
"""

from __future__ import annotations

import sqlite3

from .. import settings_service
from ... import config

ENABLED_KEY = "assistant_enabled"
MODEL_KEY = "assistant_model"
GEMINI_KEY = "assistant_gemini_api_key"
GROQ_KEY = "assistant_groq_api_key"

DEFAULT_MODEL = "gemini-2.5-flash"
GROQ_MODEL = "llama-3.3-70b-versatile"

# 설정 화면에서 고를 수 있는 모델. 목록 밖 값도 저장은 되지만(운영 중 새 모델 대응)
# 화면은 이 목록을 기본 선택지로 쓴다.
MODEL_CHOICES = (
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.5-pro",
)


def mask_key(value: str | None) -> str | None:
    """키를 가린다. 앞 4자 + … + 뒤 4자. 짧으면 길이만 드러나지 않게 전부 가린다."""
    raw = (value or "").strip()
    if not raw:
        return None
    if len(raw) <= 8:
        return "…"
    return f"{raw[:4]}…{raw[-4:]}"


def get_enabled(connection: sqlite3.Connection) -> bool:
    """스위치 상태. 행 없음/'1' 아님 → False."""
    return settings_service.get_setting(connection, ENABLED_KEY) == "1"


def set_enabled(
    connection: sqlite3.Connection, enabled: bool, updated_by: str | None = None
) -> None:
    settings_service.set_setting(
        connection, ENABLED_KEY, "1" if enabled else "0", updated_by=updated_by
    )


def get_model(connection: sqlite3.Connection) -> str:
    raw = (settings_service.get_setting(connection, MODEL_KEY) or "").strip()
    return raw or DEFAULT_MODEL


def set_model(
    connection: sqlite3.Connection, model: str, updated_by: str | None = None
) -> None:
    settings_service.set_setting(
        connection, MODEL_KEY, (model or "").strip() or DEFAULT_MODEL, updated_by=updated_by
    )


def _resolve_key(
    connection: sqlite3.Connection, db_key: str, env_value: str
) -> tuple[str, str | None]:
    """(키, 출처). 출처는 "db" | "env" | None."""
    stored = (settings_service.get_setting(connection, db_key) or "").strip()
    if stored:
        return stored, "db"
    env_value = (env_value or "").strip()
    if env_value:
        return env_value, "env"
    return "", None


def get_gemini_key(connection: sqlite3.Connection) -> tuple[str, str | None]:
    return _resolve_key(connection, GEMINI_KEY, config.GEMINI_API_KEY)


def get_groq_key(connection: sqlite3.Connection) -> tuple[str, str | None]:
    return _resolve_key(connection, GROQ_KEY, config.GROQ_API_KEY)


def set_api_key(
    connection: sqlite3.Connection,
    db_key: str,
    value: str,
    updated_by: str | None = None,
) -> bool:
    """키 저장. 빈 문자열이면 행을 지운다(= 환경변수로 되돌아감). 저장했으면 True."""
    raw = (value or "").strip()
    if not raw:
        try:
            connection.execute("DELETE FROM app_settings WHERE key = ?", (db_key,))
        except sqlite3.OperationalError:  # 구버전/테스트 DB — 방어적 폴백
            return False
        return False
    settings_service.set_setting(connection, db_key, raw, updated_by=updated_by)
    return True


def fake_mode() -> bool:
    """가짜 모델 모드 — 키 없이도 끝까지 도는 결정론적 응답."""
    return bool(config.ASSISTANT_FAKE)


def resolve_provider(connection: sqlite3.Connection) -> str | None:
    """실제로 쓸 공급자. "gemini" | "groq" | "fake" | None(쓸 수 있는 게 없음)."""
    if fake_mode():
        return "fake"
    if get_gemini_key(connection)[0]:
        return "gemini"
    if get_groq_key(connection)[0]:
        return "groq"
    return None


def status(connection: sqlite3.Connection) -> dict[str, object]:
    """공개 상태 — 켜짐 여부는 스위치 AND 쓸 수 있는 공급자 둘 다 있어야 True."""
    provider = resolve_provider(connection)
    switch = get_enabled(connection)
    return {
        "enabled": bool(switch and provider),
        "provider": provider if switch else None,
        "model": GROQ_MODEL if provider == "groq" else get_model(connection),
    }


def admin_view(connection: sqlite3.Connection) -> dict[str, object]:
    """책임자 화면용 설정 요약. 키 원문은 절대 넣지 않는다."""
    gemini_value, gemini_source = get_gemini_key(connection)
    groq_value, groq_source = get_groq_key(connection)
    return {
        "enabled": get_enabled(connection),
        "model": get_model(connection),
        "gemini_set": bool(gemini_value),
        "groq_set": bool(groq_value),
        "gemini_key_masked": mask_key(gemini_value),
        "groq_key_masked": mask_key(groq_value),
        "gemini_source": gemini_source,
        "groq_source": groq_source,
        "fake_mode": fake_mode(),
        "model_choices": list(MODEL_CHOICES),
    }
