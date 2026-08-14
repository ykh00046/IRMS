"""책임자 로그인 후 '보던 화면으로 복귀' — 헤더 링크가 next 를 넘기는지.

인프라(?next= 검증·bindLoginForm 리다이렉트)는 이미 있었는데 헤더의 로그인
버튼만 next 없이 /management/login 을 가리켜, 어디서 로그인하든 레시피 관리로
떨어졌다(현장 지적 2026-08-14).
"""

from __future__ import annotations

import importlib


def _client():
    import src.config as cfg
    import src.main as mainmod

    importlib.reload(cfg)
    importlib.reload(mainmod)
    from fastapi.testclient import TestClient

    return TestClient(mainmod.app)


def test_header_login_link_carries_current_path():
    client = _client()
    html = client.get("/viscosity").text
    assert "/management/login?next=/viscosity" in html
    html = client.get("/materials").text
    assert "/management/login?next=/materials" in html


def test_login_page_redirects_back_after_manager_session():
    client = _client()
    client.get("/api/blend/records")
    tok = client.cookies.get("csrftoken")
    res = client.post(
        "/api/auth/management-login",
        json={"username": "admin", "password": "admin"},
        headers={"x-csrftoken": tok} if tok else {},
    )
    assert res.status_code == 200
    # 이미 로그인된 상태로 로그인 페이지에 next 를 들고 오면 그 화면으로 즉시 복귀.
    res = client.get(
        "/management/login", params={"next": "/viscosity"}, follow_redirects=False
    )
    assert res.status_code == 303
    assert res.headers["location"] == "/viscosity"
    # 외부 URL 은 기본값으로 방어(open redirect 금지).
    res = client.get(
        "/management/login",
        params={"next": "//evil.example"},
        follow_redirects=False,
    )
    assert res.headers["location"] == "/management"
