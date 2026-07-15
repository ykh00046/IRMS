"""자재 LOT 역추적(material-lot-trace) — 백엔드 테스트.

Spec: scratchpad/material-lot-trace-spec.md
패턴은 tests/test_blend.py 의 실제 라우트 테스트를 그대로 따른다
(TestClient + importlib.reload + /api prefix + csrf + 작업자 세션 + POST /api/blend/records).
테스트 DB(.tmp-tests) 가 실행 간 남으므로, test_blend.py 처럼 uuid 토큰으로
제품명·자재 LOT 를 고유하게 만들어 잔류 데이터와 충돌하지 않게 한다.
토큰을 LOT 접두({t}-RM-A1 ...)로 두어 부분 일치 케이스도 겹치지 않게 한다.
"""

from __future__ import annotations

import importlib
import uuid


def _setup_client():
    """test_blend.py 의 실제 패턴 복사 — app reload + csrf 쿠키 + 작업자 세션 준비."""
    import src.config as cfg
    import src.main as mainmod

    importlib.reload(cfg)
    importlib.reload(mainmod)
    from fastapi.testclient import TestClient

    client = TestClient(mainmod.app)

    def headers():
        tok = client.cookies.get("csrftoken")
        return {"x-csrftoken": tok} if tok else {}

    client.get("/api/blend/records")  # csrf 쿠키 확보
    worker = "추적작업" + uuid.uuid4().hex[:6]
    client.post("/api/workers", json={"name": worker}, headers=headers())
    client.post("/api/blend/session/login", json={"worker": worker}, headers=headers())
    return client, worker, headers


def _create_record(client, headers, worker, product_name, work_date, details):
    """POST /api/blend/records 로 배합 기록 1건 생성 — test_blend.py 패턴."""
    res = client.post(
        "/api/blend/records",
        json={
            "product_name": product_name,
            "worker": worker,
            "work_date": work_date,
            "total_amount": 100,
            "details": details,
        },
        headers=headers(),
    )
    assert res.status_code == 200, res.text
    return res.json()["id"], res.json()["product_lot"]


def test_material_lot_trace_two_records_sharing_rma1():
    """기록 2건(RM-A1 공유) → record_count==2, 모든 item 의 material_lot 에 'RM-A1'."""
    client, worker, headers = _setup_client()
    t = uuid.uuid4().hex[:6]
    product = "TRC" + t
    lot_a1 = f"{t}-RM-A1"  # 두 기록이 공유
    lot_b1 = f"{t}-RM-B1"
    lot_b2 = f"{t}-RM-B2"

    # 기록1: RM-A1 / RM-B1
    rid1, _ = _create_record(client, headers, worker, product, "2026-07-10", [
        {"material_name": "원료A", "theory_amount": 60, "actual_amount": 60, "material_lot": lot_a1},
        {"material_name": "원료B", "theory_amount": 40, "actual_amount": 40, "material_lot": lot_b1},
    ])
    # 기록2: RM-A1 / RM-B2 (RM-A1 공유)
    rid2, _ = _create_record(client, headers, worker, product, "2026-07-11", [
        {"material_name": "원료A", "theory_amount": 60, "actual_amount": 60, "material_lot": lot_a1},
        {"material_name": "원료B", "theory_amount": 40, "actual_amount": 40, "material_lot": lot_b2},
    ])

    res = client.get("/api/blend/material-lot-trace", params={"lot": lot_a1})
    assert res.status_code == 200, res.text
    j = res.json()
    assert j["lot"] == lot_a1
    assert j["record_count"] == 2
    record_ids = {it["record_id"] for it in j["items"]}
    assert record_ids == {rid1, rid2}
    # RM-A1 인 자재 행만 걸려야 한다
    assert all("RM-A1" in (it["material_lot"] or "") for it in j["items"])


