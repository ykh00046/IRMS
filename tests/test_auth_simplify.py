"""레거시 admin 폴백 계정 검증.

현행 모델: 책임자는 이용자 명단(workers)에서 지정된 사람이 각자 비밀번호로
로그인한다(test_workers.py). admin/admin 은 부트스트랩·비상용 폴백이다.

2026-08-08: users 테이블 CRUD API(/admin/users 4종, /admin/users/{id}/password,
/admin/deactivate-others)를 제거했다 — 인증 단순화(2026-06-24) 이후 어떤 화면도
부르지 않는 채 남아 있었고, deactivate-others 는 UI·확인창 없이 admin 외 전 계정을
일괄 비활성화하는 파괴적 경로였다. 그 API 를 쓰던 테스트는 DB 를 직접 다루는 형태로
바꿨다(검증 대상인 마이그레이션·권한 등급 규칙은 그대로 남는다).
"""

from __future__ import annotations

import importlib
import uuid


def _client():
    import src.config as cfg
    import src.main as mainmod

    importlib.reload(cfg)
    importlib.reload(mainmod)
    from fastapi.testclient import TestClient

    return TestClient(mainmod.app)


def test_admin_account_exists_as_manager():
    """권한 2단계 통합: admin 계정은 최상위 '책임자(manager)'로 존재한다(구 admin 흡수)."""
    _client()  # triggers init_db + migrations
    from src.db import get_connection

    with get_connection() as conn:
        row = conn.execute(
            "SELECT access_level, is_active FROM users WHERE username = 'admin'"
        ).fetchone()
    assert row is not None
    assert row["access_level"] == "manager"
    assert row["is_active"] == 1


def test_admin_login_grants_manager_access():
    """admin/admin 로그인 → 책임자 전용 화면·API 접근."""
    client = _client()
    res = client.post(
        "/api/auth/management-login", json={"username": "admin", "password": "admin"}
    )
    assert res.status_code == 200
    # 책임자 전용 API(감사 로그)에 접근된다.
    assert client.get("/api/admin/audit-logs").status_code == 200
    # 책임자 전용 화면도 로그인 리다이렉트 없이 열린다.
    page = client.get("/admin/users")
    assert page.status_code == 200


def test_removed_user_crud_endpoints_are_gone():
    """제거한 계정 CRUD·일괄 비활성화가 되살아나지 않는다(회귀 방지)."""
    client = _client()
    client.post("/api/auth/management-login", json={"username": "admin", "password": "admin"})
    tok = client.cookies.get("csrftoken")
    headers = {"x-csrftoken": tok} if tok else {}

    assert client.get("/api/admin/users").status_code == 404
    assert client.post("/api/admin/deactivate-others", headers=headers).status_code == 404
    assert client.post(
        "/api/admin/users", json={"username": "x", "display_name": "x",
                                  "access_level": "manager", "password": "Passw0rd!23"},
        headers=headers,
    ).status_code == 404
    assert client.delete("/api/admin/users/1", headers=headers).status_code == 404


def test_legacy_admin_level_collapsed_to_manager():
    """권한 2단계: 남아있는 access_level='admin' 계정을 마이그레이션이 manager 로 승격."""
    _client()
    from src.db import get_connection
    from src.db.migrations import apply_schema_migrations

    username = "legacyadmin_" + uuid.uuid4().hex[:8]
    with get_connection() as conn:
        # 구 3단계 잔존값 재현 — 계정 생성 API 가 없으므로 DB 에 직접 넣는다.
        conn.execute(
            "INSERT INTO users (username, display_name, access_level, password_hash, is_active)"
            " VALUES (?, ?, 'admin', 'x', 1)",
            (username, "구 관리자"),
        )
        conn.execute(
            "DELETE FROM schema_migrations WHERE name = 'collapse_admin_into_manager'"
        )
        apply_schema_migrations(conn)
        conn.commit()
        level = conn.execute(
            "SELECT access_level FROM users WHERE username = ?", (username,)
        ).fetchone()["access_level"]
    assert level == "manager"
