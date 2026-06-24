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
            created_at TEXT NOT NULL
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
