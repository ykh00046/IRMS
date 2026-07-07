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
    assert r1 == {"name": "홍길동", "created": True, "reactivated": False}
    r2 = ws.register(conn, "홍길동", "2026-06-24")
    assert r2 == {"name": "홍길동", "created": False, "reactivated": False}
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


def test_name_validation_blocks_obvious_mistakes():
    """공백('김 민호')·자모 낱글자('ㄱ 머ㅏ미')·특수문자·1자 이름은 등록/개명 차단.
    동명이인 구분 숫자(김민호3)와 영문 이름은 허용."""
    conn = _make_db()
    # 허용
    assert ws.register(conn, "김민호", "t")["created"] is True
    assert ws.register(conn, "김민호3", "t")["created"] is True
    assert ws.register(conn, "ParkLead", "t")["created"] is True
    # 차단 — 내부 공백
    with pytest.raises(ValueError, match="공백"):
        ws.register(conn, "김 민호", "t")
    # 차단 — 자모 낱글자 포함
    with pytest.raises(ValueError):
        ws.register(conn, "ㄱ", "t")
    with pytest.raises(ValueError):
        ws.register(conn, "머ㅏ미", "t")
    # 차단 — 특수문자·1자
    with pytest.raises(ValueError):
        ws.register(conn, "김민호!", "t")
    with pytest.raises(ValueError):
        ws.register(conn, "김", "t")
    # rename 도 동일 규칙
    wid = conn.execute("SELECT id FROM workers WHERE name='김민호'").fetchone()["id"]
    with pytest.raises(ValueError):
        ws.rename(conn, wid, "김 민호")


def test_reactivation_does_not_revive_manager():
    """비활성화된 책임자를 이름 재등록으로 살려도 책임자 권한·비밀번호는 부활하지 않는다."""
    conn = _make_db()
    ws.register(conn, "복귀책임", "t")
    wid = conn.execute("SELECT id FROM workers WHERE name='복귀책임'").fetchone()["id"]
    ws.set_manager(conn, wid, "hashed-pw")
    ws.set_active(conn, wid, False)

    r = ws.register(conn, "복귀책임", "t")  # 무인증 등록 경로로 재활성화
    assert r["reactivated"] is True
    row = conn.execute(
        "SELECT is_active, is_manager, password_hash, session_token FROM workers WHERE id=?",
        (wid,),
    ).fetchone()
    assert row["is_active"] == 1
    assert row["is_manager"] == 0          # 책임자 권한 미부활
    assert row["password_hash"] is None    # 비밀번호 제거
    assert row["session_token"] is None


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

    name = "삭제대상" + uuid.uuid4().hex[:6]
    mgr = "책임삭제" + uuid.uuid4().hex[:6]
    client.post("/api/workers", json={"name": name}, headers=headers)
    client.post("/api/workers", json={"name": mgr}, headers=headers)
    from src.db import get_connection
    with get_connection() as conn:
        mid = conn.execute("SELECT id FROM workers WHERE name=?", (mgr,)).fetchone()["id"]
        did = conn.execute("SELECT id FROM workers WHERE name=?", (name,)).fetchone()["id"]
    client.post(f"/api/workers/{mid}/manager", json={"password": "pw123456"}, headers=headers)

    # 책임자는 삭제 불가
    res = client.request("DELETE", f"/api/workers/{mid}", headers=headers)
    assert res.status_code == 400

    # 일반 이용자(기록 없음)는 삭제 가능
    res = client.request("DELETE", f"/api/workers/{did}", headers=headers)
    assert res.status_code == 200
    with get_connection() as conn:
        assert conn.execute("SELECT 1 FROM workers WHERE id=?", (did,)).fetchone() is None