def test_material_lot_trace_partial_match():
    """lot={t}-RM-A → {t}-RM-A1 걸림(부분 일치)."""
    client, worker, headers = _setup_client()
    t = uuid.uuid4().hex[:6]
    _create_record(client, headers, worker, "TRC" + t, "2026-07-10", [
        {"material_name": "원료A", "theory_amount": 100, "actual_amount": 100,
         "material_lot": f"{t}-RM-A1"},
    ])

    res = client.get("/api/blend/material-lot-trace", params={"lot": f"{t}-RM-A"})
    assert res.status_code == 200, res.text
    j = res.json()
    assert j["record_count"] >= 1
    assert all(f"{t}-RM-A" in (it["material_lot"] or "") for it in j["items"])


def test_material_lot_trace_literal_percent_returns_zero():
    """lot=% → '%' 리터럴로 취급되어 0건(자재 LOT 에 % 없음, 이스케이프 동작 확인)."""
    client, worker, headers = _setup_client()
    t = uuid.uuid4().hex[:6]
    _create_record(client, headers, worker, "TRC" + t, "2026-07-10", [
        {"material_name": "원료A", "theory_amount": 100, "actual_amount": 100,
         "material_lot": f"{t}-RM-A1"},
    ])

    res = client.get("/api/blend/material-lot-trace", params={"lot": "%"})
    assert res.status_code == 200, res.text
    j = res.json()
    assert j["total"] == 0
    assert j["record_count"] == 0


def test_material_lot_trace_unknown_lot_returns_zero():
    """없는 LOT → total==0, record_count==0."""
    client, worker, headers = _setup_client()
    t = uuid.uuid4().hex[:6]
    _create_record(client, headers, worker, "TRC" + t, "2026-07-10", [
        {"material_name": "원료A", "theory_amount": 100, "actual_amount": 100,
         "material_lot": f"{t}-RM-A1"},
    ])

    res = client.get("/api/blend/material-lot-trace", params={"lot": f"{t}-NOPE-9999"})
    assert res.status_code == 200, res.text
    j = res.json()
    assert j["total"] == 0
    assert j["record_count"] == 0


def test_blend_records_search_covers_material_lot():
    """/api/blend/records?search=RM-B2 → 기록2만 반환 + 기존 제품 LOT 검색 회귀 방지."""
    client, worker, headers = _setup_client()
    t = uuid.uuid4().hex[:6]
    product = "TRC" + t
    lot_a1 = f"{t}-RM-A1"
    lot_b1 = f"{t}-RM-B1"
    lot_b2 = f"{t}-RM-B2"

    # 기록1: RM-A1 / RM-B1
    rid1, lot1 = _create_record(client, headers, worker, product, "2026-07-10", [
        {"material_name": "원료A", "theory_amount": 60, "actual_amount": 60, "material_lot": lot_a1},
        {"material_name": "원료B", "theory_amount": 40, "actual_amount": 40, "material_lot": lot_b1},
    ])
    # 기록2: RM-A1 / RM-B2
    rid2, lot2 = _create_record(client, headers, worker, product, "2026-07-11", [
        {"material_name": "원료A", "theory_amount": 60, "actual_amount": 60, "material_lot": lot_a1},
        {"material_name": "원료B", "theory_amount": 40, "actual_amount": 40, "material_lot": lot_b2},
    ])

    # 자재 LOT 검색 — 기록2(RM-B2)만 걸려야 한다.
    res = client.get("/api/blend/records", params={"search": lot_b2})
    assert res.status_code == 200, res.text
    found_ids = {it["id"] for it in res.json()["items"]}
    assert found_ids == {rid2}, found_ids

    # 기존 제품 LOT 검색 회귀 방지 — 제품 LOT(일부) 로 기록1이 걸린다.
    res_lot = client.get("/api/blend/records", params={"search": lot1[: len(lot1) - 2]})
    assert res_lot.status_code == 200, res_lot.text
    lot_ids = {it["id"] for it in res_lot.json()["items"]}
    assert rid1 in lot_ids, (lot1, lot_ids)
