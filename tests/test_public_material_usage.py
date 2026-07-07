"""자재 사용량(불출) 공개 API — 상위 재고 대시보드 연동 (로드맵 P3).

GET /api/public/material-usage?start_date&end_date&group=total|day|month
트레이 공개 API 와 동일한 내부망/토큰 보호(main.py protected_prefixes).
"""

from __future__ import annotations

import importlib
import uuid

from src.services import blend_service as bs
from tests.test_blend import _make_db  # 배합 스키마 재사용


def _seed_record(conn, product, work_date, details):
    return bs.create_blend_record(
        conn,
        recipe_id=None, product_name=product, ink_name=None, position=None,
        worker="w", work_date=work_date, work_time=None,
        total_amount=sum(d["actual_amount"] for d in details), scale=None, note=None,
        details=details, created_by="현장", created_at=f"{work_date}T00:00:00Z",
    )


def test_material_usage_periods_grouping():
    conn = _make_db()
    a = {"material_name": "MatA", "material_code": "A01", "ratio": 60,
         "theory_amount": 60, "actual_amount": 60}
    b = {"material_name": "MatB", "material_code": "B01", "ratio": 40,
         "theory_amount": 40, "actual_amount": 40}
    _seed_record(conn, "P1", "2026-07-01", [dict(a), dict(b)])
    _seed_record(conn, "P1", "2026-07-01", [dict(a)])
    _seed_record(conn, "P1", "2026-07-02", [dict(b)])
    _seed_record(conn, "P1", "2026-08-01", [dict(a)])  # 기간 밖(월별 테스트용)

    # 기간 합계 (7월만)
    res = bs.material_usage_periods(conn, start_date="2026-07-01", end_date="2026-07-31")
    by_code = {i["material_code"]: i for i in res["items"]}
    assert by_code["A01"]["total_actual"] == 120.0 and by_code["A01"]["batch_count"] == 2
    assert by_code["B01"]["total_actual"] == 80.0 and by_code["B01"]["batch_count"] == 2
    assert res["record_count"] == 3
    assert res["total_weight"] == 200.0
    assert res["unit"] == "g"

    # 일별
    res = bs.material_usage_periods(conn, start_date="2026-07-01", end_date="2026-07-31", group="day")
    days = {(i["period"], i["material_code"]): i["total_actual"] for i in res["items"]}
    assert days[("2026-07-01", "A01")] == 120.0
    assert days[("2026-07-01", "B01")] == 40.0
    assert days[("2026-07-02", "B01")] == 40.0

    # 월별 (7~8월)
    res = bs.material_usage_periods(conn, start_date="2026-07-01", end_date="2026-08-31", group="month")
    months = {(i["period"], i["material_code"]): i["total_actual"] for i in res["items"]}
    assert months[("2026-07", "A01")] == 120.0
    assert months[("2026-08", "A01")] == 60.0

    # 취소 기록은 제외
    conn.execute("UPDATE blend_records SET status='canceled' WHERE work_date='2026-07-02'")
    res = bs.material_usage_periods(conn, start_date="2026-07-01", end_date="2026-07-31")
    assert {i["material_code"] for i in res["items"]} == {"A01", "B01"}
    assert res["record_count"] == 2


