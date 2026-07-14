"""감사 F-10: 로그인 엔드포인트의 교차 출처 POST 차단.

로그인 3개는 CSRF 토큰 검사에서 면제돼 있다(토큰을 받기 전에 호출되므로). 그 틈으로
악성 페이지가 **공격자 자신의 자격증명으로** 피해자를 강제 로그인시킬 수 있다 — 피해자의
쿠키가 필요 없는 공격이라 SameSite=strict 로도 막히지 않는다. Origin 검사로 막는다.
"""

from __future__ import annotations

import importlib

import pytest

LOGIN_ENDPOINTS = [
    ("/api/auth/management-login", {"username": "admin", "password": "admin"}),
    ("/api/attendance/login", {"emp_id": "171013", "password": "irrelevant"}),
    ("/api/blend/session/login", {"worker": "홍길동"}),
]


def _client():
    import src.config as cfg
    import src.main as mainmod

    importlib.reload(cfg)
    importlib.reload(mainmod)
    from fastapi.testclient import TestClient

    return TestClient(mainmod.app)


@pytest.mark.parametrize("path,payload", LOGIN_ENDPOINTS)
def test_cross_origin_login_is_blocked(path, payload):
    """악성 사이트에서 온 로그인 POST 는 403 — 자격증명이 맞아도 처리되지 않는다."""
    client = _client()
    res = client.post(path, json=payload, headers={"origin": "https://evil.example.com"})
    assert res.status_code == 403
    assert res.json()["detail"] == "CROSS_ORIGIN_LOGIN_BLOCKED"


@pytest.mark.parametrize("path,payload", LOGIN_ENDPOINTS)
def test_same_origin_login_passes_the_origin_gate(path, payload):
    """정상 로그인(같은 출처)은 이 검사에 걸리지 않는다 — 막히면 현장이 못 들어온다."""
    client = _client()
    res = client.post(path, json=payload, headers={"origin": "http://testserver"})
    # 자격증명 성공/실패(200/401/400)는 이 테스트의 관심사가 아니다. 403 만 아니면 된다.
    assert res.status_code != 403, res.text


@pytest.mark.parametrize("path,payload", LOGIN_ENDPOINTS)
def test_missing_origin_is_allowed(path, payload):
    """Origin 없는 요청(비브라우저 클라이언트)은 교차 출처 공격이 아니므로 통과."""
    client = _client()
    res = client.post(path, json=payload)
    assert res.status_code != 403, res.text


def test_non_login_post_is_untouched_by_the_origin_gate():
    """로그인 외 경로는 이 미들웨어가 건드리지 않는다(CSRF 토큰 검사가 계속 담당)."""
    client = _client()
    res = client.post("/api/workers", json={"name": "누구"},
                      headers={"origin": "https://evil.example.com"})
    # CSRF 미들웨어가 평문으로 거절하므로 JSON 파싱하지 않는다 — 우리 마커만 없으면 된다.
    assert "CROSS_ORIGIN_LOGIN_BLOCKED" not in res.text


def test_trusted_origin_env_allows_a_proxied_domain(monkeypatch):
    """리버스 프록시가 Host 를 내부 주소로 바꿔도 IRMS_TRUSTED_ORIGINS 로 복구할 수 있다.

    이 탈출구가 없으면 프록시 설정 하나 때문에 외부 로그인이 전면 차단되고,
    복구하려면 코드를 고쳐 배포해야 한다.
    """
    monkeypatch.setenv("IRMS_TRUSTED_ORIGINS", "brm.example.com, other.example.com")
    client = _client()
    res = client.post("/api/auth/management-login",
                      json={"username": "admin", "password": "admin"},
                      headers={"origin": "https://brm.example.com"})
    assert res.status_code != 403, res.text
