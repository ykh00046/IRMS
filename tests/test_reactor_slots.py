"""반응기 현황판(reactor status board) — GET /api/reactors, POST /api/reactors/{n}/empty.

검증 범위:
 1. use_reactor 레시피 배합을 reactor=2 로 저장 → GET /api/reactors 에 반응기 2 점유.
 2. 파생(is_derived) 2차 이월 배합 — 1차 LOT 가 점유한 반응기 칸은 비워지고, 2차 의 반응기가 채워진다.
 3. POST /api/reactors/3/empty (작업자 세션) → 반응기 3 해제.
 4. GET /api/reactors 는 항상 4개 항목(1~4)을 occuped 플래그와 함께 반환.
 5. POST /api/reactors/9/empty → 400(반응기 범위 1~4).

test_blend.py 의 _mgmt_client / _import_recipe / _stage1_record 패턴을 복제해 최소 client fixture 사용.
"""

from __future__ import annotations

import importlib
import uuid


def _uid():
    return uuid.uuid4().hex[:6]


def _client():
    """책임자 로그인 + CSRF 헤더를 갖춘 TestClient(test_blend._mgmt_client 패턴)."""
    import src.config as cfg
    import src.main as mainmod
    importlib.reload(cfg)
    importlib.reload(mainmod)
    from fastapi.testclient import TestClient
    client = TestClient(mainmod.app)
    client.post("/api/auth/management-login", json={"username": "admin", "password": "admin"})

    def csrf():
        tok = client.cookies.get("csrftoken")
        return {"x-csrftoken": tok} if tok else {}
    return client, csrf


def _login_worker(client, csrf, worker):
    """작업자 등록 + 배합 세션 로그인(배합 기록 POST·반응기 비우기에 필요)."""
    client.post("/api/workers", json={"name": worker}, headers=csrf())
    client.post("/api/blend/session/login", json={"worker": worker}, headers=csrf())


def _import_recipe(client, csrf, product, materials, *, anchor=None, use_reactor=False, is_derived=False):
    """레시피 1건 등록. anchor/use_reactor/is_derived 지정 가능(test_blend._import_recipe 패턴)."""
    header = "반제품명\t" + "\t".join(m[0] for m in materials)
    row = product + "\t" + "\t".join(str(m[1]) for m in materials)
    body = {"raw_text": f"{header}\n{row}", "force": True}
    if anchor:
        body["anchor_material"] = anchor
    if use_reactor:
        body["use_reactor"] = True
    if is_derived:
        body["is_derived"] = True
    res = client.post("/api/recipes/import", json=body, headers=csrf())
    assert res.status_code == 200, res.text
    return res.json()["created_ids"][0]


def _stage1_record(client, csrf, intermediate, worker, reactor=None, total=150.0):
    """1차 배합 기록 생성(반제품명=intermediate). reactor 지정 시 그 반응기를 채운다.
    반환: (id, product_lot). test_blend._stage1_record 확장 — reactor 인자 추가.
    """
    res = client.post("/api/blend/records", json={
        "product_name": intermediate, "worker": worker, "work_date": "2026-07-01",
        "total_amount": total, "scale": None, "reactor": reactor,
        "details": [
            {"material_name": "원료1", "ratio": 100, "theory_amount": total, "actual_amount": total},
        ],
    }, headers=csrf())
    assert res.status_code == 200, res.text
    j = res.json()
    return j["id"], j["product_lot"]


# ── GET /api/reactors: 기본 구조 + 반응기 점유 ──

def test_reactors_always_returns_four_entries():
    """GET /api/reactors 는 항상 4개 항목(1~4)을 occupied 플래그와 함께 반환."""
    client, csrf = _client()
    res = client.get("/api/reactors")
    assert res.status_code == 200, res.text
    slots = res.json()["slots"]
    assert len(slots) == 4
    assert [s["reactor"] for s in slots] == [1, 2, 3, 4]
    # 초기(또는 다른 테스트 영향 무관하게) 각 항목은 occupied bool + 필수 키를 갖는다.
    for s in slots:
        assert isinstance(s["occupied"], bool)
        for k in ("product_name", "product_lot", "amount", "filled_at", "filled_by"):
            assert k in s


def test_reactor_recipe_blend_fills_slot():
    """use_reactor 레시피 배합을 reactor=2 로 저장 → 반응기 2 가 그 product/lot/amount 로 점유."""
    client, csrf = _client()
    worker = "현황작업" + _uid()
    _login_worker(client, csrf, worker)
    # use_reactor 레시피 등록(반응기 번호 요구).
    product = "현황제품" + _uid()
    rid = _import_recipe(client, csrf, product, [("원료A", 60), ("원료B", 40)],
                         use_reactor=True)
    # reactor=2 로 배합 저장.
    res = client.post("/api/blend/records", json={
        "recipe_id": rid, "product_name": product, "worker": worker,
        "work_date": "2026-07-02", "total_amount": 220, "scale": None, "reactor": 2,
        "details": [
            {"material_name": "원료A", "ratio": 60, "theory_amount": 132, "actual_amount": 132},
            {"material_name": "원료B", "ratio": 40, "theory_amount": 88, "actual_amount": 88},
        ],
    }, headers=csrf())
    assert res.status_code == 200, res.text
    lot = res.json()["product_lot"]

    slots = client.get("/api/reactors").json()["slots"]
    by_n = {s["reactor"]: s for s in slots}
    assert by_n[2]["occupied"] is True
    assert by_n[2]["product_name"] == product
    assert by_n[2]["product_lot"] == lot
    assert by_n[2]["amount"] == 220.0
    # filled_by = created_by(관리 세션의 actor) 또는 worker. 관리 세션으로 저장했으므로 actor 값.
    assert by_n[2]["filled_by"] is not None


