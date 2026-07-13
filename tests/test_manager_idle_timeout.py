"""책임자 세션 유휴 만료(15분) — 공용 PC 에서 권한이 열린 채 방치되는 것 차단.

배합 작업자 세션(이름·무비번)은 계량 중 끊기면 안 되므로 이 규칙의 대상이 아니다.
검증 항목:
  (a) 로그인 직후에는 책임자 API 가 정상 동작
  (b) 유휴 시간이 지나면 401 (세션 파기)
  (c) 활동이 있으면 유휴 카운트가 리셋돼 만료되지 않음(슬라이딩)
  (d) 배합 작업자 세션은 책임자 유휴 만료의 영향을 받지 않는다
  (e) 타임아웃 0 이하면 유휴 만료 비활성
"""

from __future__ import annotations

import importlib
import time

import src.auth as auth


def _client():
    import src.config as cfg
    import src.main as mainmod

    importlib.reload(cfg)
    importlib.reload(mainmod)
    from fastapi.testclient import TestClient

    return TestClient(mainmod.app)


def _login_manager(client):
    res = client.post(
        "/api/auth/management-login", json={"username": "admin", "password": "admin"}
    )
    assert res.status_code == 200
    tok = client.cookies.get("csrftoken")
    return {"x-csrftoken": tok} if tok else {}


def _me(client):
    return client.get("/api/auth/me")


def test_manager_session_alive_right_after_login():
    client = _client()
    _login_manager(client)
    assert _me(client).status_code == 200


def test_manager_session_expires_after_idle(monkeypatch):
    """마지막 활동 후 유휴 시간이 지나면 책임자 세션이 끊긴다."""
    client = _client()
    _login_manager(client)
    assert _me(client).status_code == 200

    # 실제로 15분을 기다릴 수 없으므로 유휴 판정 시계(_now)만 앞으로 돌린다.
    # (전역 time.time 을 밀면 세션 쿠키 서명까지 만료돼 엉뚱한 401 이 난다.)
    later = time.time() + auth.config.MANAGER_IDLE_TIMEOUT_SECONDS + 60
    monkeypatch.setattr(auth, "_now", lambda: later)
    assert _me(client).status_code == 401


def test_manager_session_slides_on_activity(monkeypatch):
    """활동이 있으면 유휴 카운트가 리셋된다 — 작업 중에는 끊기지 않는다."""
    client = _client()
    _login_manager(client)
    timeout = auth.config.MANAGER_IDLE_TIMEOUT_SECONDS

    now = [time.time()]
    monkeypatch.setattr(auth, "_now", lambda: now[0])
    # 타임아웃 직전마다 요청 → 계속 살아있어야 한다(총 경과는 타임아웃의 3배)
    for _ in range(3):
        now[0] += timeout - 10
        assert _me(client).status_code == 200
    # 마지막 활동 이후 타임아웃을 넘기면 만료
    now[0] += timeout + 10
    assert _me(client).status_code == 401


def test_blend_worker_session_unaffected_by_manager_idle(monkeypatch):
    """책임자 유휴 만료가 배합 작업자 세션까지 끊으면 안 된다(계량 중 데이터 유실 방지)."""
    client = _client()
    _login_manager(client)
    headers = {"x-csrftoken": client.cookies.get("csrftoken") or ""}
    # 작업자 세션 로그인은 등록된 이름만 허용 → 먼저 명단에 등록
    reg = client.post("/api/workers", json={"name": "유휴테스트작업자"}, headers=headers)
    assert reg.status_code in (200, 201, 409)
    res = client.post(
        "/api/blend/session/login", json={"worker": "유휴테스트작업자"}, headers=headers
    )
    assert res.status_code in (200, 201)
    assert client.get("/api/blend/session/me").status_code == 200

    later = time.time() + auth.config.MANAGER_IDLE_TIMEOUT_SECONDS + 60
    monkeypatch.setattr(auth, "_now", lambda: later)
    assert _me(client).status_code == 401                      # 책임자만 끊기고
    assert client.get("/api/blend/session/me").status_code == 200  # 작업자는 유지


def test_idle_timeout_can_be_disabled(monkeypatch):
    """타임아웃 0 이하 = 유휴 만료 비활성(환경변수로 끌 수 있어야 한다)."""
    client = _client()
    _login_manager(client)
    monkeypatch.setattr(auth.config, "MANAGER_IDLE_TIMEOUT_SECONDS", 0)
    later = time.time() + 10 * 60 * 60
    monkeypatch.setattr(auth, "_now", lambda: later)
    assert _me(client).status_code == 200
