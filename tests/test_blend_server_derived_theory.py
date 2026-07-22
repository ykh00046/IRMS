"""감사 F-5: 비율·이론량은 서버가 레시피에서 산출한다 (클라이언트 값 불신).

핵심은 '비교 후 거부'가 아니라 '서버 산출'이라는 점 — 반올림·기준자재 파생 때문에
정상 저장이 오판으로 막히면 현장이 멈춘다. 그래서 anchor 레시피가 그대로 저장되는지를
반드시 함께 검증한다.
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


def _uid():
    return uuid.uuid4().hex[:8].upper()


def _mgr(client):
    assert client.post(
        "/api/auth/management-login", json={"username": "admin", "password": "admin"}
    ).status_code == 200
    tok = client.cookies.get("csrftoken")
    return {"x-csrftoken": tok} if tok else {}


def _import(client, headers, product, a, b, revision_of=None):
    body = {"raw_text": f"반제품명\t원료A\t원료B\n{product}\t{a}\t{b}"}
    if revision_of is not None:
        body["revision_of"] = revision_of
    res = client.post("/api/recipes/import", json=body, headers=headers)
    assert res.status_code == 200, res.text
    return res.json()["created_ids"][0]


def _blend_worker(client, headers):
    worker = "F5작업" + _uid()[:4]
    client.post("/api/workers", json={"name": worker}, headers=headers)
    client.post("/api/blend/session/login", json={"worker": worker}, headers=headers)
    return worker


def test_server_recomputes_theory_and_ignores_client_values():
    """조작·낡은 클라이언트가 보낸 ratio·theory 는 버려지고 레시피 기준으로 저장된다."""
    client = _client()
    headers = _mgr(client)
    product = f"F5A{_uid()}"
    rid = _import(client, headers, product, 60, 40)   # 원료A 60 : 원료B 40
    worker = _blend_worker(client, headers)

    # 클라이언트가 거짓 비율·이론량을 보낸다 (실제량은 레시피 기준 정답값)
    res = client.post("/api/blend/records", json={
        "recipe_id": rid, "product_name": product, "worker": worker,
        "work_date": "2026-07-14", "total_amount": 1000,
        "details": [
            {"material_name": "원료A", "ratio": 99, "theory_amount": 990, "actual_amount": 600, "material_lot": "LOT-A"},
            {"material_name": "원료B", "ratio": 1, "theory_amount": 10, "actual_amount": 400, "material_lot": "LOT-B"},
        ],
    }, headers=headers)
    assert res.status_code == 200, res.text

    rec = client.get(f"/api/blend/records/{res.json()['id']}").json()
    by_name = {d["material_name"]: d for d in rec["details"]}
    assert by_name["원료A"]["theory_amount"] == 600      # 거짓 990 이 아니라 서버 산출
    assert by_name["원료B"]["theory_amount"] == 400
    assert by_name["원료A"]["ratio"] == 60
    assert by_name["원료B"]["ratio"] == 40


def test_revised_recipe_is_rejected_not_silently_saved():
    """화면을 열어둔 사이 개정되면 409 — 옛 배합비로 조용히 저장하지 않는다."""
    client = _client()
    headers = _mgr(client)
    product = f"F5R{_uid()}"
    v1 = _import(client, headers, product, 60, 40)
    _import(client, headers, product, 70, 30, revision_of=v1)   # 개정 발생
    worker = _blend_worker(client, headers)

    res = client.post("/api/blend/records", json={
        "recipe_id": v1, "product_name": product, "worker": worker,   # 옛 id 로 저장 시도
        "work_date": "2026-07-14", "total_amount": 1000,
        "details": [
            {"material_name": "원료A", "ratio": 60, "theory_amount": 600, "actual_amount": 600},
            {"material_name": "원료B", "ratio": 40, "theory_amount": 400, "actual_amount": 400},
        ],
    }, headers=headers)
    assert res.status_code == 409, res.text
    assert "개정" in res.json()["detail"]


def test_material_set_mismatch_is_rejected():
    """레시피에 없는 자재를 끼워 넣으면 400."""
    client = _client()
    headers = _mgr(client)
    product = f"F5M{_uid()}"
    rid = _import(client, headers, product, 60, 40)
    worker = _blend_worker(client, headers)

    res = client.post("/api/blend/records", json={
        "recipe_id": rid, "product_name": product, "worker": worker,
        "work_date": "2026-07-14", "total_amount": 1000,
        "details": [
            {"material_name": "원료A", "ratio": 60, "theory_amount": 600, "actual_amount": 600},
            {"material_name": "몰래추가", "ratio": 40, "theory_amount": 400, "actual_amount": 400},
        ],
    }, headers=headers)
    assert res.status_code == 400, res.text
    assert "자재 구성" in res.json()["detail"]


def test_anchor_recipe_saves_and_derives_total_from_measured_anchor():
    """기준 자재(anchor) 레시피는 실측에서 총량이 파생된다 — 오판으로 막히면 안 된다.

    F-5 를 '서버 재계산 후 클라이언트 값과 비교해 거부' 로 만들었다면 이 케이스가
    통째로 저장 불가가 된다(총량이 작업자 입력이 아니라 실측 파생이므로).
    """
    from src.db import get_connection

    client = _client()
    headers = _mgr(client)
    product = f"F5ANC{_uid()}"
    rid = _import(client, headers, product, 60, 40)   # A 60 : B 40 (기준 총량 100)

    # 원료A 를 기준 자재로 지정
    with get_connection() as conn:
        mat = conn.execute(
            "SELECT ri.material_id FROM recipe_items ri JOIN materials m ON m.id = ri.material_id "
            "WHERE ri.recipe_id = ? AND m.name = '원료A'", (rid,)
        ).fetchone()
    res = client.put(f"/api/recipes/{rid}/anchor",
                     json={"material_id": int(mat["material_id"])}, headers=headers)
    assert res.status_code == 200, res.text
    assert res.json()["anchor_material_id"] == int(mat["material_id"])   # 실제로 걸렸는지 확인

    worker = _blend_worker(client, headers)
    # 반응기에서 나온 기준 자재 실측 = 630g → 총량 1050, 원료B 이론 420
    res = client.post("/api/blend/records", json={
        "recipe_id": rid, "product_name": product, "worker": worker,
        "work_date": "2026-07-14", "total_amount": 1000,   # 화면이 보낸 총량은 무시된다
        "details": [
            {"material_name": "원료A", "ratio": 60, "theory_amount": 600, "actual_amount": 630, "material_lot": "LOT-A"},
            {"material_name": "원료B", "ratio": 40, "theory_amount": 420, "actual_amount": 420, "material_lot": "LOT-B"},
        ],
    }, headers=headers)
    assert res.status_code == 200, res.text

    rec = client.get(f"/api/blend/records/{res.json()['id']}").json()
    by_name = {d["material_name"]: d for d in rec["details"]}
    assert rec["total_amount"] == 1050                  # 실측(630)에서 파생
    assert by_name["원료A"]["theory_amount"] == 630     # 기준 행: 이론 = 실측 (편차 0)
    assert by_name["원료B"]["theory_amount"] == 420
