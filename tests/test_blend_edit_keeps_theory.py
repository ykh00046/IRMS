"""LOT 만 고치는 정정은 저장 당시 이론량을 지킨다(2026-09-09 현장 신고).

옛 기록을 저장한 뒤 그 자재에 투입 로스 보정 1.0 g 이 붙으면, 정정 저장의 재산출
이론량이 1.0 g 커져 실제량과 딱 맞던 기록이 '허용 편차 초과'로 막혔다.
총량이 그대로인 정정은 기록의 이론량을 그대로 쓰고, 총량을 바꾼 정정만 새로 산출한다.
"""
from tests.test_recipe_loss_comp import _client, _login, _uid, _import


def _worker(client, headers):
    name = "정정" + _uid()[:5]
    client.post("/api/workers", json={"name": name}, headers=headers)
    client.post("/api/blend/session/login", json={"worker": name}, headers=headers)
    return name


def _record(client, headers, product, rid, worker, total=1000):
    body = {
        "recipe_id": rid, "product_name": product, "worker": worker,
        "work_date": "2026-06-17", "total_amount": total,
        "details": [
            {"material_name": "PowderA", "material_lot": "'03136002", "ratio": 60,
             "theory_amount": total * 0.6, "actual_amount": total * 0.6},
            {"material_name": "LiquidB", "material_lot": "L-2", "ratio": 40,
             "theory_amount": total * 0.4, "actual_amount": total * 0.4},
        ],
    }
    res = client.post("/api/blend/records", json=body, headers=headers)
    assert res.status_code == 200, res.text
    return res.json()["id"], body


def test_lot_fix_after_loss_comp_added_keeps_recorded_theory():
    client = _client()
    headers = _login(client)
    product = "KEEP" + _uid()[:6]
    rid = _import(client, headers, product, {"PowderA": 60, "LiquidB": 40}).json()["created_ids"][0]
    worker = _worker(client, headers)
    record_id, body = _record(client, headers, product, rid, worker)

    # 기록 뒤에 로스 보정이 붙는다 — 재산출하면 PowderA 이론량이 601.0 이 된다.
    res = client.put(f"/api/recipes/{rid}/loss-comp",
                     json={"items": [{"material_name": "PowderA", "loss_comp_g": 1.0}]}, headers=headers)
    assert res.status_code == 200, res.text

    # LOT 의 따옴표만 지우는 정정 — 총량 그대로. 편차 초과로 막히면 안 된다.
    edit = dict(body)
    edit["details"] = [dict(d) for d in body["details"]]
    edit["details"][0]["material_lot"] = "03136002"
    res = client.put(f"/api/blend/records/{record_id}", json=edit, headers=headers)
    assert res.status_code == 200, res.text
    got = {d["material_name"]: d for d in res.json()["details"]}
    assert got["PowderA"]["material_lot"] == "03136002"
    assert got["PowderA"]["theory_amount"] == 600.0          # 저장 당시 값 유지(보정 미반영)
    assert float(got["PowderA"].get("loss_comp_g") or 0) == 0.0


def test_total_change_still_rederives_theory():
    client = _client()
    headers = _login(client)
    product = "RECALC" + _uid()[:6]
    rid = _import(client, headers, product, {"PowderA": 60, "LiquidB": 40}).json()["created_ids"][0]
    worker = _worker(client, headers)
    record_id, body = _record(client, headers, product, rid, worker)

    edit = dict(body, total_amount=2000)
    edit["details"] = [
        {"material_name": "PowderA", "material_lot": "L-1", "ratio": 60, "theory_amount": 1200, "actual_amount": 1200},
        {"material_name": "LiquidB", "material_lot": "L-2", "ratio": 40, "theory_amount": 800, "actual_amount": 800},
    ]
    res = client.put(f"/api/blend/records/{record_id}", json=edit, headers=headers)
    assert res.status_code == 200, res.text
    got = {d["material_name"]: d["theory_amount"] for d in res.json()["details"]}
    assert got == {"PowderA": 1200.0, "LiquidB": 800.0}
