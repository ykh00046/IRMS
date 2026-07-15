"""이어서 계량(연속 배합) — 한 레시피·동일 총량으로 N개 로트를 한 번에 저장하는 API 테스트.

설계: docs/02-design/features/blend-overhaul.design.md
스펙: scratchpad/continuous-blend-backend-spec.md

핵심 규칙:
- 서버가 레시피에서 비율·이론량을 재산출(클라이언트 ratio/theory 불신, 감사 F-5).
- 저장 전 전 로트 도출·편차검사 → 하나라도 실패하면 아무 기록도 만들지 않음(원자성).
- product_lot 은 로트마다 연속 채번(…01, …02).
- 작업자 세션(POST /blend/session/login) 필수 — 없으면 401.

기존 tests/test_blend.py · test_blend_server_derived_theory.py 의 실제 패턴을 그대로 따른다.
"""

from __future__ import annotations

import importlib
import uuid


def _client():
    """FastAPI TestClient — 기존 테스트와 동일한 reload 패턴 (src.config, src.main)."""
    import src.config as cfg
    import src.main as mainmod

    importlib.reload(cfg)
    importlib.reload(mainmod)
    from fastapi.testclient import TestClient

    return TestClient(mainmod.app)


def _uid():
    return uuid.uuid4().hex[:8].upper()


def _csrf_headers(client):
    """csrftoken 쿠키 → x-csrftoken 헤더. 기존 테스트의 csrf_headers() 와 동일."""
    tok = client.cookies.get("csrftoken")
    return {"x-csrftoken": tok} if tok else {}


def _manager(client):
    """관리자 로그인 → csrf 헤더 확보. 기존 test_blend_server_derived_theory._mgr 와 동일."""
    assert client.post(
        "/api/auth/management-login", json={"username": "admin", "password": "admin"}
    ).status_code == 200
    return _csrf_headers(client)


def _import_recipe(client, headers, product, a, b):
    """레시피 가져오기(원료A a% : 원료B b%). material_name 이 시드되는 실제 경로."""
    raw = f"반제품명\t원료A\t원료B\n{product}\t{a}\t{b}"
    res = client.post("/api/recipes/import", json={"raw_text": raw, "force": True}, headers=headers)
    assert res.status_code == 200, res.text
    return res.json()["created_ids"][0]


def _blend_session(client, headers, worker=None):
    """배합 작업자 세션 로그인. csrf 쿠키 확보(GET) → 작업자 등록 → 세션 로그인.

    기존 tests/test_blend.py(test_blend_update_route_requires_manager_and_full_edit) 의
    실제 시퀀스를 그대로 복사.
    """
    worker = worker or ("연속작업" + _uid()[:6])
    client.get("/api/blend/records")  # csrf 쿠키 확보
    client.post("/api/workers", json={"name": worker}, headers=headers)
    client.post("/api/blend/session/login", json={"worker": worker}, headers=headers)
    return worker


def _details(actual_a, actual_b, lot_a=None, lot_b=None):
    """2자재(원료A/원료B) 로트 상세. actual 은 사람이 아는 값, ratio/theory 는 서버가 무시."""
    return [
        {"material_name": "원료A", "ratio": 60, "theory_amount": actual_a, "actual_amount": actual_a, "material_lot": lot_a},
        {"material_name": "원료B", "ratio": 40, "theory_amount": actual_b, "actual_amount": actual_b, "material_lot": lot_b},
    ]


def test_continuous_two_lots_creates_sequential_lots():
    """정상 2로트: 이론량대로 실제량 채운 2개 로트 → 200, created==2, product_lots 연속 순번(01,02)."""
    client = _client()
    headers = _manager(client)
    product = f"CB2{_uid()}"
    rid = _import_recipe(client, headers, product, 60, 40)   # 원료A 60 : 원료B 40
    _blend_session(client, headers)

    res = client.post("/api/blend/records/continuous", json={
        "recipe_id": rid, "product_name": product, "work_date": "2026-07-15",
        "total_amount": 100,
        "lots": [_details(60, 40), _details(60, 40)],
    }, headers=headers)
    assert res.status_code == 200, res.text
    j = res.json()
    assert j["created"] == 2
    assert len(j["product_lots"]) == 2
    # 동일 제품·날짜로 연속 채번 — 01, 02
    assert j["product_lots"][0].endswith("01")
    assert j["product_lots"][1].endswith("02")
    # 서버 도출 확인: ratio/theory 가 레시피 기준(60/40)으로 저장됐는지
    rec0 = client.get(f"/api/blend/records/{j['ids'][0]}").json()
    by_name = {d["material_name"]: d for d in rec0["details"]}
    assert by_name["원료A"]["theory_amount"] == 60
    assert by_name["원료B"]["theory_amount"] == 40
    assert by_name["원료A"]["ratio"] == 60


