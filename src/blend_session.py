from __future__ import annotations

import datetime as _dt

from fastapi import Request

SESSION_KEY = "blend_worker"
IDLE_TIMEOUT_SECONDS = 5 * 60


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
