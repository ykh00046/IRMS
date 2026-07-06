"""배합 작업자 세션 유휴 만료 — 근무 중 재로그인 방지.

현장에서 저울로 계량하는 동안(로컬 저울 통신, 서버 요청 없음) 5분 유휴로 세션이 끊겨
저장 시 작업자 입력 화면으로 튕기던 문제. 유휴를 근무 시간 단위(12h)로 늘린 것을 검증.
"""

from __future__ import annotations

import datetime as dt

from src import blend_session as bs


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

    # 구 만료(5분)를 훌쩍 넘긴 6분·11시간 경과에도 유효해야 한다(터치 없이도).
    monkeypatch.setattr(bs, "_utc_now", _at(base, minutes=6))
    assert bs.current_blend_worker(req) == "홍길동"
    monkeypatch.setattr(bs, "_utc_now", _at(base, hours=11))
    assert bs.current_blend_worker(req) == "홍길동"


def test_blend_session_expires_after_long_idle(monkeypatch):
    req = _FakeReq()
    base = dt.datetime(2026, 7, 6, 9, 0, 0, tzinfo=dt.timezone.utc)
    monkeypatch.setattr(bs, "_utc_now", lambda: base)
    bs.login_worker_session(req, "홍길동")

    # 12시간 유휴 상한을 넘기면(13h) 만료.
    monkeypatch.setattr(bs, "_utc_now", _at(base, hours=13))
    assert bs.current_blend_worker(req) is None


def test_touch_heartbeat_extends_session(monkeypatch):
    req = _FakeReq()
    base = dt.datetime(2026, 7, 6, 9, 0, 0, tzinfo=dt.timezone.utc)
    monkeypatch.setattr(bs, "_utc_now", lambda: base)
    bs.login_worker_session(req, "홍길동")

    # 11h 시점 하트비트(touch)로 last_activity 갱신 → 이후 11h(총 22h) 더 유효.
    monkeypatch.setattr(bs, "_utc_now", _at(base, hours=11))
    bs.touch_worker_session(req)
    monkeypatch.setattr(bs, "_utc_now", _at(base, hours=22))
    assert bs.current_blend_worker(req) == "홍길동"
