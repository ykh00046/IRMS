"""AI 도우미 설정 — 켬/끔, 모델, API 키.

값은 기존 ``app_settings`` 테이블(settings_service)에 담는다. 새 테이블을 만들지
않는 이유는 저울 전용 입력 모드와 같은 성격의 전역 스위치이기 때문이다.

키 우선순위는 **DB → 환경변수** 다. DB 에 넣으면 화면에서 바꿀 수 있고, 환경변수는
운영 PC 에 붙박이로 두는 용도다. 어느 쪽이든 값 자체는 응답·로그에 싣지 않고
가린 형태(앞 4자 + … + 뒤 4자)만 내보낸다.
"""

from __future__ import annotations

import json
import datetime as _dt
import sqlite3

from ...db import get_connection
from .. import settings_service
from ... import config

ENABLED_KEY = "assistant_enabled"
MODEL_KEY = "assistant_model"
GEMINI_KEY = "assistant_gemini_api_key"
QUOTA_KEY = "assistant_quota_seen"
GROQ_KEY = "assistant_groq_api_key"

# 2026-09-10 실측으로 갱신. gemini-2.5-flash 는 무료 티어 **하루 20회**라 현장에서 오전에
# 다 쓴다(운영 장애 실측: quotaValue 20). 계정에 열려 있는 3.x flash 계열은 한도가 훨씬
# 넉넉하고(공개 자료 기준 Flash 1,500 RPD) 도구 호출·한국어 답변 모두 정상 확인했다.
DEFAULT_MODEL = "gemini-3.5-flash"
# 무료로 쓸 수 없게 된(또는 사실상 못 쓰는) 옛 모델 — 저장돼 있어도 기본값으로 돌린다.
# 2.5 계열은 하루 20회, pro 는 2026-04 부터 유료 전용.
RETIRED_MODELS = frozenset({
    "gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.5-pro",
    "gemini-1.5-flash", "gemini-1.5-pro",
})
# Groq 예비 모델 사슬. llama-3.3-70b-versatile 은 2026-09 현재 계정에서 404(은퇴) 라
# 계정 모델 목록에 실제로 있는 것으로 바꿨다. 앞이 막히면(404·429·5xx) 다음으로 넘어간다.
GROQ_MODELS = ("openai/gpt-oss-120b", "qwen/qwen3.8-27b", "openai/gpt-oss-20b")
GROQ_MODEL = GROQ_MODELS[0]

# 설정 화면에서 고를 수 있는 모델. 목록 밖 값도 저장은 되지만(운영 중 새 모델 대응)
# 화면은 이 목록을 기본 선택지로 쓴다.
# 2026-09-10 계정 실측(분당 한도는 429 의 quotaId 로 확인):
#   gemini-3.5-flash       분당 5회   도구 호출 2.6초   ← 기본
#   gemini-3.5-flash-lite  14회 연속 호출까지 한도 안 걸림   도구 호출 2.2초   ← 예비
#   gemini-3-flash-preview 분당 5회   — 미리보기라 이름이 사라질 수 있고 3.5-flash 대비 이점 없음. 뺐다.
MODEL_CHOICES = (
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
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
    if not raw or raw in RETIRED_MODELS:
        # 옛 값이 저장돼 있어도 기본값으로 돌린다 — 하루 20회짜리 모델을 물고 있으면
        # 도우미가 오전에 죽는다. 책임자는 설정 화면에서 현재 목록 중 다시 고를 수 있다.
        return DEFAULT_MODEL
    return raw


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
        "quotas": get_quotas(connection),
    }


# ============================================================
# 관측한 한도 — 모델마다 무료 한도가 다르고 문서로는 알 수 없다
# ============================================================
def get_quotas(connection: sqlite3.Connection) -> dict[str, Any]:
    """429 를 맞으면서 알게 된 모델별 한도. {"모델": {"kind","value","at"}}"""
    raw = settings_service.get_setting(connection, QUOTA_KEY) or ""
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def record_quota(model: str, kind: str, value: int) -> None:
    """무료 한도에 걸린 사실을 남긴다 — 설정 화면이 "이 모델은 분당 5회"를 말할 수 있게.

    한도는 계정·모델마다 다르고 API 로 물어볼 수 없어서, 걸렸을 때만 알 수 있다
    (2026-09-10: gemini-2.5-flash 하루 20회를 이렇게 알아냈다).
    """
    name = (model or "").strip()
    if not name or value <= 0:
        return
    try:
        with get_connection() as connection:
            seen = get_quotas(connection)
            entry = seen.get(name) or {}
            if entry.get("kind") == kind and entry.get("value") == value:
                return
            entry.update({
                "kind": kind, "value": int(value),
                "at": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            })
            seen[name] = entry
            settings_service.set_setting(
                connection, QUOTA_KEY, json.dumps(seen, ensure_ascii=False),
                updated_by="assistant",
            )
    except Exception:  # noqa: BLE001 - 기록 실패가 답변을 막으면 안 된다
        pass
