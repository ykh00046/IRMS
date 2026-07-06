"""인증 단순화 — 단일 admin 계정 + 로그인 + 기타 계정 비활성화.

Design: 관리자는 admin 하나, 작업자는 이름 입력(근태 제외).
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


def test_admin_delete_user_removes_row():
    """삭제 API는 비활성화가 아니라 실제로 행을 제거해 목록에서 사라져야 한다."""
    client = _client()
    res = client.post(
        "/api/auth/management-login", json={"username": "admin", "password": "admin"}
    )
    assert res.status_code == 200
    tok = client.cookies.get("csrftoken")
    headers = {"x-csrftoken": tok} if tok else {}

    res = client.post(
        "/api/admin/users",
        json={
            "username": "todelete1",
            "display_name": "삭제 대상",
            "access_level": "operator",
            "password": "Passw0rd!23",
        },
        headers=headers,
    )
    assert res.status_code == 200
    user_id = res.json()["user"]["id"]

    res = client.delete(f"/api/admin/users/{user_id}", headers=headers)
    assert res.status_code == 200

    from src.db import get_connection

    with get_connection() as conn:
        row = conn.execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone()
    assert row is None

    usernames = [item["username"] for item in client.get("/api/admin/users").json()["items"]]
    assert "todelete1" not in usernames


def test_admin_delete_user_with_legacy_chat_table():
    """레거시 chat_messages(FK→users) 잔재 DB: 500 대신 409, 마이그레이션 정리 후 삭제 가능."""
    client = _client()
    res = client.post(
        "/api/auth/management-login", json={"username": "admin", "password": "admin"}
    )
    assert res.status_code == 200
    tok = client.cookies.get("csrftoken")
    headers = {"x-csrftoken": tok} if tok else {}

    uname = "todelete_" + uuid.uuid4().hex[:8]
    res = client.post(
        "/api/admin/users",
        json={
            "username": uname,
            "display_name": "잔재 참조 대상",
            "access_level": "operator",
            "password": "Passw0rd!23",
        },
        headers=headers,
    )
    assert res.status_code == 200
    user_id = res.json()["user"]["id"]

    from src.db import get_connection
    from src.db.migrations import apply_schema_migrations

    # 제거된 채팅 기능의 테이블이 남아 사용자를 FK 로 참조하는 상황 재현
    with get_connection() as conn:
        conn.execute("DROP TABLE IF EXISTS chat_messages")
        conn.execute(
            "CREATE TABLE chat_messages ("
            "id INTEGER PRIMARY KEY, created_by_user_id INTEGER REFERENCES users(id))"
        )
        conn.execute(
            "INSERT INTO chat_messages (created_by_user_id) VALUES (?)", (user_id,)
        )
        conn.commit()

    # FK 위반은 500 이 아니라 409 + 안내 메시지
    res = client.delete(f"/api/admin/users/{user_id}", headers=headers)
    assert res.status_code == 409

    # 마이그레이션이 잔재 테이블을 DROP → 이후 삭제 정상
    with get_connection() as conn:
        conn.execute(
            "DELETE FROM schema_migrations WHERE name = 'drop_orphan_chat_tables'"
        )
        apply_schema_migrations(conn)
        conn.commit()
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='chat_messages'"
        ).fetchone() is None


def test_legacy_admin_level_collapsed_to_manager():
    """권한 2단계: 남아있는 access_level='admin' 계정을 마이그레이션이 manager 로 승격."""
    client = _client()
    res = client.post(
        "/api/auth/management-login", json={"username": "admin", "password": "admin"}
    )
    tok = client.cookies.get("csrftoken")
    headers = {"x-csrftoken": tok} if tok else {}
    res = client.post(
        "/api/admin/users",
        json={
            "username": "legacyadmin_" + uuid.uuid4().hex[:8],
            "display_name": "구 관리자",
            "access_level": "manager",
            "password": "Passw0rd!23",
        },
        headers=headers,
    )
    assert res.status_code == 200
    uid = res.json()["user"]["id"]

    from src.db import get_connection
    from src.db.migrations import apply_schema_migrations

    with get_connection() as conn:
        # 구 3단계 잔존값 재현
        conn.execute("UPDATE users SET access_level = 'admin' WHERE id = ?", (uid,))
        conn.execute(
            "DELETE FROM schema_migrations WHERE name = 'collapse_admin_into_manager'"
        )
        apply_schema_migrations(conn)
        conn.commit()
        level = conn.execute(
            "SELECT access_level FROM users WHERE id = ?", (uid,)
        ).fetchone()["access_level"]
    assert level == "manager"


def test_create_user_rejects_admin_level():
    """권한 2단계: 사용자 생성 시 'admin' 등급은 더 이상 허용되지 않는다(422)."""
    client = _client()
    res = client.post(
        "/api/auth/management-login", json={"username": "admin", "password": "admin"}
    )
    tok = client.cookies.get("csrftoken")
    headers = {"x-csrftoken": tok} if tok else {}
    res = client.post(
        "/api/admin/users",
        json={
            "username": "wannabeadmin",
            "display_name": "관리자 지망",
            "access_level": "admin",
            "password": "Passw0rd!23",
        },
        headers=headers,
    )
    assert res.status_code == 422
