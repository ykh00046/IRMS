"""새 작업자 등록 시 category(파트) — POST /api/workers 의 category 처리 검증.

허용값: "약품" | "합성" | "잉크" | "용수". 규칙:
- category 생략(None) → 미지정(NULL)로 생성(하위호환).
- 허용값 → 생성/재활성 시 저장 + GET /api/workers 에 반영.
- 허용값 외 → 400(PATCH 의 검증 문구와 동일).
- 비활성 동명 작업자 재등록(reactivate) 시 category 갱신.

worker_service.register 의 단위 테스트도 함께 다룬다. 기존 test_worker_category.py 의
실제 패턴(client/management-login/csrf/get_connection)을 그대로 따른다.
"""

from __future__ import annotations

import importlib
import sqlite3
import uuid

from src.services import worker_service as ws


# ── 단위(worker_service.register) ──────────────────────────────────────────

def _unit_db() -> sqlite3.Connection:
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
            session_token TEXT,
            category TEXT
        )
        """
    )
    return conn


def _category_of(conn, name):
    row = conn.execute(
        "SELECT category FROM workers WHERE name = ?", (name,)
    ).fetchone()
    return row["category"] if row else None


def test_unit_register_new_with_category_stored():
    conn = _unit_db()
    r = ws.register(conn, "새작업", "2026-07-17", category="합성")
    assert r == {"name": "새작업", "created": True, "reactivated": False,
                 "category": "합성"}
    assert _category_of(conn, "새작업") == "합성"


def test_unit_register_without_category_is_null_backward_compat():
    """category 키워드 없이 기존 시그니처 register(conn, name, now) 호출 → 그대로 동작."""
    conn = _unit_db()
    r = ws.register(conn, "기존호환", "2026-07-17")
    assert r["created"] is True
    assert r["category"] is None
    assert _category_of(conn, "기존호환") is None


def test_unit_register_blank_category_normalized_to_null():
    """빈 문자열/공백 category → None 으로 정리. (라우트에서도 strip 후 None 취급.)"""
    conn = _unit_db()
    r = ws.register(conn, "빈파트", "t", category="   ")
    assert r["category"] is None
    assert _category_of(conn, "빈파트") is None


def test_unit_reactivate_updates_category():
    """비활성 동명 작업자 재등록(reactivate) 시 category 가 주어지면 갱신."""
    conn = _unit_db()
    ws.register(conn, "재활성", "t", category="약품")
    wid = conn.execute(
        "SELECT id FROM workers WHERE name='재활성'"
    ).fetchone()["id"]
    ws.set_active(conn, wid, False)

    r = ws.register(conn, "재활성", "t", category="용수")
    assert r == {"name": "재활성", "created": False, "reactivated": True,
                 "category": "용수"}
    assert _category_of(conn, "재활성") == "용수"


def test_unit_reactivate_without_category_keeps_existing():
    """reactivate 시 category 미전달 → 기존 파트 유지(COALESCE)."""
    conn = _unit_db()
    ws.register(conn, "파트유지", "t", category="잉크")
    wid = conn.execute(
        "SELECT id FROM workers WHERE name='파트유지'"
    ).fetchone()["id"]
    ws.set_active(conn, wid, False)

    r = ws.register(conn, "파트유지", "t")
    assert r["reactivated"] is True
    assert _category_of(conn, "파트유지") == "잉크"


# ── 라우트(POST /api/workers) ──────────────────────────────────────────────

def _client():
    import src.config as cfg
    import src.main as mainmod

    importlib.reload(cfg)
    importlib.reload(mainmod)
    from fastapi.testclient import TestClient

    return TestClient(mainmod.app)


def _login(client):
    res = client.post(
        "/api/auth/management-login", json={"username": "admin", "password": "admin"}
    )
    assert res.status_code == 200
    tok = client.cookies.get("csrftoken")
    return {"x-csrftoken": tok} if tok else {}


def _uid():
    return uuid.uuid4().hex[:8].upper()


def _category_in_list(client, name):
    items = client.get("/api/workers").json()["items"]
    return next(it for it in items if it["name"] == name)["category"]


def test_route_register_with_category_stored():
    """POST /workers {name, category:'합성'} → 생성 + DB category 저장 + 목록 반영."""
    client = _client()
    headers = _login(client)
    name = "라우트합성" + _uid()

    res = client.post(
        "/api/workers", json={"name": name, "category": "합성"}, headers=headers
    )
    assert res.status_code == 200, res.text
    assert res.json()["created"] is True
    assert res.json()["category"] == "합성"
    assert _category_in_list(client, name) == "합성"


def test_route_register_without_category_is_null():
    """category 생략 → null 로 생성(하위호환)."""
    client = _client()
    headers = _login(client)
    name = "라우트미지정" + _uid()

    res = client.post("/api/workers", json={"name": name}, headers=headers)
    assert res.status_code == 200, res.text
    assert res.json()["category"] is None
    assert _category_in_list(client, name) is None


def test_route_register_blank_category_treated_as_null():
    """빈 문자열 category → 미지정(NULL). 모달이 보내는 케이스(미지정 회피) 회귀."""
    client = _client()
    headers = _login(client)
    name = "빈문자열파트" + _uid()

    res = client.post(
        "/api/workers", json={"name": name, "category": ""}, headers=headers
    )
    assert res.status_code == 200, res.text
    assert res.json()["category"] is None
    assert _category_in_list(client, name) is None


def test_route_register_invalid_category_returns_400():
    """category:'엉뚱' → 400(PATCH 의 검증 문구와 동일)."""
    client = _client()
    headers = _login(client)
    name = "오류파트" + _uid()

    res = client.post(
        "/api/workers", json={"name": name, "category": "엉뚱"}, headers=headers
    )
    assert res.status_code == 400
    detail = res.json()["detail"]
    assert "분류" in detail and "약품" in detail


def test_route_register_strips_whitespace_category():
    """category 값은 strip 후 검증 — ' 약품 ' → '약품'."""
    client = _client()
    headers = _login(client)
    name = "공백파트" + _uid()

    res = client.post(
        "/api/workers", json={"name": name, "category": "  약품  "}, headers=headers
    )
    assert res.status_code == 200, res.text
    assert res.json()["category"] == "약품"
    assert _category_in_list(client, name) == "약품"


def test_route_reactivate_updates_category():
    """비활성 동명 작업자 재등록(reactivate) 시 category 갱신 —
    라우트 → service → DB 까지 종단 검증."""
    client = _client()
    headers = _login(client)
    name = "재활성라우트" + _uid()

    # 1차 등록(파트: 합성)
    res1 = client.post(
        "/api/workers", json={"name": name, "category": "합성"}, headers=headers
    )
    assert res1.status_code == 200
    assert res1.json()["category"] == "합성"
    assert _category_in_list(client, name) == "합성"

    # 비활성화
    from src.db import get_connection

    with get_connection() as conn:
        wid = conn.execute(
            "SELECT id FROM workers WHERE name = ?", (name,)
        ).fetchone()["id"]
    client.patch(
        f"/api/workers/{wid}", json={"is_active": False}, headers=headers
    )
    # GET /api/workers 는 활성만 → 더 이상 안 보임
    assert all(it["name"] != name for it in client.get("/api/workers").json()["items"])

    # 2차 등록(동명, 파트: 용수) → reactivate
    res2 = client.post(
        "/api/workers", json={"name": name, "category": "용수"}, headers=headers
    )
    assert res2.status_code == 200, res2.text
    assert res2.json()["reactivated"] is True
    assert res2.json()["category"] == "용수"
    assert _category_in_list(client, name) == "용수"


def test_route_register_category_preserved_when_omitted_on_reactivate():
    """reactivate 시 category 생략 → 기존 파트 유지(덮어쓰지 않음)."""
    client = _client()
    headers = _login(client)
    name = "파트보존라우트" + _uid()

    client.post(
        "/api/workers", json={"name": name, "category": "잉크"}, headers=headers
    )
    from src.db import get_connection

    with get_connection() as conn:
        wid = conn.execute(
            "SELECT id FROM workers WHERE name = ?", (name,)
        ).fetchone()["id"]
    client.patch(
        f"/api/workers/{wid}", json={"is_active": False}, headers=headers
    )

    res = client.post("/api/workers", json={"name": name}, headers=headers)
    assert res.status_code == 200, res.text
    assert res.json()["reactivated"] is True
    assert _category_in_list(client, name) == "잉크"
