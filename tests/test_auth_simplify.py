"""인증 단순화 — 단일 admin 계정 + 로그인 + 기타 계정 비활성화.

Design: 관리자는 admin 하나, 작업자는 이름 입력(근태 제외).
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


def test_admin_account_exists_and_is_admin():
    _client()  # triggers init_db + migrations
    from src.db import get_connection

    with get_connection() as conn:
        row = conn.execute(
            "SELECT access_level, is_active FROM users WHERE username = 'admin'"
        ).fetchone()
    assert row is not None
    assert row["access_level"] == "admin"
    assert row["is_active"] == 1


def test_admin_login_access_and_deactivate_others():
    """admin/admin 로그인 → 관리 접근 → 기타 계정 비활성화(admin 유지). 로그인 1회."""
    client = _client()
    res = client.post(
        "/api/auth/management-login", json={"username": "admin", "password": "admin"}
    )
    assert res.status_code == 200
    # 단일 admin 으로 관리 화면(사용자 목록) 접근
    assert client.get("/api/admin/users").status_code == 200
    # 기타 계정 일괄 비활성화
    tok = client.cookies.get("csrftoken")
    headers = {"x-csrftoken": tok} if tok else {}
    res = client.post("/api/admin/deactivate-others", headers=headers)
    assert res.status_code == 200
    from src.db import get_connection

    with get_connection() as conn:
        admin = conn.execute(
            "SELECT is_active FROM users WHERE username = 'admin'"
        ).fetchone()
        others = conn.execute(
            "SELECT COUNT(*) AS n FROM users WHERE username != 'admin' AND is_active = 1"
        ).fetchone()
    assert admin["is_active"] == 1
    assert others["n"] == 0
