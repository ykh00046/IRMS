"""수정 등록의 3상태 처리 + 위치 왕복 보존 (2026-07-25 감사).

화면·모델은 use_reactor/is_derived 를 bool|None 3상태로 정의했는데 라우터가
`1 if body.use_reactor else 승계` 로 읽어, 체크를 해제해도(명시 False) 부모 값이 살아남아
'끌 수 없는' 상태였다. 기준 자재·1차 연계는 아예 '없음'을 표현할 값이 없었다.
또 수정 등록이 되돌리는 detail TSV 에 위치 열이 없어 개정할 때마다 위치가 사라졌다.
"""

import importlib
import uuid

import pytest


@pytest.fixture(autouse=True)
def _cleanup_test_master():
    yield
    from src.db import get_connection

    with get_connection() as conn:
        conn.execute("DELETE FROM item_code_master WHERE source = 'manual'")
        conn.commit()


def _client():
    import src.config as cfg
    import src.main as mainmod

    importlib.reload(cfg)
    importlib.reload(mainmod)
    from fastapi.testclient import TestClient

    return TestClient(mainmod.app)


def _mgr(client):
    assert client.post(
        "/api/auth/management-login", json={"username": "admin", "password": "admin"}
    ).status_code == 200
    tok = client.cookies.get("csrftoken")
    return {"x-csrftoken": tok} if tok else {}


def test_revision_can_turn_flags_off_and_keeps_position():
    client = _client()
    headers = _mgr(client)
    product = "TRI" + uuid.uuid4().hex[:6].upper()

    raw = f"반제품명\t위치\t원료A\t원료B\n{product}\t3층탱크\t60\t40"
    v1 = client.post(
        "/api/recipes/import",
        json={"raw_text": raw, "use_reactor": True, "is_derived": True,
              "anchor_material": "원료A"},
        headers=headers,
    )
    assert v1.status_code == 200, v1.text
    rid = v1.json()["created_ids"][0]
    d1 = client.get(f"/api/recipes/{rid}/detail", headers=headers).json()
    assert d1["use_reactor"] == 1 and d1["is_derived"] == 1
    assert d1["anchor_material_name"] == "원료A"
    assert d1["position"] == "3층탱크"
    # 수정 등록이 되돌리는 TSV 에 위치가 실려야 왕복에서 살아남는다.
    assert "위치" in d1["tsv"].split("\n")[0]

    # 명시 False / 빈 문자열 / 0 = '끄기·없음'. 예전에는 전부 무시되고 부모 값이 승계됐다.
    v2 = client.post(
        "/api/recipes/import",
        json={"raw_text": d1["tsv"], "revision_of": rid,
              "use_reactor": False, "is_derived": False,
              "anchor_material": "", "stage1_recipe_id": 0},
        headers=headers,
    )
    assert v2.status_code == 200, v2.text
    d2 = client.get(
        f"/api/recipes/{v2.json()['created_ids'][0]}/detail", headers=headers
    ).json()
    assert d2["use_reactor"] == 0, "반응기를 껐는데 승계로 되살아남"
    assert d2["is_derived"] == 0, "파생을 껐는데 승계로 되살아남"
    assert d2["anchor_material_name"] is None, "기준 자재를 해제했는데 승계로 남음"
    assert d2["stage1_recipe_id"] is None
    assert d2["position"] == "3층탱크", "개정에서 위치가 사라짐"


def test_revision_without_explicit_values_still_inherits():
    """미전송(None)은 종전대로 부모 승계 — 3상태의 나머지 한 축이 깨지지 않았는지 확인."""
    client = _client()
    headers = _mgr(client)
    product = "TRI" + uuid.uuid4().hex[:6].upper()

    raw = f"반제품명\t원료A\t원료B\n{product}\t60\t40"
    v1 = client.post(
        "/api/recipes/import",
        json={"raw_text": raw, "use_reactor": True, "is_derived": True},
        headers=headers,
    )
    rid = v1.json()["created_ids"][0]
    v2 = client.post(
        "/api/recipes/import",
        json={"raw_text": f"반제품명\t원료A\t원료B\n{product}\t61\t39", "revision_of": rid},
        headers=headers,
    )
    assert v2.status_code == 200, v2.text
    d2 = client.get(
        f"/api/recipes/{v2.json()['created_ids'][0]}/detail", headers=headers
    ).json()
    assert d2["use_reactor"] == 1 and d2["is_derived"] == 1
