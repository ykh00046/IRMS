"""레시피 분류(category) — 책임자 지정 엔드포인트 + 목록 API 반영 검증.

허용값: "약품" | "합성" | "잉크" | null(미분류). 검증은 API에서(DB CHECK 없음).
'잉크'는 이 분류명에 한해 허용된 표기다(그대로 사용).

기존 test_recipe_management.py 의 실제 패턴(client/management-login/csrf/import)을
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


def _import(client, headers, product, a, b):
    """레시피 1건 등록(등록 즉시 completed) → id 반환. 기존 테스트의 import 패턴."""
    body = {"raw_text": f"반제품명\t원료A\t원료B\n{product}\t{a}\t{b}"}
    res = client.post("/api/recipes/import", json=body, headers=headers)
    assert res.status_code == 200, res.text
    return res.json()["created_ids"][0]


def test_manager_sets_category_reflected_in_lists():
    """책임자가 category='합성' 지정 → 200, 관리 목록·배합 목록 모두에 반영."""
    client = _client()
    headers = _login(client)
    product = f"PCAT{_uid()}"
    rid = _import(client, headers, product, 60, 40)

    res = client.put(
        f"/api/recipes/{rid}/category", json={"category": "합성"}, headers=headers
    )
    assert res.status_code == 200, res.text
    assert res.json()["category"] == "합성"

    # 관리 목록(GET /api/recipes) 반영
    items = client.get("/api/recipes", params={"search": product}).json()["items"]
    target = next(it for it in items if it["id"] == rid)
    assert target["category"] == "합성"

    # 배합 목록(GET /api/blend/recipes) 반영
    blend_items = client.get("/api/blend/recipes").json()["items"]
    blend_target = next(it for it in blend_items if it["id"] == rid)
    assert blend_target["category"] == "합성"


def test_clear_category_to_null():
    """category=null(또는 빈 문자열) → 미분류로 되돌림. 이후 조회 category is None."""
    client = _client()
    headers = _login(client)
    product = f"PCAT{_uid()}"
    rid = _import(client, headers, product, 50, 50)
    client.put(
        f"/api/recipes/{rid}/category", json={"category": "약품"}, headers=headers
    )

    res = client.put(
        f"/api/recipes/{rid}/category", json={"category": None}, headers=headers
    )
    assert res.status_code == 200, res.text
    assert res.json()["category"] is None

    items = client.get("/api/recipes", params={"search": product}).json()["items"]
    target = next(it for it in items if it["id"] == rid)
    assert target["category"] is None


def test_invalid_category_rejected():
    """허용값 외(예: '기타') → 400."""
    client = _client()
    headers = _login(client)
    rid = _import(client, headers, f"PCAT{_uid()}", 60, 40)

    res = client.put(
        f"/api/recipes/{rid}/category", json={"category": "기타"}, headers=headers
    )
    assert res.status_code == 400


def test_non_manager_blocked():
    """미로그인(또는 비책임자) → 401 또는 403. 기존 tolerance/anchor 테스트 기대 코드와 동일."""
    client = _client()
    rid = _import(client, _login(client), f"PCAT{_uid()}", 60, 40)  # 시드만 책임자로

    # 쿠키/csrf 없이(미로그인) PUT → 차단
    blocked = client.put(f"/api/recipes/{rid}/category", json={"category": "잉크"})
    assert blocked.status_code in (401, 403)