def test_erp_code_resolution_from_aliases():
    """ERP 품목코드(RM…)는 material_aliases 별칭에서 우선 해석 —
    IRMS category('기타' 등)는 ERP 코드가 아니므로 대체 키가 필요하다."""
    conn = _make_db()
    conn.execute(
        "CREATE TABLE material_aliases (id INTEGER PRIMARY KEY, "
        "material_id INTEGER NOT NULL, alias_name TEXT NOT NULL)"
    )
    # MatA: RM 별칭 보유 / MatB: 별칭 없음, 저장 코드가 RM 형태 / MatC: 아무 코드 없음
    conn.execute("INSERT INTO materials (name, unit_type, unit, category) VALUES ('MatA','weight','g','기타')")
    aid = conn.execute("SELECT id FROM materials WHERE name='MatA'").fetchone()["id"]
    conn.execute("INSERT INTO material_aliases (material_id, alias_name) VALUES (?, 'RM00123')", (aid,))

    _seed_record(conn, "P2", "2026-07-10", [
        {"material_name": "MatA", "material_code": "기타", "ratio": 50,
         "theory_amount": 50, "actual_amount": 50},
        {"material_name": "MatB", "material_code": "RM00456", "ratio": 30,
         "theory_amount": 30, "actual_amount": 30},
        {"material_name": "MatC", "material_code": "", "ratio": 20,
         "theory_amount": 20, "actual_amount": 20},
    ])
    res = bs.material_usage_periods(conn, start_date="2026-07-10", end_date="2026-07-10")
    codes = {i["material_name"]: i["erp_code"] for i in res["items"]}
    assert codes["MatA"] == "RM00123"   # 별칭 우선
    assert codes["MatB"] == "RM00456"   # 저장 코드가 RM 형태면 사용
    assert codes["MatC"] == ""          # 매칭 불가 → 빈 값(대시보드에서 제외 가능)


def _client():
    import src.config as cfg
    import src.main as mainmod

    importlib.reload(cfg)
    importlib.reload(mainmod)
    from fastapi.testclient import TestClient

    return TestClient(mainmod.app)


def test_public_material_usage_route():
    client = _client()
    tok = None
    client.get("/api/blend/records")
    tok = client.cookies.get("csrftoken")
    headers = {"x-csrftoken": tok} if tok else {}

    worker = "불출작업" + uuid.uuid4().hex[:6]
    prod = "MU" + uuid.uuid4().hex[:6].upper()
    client.post("/api/workers", json={"name": worker}, headers=headers)
    client.post("/api/blend/session/login", json={"worker": worker}, headers=headers)
    created = client.post("/api/blend/records", json={
        "product_name": prod, "worker": worker, "work_date": "2026-07-03",
        "total_amount": 500,
        "details": [
            {"material_name": "MatX", "material_code": "X01",
             "ratio": 100, "theory_amount": 500, "actual_amount": 500},
        ],
    }, headers=headers)
    assert created.status_code == 200, created.text

    # 공개 API 는 내부망 IP 로 접근(미들웨어 보호 — 사설 IP 위장 클라이언트)
    import src.main as mainmod
    from fastapi.testclient import TestClient
    internal = TestClient(mainmod.app, client=("192.168.11.108", 50000))

    res = internal.get("/api/public/material-usage",
                       params={"start_date": "2026-07-01", "end_date": "2026-07-31"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["unit"] == "g" and body["group"] == "total"
    mine = [i for i in body["items"] if i["material_code"] == "X01"]
    assert mine and mine[0]["total_actual"] >= 500.0

    # group=day 에 period 채워짐
    res = internal.get("/api/public/material-usage",
                       params={"start_date": "2026-07-01", "end_date": "2026-07-31", "group": "day"})
    assert any(i["period"] == "2026-07-03" for i in res.json()["items"])

    # 검증: 형식 오류 400, 잘못된 group 422, 역전 기간 400
    assert internal.get("/api/public/material-usage",
                        params={"start_date": "07/01/2026"}).status_code == 400
    assert internal.get("/api/public/material-usage",
                        params={"group": "week"}).status_code == 422
    assert internal.get("/api/public/material-usage",
                        params={"start_date": "2026-07-31", "end_date": "2026-07-01"}).status_code == 400


def test_public_material_usage_blocked_outside_internal_network():
    """내부망 밖(비사설 IP)에서는 403 — 트레이 공개 API 와 동일 보호."""
    client = _client()  # 기본 TestClient 는 사설 IP 가 아님
    res = client.get("/api/public/material-usage")
    assert res.status_code == 403
