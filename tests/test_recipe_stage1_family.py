"""1차→2차 가족 묶음이 1차 개정을 넘겨 살아남는지 — GET /api/recipes 의 stage1 해석.

현황 목록은 개정 체인의 최신본만 보여준다(SUPERSEDED_RECIPE_IDS_SQL). 그런데 2차의
stage1_recipe_id 는 특정 개정본 id 를 가리키므로, 1차를 개정하면 2차가 가리키던 id 가
목록에서 빠져 화면이 짝을 못 찾고 가족이 통째로 사라졌다(2026-08-08 확인). 1차를 여러
2차가 공유하면 그 수만큼 한 번에 끊긴다.

지금은 서버가 읽을 때 체인의 현재 버전으로 해석해서 내려준다 — 저장된 링크는 그대로 두고
(그 개정본이 어느 1차 버전에 걸려 있었는지는 기록으로 남는 게 맞다), 해석 결과와 원본을
함께 실어 화면이 "옛 버전에 걸려 있다"까지 말할 수 있게 한다.

test_recipe_derived.py 의 client/management-login/csrf/import 패턴을 따른다.
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


def _import(client, headers, product, **extra):
    body = {"raw_text": f"반제품명\t원료A\t원료B\n{product}\t60\t40"}
    body.update(extra)
    res = client.post("/api/recipes/import", json=body, headers=headers)
    assert res.status_code == 200, res.text
    return res.json()["created_ids"][0]


def _find(client, product, recipe_id):
    items = client.get("/api/recipes", params={"search": product}).json()["items"]
    return next((it for it in items if it["id"] == recipe_id), None)


def _visible_ids(client, product):
    items = client.get("/api/recipes", params={"search": product}).json()["items"]
    return {it["id"] for it in items}


def test_family_survives_a_stage1_revision():
    """1차를 개정해도 2차가 현재 1차를 가리킨다 — 예전엔 가족이 통째로 사라졌다."""
    client = _client()
    headers = _login(client)
    tag = _uid()
    one = _import(client, headers, f"S1{tag}")
    two = _import(client, headers, f"S2{tag}", stage1_recipe_id=one)

    before = _find(client, f"S2{tag}", two)
    assert before["stage1_recipe_id"] == one
    assert before["stage1_superseded"] is False

    # 1차 개정 — 옛 1차 id 는 목록에서 빠진다.
    one_v2 = _import(client, headers, f"S1{tag}", revision_of=one)
    assert one_v2 != one
    assert one not in _visible_ids(client, f"S1{tag}")
    assert one_v2 in _visible_ids(client, f"S1{tag}")

    after = _find(client, f"S2{tag}", two)
    # 해석된 값은 새 1차 — 이 값으로 화면이 가족을 묶는다.
    assert after["stage1_recipe_id"] == one_v2
    # 저장된 원본은 건드리지 않는다(그 시점의 1차 버전은 기록으로 남는다).
    assert after["stage1_recipe_id_stored"] == one
    assert after["stage1_superseded"] is True


def test_shared_stage1_keeps_every_child_linked_after_revision():
    """1차를 공유하는 2차가 여럿이어도 개정 한 번에 다 같이 끊기지 않는다."""
    client = _client()
    headers = _login(client)
    tag = _uid()
    one = _import(client, headers, f"P1{tag}")
    kids = [
        _import(client, headers, f"P2A{tag}", stage1_recipe_id=one),
        _import(client, headers, f"P2B{tag}", stage1_recipe_id=one),
        _import(client, headers, f"P2C{tag}", stage1_recipe_id=one),
    ]
    one_v2 = _import(client, headers, f"P1{tag}", revision_of=one)

    for kid, name in zip(kids, ("P2A", "P2B", "P2C")):
        row = _find(client, f"{name}{tag}", kid)
        assert row["stage1_recipe_id"] == one_v2, name
        assert row["stage1_superseded"] is True, name
        # 화면이 가족 이름표에 쓰는 값 — 해석된 1차의 이름이어야 한다.
        assert row["stage1_product_name"] == f"P1{tag}", name


def test_unrevised_stage1_reports_no_supersession():
    """개정이 없으면 원본과 해석값이 같고 안내 문구도 뜨지 않아야 한다."""
    client = _client()
    headers = _login(client)
    tag = _uid()
    one = _import(client, headers, f"Q1{tag}")
    two = _import(client, headers, f"Q2{tag}", stage1_recipe_id=one)

    row = _find(client, f"Q2{tag}", two)
    assert row["stage1_recipe_id"] == row["stage1_recipe_id_stored"] == one
    assert row["stage1_superseded"] is False


def test_recipe_without_stage1_has_no_extra_fields():
    """1차 연계가 없는 레시피에는 해석 필드가 붙지 않는다(빈 값 오해 방지)."""
    client = _client()
    headers = _login(client)
    tag = _uid()
    rid = _import(client, headers, f"N1{tag}")
    row = _find(client, f"N1{tag}", rid)
    assert row["stage1_recipe_id"] is None
    assert "stage1_superseded" not in row
    assert "stage1_recipe_id_stored" not in row
