"""작업자 분류(파트) — PATCH /api/workers/{id} 의 category 처리 검증.

허용값: "약품" | "합성" | "잉크" | "용수". 규칙:
- PATCH 본문에 category 키가 없거나 None → 변경 안 함(기존 PATCH 규칙).
- 빈 문자열 "" → 미지정(NULL)으로 해제.
- 허용값 외 → 400.

기존 test_workers.py 의 실제 패턴(client/management-login/csrf/get_connection)을
그대로 따른다 — 임의 스키마/픽스처 추정 금지.
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


def _login(client):
    res = client.post(
        "/api/auth/management-login", json={"username": "admin", "password": "admin"}
    )
    assert res.status_code == 200
    tok = client.cookies.get("csrftoken")
    return {"x-csrftoken": tok} if tok else {}


def _uid():
    return uuid.uuid4().hex[:8].upper()


def _register_worker(client, headers, name):
    """작업자 1명 등록 → id 반환. 기존 test_workers 의 등록 패턴."""
    res = client.post("/api/workers", json={"name": name}, headers=headers)
    assert res.status_code == 200, res.text
    from src.db import get_connection

    with get_connection() as conn:
        wid = conn.execute(
            "SELECT id FROM workers WHERE name = ?", (name,)
        ).fetchone()["id"]
    return wid


def _category_of(client, name):
    """GET /api/workers(무로그인 개방) 에서 이름으로 항목을 찾아 category 반환."""
    items = client.get("/api/workers").json()["items"]
    return next(it for it in items if it["name"] == name)["category"]


def test_manager_sets_category_reflected_in_list():
    """책임자가 PATCH {category:'용수'} → 200, GET /api/workers 항목에 category=='용수'."""
    client = _client()
    headers = _login(client)
    name = "파트용수" + _uid()
    wid = _register_worker(client, headers, name)

    res = client.patch(
        f"/api/workers/{wid}", json={"category": "용수"}, headers=headers
    )
    assert res.status_code == 200, res.text
    assert _category_of(client, name) == "용수"


def test_clear_category_with_empty_string():
    """category=''(빈 문자열) → 미지정(NULL) 해제. 이후 목록 category is None."""
    client = _client()
    headers = _login(client)
    name = "해제대상" + _uid()
    wid = _register_worker(client, headers, name)
    client.patch(f"/api/workers/{wid}", json={"category": "합성"}, headers=headers)
    assert _category_of(client, name) == "합성"

    res = client.patch(
        f"/api/workers/{wid}", json={"category": ""}, headers=headers
    )
    assert res.status_code == 200, res.text
    assert _category_of(client, name) is None


def test_invalid_category_rejected():
    """허용값 외(예: '기타') → 400."""
    client = _client()
    headers = _login(client)
    name = "오류분류" + _uid()
    wid = _register_worker(client, headers, name)

    res = client.patch(
        f"/api/workers/{wid}", json={"category": "기타"}, headers=headers
    )
    assert res.status_code == 400


def test_name_only_patch_leaves_category_unchanged():
    """category 키 없이 name 만 PATCH → category 불변(None=변경 안 함 규칙)."""
    client = _client()
    headers = _login(client)
    name = "불변검사" + _uid()
    wid = _register_worker(client, headers, name)
    client.patch(f"/api/workers/{wid}", json={"category": "잉크"}, headers=headers)
    assert _category_of(client, name) == "잉크"

    new_name = "불변개명" + _uid()
    res = client.patch(
        f"/api/workers/{wid}", json={"name": new_name}, headers=headers
    )
    assert res.status_code == 200, res.text
    # 이름은 바뀌고, category 는 잉크 그대로 유지
    assert _category_of(client, new_name) == "잉크"


def test_non_manager_blocked():
    """비책임자(미로그인) PATCH → 401 또는 403. 기존 admin 라우트 의존성 그대로."""
    client = _client()
    headers = _login(client)
    name = "권한검사" + _uid()
    wid = _register_worker(client, headers, name)

    # 쿠키/csrf 없이(미로그인) PATCH → 차단
    blocked = client.patch(f"/api/workers/{wid}", json={"category": "약품"})
    assert blocked.status_code in (401, 403)