def test_manager_password_strength_enforced():
    """책임자 비밀번호는 8자 이상 + 반복/연속 차단(근태와 동일 수준)."""
    import importlib
    import uuid

    import src.config as cfg
    import src.main as mainmod

    importlib.reload(cfg)
    importlib.reload(mainmod)
    from fastapi.testclient import TestClient

    from src.db import get_connection

    client = TestClient(mainmod.app)
    client.post("/api/auth/management-login", json={"username": "admin", "password": "admin"})
    tok = client.cookies.get("csrftoken")
    headers = {"x-csrftoken": tok} if tok else {}
    name = "강도검사" + uuid.uuid4().hex[:6]
    client.post("/api/workers", json={"name": name}, headers=headers)
    with get_connection() as conn:
        wid = conn.execute("SELECT id FROM workers WHERE name=?", (name,)).fetchone()["id"]

    for bad in ("short7!", "11111111", "12345678"):  # 7자·반복·연속
        res = client.post(f"/api/workers/{wid}/manager", json={"password": bad}, headers=headers)
        assert res.status_code == 422, f"{bad!r} 가 거부되지 않음"
    ok = client.post(f"/api/workers/{wid}/manager", json={"password": "goodpw12"}, headers=headers)
    assert ok.status_code == 200


def test_worker_name_validation_route():
    """POST /workers 라우트에서 공백·자모 이름이 400 + 한글 안내 메시지."""
    import importlib

    import src.config as cfg
    import src.main as mainmod

    importlib.reload(cfg)
    importlib.reload(mainmod)
    from fastapi.testclient import TestClient

    client = TestClient(mainmod.app)
    client.get("/api/workers")  # csrf 쿠키 확보
    tok = client.cookies.get("csrftoken")
    headers = {"x-csrftoken": tok} if tok else {}

    res = client.post("/api/workers", json={"name": "김 민호"}, headers=headers)
    assert res.status_code == 400 and "공백" in res.json()["detail"]
    res = client.post("/api/workers", json={"name": "ㄱ 머ㅏ미"}, headers=headers)
    assert res.status_code == 400


def test_manager_self_change_password_flow():
    """책임자 본인이 현재 비밀번호 확인 후 직접 비밀번호 변경 → 새 비번으로 재로그인."""
    import importlib
    import uuid

    import src.config as cfg
    import src.main as mainmod

    importlib.reload(cfg)
    importlib.reload(mainmod)
    from fastapi.testclient import TestClient

    from src.db import get_connection

    client = TestClient(mainmod.app)
    name = "본인변경" + uuid.uuid4().hex[:6]

    # admin(폴백)으로 이용자 등록 + 책임자 지정
    client.post("/api/auth/management-login", json={"username": "admin", "password": "admin"})
    tok = client.cookies.get("csrftoken")
    headers = {"x-csrftoken": tok} if tok else {}
    client.post("/api/workers", json={"name": name}, headers=headers)
    with get_connection() as conn:
        wid = conn.execute("SELECT id FROM workers WHERE name=?", (name,)).fetchone()["id"]
    client.post(f"/api/workers/{wid}/manager", json={"password": "oldpw123"}, headers=headers)

    # 본인 로그인
    client.post("/api/auth/logout", headers=headers)
    res = client.post("/api/auth/management-login", json={"username": name, "password": "oldpw123"})
    assert res.status_code == 200
    tok = client.cookies.get("csrftoken")
    headers = {"x-csrftoken": tok} if tok else {}

    # 현재 비번 틀리면 400
    bad = client.post(
        "/api/auth/change-password",
        json={"current_password": "wrong", "new_password": "newpw123"},
        headers=headers,
    )
    assert bad.status_code == 400

    # 현재 비번 맞으면 변경 성공(세션 유지)
    ok = client.post(
        "/api/auth/change-password",
        json={"current_password": "oldpw123", "new_password": "newpw123"},
        headers=headers,
    )
    assert ok.status_code == 200, ok.text
    assert client.get("/api/workers/all").status_code == 200  # 여전히 로그인 상태

    # 재로그인: 옛 비번 거부, 새 비번 허용
    client.post("/api/auth/logout", headers=headers)
    assert client.post("/api/auth/management-login", json={"username": name, "password": "oldpw123"}).status_code == 401
    assert client.post("/api/auth/management-login", json={"username": name, "password": "newpw123"}).status_code == 200


def test_change_password_requires_login():
    import importlib

    import src.config as cfg
    import src.main as mainmod

    importlib.reload(cfg)
    importlib.reload(mainmod)
    from fastapi.testclient import TestClient

    client = TestClient(mainmod.app)
    res = client.post(
        "/api/auth/change-password",
        json={"current_password": "x", "new_password": "newpw123"},
    )
    assert res.status_code in (401, 403)


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
    name = "책임" + uuid.uuid4().hex[:8]

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
