"""배합 작업자 세션 유휴 만료 — 근무 중 재로그인 방지.

현장에서 저울로 계량하는 동안(로컬 저울 통신, 서버 요청 없음) 5분 유휴로 세션이 끊겨
저장 시 작업자 입력 화면으로 튕기던 문제. 유휴를 근무 시간 단위로 늘린 것을 검증한다.
(상한은 세션 쿠키 수명과 맞춘 8h — 값이 바뀌어도 깨지지 않게 IDLE_TIMEOUT_SECONDS 기준으로 검증)
"""

from __future__ import annotations

import datetime as dt

from src import blend_session as bs

IDLE = bs.IDLE_TIMEOUT_SECONDS


class _FakeReq:
    def __init__(self):
        self.session = {}


def _at(base, **delta):
    return lambda: base + dt.timedelta(**delta)


def test_blend_session_survives_past_old_5min(monkeypatch):
    req = _FakeReq()
    base = dt.datetime(2026, 7, 6, 9, 0, 0, tzinfo=dt.timezone.utc)
    monkeypatch.setattr(bs, "_utc_now", lambda: base)
    bs.login_worker_session(req, "홍길동")

    # 구 만료(5분)를 훌쩍 넘긴 6분 경과, 그리고 유휴 상한 직전까지 유효해야 한다(터치 없이도).
    monkeypatch.setattr(bs, "_utc_now", _at(base, minutes=6))
    assert bs.current_blend_worker(req) == "홍길동"
    monkeypatch.setattr(bs, "_utc_now", _at(base, seconds=IDLE - 60))
    assert bs.current_blend_worker(req) == "홍길동"


def test_blend_session_expires_after_long_idle(monkeypatch):
    req = _FakeReq()
    base = dt.datetime(2026, 7, 6, 9, 0, 0, tzinfo=dt.timezone.utc)
    monkeypatch.setattr(bs, "_utc_now", lambda: base)
    bs.login_worker_session(req, "홍길동")

    # 유휴 상한을 넘기면 만료.
    monkeypatch.setattr(bs, "_utc_now", _at(base, seconds=IDLE + 3600))
    assert bs.current_blend_worker(req) is None


def test_touch_heartbeat_extends_session(monkeypatch):
    req = _FakeReq()
    base = dt.datetime(2026, 7, 6, 9, 0, 0, tzinfo=dt.timezone.utc)
    monkeypatch.setattr(bs, "_utc_now", lambda: base)
    bs.login_worker_session(req, "홍길동")

    # 상한 직전 하트비트(touch)로 last_activity 갱신 → 그 시점부터 다시 상한만큼 유효.
    near = IDLE - 60
    monkeypatch.setattr(bs, "_utc_now", _at(base, seconds=near))
    bs.touch_worker_session(req)
    monkeypatch.setattr(bs, "_utc_now", _at(base, seconds=near + IDLE - 60))
    assert bs.current_blend_worker(req) == "홍길동"