def test_continuous_lot_numbers_continue_after_existing_record():
    """순번 연속: 같은 제품·날짜에 단건 1건 있을 때 연속 2로트 → 02, 03."""
    client = _client()
    headers = _manager(client)
    product = f"CBC{_uid()}"
    rid = _import_recipe(client, headers, product, 60, 40)
    worker = _blend_session(client, headers)

    # 같은 제품·날짜로 단건 1건 먼저 저장(순번 01 점유)
    single = client.post("/api/blend/records", json={
        "recipe_id": rid, "product_name": product, "worker": worker, "work_date": "2026-07-15",
        "total_amount": 100,
        "details": _details(60, 40),
    }, headers=headers)
    assert single.status_code == 200, single.text
    assert single.json()["product_lot"].endswith("01")

    res = client.post("/api/blend/records/continuous", json={
        "recipe_id": rid, "product_name": product, "work_date": "2026-07-15",
        "total_amount": 100,
        "lots": [_details(60, 40), _details(60, 40)],
    }, headers=headers)
    assert res.status_code == 200, res.text
    lots = res.json()["product_lots"]
    assert lots[0].endswith("02")
    assert lots[1].endswith("03")


def test_continuous_tolerance_violation_creates_nothing():
    """편차 초과 로트: 한 로트의 한 자재를 tolerance 초과 → 400, DB 에 기록 0건(원자성)."""
    from src.db import get_connection

    client = _client()
    headers = _manager(client)
    product = f"CBV{_uid()}"
    rid = _import_recipe(client, headers, product, 60, 40)
    _blend_session(client, headers)

    # 기존 기록 수(0건) 확인
    with get_connection() as conn:
        before = conn.execute(
            "SELECT COUNT(*) FROM blend_records WHERE product_name = ?", (product,)
        ).fetchone()[0]

    # 첫 로트는 정상, 둘째 로트 원료A 를 tolerance 초과(이론 60 → 실제 90)
    res = client.post("/api/blend/records/continuous", json={
        "recipe_id": rid, "product_name": product, "work_date": "2026-07-15",
        "total_amount": 100,
        "lots": [_details(60, 40), _details(90, 40)],
    }, headers=headers)
    assert res.status_code == 400, res.text
    assert "편차" in res.json()["detail"]

    # 원자성 — DB 에 추가된 기록이 없어야 한다
    with get_connection() as conn:
        after = conn.execute(
            "SELECT COUNT(*) FROM blend_records WHERE product_name = ?", (product,)
        ).fetchone()[0]
    assert after == before


def test_continuous_requires_blend_session():
    """세션 없음: 작업자 세션 없이 호출 → 401 (BLEND_WORKER_REQUIRED)."""
    client = _client()
    headers = _manager(client)
    product = f"CBS{_uid()}"
    rid = _import_recipe(client, headers, product, 60, 40)
    # _blend_session 호출 생략 — 세션 없음

    res = client.post("/api/blend/records/continuous", json={
        "recipe_id": rid, "product_name": product, "work_date": "2026-07-15",
        "total_amount": 100,
        "lots": [_details(60, 40), _details(60, 40)],
    }, headers=headers)
    assert res.status_code == 401
    assert res.json()["detail"] == "BLEND_WORKER_REQUIRED"


def test_continuous_empty_lots_rejected():
    """빈 lots: lots=[] → 400."""
    client = _client()
    headers = _manager(client)
    product = f"CBE{_uid()}"
    rid = _import_recipe(client, headers, product, 60, 40)
    _blend_session(client, headers)

    res = client.post("/api/blend/records/continuous", json={
        "recipe_id": rid, "product_name": product, "work_date": "2026-07-15",
        "total_amount": 100,
        "lots": [],
    }, headers=headers)
    assert res.status_code == 400
    assert "로트" in res.json()["detail"]
