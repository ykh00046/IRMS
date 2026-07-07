"""작업자 명단(worker registry) 단위 + 라우트 테스트.

Design: 인증 단순화 — 근태 제외 작업자는 이름 입력, 처음 보면 등록 확인.
"""

from __future__ import annotations

import sqlite3

import pytest

from src.services import worker_service as ws


def _make_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE workers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            is_manager INTEGER NOT NULL DEFAULT 0,
            password_hash TEXT,
            session_token TEXT
        )
        """
    )
    return conn


def test_register_new_then_existing():
    conn = _make_db()
    r1 = ws.register(conn, "홍길동", "2026-06-24")
    assert r1 == {"name": "홍길동", "created": True}
    r2 = ws.register(conn, "홍길동", "2026-06-24")
    assert r2 == {"name": "홍길동", "created": False}
    assert ws.worker_names(conn) == ["홍길동"]


def test_register_trims_and_rejects_empty():
    conn = _make_db()
    assert ws.register(conn, "  김작업  ", "t")["name"] == "김작업"
    with pytest.raises(ValueError):
        ws.register(conn, "   ", "t")


def test_exists_active_only():
    conn = _make_db()
    ws.register(conn, "이활성", "t")
    assert ws.exists(conn, "이활성") is True
    assert ws.exists(conn, "없는사람") is False


def test_deactivate_then_reregister_reactivates():
    conn = _make_db()
    ws.register(conn, "박철수", "t")
    wid = conn.execute("SELECT id FROM workers WHERE name='박철수'").fetchone()["id"]
    ws.set_active(conn, wid, False)
    assert ws.exists(conn, "박철수") is False
    # 같은 이름 재등록 → created False + 재활성화
    assert ws.register(conn, "박철수", "t")["created"] is False
    assert ws.exists(conn, "박철수") is True


def test_rename():
    conn = _make_db()
    ws.register(conn, "오타", "t")
    wid = conn.execute("SELECT id FROM workers WHERE name='오타'").fetchone()["id"]
    ws.rename(conn, wid, "정정")
    assert ws.worker_names(conn) == ["정정"]


def test_manager_designation_flow():
    """이용자를 책임자로 지정(비밀번호) → 목록·명단 반영 → 해제 → 원복."""
    conn = _make_db()
    ws.register(conn, "김책임", "t")
    ws.register(conn, "이현장", "t")
    wid = conn.execute("SELECT id FROM workers WHERE name='김책임'").fetchone()["id"]

    # 지정 전: 아무도 책임자 아님
    assert ws.manager_names(conn) == []
    assert ws.active_manager_count(conn) == 0
    assert all(not w["is_manager"] for w in ws.list_workers(conn))

    # 지정(비밀번호 해시)
    ws.set_manager(conn, wid, "hashed-pw")
    assert ws.manager_names(conn) == ["김책임"]
    assert ws.active_manager_count(conn) == 1
    by_name = {w["name"]: w for w in ws.list_workers(conn)}
    assert by_name["김책임"]["is_manager"] is True
    assert by_name["이현장"]["is_manager"] is False
    assert ws.get_worker(conn, wid)["is_manager"] is True

    # 해제 → 비밀번호·책임자 제거
    ws.revoke_manager(conn, wid)
    assert ws.manager_names(conn) == []
    assert ws.get_worker(conn, wid)["is_manager"] is False
    row = conn.execute("SELECT password_hash FROM workers WHERE id=?", (wid,)).fetchone()
    assert row["password_hash"] is None


def test_manager_needs_password_to_count():
    """is_manager=1 이라도 비밀번호(password_hash)가 없으면 로그인 가능한 책임자가 아니다."""
    conn = _make_db()
    ws.register(conn, "반쪽", "t")
    wid = conn.execute("SELECT id FROM workers WHERE name='반쪽'").fetchone()["id"]
    conn.execute("UPDATE workers SET is_manager = 1 WHERE id = ?", (wid,))  # 비번 없이 플래그만
    assert ws.manager_names(conn) == []
    assert ws.list_workers(conn)[0]["is_manager"] is False


def test_delete_worker_and_has_records_guard():
    conn = _make_db()
    # 배합 기록 테이블(삭제 안전장치 검사용)
    conn.execute("CREATE TABLE blend_records (id INTEGER PRIMARY KEY, worker TEXT NOT NULL)")
    ws.register(conn, "오타이름", "t")
    ws.register(conn, "기록보유", "t")
    conn.execute("INSERT INTO blend_records (worker) VALUES ('기록보유')")

    assert ws.has_blend_records(conn, "기록보유") is True
    assert ws.has_blend_records(conn, "오타이름") is False

    wid = conn.execute("SELECT id FROM workers WHERE name='오타이름'").fetchone()["id"]
    ws.delete_worker(conn, wid)
    assert conn.execute("SELECT 1 FROM workers WHERE id=?", (wid,)).fetchone() is None


def test_delete_worker_blocked_for_manager_and_records():
    """책임자·배합 기록 있는 이름은 삭제가 400 으로 막히고 비활성화 안내."""
    import importlib
    import uuid

    import src.config as cfg
    import src.main as mainmod

    importlib.reload(cfg)
    importlib.reload(mainmod)
    from fastapi.testclient import TestClient

    client = TestClient(mainmod.app)
    client.post("/api/auth/management-login", json={"username": "admin", "password": "admin"})
    tok = client.cookies.get("csrftoken")
    headers = {"x-csrftoken": tok} if tok else {}

    name = "삭제대상_" + uuid.uuid4().hex[:6]
    mgr = "책임삭제_" + uuid.uuid4().hex[:6]
    client.post("/api/workers", json={"name": name}, headers=headers)
    client.post("/api/workers", json={"name": mgr}, headers=headers)
    from src.db import get_connection
    with get_connection() as conn:
        mid = conn.execute("SELECT id FROM workers WHERE name=?", (mgr,)).fetchone()["id"]
        did = conn.execute("SELECT id FROM workers WHERE name=?", (name,)).fetchone()["id"]
    client.post(f"/api/workers/{mid}/manager", json={"password": "pw12345"}, headers=headers)

    # 책임자는 삭제 불가
    res = client.request("DELETE", f"/api/workers/{mid}", headers=headers)
    assert res.status_code == 400

    # 일반 이용자(기록 없음)는 삭제 가능
    res = client.request("DELETE", f"/api/workers/{did}", headers=headers)
    assert res.status_code == 200
    with get_connection() as conn:
        assert conn.execute("SELECT 1 FROM workers WHERE id=?", (did,)).fetchone() is None


def test_worker_routes_open():
    import importlib

    import src.config as cfg
    import src.main as mainmod

    importlib.reload(cfg)
    importlib.reload(mainmod)
    from fastapi.testclient import TestClient

    client = TestClient(mainmod.app)
    # 조회 무로그인 가능
    assert client.get("/api/workers").status_code == 200
    # 관리용 전체 목록은 admin 필요 → 비로그인 차단
    assert client.get("/api/workers/all").status_code in (401, 403)


def test_name_based_manager_login_flow():
    """이름 기반 책임자: admin(폴백)으로 지정 → 그 이름+비번으로 로그인 → 관리 접근."""
    import importlib
    import uuid

    import src.config as cfg
    import src.main as mainmod

    importlib.reload(cfg)
    importlib.reload(mainmod)
    from fastapi.testclient import TestClient

    client = TestClient(mainmod.app)
    name = "책임_" + uuid.uuid4().hex[:8]

    # 1) 레거시 admin 으로 로그인(부트스트랩)
    res = client.post("/api/auth/management-login", json={"username": "admin", "password": "admin"})
    assert res.status_code == 200
    tok = client.cookies.get("csrftoken")
    headers = {"x-csrftoken": tok} if tok else {}

    # 2) 이용자 등록 + 책임자 지정(개인 비밀번호)
    res = client.post("/api/workers", json={"name": name}, headers=headers)
    assert res.status_code == 200
    from src.db import get_connection
    with get_connection() as conn:
        wid = conn.execute("SELECT id FROM workers WHERE name = ?", (name,)).fetchone()["id"]
    res = client.post(f"/api/workers/{wid}/manager", json={"password": "field123"}, headers=headers)
    assert res.status_code == 200

    # 3) 로그아웃 후, 그 이름 + 비밀번호로 로그인
    client.post("/api/auth/logout", headers=headers)
    res = client.post("/api/auth/management-login", json={"username": name, "password": "field123"})
    assert res.status_code == 200, res.text
    assert res.json()["user"]["access_level"] == "manager"
    assert res.json()["user"]["display_name"] == name

    # 4) 관리 접근 가능(작업자 관리 목록)
    assert client.get("/api/workers/all").status_code == 200

    # 5) 틀린 비밀번호는 거부
    client.post("/api/auth/logout")
    bad = client.post("/api/auth/management-login", json={"username": name, "password": "wrong"})
    assert bad.status_code == 401
