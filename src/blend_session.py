from __future__ import annotations

import datetime as _dt

from fastapi import Request

SESSION_KEY = "blend_worker"
# 배합 작업자 세션은 민감정보 없는 현장 편의 세션(이름 기반)이다. 홈(/) 복귀나 탭 닫기 시
# 이미 정리되고, 배합 기록은 작업자 이름 귀속일 뿐 보안 경계가 아니다. 5분 유휴 만료는
# 현장에서 저울로 몇 분간 계량하는 동안(로컬 저울 통신이라 서버 요청 없음) 세션이 끊겨
# 저장 시 작업자 입력 화면으로 튕기고 입력 데이터가 날아가는 문제를 유발했다. 근무 시간
# 단위(12h)로 늘려 근무 중 재로그인을 없앤다. 실질 상한은 세션 쿠키 max_age(8h).
IDLE_TIMEOUT_SECONDS = 12 * 60 * 60


def _utc_now() -> _dt.datetime:
    return _dt.datetime.now(tz=_dt.timezone.utc)


def _parse_utc(text: str | None) -> _dt.datetime | None:
    if not text:
        return None
    try:
        return _dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _format_utc(when: _dt.datetime) -> str:
    return when.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def login_worker_session(request: Request, worker_name: str) -> None:
    now = _format_utc(_utc_now())
    request.session[SESSION_KEY] = {
        "worker_name": worker_name,
        "authenticated_at": now,
        "last_activity": now,
    }


def logout_worker_session(request: Request) -> None:
    request.session.pop(SESSION_KEY, None)


def touch_worker_session(request: Request) -> None:
    session = request.session.get(SESSION_KEY)
    if session:
        session["last_activity"] = _format_utc(_utc_now())
        request.session[SESSION_KEY] = session


def current_blend_worker(request: Request) -> str | None:
    session = request.session.get(SESSION_KEY)
    if not session:
        return None
    last_activity = _parse_utc(session.get("last_activity"))
    if last_activity is None:
        logout_worker_session(request)
        return None
    if (_utc_now() - last_activity).total_seconds() > IDLE_TIMEOUT_SECONDS:
        logout_worker_session(request)
        return None
    worker_name = session.get("worker_name")
    if not worker_name:
        logout_worker_session(request)
        return None
    return str(worker_name)
