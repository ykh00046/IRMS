"""배합 화면 반제품 LOT 점도 경고 — GET /api/blend/product-lot-viscosity.

사용자 요청(2026-09-08): PB 를 쓰는 품목의 배합에서 PB LOT 을 입력하면 그 PB 점도가
경고 하한(48) 이하인지 작업자가 그 행 아래에서 바로 보게. 차단이 아니라 안내.
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

    client = TestClient(mainmod.app)

    def headers():
        tok = client.cookies.get("csrftoken")
        return {"x-csrftoken": tok} if tok else {}

    client.get("/api/blend/records")
    worker = "점도경고" + uuid.uuid4().hex[:6]
    client.post("/api/workers", json={"name": worker}, headers=headers())
    client.post("/api/blend/session/login", json={"worker": worker}, headers=headers())
    return client, worker, headers


def _pb_record(client, headers, worker, date):
    res = client.post(
        "/api/blend/records",
        json={
            "product_name": "PB", "worker": worker, "work_date": date, "total_amount": 100,
            "details": [{"material_name": "MMA" + uuid.uuid4().hex[:4], "material_lot": "M-1",
                         "ratio": 100, "theory_amount": 100, "actual_amount": 100}],
        },
        headers=headers(),
    )
    assert res.status_code == 200, res.text
    return res.json()["id"], res.json()["product_lot"]


def _register(client, headers, record_id, value):
    res = client.post(f"/api/blend/records/{record_id}/viscosity",
                      json={"viscosity": value}, headers=headers())
    assert res.status_code == 200, res.text


def test_pb_lot_at_or_below_warn_low_is_flagged_for_the_blend_screen():
    client, worker, headers = _client()
    rid, lot = _pb_record(client, headers, worker, "2026-09-01")
    _register(client, headers, rid, 47.5)

    res = client.get("/api/blend/product-lot-viscosity", params={"name": "PB", "lot": lot})
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["found"] is True and data["viscosity"] == 47.5
    assert data["level"] == "warn"
    assert data["reason"] == "경고 하한 이하"
    assert data["threshold"] == 48.0
    assert data["message"] == "PB 점도 47.5 · 경고 하한 48 이하"


def test_pb_lot_above_warn_low_has_no_alert():
    client, worker, headers = _client()
    rid, lot = _pb_record(client, headers, worker, "2026-09-02")
    _register(client, headers, rid, 49.2)

    data = client.get("/api/blend/product-lot-viscosity", params={"name": "pb", "lot": lot}).json()
    assert data["found"] is True and data["level"] is None and data["message"] is None


def test_unknown_lot_or_plain_material_returns_found_false():
    client, _worker, _headers = _client()
    data = client.get("/api/blend/product-lot-viscosity",
                      params={"name": "PB", "lot": "없는LOT" + uuid.uuid4().hex[:6]}).json()
    assert data["found"] is False and data["level"] is None
    data = client.get("/api/blend/product-lot-viscosity",
                      params={"name": "MMA", "lot": "M-1"}).json()
    assert data["found"] is False
    assert client.get("/api/blend/product-lot-viscosity").json()["found"] is False
