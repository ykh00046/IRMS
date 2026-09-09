"""AI 도우미 대화 이력 저장소 — 프로세스 메모리, 영속성 없음.

session_id → {"history": [(role, text), ...], "last_access": float}

- 30분 유휴면 버린다(TTL).
- 세션당 최근 10턴(사용자+모델 = 20줄)만 남긴다.
- 전역 200세션 상한 — 무한히 쌓여 메모리를 먹는 길을 막는다(가장 오래된 것부터 축출).
- 정리는 저장할 때마다 게으르게 수행한다(백그라운드 스레드 없음).

이력은 공급자 중립적인 ``(role, text)`` 튜플이라 Gemini ``Content`` 든 Groq
``messages`` 든 호출 지점에서 얇게 변환해 쓴다.
"""

from __future__ import annotations

import time

SESSION_TTL = 1800  # 30분 유휴 → 축출
SESSION_MAX_TURNS = 10
SESSION_MAX_ENTRIES = SESSION_MAX_TURNS * 2
SESSION_MAX_COUNT = 200

HistoryEntry = tuple[str, str]  # role ∈ {"user", "model"}

_sessions: dict[str, dict[str, object]] = {}


def get_history(session_id: str | None) -> list[HistoryEntry]:
    """세션 이력 사본. 없으면 빈 목록."""
    if not session_id or session_id not in _sessions:
        return []
    session = _sessions[session_id]
    session["last_access"] = time.time()
    return list(session["history"])  # type: ignore[arg-type]


def append_exchange(
    session_id: str | None,
    user_text: str,
    model_text: str,
) -> None:
    """사용자+모델 한 쌍을 붙이고 상한까지 자른다. session_id 가 없으면 무시."""
    if not session_id:
        return

    cleanup_expired()

    session = _sessions.get(session_id)
    if session is None:
        if len(_sessions) >= SESSION_MAX_COUNT:
            _evict_oldest()
        session = {"history": [], "last_access": time.time()}
        _sessions[session_id] = session

    history: list[HistoryEntry] = session["history"]  # type: ignore[assignment]
    history.append(("user", user_text))
    history.append(("model", model_text))
    if len(history) > SESSION_MAX_ENTRIES:
        del history[: len(history) - SESSION_MAX_ENTRIES]
    session["last_access"] = time.time()


def clear(session_id: str | None) -> bool:
    """한 세션을 지운다. 지운 게 있으면 True."""
    if not session_id:
        return False
    return _sessions.pop(session_id, None) is not None


def cleanup_expired() -> int:
    """유휴 세션 축출. 지운 개수 반환."""
    now = time.time()
    expired = [
        sid
        for sid, data in _sessions.items()
        if now - float(data["last_access"]) > SESSION_TTL  # type: ignore[arg-type]
    ]
    for sid in expired:
        del _sessions[sid]
    return len(expired)


def _evict_oldest() -> None:
    if not _sessions:
        return
    oldest = min(
        _sessions.items(),
        key=lambda kv: float(kv[1]["last_access"]),  # type: ignore[arg-type]
    )[0]
    del _sessions[oldest]


def reset() -> None:
    """테스트 전용 — 전부 비운다."""
    _sessions.clear()


def session_count() -> int:
    return len(_sessions)
