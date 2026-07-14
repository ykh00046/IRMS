"""감사 F-11: 근태 로그인 실패 카운터는 시간이 지나면 감쇠한다.

옛 코드는 실패 횟수를 성공 로그인 전까지 영구 누적했다 — 며칠에 걸쳐 4번 오타한
사용자가 오늘 1번 더 틀리면 잠겼다. 이제 마지막 실패가 FAILED_WINDOW_SECONDS 를
넘겼으면 카운터를 1부터 다시 센다.
"""

from __future__ import annotations

import datetime as dt
from unittest.mock import patch

import pytest

from src import attendance_auth

NOW = dt.datetime(2026, 7, 14, 9, 0, 0, tzinfo=dt.timezone.utc)


def _record(failed_attempts: int, last_failed_at: dt.datetime | None) -> dict:
    return {
        "emp_id": "171013",
        "password_hash": "irrelevant-hash",
        "password_reset_required": 0,
        "failed_attempts": failed_attempts,
        "locked_until": None,
        "last_failed_at": attendance_auth._format_utc(last_failed_at) if last_failed_at else None,
        "last_login_at": None,
        "created_at": "2026-01-01T00:00:00Z",
    }


def _attempt_login(record: dict):
    """비밀번호가 틀린 로그인 1회. (발생한 예외, _update_failed 호출인자)를 돌려준다."""
    with (
        patch.object(attendance_auth, "_fetch", return_value=record),
        patch.object(attendance_auth, "_utc_now", return_value=NOW),
        patch.object(attendance_auth, "verify_password", return_value=False),
        patch.object(attendance_auth, "_update_failed") as update_failed,
        patch.object(attendance_auth, "_log_failed_login"),
    ):
        with pytest.raises(attendance_auth.AttendanceAuthError) as raised:
            attendance_auth.authenticate("171013", "wrong-password")
    return raised.value, update_failed


def test_failure_older_than_window_restarts_the_counter():
    """창 밖(1시간 전) 실패 4건이 쌓여 있어도, 오늘의 1회 실패로 잠기지 않는다."""
    stale = NOW - dt.timedelta(hours=1)
    error, update_failed = _attempt_login(_record(4, stale))

    assert error.code == "INVALID_CREDENTIALS"          # LOCKED 아님
    assert error.status_code == 401
    count, locked_until = update_failed.call_args.args[1], update_failed.call_args.args[2]
    assert count == 1                                    # 카운터 재시작
    assert locked_until is None
    # 남은 시도 횟수도 새 카운터 기준으로 안내된다
    assert error.extra["remaining"] == attendance_auth.MAX_FAILED_ATTEMPTS - 1


def test_failures_inside_window_still_lock():
    """창 안(1분 전) 실패가 쌓여 MAX 에 도달하면 종전대로 잠긴다 — 무차별 대입 방어는 유지."""
    recent = NOW - dt.timedelta(minutes=1)
    error, update_failed = _attempt_login(_record(attendance_auth.MAX_FAILED_ATTEMPTS - 1, recent))

    assert error.code == "LOCKED"
    assert error.status_code == 423
    assert update_failed.call_args.args[2] is not None   # locked_until 설정됨


def test_window_boundary_counts_as_inside():
    """정확히 창 경계에 걸친 실패는 아직 유효 — 창을 '넘어야' 감쇠한다."""
    edge = NOW - dt.timedelta(seconds=attendance_auth.FAILED_WINDOW_SECONDS)
    error, _ = _attempt_login(_record(attendance_auth.MAX_FAILED_ATTEMPTS - 1, edge))
    assert error.code == "LOCKED"