# ── 파생 2차 이월: 1차 칸 비움 + 2차 반응기 채움 ──

def test_carryover_clears_stage1_slot_and_fills_stage2_reactor():
    """파생 2차 이월 — 1차 LOT 가 점유한 반응기 칸은 비워지고, 2차 의 반응기가 채워진다.

    1차(중간체)를 reactor=1 로 저장(반응기 1 점유). 2차(파생, is_derived)를 reactor=3 로 저장하며
    기준 자재(중간체) 행을 carried_over=true + 1차 LOT 로 이월 → 반응기 1 은 비워지고 3 은 2차 로 채워진다.
    """
    client, csrf = _client()
    worker = "이월현황" + _uid()
    _login_worker(client, csrf, worker)
    intermediate = "현황중간체" + _uid()
    final = "현황최종" + _uid()
    # 1차 배합(중간체) — reactor=1 로 저장 → 반응기 1 점유.
    stage1_id, stage1_lot = _stage1_record(client, csrf, intermediate, worker, reactor=1, total=150.0)
    by_n = {s["reactor"]: s for s in client.get("/api/reactors").json()["slots"]}
    assert by_n[1]["occupied"] is True
    assert by_n[1]["product_lot"] == stage1_lot
    # 2차 레시피 — 파생(is_derived), 기준 자재=중간체.
    rid = _import_recipe(client, csrf, final,
                         [(intermediate, 60), ("최종원료", 40)],
                         anchor=intermediate, use_reactor=True, is_derived=True)
    # 2차 배합 저장 — reactor=3, 기준 자재(중간체) 행을 carried_over=true + 1차 LOT.
    res = client.post("/api/blend/records", json={
        "recipe_id": rid, "product_name": final, "worker": worker,
        "work_date": "2026-07-03", "total_amount": 250, "scale": None, "reactor": 3,
        "details": [
            {"material_name": intermediate, "material_lot": stage1_lot,
             "actual_amount": 999, "carried_over": True},
            {"material_name": "최종원료", "actual_amount": 100},
        ],
    }, headers=csrf())
    assert res.status_code == 200, res.text
    final_lot = res.json()["product_lot"]

    by_n = {s["reactor"]: s for s in client.get("/api/reactors").json()["slots"]}
    # 1차가 있던 반응기 1 은 비워짐(1차 제품이 소비됨).
    assert by_n[1]["occupied"] is False
    # 2차의 반응기 3 은 2차 제품으로 채워짐.
    assert by_n[3]["occupied"] is True
    assert by_n[3]["product_name"] == final
    assert by_n[3]["product_lot"] == final_lot


# ── POST /api/reactors/{n}/empty ──

def test_empty_reactor_clears_slot():
    """POST /api/reactors/3/empty (작업자 세션) → 반응기 3 해제."""
    client, csrf = _client()
    worker = "비우기작업" + _uid()
    _login_worker(client, csrf, worker)
    product = "비우기제품" + _uid()
    rid = _import_recipe(client, csrf, product, [("원료A", 100)], use_reactor=True)
    # 반응기 3 채우기.
    client.post("/api/blend/records", json={
        "recipe_id": rid, "product_name": product, "worker": worker,
        "work_date": "2026-07-02", "total_amount": 100, "scale": None, "reactor": 3,
        "details": [{"material_name": "원료A", "ratio": 100, "theory_amount": 100, "actual_amount": 100}],
    }, headers=csrf())
    by_n = {s["reactor"]: s for s in client.get("/api/reactors").json()["slots"]}
    assert by_n[3]["occupied"] is True

    # 작업자 세션으로 반응기 3 비우기.
    res = client.post("/api/reactors/3/empty", headers=csrf())
    assert res.status_code == 200, res.text
    assert res.json() == {"status": "ok", "reactor": 3}
    by_n = {s["reactor"]: s for s in client.get("/api/reactors").json()["slots"]}
    assert by_n[3]["occupied"] is False


def test_empty_reactor_out_of_range_rejected():
    """POST /api/reactors/9/empty → 400(반응기 범위 1~4)."""
    client, csrf = _client()
    worker = "범위작업" + _uid()
    _login_worker(client, csrf, worker)
    res = client.post("/api/reactors/9/empty", headers=csrf())
    assert res.status_code == 400


def test_empty_reactor_requires_worker_session():
    """작업자 세션 없이 POST /api/reactors/1/empty → 401."""
    client, csrf = _client()
    # 세션 로그인 없이 호출 → require_blend_worker 가 401.
    res = client.post("/api/reactors/1/empty", headers=csrf())
    assert res.status_code == 401
