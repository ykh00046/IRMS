"""Unit + route tests for blend-overhaul (배합 실적 / 잉크 계량 재구축).

Design: docs/02-design/features/blend-overhaul.design.md
"""

from __future__ import annotations

import sqlite3

from src.services import blend_service as bs
from src.services import viscosity_service as vs


def _make_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE materials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL, unit_type TEXT, unit TEXT DEFAULT 'g',
            category TEXT, is_active INTEGER DEFAULT 1
        );
        CREATE TABLE recipes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_name TEXT, position TEXT, ink_name TEXT,
            status TEXT DEFAULT 'completed', created_at TEXT DEFAULT '2026-01-01',
            revision_of INTEGER, base_total REAL, base_totals TEXT
        );
        CREATE TABLE recipe_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipe_id INTEGER, material_id INTEGER,
            value_weight REAL, value_text TEXT
        );
        CREATE TABLE blend_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_lot TEXT NOT NULL, recipe_id INTEGER, product_name TEXT NOT NULL,
            ink_name TEXT, position TEXT, worker TEXT NOT NULL, work_date TEXT NOT NULL,
            work_time TEXT, total_amount REAL NOT NULL, scale TEXT,
            status TEXT NOT NULL DEFAULT 'completed', note TEXT, reactor INTEGER,
            manual_entry INTEGER NOT NULL DEFAULT 0,
            reviewed_by TEXT, reviewed_at TEXT, approved_by TEXT, approved_at TEXT,
            worker_sign TEXT, reviewed_sign TEXT, approved_sign TEXT,
            created_by TEXT, created_at TEXT NOT NULL, updated_at TEXT
        );
        CREATE TABLE blend_details (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            blend_record_id INTEGER NOT NULL, material_id INTEGER,
            material_code TEXT, material_name TEXT NOT NULL, material_lot TEXT,
            ratio REAL, theory_amount REAL, actual_amount REAL,
            sequence_order INTEGER NOT NULL DEFAULT 0,
            manual_entry INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
        );
        CREATE TABLE viscosity_products (
            id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT UNIQUE, name TEXT,
            target REAL, lower_limit REAL, upper_limit REAL, sigma_k REAL DEFAULT 3,
            rpm REAL, temperature REAL, remind_daily INTEGER DEFAULT 0,
            use_reactor INTEGER DEFAULT 0, is_active INTEGER DEFAULT 1, created_at TEXT
        );
        CREATE TABLE viscosity_readings (
            id INTEGER PRIMARY KEY AUTOINCREMENT, product_id INTEGER, lot_no TEXT,
            viscosity REAL, measured_date TEXT, memo TEXT, recipe_material TEXT,
            material_lot TEXT, reactor INTEGER, created_by TEXT, created_at TEXT, blend_record_id INTEGER
        );
        """
    )
    return conn


def _seed_recipe(conn, product="잉크A", weights=(60.0, 30.0, 10.0)):
    rid = conn.execute(
        "INSERT INTO recipes (product_name, ink_name, status) VALUES (?, ?, 'completed')",
        (product, f"{product}-ink"),
    ).lastrowid
    for i, w in enumerate(weights):
        mid = conn.execute(
            "INSERT INTO materials (name, unit_type, unit, category) VALUES (?, 'weight', 'g', ?)",
            (f"원료{i+1}", f"M00{i+1}"),
        ).lastrowid
        conn.execute(
            "INSERT INTO recipe_items (recipe_id, material_id, value_weight) VALUES (?, ?, ?)",
            (rid, mid, w),
        )
    return rid


# ── 비율/이론량 ─────────────────────────────────────────────────
def test_compute_ratios():
    assert bs.compute_ratios([60, 30, 10]) == [60.0, 30.0, 10.0]
    assert bs.compute_ratios([0, 0]) == [0.0, 0.0]


def test_scale_theory():
    # 60:30:10 레시피를 총량 200g 으로 → 120/60/20
    assert bs.scale_theory([60, 30, 10], 200) == [120.0, 60.0, 20.0]


def test_get_recipe_for_blend_scales_to_total():
    conn = _make_db()
    rid = _seed_recipe(conn, weights=(60, 30, 10))
    result = bs.get_recipe_for_blend(conn, rid, total_amount=200)
    assert result["total_amount"] == 200.0
    assert result["base_total"] == 100.0
    theory = [it["theory_amount"] for it in result["items"]]
    assert theory == [120.0, 60.0, 20.0]
    ratios = [it["ratio"] for it in result["items"]]
    assert ratios == [60.0, 30.0, 10.0]


def test_get_recipe_for_blend_default_totals_only_when_designated():
    """기준 배합량을 지정한 레시피만 default_totals 반환(버튼 노출, 최대 3개).
    미지정 레시피는 빈 목록 — 배합 화면에 기준량 버튼이 뜨지 않는다."""
    conn = _make_db()
    rid = _seed_recipe(conn, product="BASE1", weights=(60, 40))  # 합계 100
    # 미지정 → 빈 목록 (버튼 없음)
    res = bs.get_recipe_for_blend(conn, rid)
    assert res["default_totals"] == [] and res["default_total"] is None
    # 다중 지정(CSV) → 순서 보존, 최대 3개
    conn.execute("UPDATE recipes SET base_totals = '3924.38,2000,100' WHERE id = ?", (rid,))
    result = bs.get_recipe_for_blend(conn, rid)
    assert result["default_totals"] == [3924.38, 2000.0, 100.0]
    assert result["default_total"] == 3924.38  # 하위호환(첫 값)
    assert result["base_total"] == 100.0  # 합계는 그대로(비율 계산용)
    # (구) 단일 base_total 만 있는 기존 레시피 → 폴백
    conn.execute("UPDATE recipes SET base_totals = NULL, base_total = 500 WHERE id = ?", (rid,))
    assert bs.get_recipe_for_blend(conn, rid)["default_totals"] == [500.0]


def test_import_stores_and_inherits_base_total():
    """등록 시 기준 배합량 저장 + 수정 등록(개정) 시 미입력이면 승계."""
    import importlib
    import uuid

    import src.config as cfg
    import src.main as mainmod

    importlib.reload(cfg)
    importlib.reload(mainmod)
    from fastapi.testclient import TestClient

    from src.db import get_connection

    client = TestClient(mainmod.app)
    client.post("/api/auth/management-login", json={"username": "admin", "password": "admin"})
    tok = client.cookies.get("csrftoken")
    headers = {"x-csrftoken": tok} if tok else {}
    prod = "BT" + uuid.uuid4().hex[:6]

    raw = f"반제품명\tMatA\tMatB\n{prod}\t60\t40"
    res = client.post("/api/recipes/import",
                      json={"raw_text": raw, "force": True,
                            "base_totals": [3924.38, 2000, 100]},
                      headers=headers)
    assert res.status_code == 200, res.text
    rid = res.json()["created_ids"][0]
    with get_connection() as conn:
        assert conn.execute("SELECT base_totals FROM recipes WHERE id=?",
                            (rid,)).fetchone()[0] == "3924.38,2000,100"

    # 배합 API 에도 반영(최대 3개 목록)
    detail = client.get(f"/api/blend/recipes/{rid}").json()
    assert detail["default_totals"] == [3924.38, 2000.0, 100.0]

    # 4개 이상은 422
    bad = client.post("/api/recipes/import",
                      json={"raw_text": raw, "force": True,
                            "base_totals": [1, 2, 3, 4]}, headers=headers)
    assert bad.status_code == 422

    # 개정(미입력) → 승계
    raw2 = f"반제품명\tMatA\tMatB\n{prod}\t70\t30"
    res2 = client.post("/api/recipes/import",
                       json={"raw_text": raw2, "force": True, "revision_of": rid},
                       headers=headers)
    assert res2.status_code == 200, res2.text
    rid2 = res2.json()["created_ids"][0]
    with get_connection() as conn:
        assert conn.execute("SELECT base_totals FROM recipes WHERE id=?",
                            (rid2,)).fetchone()[0] == "3924.38,2000,100"

    # (구) 단일 base_total 필드도 여전히 동작(하위호환)
    prod2 = "BT2" + uuid.uuid4().hex[:5]
    raw3 = f"반제품명\tMatA\n{prod2}\t100"
    res3 = client.post("/api/recipes/import",
                       json={"raw_text": raw3, "force": True, "base_total": 777},
                       headers=headers)
    assert res3.status_code == 200
    rid3 = res3.json()["created_ids"][0]
    assert client.get(f"/api/blend/recipes/{rid3}").json()["default_totals"] == [777.0]


def test_get_recipe_for_blend_resolves_to_latest_revision():
    """옛(개정 전) 레시피 id 로 요청해도 최신 개정판을 돌려준다.

    배합 화면을 계속 띄워두는 단말은 목록이 낡아 옛 id 로 요청할 수 있다 —
    '레시피 수정이 배합에 반영 안 됨' 원인(2026-07-08)."""
    conn = _make_db()
    old_id = _seed_recipe(conn, product="NTOP", weights=(60, 40))
    # 개정판(값 변경): revision_of=old_id
    new_id = _seed_recipe(conn, product="NTOP", weights=(70, 30))
    conn.execute("UPDATE recipes SET revision_of = ? WHERE id = ?", (old_id, new_id))

    result = bs.get_recipe_for_blend(conn, old_id, total_amount=100)
    assert result["recipe"]["id"] == new_id                       # 최신판으로 귀결
    assert [it["theory_amount"] for it in result["items"]] == [70.0, 30.0]

    # 개정판이 취소되면 원본 유지
    conn.execute("UPDATE recipes SET status = 'canceled' WHERE id = ?", (new_id,))
    result = bs.get_recipe_for_blend(conn, old_id, total_amount=100)
    assert result["recipe"]["id"] == old_id


def test_get_recipe_for_blend_defaults_total_to_base():
    conn = _make_db()
    rid = _seed_recipe(conn, weights=(60, 30, 10))
    result = bs.get_recipe_for_blend(conn, rid)
    assert result["total_amount"] == 100.0  # 미지정 → 절대중량 합계


# ── product_lot 생성 ────────────────────────────────────────────
def test_generate_product_lot_sequence():
    conn = _make_db()
    lot1 = bs.generate_product_lot(conn, "잉크A", "2026-06-24")
    assert lot1 == "잉크A26062401"
    # 같은 날 같은 제품 1건 저장 후 다음 순번
    conn.execute(
        "INSERT INTO blend_records (product_lot, product_name, worker, work_date, total_amount, created_at) "
        "VALUES (?, '잉크A', 'w', '2026-06-24', 100, '2026-06-24')",
        (lot1,),
    )
    assert bs.generate_product_lot(conn, "잉크A", "2026-06-24") == "잉크A26062402"


# ── 기록 생성/조회 + 편차 ───────────────────────────────────────
def test_create_and_get_blend_record_variance():
    conn = _make_db()
    rid = _seed_recipe(conn, weights=(60, 30, 10))
    record_id = bs.create_blend_record(
        conn,
        recipe_id=rid, product_name="잉크A", ink_name="잉크A-ink", position=None,
        worker="홍길동", work_date="2026-06-24", work_time="10:00:00",
        total_amount=200, scale="M-65", note="테스트",
        details=[
            {"material_name": "원료1", "ratio": 60, "theory_amount": 120, "actual_amount": 121, "material_lot": "L1"},
            {"material_name": "원료2", "ratio": 30, "theory_amount": 60, "actual_amount": 59},
            {"material_name": "원료3", "ratio": 10, "theory_amount": 20, "actual_amount": 20},
        ],
        created_by="현장", created_at="2026-06-24T01:00:00Z",
    )
    rec = bs.get_blend_record(conn, record_id)
    assert rec["product_lot"] == "잉크A26062401"
    assert len(rec["details"]) == 3
    d0 = rec["details"][0]
    assert d0["variance"] == 1.0
    assert d0["variance_pct"] == round(1 / 120 * 100, 2)
    v = rec["variance"]
    assert v["theory_total"] == 200.0
    assert v["actual_total"] == 200.0
    assert v["net_variance"] == 0.0
    assert v["abs_variance"] == 2.0  # |+1| + |-1| + 0


def test_update_blend_record_full_edit_preserves_lot_and_sign():
    conn = _make_db()
    rid = _seed_recipe(conn, weights=(60, 40))
    record_id = bs.create_blend_record(
        conn,
        recipe_id=rid, product_name="EditP", ink_name=None, position=None,
        worker="김작업", work_date="2026-07-01", work_time="09:00:00",
        total_amount=100, scale="M-65", note="원본",
        details=[
            {"material_name": "A", "ratio": 60, "theory_amount": 60, "actual_amount": 60},
            {"material_name": "B", "ratio": 40, "theory_amount": 40, "actual_amount": 40},
        ],
        created_by="현장", created_at="2026-07-01T00:00:00Z",
        worker_sign="data:image/png;base64,AAA",
    )
    lot = bs.get_blend_record(conn, record_id)["product_lot"]

    bs.update_blend_record(
        conn, record_id,
        product_name="EditP2", ink_name=None, position=None, worker="이수정",
        work_date="2026-07-02", work_time="11:00:00", total_amount=150, scale="S1",
        note="수정본", reactor=None,
        details=[
            {"material_name": "A", "ratio": 50, "theory_amount": 75, "actual_amount": 75},
            {"material_name": "C", "ratio": 50, "theory_amount": 75, "actual_amount": 75},
        ],
        updated_at="2026-07-02T00:00:00Z",
    )
    rec = bs.get_blend_record(conn, record_id)
    assert rec["product_lot"] == lot                          # LOT 보존
    assert rec["product_name"] == "EditP2"
    assert rec["worker"] == "이수정"
    assert rec["total_amount"] == 150.0
    assert rec["note"] == "수정본"
    assert rec["worker_sign"] == "data:image/png;base64,AAA"  # 서명 보존
    assert [d["material_name"] for d in rec["details"]] == ["A", "C"]  # 상세 전량 교체


def test_blend_update_route_requires_manager_and_full_edit():
    import importlib
    import uuid

    import src.config as cfg
    import src.main as mainmod

    importlib.reload(cfg)
    importlib.reload(mainmod)
    from fastapi.testclient import TestClient

    client = TestClient(mainmod.app)
    worker = "수정작업" + uuid.uuid4().hex[:6]
    prod = "UPDP" + uuid.uuid4().hex[:4]

    def csrf_headers():
        tok = client.cookies.get("csrftoken")
        return {"x-csrftoken": tok} if tok else {}

    client.get("/api/blend/records")  # csrf 쿠키 확보
    client.post("/api/workers", json={"name": worker}, headers=csrf_headers())
    client.post("/api/blend/session/login", json={"worker": worker}, headers=csrf_headers())
    created = client.post("/api/blend/records", json={
        "product_name": prod, "worker": worker, "work_date": "2026-07-01",
        "total_amount": 100, "scale": "M-65",
        "details": [
            {"material_name": "A", "ratio": 60, "theory_amount": 60, "actual_amount": 60},
            {"material_name": "B", "ratio": 40, "theory_amount": 40, "actual_amount": 40},
        ],
    }, headers=csrf_headers())
    assert created.status_code == 200, created.text
    rid = created.json()["id"]
    lot = created.json()["product_lot"]

    # 현장(배합 세션만, 관리 미로그인)은 수정 불가
    blocked = client.request("PUT", f"/api/blend/records/{rid}", json={
        "product_name": prod, "worker": worker, "work_date": "2026-07-01", "total_amount": 100,
        "details": [{"material_name": "A", "ratio": 100, "theory_amount": 100, "actual_amount": 100}],
    }, headers=csrf_headers())
    assert blocked.status_code in (401, 403)

    # 책임자(admin) 로그인 후 전체 수정 → 200
    client.post("/api/auth/management-login", json={"username": "admin", "password": "admin"})
    res = client.request("PUT", f"/api/blend/records/{rid}", json={
        "product_name": prod + "X", "worker": "책임수정", "work_date": "2026-07-03",
        "total_amount": 200, "scale": "S9", "note": "정정",
        "details": [
            {"material_name": "A", "ratio": 50, "theory_amount": 100, "actual_amount": 100},
            {"material_name": "C", "ratio": 50, "theory_amount": 100, "actual_amount": 100},
        ],
    }, headers=csrf_headers())
    assert res.status_code == 200, res.text
    j = res.json()
    assert j["product_lot"] == lot                 # LOT 보존
    assert j["product_name"] == prod + "X"
    assert j["worker"] == "책임수정"
    assert [d["material_name"] for d in j["details"]] == ["A", "C"]

    # 편차 초과는 400
    bad = client.request("PUT", f"/api/blend/records/{rid}", json={
        "product_name": prod, "worker": "x", "work_date": "2026-07-03", "total_amount": 100,
        "details": [{"material_name": "A", "ratio": 100, "theory_amount": 100, "actual_amount": 105}],
    }, headers=csrf_headers())
    assert bad.status_code == 400


def test_blend_approve_requires_manager():
    """결재(검토/승인 기록)는 책임자 전용 — 현장 무로그인은 401/403."""
    import importlib
    import uuid

    import src.config as cfg
    import src.main as mainmod

    importlib.reload(cfg)
    importlib.reload(mainmod)
    from fastapi.testclient import TestClient

    client = TestClient(mainmod.app)
    worker = "결재작업" + uuid.uuid4().hex[:6]
    prod = "APPR" + uuid.uuid4().hex[:4]

    def headers():
        tok = client.cookies.get("csrftoken")
        return {"x-csrftoken": tok} if tok else {}

    client.get("/api/blend/records")
    client.post("/api/workers", json={"name": worker}, headers=headers())
    client.post("/api/blend/session/login", json={"worker": worker}, headers=headers())
    created = client.post("/api/blend/records", json={
        "product_name": prod, "worker": worker, "work_date": "2026-07-05",
        "total_amount": 100,
        "details": [{"material_name": "A", "ratio": 100, "theory_amount": 100, "actual_amount": 100}],
    }, headers=headers())
    assert created.status_code == 200, created.text
    rid = created.json()["id"]

    # 배합 세션만으로는 결재 불가
    blocked = client.post(f"/api/blend/records/{rid}/approve",
                          json={"role": "review", "name": "몰래검토"}, headers=headers())
    assert blocked.status_code in (401, 403)

    # 책임자 로그인 후 가능
    client.post("/api/auth/management-login", json={"username": "admin", "password": "admin"})
    ok = client.post(f"/api/blend/records/{rid}/approve",
                     json={"role": "review", "name": "김검토"}, headers=headers())
    assert ok.status_code == 200, ok.text


def test_blend_viscosity_route_links_reading():
    """POST /blend/records/{id}/viscosity — 점도 관리 화면의 저장 경로.
    배합 실적에 연계(blend_record_id)되고, 같은 LOT 재등록은 409.
    (2026-07-07 실수로 제거돼 점도 등록이 깨졌던 회귀 방지용 라우트 테스트.)"""
    import importlib
    import uuid

    import src.config as cfg
    import src.main as mainmod

    importlib.reload(cfg)
    importlib.reload(mainmod)
    from fastapi.testclient import TestClient

    client = TestClient(mainmod.app)
    worker = "점도작업" + uuid.uuid4().hex[:6]
    prod = "VISCR" + uuid.uuid4().hex[:4]

    def headers():
        tok = client.cookies.get("csrftoken")
        return {"x-csrftoken": tok} if tok else {}

    client.get("/api/blend/records")  # csrf 쿠키 확보
    client.post("/api/workers", json={"name": worker}, headers=headers())
    client.post("/api/blend/session/login", json={"worker": worker}, headers=headers())
    created = client.post("/api/blend/records", json={
        "product_name": prod, "worker": worker, "work_date": "2026-07-05",
        "total_amount": 100,
        "details": [{"material_name": "A", "ratio": 100, "theory_amount": 100, "actual_amount": 100}],
    }, headers=headers())
    assert created.status_code == 200, created.text
    rid = created.json()["id"]

    res = client.post(f"/api/blend/records/{rid}/viscosity",
                      json={"viscosity": 1234.5, "memo": "route"}, headers=headers())
    assert res.status_code == 200, res.text
    linked = res.json()["viscosity"]
    assert len(linked) == 1 and linked[0]["viscosity"] == 1234.5

    dup = client.post(f"/api/blend/records/{rid}/viscosity",
                      json={"viscosity": 2000}, headers=headers())
    assert dup.status_code == 409  # 같은 LOT 중복 등록 차단


def test_list_blend_records_filters():
    conn = _make_db()
    rid = _seed_recipe(conn)
    for d, worker in [("2026-06-20", "김"), ("2026-06-24", "이"), ("2026-06-25", "김")]:
        bs.create_blend_record(
            conn, recipe_id=rid, product_name="잉크A", ink_name=None, position=None,
            worker=worker, work_date=d, work_time=None, total_amount=100, scale=None, note=None,
            details=[{"material_name": "원료1", "theory_amount": 100, "actual_amount": 100}],
            created_by="t", created_at="2026-06-24T00:00:00Z",
        )
    assert len(bs.list_blend_records(conn)) == 3
    assert len(bs.list_blend_records(conn, worker="김")) == 2
    ranged = bs.list_blend_records(conn, start_date="2026-06-24", end_date="2026-06-30")
    assert len(ranged) == 2
    assert len(bs.list_blend_records(conn, search="잉크A")) == 3


# ── 전자서명 저장 ───────────────────────────────────────────────
def test_worker_signature_stored():
    conn = _make_db()
    rid = _seed_recipe(conn)
    sign = "data:image/png;base64,iVBORw0KGgo="
    record_id = bs.create_blend_record(
        conn, recipe_id=rid, product_name="잉크A", ink_name=None, position=None,
        worker="홍", work_date="2026-06-24", work_time=None, total_amount=100, scale=None, note=None,
        details=[{"material_name": "원료1", "theory_amount": 100, "actual_amount": 100}],
        created_by="현장", created_at="2026-06-24T00:00:00Z", worker_sign=sign,
    )
    rec = bs.get_blend_record(conn, record_id)
    assert rec["worker_sign"] == sign
    assert rec["reviewed_sign"] is None


def test_manual_entry_flag_stored_and_default_false():
    """저울 연동 수동 입력 여부 저장·직렬화 — 배치 단위 + 자재 행 단위. 기본 False."""
    conn = _make_db()
    rid = _seed_recipe(conn)
    base = dict(
        recipe_id=rid, product_name="잉크A", ink_name=None, position=None,
        worker="홍", work_date="2026-06-24", work_time=None, total_amount=100,
        scale=None, note=None,
        created_by="현장", created_at="2026-06-24T00:00:00Z",
    )
    # 기본(미지정) → 모두 False
    r1 = bs.create_blend_record(conn, **base, details=[
        {"material_name": "원료1", "theory_amount": 100, "actual_amount": 100},
    ])
    rec1 = bs.get_blend_record(conn, r1)
    assert rec1["manual_entry"] is False
    assert rec1["details"][0]["manual_entry"] is False
    # 행 단위: 원료2만 손입력 → 그 행만 True, 배치 플래그도 True
    r2 = bs.create_blend_record(conn, **base, manual_entry=True, details=[
        {"material_name": "원료1", "theory_amount": 60, "actual_amount": 60},
        {"material_name": "원료2", "theory_amount": 40, "actual_amount": 40, "manual_entry": True},
    ])
    rec2 = bs.get_blend_record(conn, r2)
    assert rec2["manual_entry"] is True
    by_name = {d["material_name"]: d["manual_entry"] for d in rec2["details"]}
    assert by_name == {"원료1": False, "원료2": True}
    # 목록에도 배치 플래그 노출
    listed = {r["id"]: r for r in bs.list_blend_records(conn)}
    assert listed[r2]["manual_entry"] is True
    assert listed[r1]["manual_entry"] is False


# ── 점도 ↔ 배합 연계 ────────────────────────────────────────────
def test_viscosity_linked_to_blend():
    conn = _make_db()
    rid = _seed_recipe(conn)
    conn.execute(
        "INSERT INTO viscosity_products (code, name, sigma_k, is_active, created_at) "
        "VALUES ('잉크A', '잉크A', 3, 1, '2026-01-01')"
    )
    pid = conn.execute("SELECT id FROM viscosity_products").fetchone()["id"]
    record_id = bs.create_blend_record(
        conn, recipe_id=rid, product_name="잉크A", ink_name=None, position=None,
        worker="홍", work_date="2026-06-24", work_time=None, total_amount=100, scale=None, note=None,
        details=[{"material_name": "원료1", "theory_amount": 100, "actual_amount": 100, "material_lot": "L1"}],
        created_by="t", created_at="2026-06-24T00:00:00Z",
    )
    rec = bs.get_blend_record(conn, record_id)
    vs.add_reading(
        conn, product_id=pid, lot_no=rec["product_lot"], viscosity=49.2,
        measured_date=rec["work_date"], memo=None, recipe_material="잉크A",
        material_lot="L1", created_by="현장", created_at="2026-06-24T01:00:00Z",
        blend_record_id=record_id,
    )
    linked = vs.list_readings_for_blend(conn, record_id)
    assert len(linked) == 1
    assert linked[0]["viscosity"] == 49.2
    assert linked[0]["product_code"] == "잉크A"
    # 연계 안 된 다른 배합엔 안 보임
    assert vs.list_readings_for_blend(conn, 999) == []


def test_reactor_stored_on_blend_record_and_exposed_to_recipe():
    """반응기는 배합 실적에 기록되고, 레시피 조회에 use_reactor 로 노출된다."""
    conn = _make_db()
    rid = _seed_recipe(conn)  # product_name = '잉크A'
    conn.execute(
        "INSERT INTO viscosity_products (code, name, use_reactor, sigma_k, is_active, created_at) "
        "VALUES ('잉크A', '잉크A', 1, 3, 1, '2026-01-01')"
    )
    assert bs.product_uses_reactor(conn, "잉크A") is True
    assert bs.product_uses_reactor(conn, "없는제품") is False

    recipe = bs.get_recipe_for_blend(conn, rid)
    assert recipe["recipe"]["use_reactor"] is True

    record_id = bs.create_blend_record(
        conn, recipe_id=rid, product_name="잉크A", ink_name=None, position=None,
        worker="홍", work_date="2026-06-24", work_time=None, total_amount=100, scale=None, note=None,
        details=[{"material_name": "원료1", "theory_amount": 100, "actual_amount": 100, "material_lot": "L1"}],
        created_by="t", created_at="2026-06-24T00:00:00Z", reactor=3,
    )
    assert bs.get_blend_record(conn, record_id)["reactor"] == 3


def test_create_bulk():
    conn = _make_db()
    rid = _seed_recipe(conn, weights=(60, 40))
    ids = bs.create_bulk(
        conn, recipe_id=rid, worker="홍", scale="M-65",
        entries=[
            {"work_date": "2026-06-24", "total_amount": 100},
            {"work_date": "2026-06-25", "total_amount": 200},
        ],
        created_by="t", created_at="2026-06-24T00:00:00Z",
    )
    assert len(ids) == 2
    r1 = bs.get_blend_record(conn, ids[0])
    r2 = bs.get_blend_record(conn, ids[1])
    assert r1["total_amount"] == 100 and r2["total_amount"] == 200
    # 200g 배치의 첫 자재 이론량 = 60% × 200 = 120, actual=theory
    assert r2["details"][0]["theory_amount"] == 120.0
    assert r2["details"][0]["actual_amount"] == 120.0
    assert r1["product_lot"] != r2["product_lot"]


# ── 배합 분석 (제품별 빈도 + 배치 상세, Dashboard 반제품 배합 분석 흡수) ──
def _seed_analysis_records(conn, rid):
    for d, product, actual in [
        ("2026-06-20", "제품A", 98.5),
        ("2026-06-21", "제품A", 100.0),
        ("2026-06-22", "제품A", 101.0),
        ("2026-06-21", "제품B", 100.0),
    ]:
        bs.create_blend_record(
            conn, recipe_id=rid, product_name=product, ink_name=None, position=None,
            worker="홍", work_date=d, work_time=None, total_amount=100, scale=None, note=None,
            details=[{
                "material_name": "원료1", "material_lot": "L1",
                "ratio": 100.0, "theory_amount": 100, "actual_amount": actual,
            }],
            created_by="t", created_at=f"{d}T00:00:00Z",
        )


def test_product_usage_counts_batches():
    conn = _make_db()
    rid = _seed_recipe(conn)
    _seed_analysis_records(conn, rid)

    result = bs.product_usage(conn)
    assert result["product_count"] == 2
    assert result["batch_total"] == 4
    assert result["last_work_date"] == "2026-06-22"
    top = result["items"][0]
    assert top["product_name"] == "제품A"
    assert top["batch_count"] == 3
    assert top["total_amount"] == 300.0

    # 기간 필터
    filtered = bs.product_usage(conn, start_date="2026-06-21")
    by_name = {i["product_name"]: i for i in filtered["items"]}
    assert by_name["제품A"]["batch_count"] == 2

    # 취소 기록 제외
    conn.execute("UPDATE blend_records SET status = 'canceled' WHERE product_name = '제품B'")
    assert bs.product_usage(conn)["product_count"] == 1


def test_batch_details_variance_and_product_filter():
    conn = _make_db()
    rid = _seed_recipe(conn)
    _seed_analysis_records(conn, rid)

    result = bs.batch_details(conn)
    assert result["batch_count"] == 4
    assert result["material_count"] == 1
    # 작업일 역순 정렬
    assert result["items"][0]["work_date"] == "2026-06-22"

    # 제품 필터 + 편차(실제-이론)
    only_a = bs.batch_details(conn, product="제품A")
    assert only_a["batch_count"] == 3
    variances = {it["work_date"]: it["variance"] for it in only_a["items"]}
    assert variances["2026-06-20"] == -1.5
    assert variances["2026-06-21"] == 0.0


# ── 라우트 (무로그인 개방) ──────────────────────────────────────
def test_blend_routes_public_and_create():
    import importlib

    import src.config as cfg
    import src.main as mainmod

    importlib.reload(cfg)
    importlib.reload(mainmod)
    from fastapi.testclient import TestClient

    client = TestClient(mainmod.app)
    # 무로그인 조회 가능
    assert client.get("/api/blend/recipes").status_code == 200
    assert client.get("/api/blend/records").status_code == 200
    assert client.get("/api/blend/product-usage").status_code == 200
    assert client.get("/api/blend/batch-details").status_code == 200
    export = client.get("/api/blend/batch-details/export")
    assert export.status_code == 200
    assert "spreadsheetml" in export.headers["content-type"]


def test_blend_next_lot_route():
    """GET /blend/next-lot 은 저장 시 부여될 실제 LOT({제품}{YYMMDD}{순번:02d})을 준다."""
    import importlib

    import src.config as cfg
    import src.main as mainmod

    importlib.reload(cfg)
    importlib.reload(mainmod)
    from fastapi.testclient import TestClient

    client = TestClient(mainmod.app)
    res = client.get(
        "/api/blend/next-lot", params={"product": "NEXTLOTX", "date": "2026-07-01"}
    )
    assert res.status_code == 200
    # 기록이 없는 제품이므로 첫 순번 01
    assert res.json()["next_lot"] == "NEXTLOTX26070101"


def test_reactor_required_route_and_settings_patch():
    """반응기 반제품 설정(PATCH use_reactor/remind_daily 저장) + 실적 저장 시 반응기 필수 400."""
    import importlib

    import src.config as cfg
    import src.main as mainmod

    importlib.reload(cfg)
    importlib.reload(mainmod)
    from fastapi.testclient import TestClient

    client = TestClient(mainmod.app)
    client.get("/viscosity")  # csrftoken 쿠키 확보
    token = client.cookies.get("csrftoken")
    headers = {"x-csrftoken": token} if token else {}

    # 반제품 생성은 레시피 연동 강제 — 레시피에 없는 코드는 400
    denied = client.post(
        "/api/viscosity/products",
        json={"code": "NO-SUCH-RECIPE-XYZ", "name": "x"},
        headers=headers,
    )
    assert denied.status_code == 400

    # RXTEST 레시피를 만들어 두면 생성 가능 (재실행 멱등: INSERT OR IGNORE 성격)
    from src.db import get_connection
    with get_connection() as conn:
        exists = conn.execute(
            "SELECT 1 FROM recipes WHERE product_name = 'RXTEST'"
        ).fetchone()
        if not exists:
            conn.execute(
                "INSERT INTO recipes (product_name, ink_name, status, created_by, created_at) "
                "VALUES ('RXTEST', 'RXTEST', 'completed', 'test', '2026-07-01T00:00:00Z')"
            )
            conn.commit()

    # 반제품 확보 (재실행 멱등: 이미 있으면 409 → 목록에서 id 조회)
    created = client.post(
        "/api/viscosity/products",
        json={"code": "RXTEST", "name": "RXTEST"},
        headers=headers,
    )
    if created.status_code == 409:
        items = client.get("/api/viscosity/products").json()["items"]
        pid = next(it["id"] for it in items if it["code"] == "RXTEST")
    else:
        assert created.status_code == 200
        pid = created.json()["id"]

    # PATCH 로 use_reactor + remind_daily 저장이 실제 반영되는지
    patched = client.patch(
        f"/api/viscosity/products/{pid}",
        json={
            "name": "RXTEST",
            "sigma_k": 3,
            "remind_daily": True,
            "use_reactor": True,
            "is_active": True,
        },
        headers=headers,
    )
    assert patched.status_code == 200
    assert patched.json()["use_reactor"] is True
    assert patched.json()["remind_daily"] is True

    # 반응기 미지정 실적 저장 → 400 (작업자 세션 검증보다 앞서 걸림)
    res = client.post(
        "/api/blend/records",
        json={
            "product_name": "RXTEST",
            "worker": "테스트",
            "work_date": "2026-07-01",
            "total_amount": 100,
            "details": [
                {
                    "material_name": "원료1",
                    "theory_amount": 100,
                    "actual_amount": 100,
                    "sequence_order": 1,
                }
            ],
        },
        headers=headers,
    )
    assert res.status_code == 400
    assert "반응기" in res.json()["detail"]
