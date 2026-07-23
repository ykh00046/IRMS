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
            is_bulk_regenerated INTEGER NOT NULL DEFAULT 0,
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
            manual_entry INTEGER NOT NULL DEFAULT 0,
            carried_over INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
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
            {"material_name": "A", "ratio": 60, "theory_amount": 60, "actual_amount": 60, "material_lot": "LA"},
            {"material_name": "B", "ratio": 40, "theory_amount": 40, "actual_amount": 40, "material_lot": "LB"},
        ],
    }, headers=csrf_headers())
    assert created.status_code == 200, created.text
    rid = created.json()["id"]
    lot = created.json()["product_lot"]

    # 현장(배합 세션만, 관리 미로그인)은 수정 불가
    blocked = client.request("PUT", f"/api/blend/records/{rid}", json={
        "product_name": prod, "worker": worker, "work_date": "2026-07-01", "total_amount": 100,
        "details": [{"material_name": "A", "ratio": 100, "theory_amount": 100, "actual_amount": 100, "material_lot": "LA"}],
    }, headers=csrf_headers())
    assert blocked.status_code in (401, 403)

    client.post("/api/auth/management-login", json={"username": "admin", "password": "admin"})

    # 제품명 변경은 거부 — product_lot 이 제품명으로 채번되므로 둘이 어긋난다(감사 F-8).
    renamed = client.request("PUT", f"/api/blend/records/{rid}", json={
        "product_name": prod + "X", "worker": worker, "work_date": "2026-07-01",
        "total_amount": 100,
        "details": [{"material_name": "A", "ratio": 100, "theory_amount": 100, "actual_amount": 100, "material_lot": "LA"}],
    }, headers=csrf_headers())
    assert renamed.status_code == 400, renamed.text
    assert "제품명" in renamed.json()["detail"]
    kept = client.get(f"/api/blend/records/{rid}").json()
    assert kept["product_name"] == prod                     # 거부됐으니 원래대로

    # 제품명을 유지한 전체 수정 → 200 (자재 LOT 는 create 와 동일하게 필수 — GAP-2)
    res = client.request("PUT", f"/api/blend/records/{rid}", json={
        "product_name": prod, "worker": "책임수정", "work_date": "2026-07-03",
        "total_amount": 200, "scale": "S9", "note": "정정",
        "details": [
            {"material_name": "A", "ratio": 50, "theory_amount": 100, "actual_amount": 100, "material_lot": "LA"},
            {"material_name": "C", "ratio": 50, "theory_amount": 100, "actual_amount": 100, "material_lot": "LC"},
        ],
    }, headers=csrf_headers())
    assert res.status_code == 200, res.text
    j = res.json()
    assert j["product_lot"] == lot                 # LOT 보존
    assert j["product_name"] == prod
    assert j["worker"] == "책임수정"
    assert [d["material_name"] for d in j["details"]] == ["A", "C"]

    # 규제 보존(before-image): blend_record_update 감사에 변경 전 헤더+상세와
    # 변경된 필드 요약이 담긴다.
    import json as _json

    from src.db import get_connection
    with get_connection() as conn:
        arow = conn.execute(
            "SELECT details_json FROM audit_logs "
            "WHERE action = 'blend_record_update' AND target_id = ? "
            "ORDER BY id DESC LIMIT 1",
            (str(rid),),
        ).fetchone()
    assert arow is not None
    adet = _json.loads(arow["details_json"])
    assert "before" in adet and "after_summary" in adet
    bh = adet["before"]["header"]
    assert bh["worker"] == worker              # 변경 전 작업자
    assert bh["work_date"] == "2026-07-01"     # 변경 전 작업일
    assert bh["total_amount"] == 100           # 변경 전 총량
    assert bh["scale"] == "M-65"
    assert bh["note"] is None
    # 변경 전 상세 행(terse): [자재명, 실제량, LOT, 이월, 수동]
    before_names = [r[0] for r in adet["before"]["rows"]]
    assert before_names == ["A", "B"]
    # after_summary 는 변경된 필드만(신규 값) — worker/work_date/total_amount/scale/note.
    asum = adet["after_summary"]
    assert asum["worker"] == "책임수정"
    assert asum["work_date"] == "2026-07-03"
    assert asum["total_amount"] == 200
    assert asum["scale"] == "S9"
    assert asum["note"] == "정정"
    # 상세 행이 바뀌었으므로 rows 도 요약에 포함, 자재명이 A/C 로 갱신됨.
    assert [r[0] for r in asum["rows"]] == ["A", "C"]

    # 편차 초과는 400 (자재 LOT 는 채워 편차 검사까지 도달하게 한다)
    bad = client.request("PUT", f"/api/blend/records/{rid}", json={
        "product_name": prod, "worker": "x", "work_date": "2026-07-03", "total_amount": 100,
        "details": [{"material_name": "A", "ratio": 100, "theory_amount": 100, "actual_amount": 105, "material_lot": "LA"}],
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
        "details": [{"material_name": "A", "ratio": 100, "theory_amount": 100, "actual_amount": 100, "material_lot": "LA"}],
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
        "details": [{"material_name": "A", "ratio": 100, "theory_amount": 100, "actual_amount": 100, "material_lot": "LA"}],
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


def test_count_blend_records_matches_filters():
    """count_blend_records 는 list_blend_records 와 동일 필터의 전체 M 을 센다(표시 상한 무관)."""
    conn = _make_db()
    rid = _seed_recipe(conn)
    for d, worker in [("2026-06-20", "김"), ("2026-06-24", "이"), ("2026-06-25", "김")]:
        bs.create_blend_record(
            conn, recipe_id=rid, product_name="잉크A", ink_name=None, position=None,
            worker=worker, work_date=d, work_time=None, total_amount=100, scale=None, note=None,
            details=[{"material_name": "원료1", "theory_amount": 100, "actual_amount": 100}],
            created_by="t", created_at="2026-06-24T00:00:00Z",
        )
    assert bs.count_blend_records(conn) == 3
    assert bs.count_blend_records(conn, worker="김") == 2
    assert bs.count_blend_records(conn, start_date="2026-06-24", end_date="2026-06-30") == 2
    # limit 이 전체보다 작으면 route 가 truncated 로 판정하도록 count > len(items) 성립
    assert bs.count_blend_records(conn) > len(bs.list_blend_records(conn, limit=2))


def test_blend_records_route_reports_truncation():
    """GET /blend/records 는 total_available·truncated·limit 로 표시 상한 도달을 표면화한다."""
    import importlib

    import src.config as cfg
    import src.main as mainmod

    importlib.reload(cfg)
    importlib.reload(mainmod)
    from fastapi.testclient import TestClient

    client = TestClient(mainmod.app)
    r = client.get("/api/blend/records", params={"limit": 1})
    assert r.status_code == 200, r.text
    j = r.json()
    assert "total_available" in j and "truncated" in j and "limit" in j
    assert j["limit"] == 1
    assert len(j["items"]) <= 1
    assert j["truncated"] == (j["total_available"] > len(j["items"]))


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
    # 일괄 재생성 표식: create_bulk 로 만든 기록은 is_bulk_regenerated=True.
    assert r1["is_bulk_regenerated"] is True
    assert r2["is_bulk_regenerated"] is True


def test_create_bulk_flag_in_list_payload_and_normal_record_false():
    """일괄 재생성 플래그가 목록 직렬화에 실리고, 일반 실적은 False 로 남는다."""
    conn = _make_db()
    rid = _seed_recipe(conn, weights=(60, 40))
    bulk_ids = bs.create_bulk(
        conn, recipe_id=rid, worker="홍", scale=None,
        entries=[{"work_date": "2026-06-24", "total_amount": 100}],
        created_by="t", created_at="2026-06-24T00:00:00Z",
    )
    # 일반 실적 1건(현장 계량 경로).
    normal_id = bs.create_blend_record(
        conn, recipe_id=rid, product_name="잉크A", ink_name=None, position=None,
        worker="홍", work_date="2026-06-24", work_time=None, total_amount=100,
        scale=None, note=None,
        details=[{"material_name": "원료1", "material_lot": "L1",
                  "ratio": 100.0, "theory_amount": 100, "actual_amount": 100}],
        created_by="t", created_at="2026-06-24T00:00:00Z",
    )
    rows = {r["id"]: r for r in bs.list_blend_records(conn)}
    assert rows[bulk_ids[0]]["is_bulk_regenerated"] is True
    assert rows[normal_id]["is_bulk_regenerated"] is False


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


def test_mistake_stats_manual_and_canceled():
    """작업자·자재별 이상 통계 — 수동 입력·취소 집계(편차 아님)."""
    conn = _make_db()
    rid = _seed_recipe(conn)
    # 홍: 완료 2건(그중 1건 수동 입력), 취소 1건. 김: 완료 1건(수동 아님).
    def rec(worker, d, manual, status="completed", mat_manual=False):
        rowid = bs.create_blend_record(
            conn, recipe_id=rid, product_name="제품A", ink_name=None, position=None,
            worker=worker, work_date=d, work_time=None, total_amount=100, scale=None, note=None,
            details=[{
                "material_name": "원료1", "material_lot": "L1", "ratio": 100.0,
                "theory_amount": 100, "actual_amount": 100,
                "manual_entry": mat_manual,
            }],
            created_by="t", created_at=f"{d}T00:00:00Z", manual_entry=manual,
        )
        if status != "completed":
            conn.execute("UPDATE blend_records SET status = ? WHERE id = ?", (status, rowid))
        return rowid

    rec("홍", "2026-06-20", manual=True, mat_manual=True)
    rec("홍", "2026-06-21", manual=False)
    rec("홍", "2026-06-22", manual=False, status="canceled")
    rec("김", "2026-06-21", manual=False)

    stats = bs.mistake_stats(conn)
    by_worker = {w["worker"]: w for w in stats["by_worker"]}
    assert by_worker["홍"]["records"] == 2          # 완료 2건
    assert by_worker["홍"]["manual_records"] == 1   # 수동 1건
    assert by_worker["홍"]["canceled_records"] == 1 # 취소 1건
    assert by_worker["홍"]["manual_rate"] == 50.0
    assert by_worker["김"]["manual_records"] == 0
    # 자재별: 수동 입력 행이 있는 자재만 노출.
    by_mat = {m["material_name"]: m for m in stats["by_material"]}
    assert "원료1" in by_mat
    assert by_mat["원료1"]["manual_rows"] == 1
    # 기간 필터 — 6/21 이후면 홍의 수동(6/20)은 빠진다.
    later = bs.mistake_stats(conn, start_date="2026-06-21")
    lw = {w["worker"]: w for w in later["by_worker"]}
    assert lw["홍"]["manual_records"] == 0


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
    """반응기 반제품: use_reactor 는 레시피 소유(점도 PATCH 는 무시) + 실적 저장 시 반응기 필수 400.

    소유권이 recipes 로 이전(reactor-ownership)된 뒤에도 이 테스트가 점도 PATCH 저장을
    단언하고 있었다 — 무인증 시절엔 401 로 가려져 있다가 정책 ⓑ 적용 후 드러난 잔재.
    현행 설계대로: 레시피의 use_reactor=1 이 점도 제품 payload 와 실적 저장 400 을 결정하고,
    점도 PATCH 의 use_reactor 본문 필드는 호환용으로 받되 무시됨을 함께 증명한다.
    """
    import importlib

    import src.config as cfg
    import src.main as mainmod

    importlib.reload(cfg)
    importlib.reload(mainmod)
    from fastapi.testclient import TestClient

    client = TestClient(mainmod.app)
    # 점도 제품 생성/수정은 정책 ⓑ 로 책임자 강제 — 책임자 세션으로 로그인한다.
    client.post("/api/auth/management-login", json={"username": "admin", "password": "admin"})
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
                "INSERT INTO recipes (product_name, ink_name, status, created_by, created_at, use_reactor) "
                "VALUES ('RXTEST', 'RXTEST', 'completed', 'test', '2026-07-01T00:00:00Z', 1)"
            )
        # 재실행 멱등: 기존 행이어도 반응기 소유값을 확실히 켠다(레시피가 단일 소스).
        conn.execute("UPDATE recipes SET use_reactor = 1 WHERE product_name = 'RXTEST'")
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

    # PATCH: remind_daily 는 점도 소유(저장됨), use_reactor 는 레시피 소유 —
    # 본문으로 False 를 보내도 payload 는 레시피 값(True)을 반환해야 한다(무시 증명).
    patched = client.patch(
        f"/api/viscosity/products/{pid}",
        json={
            "name": "RXTEST",
            "sigma_k": 3,
            "remind_daily": True,
            "use_reactor": False,
            "is_active": True,
        },
        headers=headers,
    )
    assert patched.status_code == 200
    assert patched.json()["use_reactor"] is True  # 레시피(use_reactor=1)가 단일 소스
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


# ── 반제품 원료 LOT 자동 제안: GET /api/blend/recent-product-lots ──
def _blend_client():
    """최소 TestClient + 작업자 세션. recent-product-lots 조회(GET, CSRF 불필요) 용."""
    import importlib
    import src.config as cfg
    import src.main as mainmod
    importlib.reload(cfg)
    importlib.reload(mainmod)
    from fastapi.testclient import TestClient
    client = TestClient(mainmod.app)
    # CSRF 쿠키 확보(아무 GET) + 작업자 세션 로그인(배합 기록 POST 에 필요).
    client.get("/api/blend/records")
    return client


def _create_blend(client, prod, worker, lot_suffix=""):
    """배합 기록 1건 생성(반제품 제품명=prod). product_lot 은 서버가 자동 채번."""
    def csrf_headers():
        tok = client.cookies.get("csrftoken")
        return {"x-csrftoken": tok} if tok else {}
    res = client.post("/api/blend/records", json={
        "product_name": prod, "worker": worker, "work_date": "2026-07-01",
        "total_amount": 100, "scale": None,
        "details": [
            {"material_name": "원료1", "ratio": 100, "theory_amount": 100, "actual_amount": 100, "material_lot": "L1"},
        ],
    }, headers=csrf_headers())
    assert res.status_code == 200, res.text
    return res.json()


def test_recent_product_lots_completed_only_latest_first():
    """완료 2건 + 취소 1건 → 완료분만 최신순(id DESC), 취소 제외, 중복 제거."""
    client = _blend_client()

    def csrf_headers():
        tok = client.cookies.get("csrftoken")
        return {"x-csrftoken": tok} if tok else {}
    client.post("/api/workers", json={"name": "제안작업"}, headers=csrf_headers())
    client.post("/api/blend/session/login", json={"worker": "제안작업"}, headers=csrf_headers())

    prod = "RPL" + __import__("uuid").uuid4().hex[:4]
    r1 = _create_blend(client, prod, "제안작업")
    r2 = _create_blend(client, prod, "제안작업")
    # 취소(soft) 기록 1건 — 책임자 로그인 후 DELETE.
    r3 = _create_blend(client, prod, "제안작업")
    client.post("/api/auth/management-login", json={"username": "admin", "password": "admin"})
    canceled = client.request(
        "DELETE", f"/api/blend/records/{r3['id']}",
        headers=csrf_headers(),
    )
    assert canceled.status_code == 200

    # 완료 기록만 최신순 — r2(최신) → r1. r3(취소) 제외. 각 항목은 {lot, total}.
    res = client.get(f"/api/blend/recent-product-lots?names={prod}&limit=5")
    assert res.status_code == 200, res.text
    items = res.json()["items"]
    assert prod in items
    lots = items[prod]
    # 모양 검증 — 각 원소는 {"lot": str, "total": float}.
    assert all(isinstance(it, dict) and "lot" in it and "total" in it for it in lots)
    assert lots[0]["lot"] == r2["product_lot"]   # 최신(id DESC)
    assert lots[1]["lot"] == r1["product_lot"]
    assert r3["product_lot"] not in [it["lot"] for it in lots]  # 취소 제외
    assert len(lots) == 2
    # total 은 그 기록의 total_amount(=100, _create_blend 기본값).
    assert lots[0]["total"] == 100.0


def test_recent_product_lots_limit_clamp():
    """limit 클램프: 0 → 1(최소), 100 → 20(최대). 둘 다 200 + 유효 개수."""
    client = _blend_client()

    def csrf_headers():
        tok = client.cookies.get("csrftoken")
        return {"x-csrftoken": tok} if tok else {}
    client.post("/api/workers", json={"name": "제한작업"}, headers=csrf_headers())
    client.post("/api/blend/session/login", json={"worker": "제한작업"}, headers=csrf_headers())

    prod = "LMT" + __import__("uuid").uuid4().hex[:4]
    # 3건 생성(클램프 확인용).
    for _ in range(3):
        _create_blend(client, prod, "제한작업")

    # limit=0 → 클램프 1. 정상 200, 1개만.
    res0 = client.get(f"/api/blend/recent-product-lots?names={prod}&limit=0")
    assert res0.status_code == 200, res0.text
    assert len(res0.json()["items"][prod]) == 1

    # limit=100 → 클램프 20. 정상 200(3건 시드라 3개).
    res100 = client.get(f"/api/blend/recent-product-lots?names={prod}&limit=100")
    assert res100.status_code == 200, res100.text
    assert len(res100.json()["items"][prod]) == 3


def test_recent_product_lots_unknown_name_omitted():
    """기록 없는 이름은 응답 items 에 키 자체를 넣지 않는다."""
    client = _blend_client()
    unknown = "절대없는제품" + __import__("uuid").uuid4().hex
    res = client.get(f"/api/blend/recent-product-lots?names={unknown}")
    assert res.status_code == 200
    assert res.json()["items"] == {}


def test_recent_product_lots_empty_names_returns_empty():
    """names 빈 값/공백만 → 빈 items(에러 아님)."""
    client = _blend_client()
    for q in ("", "   ", ",,,"):
        res = client.get(f"/api/blend/recent-product-lots?names={q}")
        assert res.status_code == 200, res.text
        assert res.json()["items"] == {}


# ── 반제품 원료 LOT 미등록 차단: GET /api/blend/product-lot-exists ──
def _ple_client_and_product():
    """product-lot-exists 테스트용 최소 TestClient + 작업자 세션. 제품명은 충돌 방지용 난수."""
    client = _blend_client()

    def csrf_headers():
        tok = client.cookies.get("csrftoken")
        return {"x-csrftoken": tok} if tok else {}

    client.post("/api/workers", json={"name": "LOT검증"}, headers=csrf_headers())
    client.post("/api/blend/session/login", json={"worker": "LOT검증"}, headers=csrf_headers())
    prod = "PLE" + __import__("uuid").uuid4().hex[:6]
    return client, prod, csrf_headers


def test_product_lot_exists_true_for_completed_record():
    """완료 기록의 product_lot → exists=true."""
    client, prod, csrf_headers = _ple_client_and_product()
    rec = _create_blend(client, prod, "LOT검증")
    res = client.get(f"/api/blend/product-lot-exists?name={prod}&lot={rec['product_lot']}")
    assert res.status_code == 200, res.text
    assert res.json()["exists"] is True


def test_product_lot_exists_false_for_unknown_lot():
    """존재하지 않는 LOT → exists=false."""
    client, prod, _ = _ple_client_and_product()
    res = client.get(f"/api/blend/product-lot-exists?name={prod}&lot=절대없는LOT")
    assert res.status_code == 200, res.text
    assert res.json()["exists"] is False


def test_product_lot_exists_false_for_cancelled_record():
    """취소(soft delete) 기록에만 있는 LOT → exists=false (status='completed' 한정)."""
    client, prod, csrf_headers = _ple_client_and_product()
    rec = _create_blend(client, prod, "LOT검증")
    client.post("/api/auth/management-login", json={"username": "admin", "password": "admin"})
    canceled = client.request(
        "DELETE", f"/api/blend/records/{rec['id']}", headers=csrf_headers())
    assert canceled.status_code == 200
    # 취소된 기록의 LOT → 더 이상 completed 가 아니므로 false.
    res = client.get(f"/api/blend/product-lot-exists?name={prod}&lot={rec['product_lot']}")
    assert res.status_code == 200, res.text
    assert res.json()["exists"] is False


def test_product_lot_exists_false_when_name_or_lot_empty():
    """name 또는 lot 이 빈 값/공백 → exists=false (에러 아님)."""
    client, prod, _ = _ple_client_and_product()
    rec = _create_blend(client, prod, "LOT검증")
    lot = rec["product_lot"]
    # name 빈/공백, lot 정상.
    for q in ("", "   "):
        res = client.get(f"/api/blend/product-lot-exists?name={q}&lot={lot}")
        assert res.status_code == 200, res.text
        assert res.json()["exists"] is False
    # lot 빈/공백, name 정상.
    for q in ("", "   "):
        res = client.get(f"/api/blend/product-lot-exists?name={prod}&lot={q}")
        assert res.status_code == 200, res.text
        assert res.json()["exists"] is False


def test_product_lot_exists_trims_surrounding_whitespace():
    """LOT 좌우 공백은 strip 후 정확 일치 → exists=true."""
    client, prod, _ = _ple_client_and_product()
    rec = _create_blend(client, prod, "LOT검증")
    padded = f"  {rec['product_lot']}  "
    res = client.get(f"/api/blend/product-lot-exists?name={prod}&lot={padded}")
    assert res.status_code == 200, res.text
    assert res.json()["exists"] is True


# ── 반응기 이월(carry-over): POST /api/blend/records ──────────────
def _mgmt_client():
    """책임자 로그인 + CSRF 헤더를 갖춘 TestClient(레시피 등록·반응기 설정용)."""
    import importlib
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


def _import_recipe(client, csrf, product, materials, *, anchor=None, use_reactor=False, is_derived=False):
    """레시피 1건 등록(반제품명=product, 자재들). anchor_material·use_reactor·is_derived 지정 가능."""
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


def _stage1_record(client, csrf, intermediate, worker, total=150.0):
    """1차 배합 기록 1건 생성(반제품명=intermediate, 총량=total). 반환: (id, product_lot)."""
    client.post("/api/workers", json={"name": worker}, headers=csrf())
    client.post("/api/blend/session/login", json={"worker": worker}, headers=csrf())
    res = client.post("/api/blend/records", json={
        "product_name": intermediate, "worker": worker, "work_date": "2026-07-01",
        "total_amount": total, "scale": None, "reactor": None,
        "details": [
            {"material_name": "원료1", "ratio": 100, "theory_amount": total, "actual_amount": total, "material_lot": "L1"},
        ],
    }, headers=csrf())
    assert res.status_code == 200, res.text
    j = res.json()
    return j["id"], j["product_lot"]


def test_carryover_happy_path_forces_amount_to_stage1_total():
    """반응기 이월 happy path — actual_amount 가 1차 배합 총량으로 강제 덮어쓰기된다.

    클라이언트가 다른 숫자를 보내도 서버가 1차 기록의 total_amount 로 강제(변조 방지).
    manual_entry=false, carried_over=true 가 저장되고 상세 응답에 노출된다.
    """
    client, csrf = _mgmt_client()
    intermediate = "이월중간체" + __import__("uuid").uuid4().hex[:4]
    final = "이월최종" + __import__("uuid").uuid4().hex[:4]
    # 1차 배합 기록(중간체) — 총량 150.
    stage1_id, stage1_lot = _stage1_record(client, csrf, intermediate, "이월작업", total=150.0)
    # 2차 레시피 — 파생 레시피(이월은 파생에 걸린다), 기준 자재=중간체. 중간체 비율 60, 최종 원료 40.
    # 반응기도 함께 지정(실제 SBCT 처럼) — reactor 번호가 유효하도록.
    rid = _import_recipe(client, csrf, final,
                         [(intermediate, 60), ("최종원료", 40)],
                         anchor=intermediate, use_reactor=True, is_derived=True)
    # 2차 배합 저장 — 기준 자재(중간체) 행을 carried_over=true + 1차 LOT.
    # 클라이언트는 틀린 actual_amount(999)를 보내지만 서버가 1차 총량(150)으로 강제.
    res = client.post("/api/blend/records", json={
        "recipe_id": rid, "product_name": final, "worker": "이월작업",
        "work_date": "2026-07-02", "total_amount": 250, "scale": None, "reactor": 1,
        "details": [
            {"material_name": intermediate, "material_lot": stage1_lot,
             "actual_amount": 999, "carried_over": True},
            {"material_name": "최종원료", "actual_amount": 100, "material_lot": "L9"},
        ],
    }, headers=csrf())
    assert res.status_code == 200, res.text
    rec_id = res.json()["id"]
    # 상세 조회 — 이월 행의 actual_amount 가 1차 총량(150)으로 강제됐는지 확인.
    detail = client.get(f"/api/blend/records/{rec_id}").json()
    rows = {d["material_name"]: d for d in detail["details"]}
    assert rows[intermediate]["actual_amount"] == 150.0       # ← 강제값(999 무시)
    assert rows[intermediate]["carried_over"] is True         # ← 표식 저장
    assert rows[intermediate]["manual_entry"] is False        # ← 강제 해제
    assert rows["최종원료"]["carried_over"] is False          # 일반 행은 그대로


def test_carryover_enforced_on_update_path():
    """편집(PUT) 경로도 이월을 검증·강제한다 — create 와 대칭(변조 방지).

    책임자 정정 저장에서 (1) 이월 행 actual 은 1차 총량으로 강제되고, (2) 기준 자재가
    아닌 행의 carried_over=true 는 400 으로 거부된다(create 경로와 동일 불변식).
    """
    client, csrf = _mgmt_client()
    intermediate = "UP중간체" + __import__("uuid").uuid4().hex[:4]
    final = "UP최종" + __import__("uuid").uuid4().hex[:4]
    _sid, stage1_lot = _stage1_record(client, csrf, intermediate, "UP작업", total=150.0)
    rid = _import_recipe(client, csrf, final,
                         [(intermediate, 60), ("최종원료", 40)],
                         anchor=intermediate, use_reactor=True, is_derived=True)
    # 먼저 정상 저장.
    rec_id = client.post("/api/blend/records", json={
        "recipe_id": rid, "product_name": final, "worker": "UP작업",
        "work_date": "2026-07-02", "total_amount": 250, "reactor": 1,
        "details": [
            {"material_name": intermediate, "material_lot": stage1_lot,
             "actual_amount": 150, "carried_over": True},
            {"material_name": "최종원료", "actual_amount": 100, "material_lot": "L9"},
        ],
    }, headers=csrf()).json()["id"]
    # (1) 편집 저장 — 이월 행에 틀린 actual(888)을 보내도 1차 총량(150)으로 강제.
    up = client.request("PUT", f"/api/blend/records/{rec_id}", json={
        "recipe_id": rid, "product_name": final, "worker": "UP작업",
        "work_date": "2026-07-02", "total_amount": 250, "reactor": 1,
        "details": [
            {"material_name": intermediate, "material_lot": stage1_lot,
             "actual_amount": 888, "carried_over": True},
            {"material_name": "최종원료", "actual_amount": 100, "material_lot": "L9"},
        ],
    }, headers=csrf())
    assert up.status_code == 200, up.text
    rows = {d["material_name"]: d for d in client.get(f"/api/blend/records/{rec_id}").json()["details"]}
    assert rows[intermediate]["actual_amount"] == 150.0   # 편집에서도 강제
    assert rows[intermediate]["carried_over"] is True
    # (2) 편집으로 기준 자재가 아닌 행에 carried_over=true → 400.
    bad = client.request("PUT", f"/api/blend/records/{rec_id}", json={
        "recipe_id": rid, "product_name": final, "worker": "UP작업",
        "work_date": "2026-07-02", "total_amount": 250, "reactor": 1,
        "details": [
            {"material_name": intermediate, "material_lot": stage1_lot, "actual_amount": 150},
            {"material_name": "최종원료", "actual_amount": 100, "carried_over": True},
        ],
    }, headers=csrf())
    assert bad.status_code == 400
    assert "최종원료" in bad.json()["detail"]


def test_carryover_rejected_for_non_derived_recipe():
    """파생이 아닌 레시피에서 carried_over=true → 400. 반응기여도 파생이 아니면 거부(디커플링).

    이월은 반응기가 아니라 '파생' 상태에 걸린다 — use_reactor=True 여도 is_derived 가
    아니면 이월을 막아야 한다.
    """
    client, csrf = _mgmt_client()
    intermediate = "NR중간체" + __import__("uuid").uuid4().hex[:4]
    final = "NR최종" + __import__("uuid").uuid4().hex[:4]
    stage1_id, stage1_lot = _stage1_record(client, csrf, intermediate, "NR작업")
    # 반응기 레시피이지만 파생은 아님(is_derived=False) — 이월 불가여야 한다.
    rid = _import_recipe(client, csrf, final,
                         [(intermediate, 60), ("최종원료", 40)],
                         anchor=intermediate, use_reactor=True)
    res = client.post("/api/blend/records", json={
        "recipe_id": rid, "product_name": final, "worker": "NR작업",
        "work_date": "2026-07-02", "total_amount": 250, "scale": None, "reactor": 1,
        "details": [
            {"material_name": intermediate, "material_lot": stage1_lot,
             "actual_amount": 150, "carried_over": True},
            {"material_name": "최종원료", "actual_amount": 100, "material_lot": "L9"},
        ],
    }, headers=csrf())
    assert res.status_code == 400
    assert intermediate in res.json()["detail"]
    assert "파생" in res.json()["detail"]


def test_carryover_accepted_for_derived_non_reactor_recipe():
    """파생이지만 반응기가 아닌 레시피에서도 이월이 동작한다(디커플링 반대 방향).

    반응기 번호 없이도(use_reactor=False) is_derived 만으로 이월이 허용돼야 한다.
    """
    client, csrf = _mgmt_client()
    intermediate = "DN중간체" + __import__("uuid").uuid4().hex[:4]
    final = "DN최종" + __import__("uuid").uuid4().hex[:4]
    _stage1_id, stage1_lot = _stage1_record(client, csrf, intermediate, "DN작업", total=150.0)
    # 파생이지만 반응기는 아님 — reactor 번호 없이 저장.
    rid = _import_recipe(client, csrf, final,
                         [(intermediate, 60), ("최종원료", 40)],
                         anchor=intermediate, is_derived=True)
    res = client.post("/api/blend/records", json={
        "recipe_id": rid, "product_name": final, "worker": "DN작업",
        "work_date": "2026-07-02", "total_amount": 250, "scale": None,
        "details": [
            {"material_name": intermediate, "material_lot": stage1_lot,
             "actual_amount": 999, "carried_over": True},
            {"material_name": "최종원료", "actual_amount": 100, "material_lot": "L9"},
        ],
    }, headers=csrf())
    assert res.status_code == 200, res.text
    detail = client.get(f"/api/blend/records/{res.json()['id']}").json()
    rows = {d["material_name"]: d for d in detail["details"]}
    assert rows[intermediate]["actual_amount"] == 150.0   # 파생만으로 강제 이월
    assert rows[intermediate]["carried_over"] is True


def test_carryover_rejected_for_non_anchor_row():
    """기준 자재가 아닌 행에 carried_over=true → 400(자재명 포함)."""
    client, csrf = _mgmt_client()
    intermediate = "NA중간체" + __import__("uuid").uuid4().hex[:4]
    final = "NA최종" + __import__("uuid").uuid4().hex[:4]
    stage1_id, stage1_lot = _stage1_record(client, csrf, intermediate, "NA작업")
    # 기준 자재=중간체, 파생 레시피.
    rid = _import_recipe(client, csrf, final,
                         [(intermediate, 60), ("최종원료", 40)],
                         anchor=intermediate, use_reactor=True, is_derived=True)
    # carried_over 를 기준 자재가 아닌 '최종원료' 행에 걸면 → 400.
    res = client.post("/api/blend/records", json={
        "recipe_id": rid, "product_name": final, "worker": "NA작업",
        "work_date": "2026-07-02", "total_amount": 250, "scale": None, "reactor": 1,
        "details": [
            {"material_name": intermediate, "material_lot": stage1_lot, "actual_amount": 150},
            {"material_name": "최종원료", "actual_amount": 100, "carried_over": True},
        ],
    }, headers=csrf())
    assert res.status_code == 400
    assert "최종원료" in res.json()["detail"]
    assert "기준 자재" in res.json()["detail"]


def test_carryover_rejected_for_unregistered_lot():
    """기준 자재 행이지만 1차 완료 기록에 없는 LOT → 400(자재명 + LOT 포함)."""
    client, csrf = _mgmt_client()
    intermediate = "UL중간체" + __import__("uuid").uuid4().hex[:4]
    final = "UL최종" + __import__("uuid").uuid4().hex[:4]
    _stage1_record(client, csrf, intermediate, "UL작업")
    rid = _import_recipe(client, csrf, final,
                         [(intermediate, 60), ("최종원료", 40)],
                         anchor=intermediate, use_reactor=True, is_derived=True)
    res = client.post("/api/blend/records", json={
        "recipe_id": rid, "product_name": final, "worker": "UL작업",
        "work_date": "2026-07-02", "total_amount": 250, "scale": None, "reactor": 1,
        "details": [
            {"material_name": intermediate, "material_lot": "절대없는LOT",
             "actual_amount": 150, "carried_over": True},
            {"material_name": "최종원료", "actual_amount": 100, "material_lot": "L9"},
        ],
    }, headers=csrf())
    assert res.status_code == 400
    assert intermediate in res.json()["detail"]
    assert "LOT" in res.json()["detail"]


def test_carryover_rejected_in_continuous_route():
    """연속(다중 로트) 화면에서 carried_over=true → 400(단일 배합 전용 메시지)."""
    client, csrf = _mgmt_client()
    intermediate = "CR중간체" + __import__("uuid").uuid4().hex[:4]
    final = "CR최종" + __import__("uuid").uuid4().hex[:4]
    stage1_id, stage1_lot = _stage1_record(client, csrf, intermediate, "CR작업")
    rid = _import_recipe(client, csrf, final,
                         [(intermediate, 60), ("최종원료", 40)],
                         anchor=intermediate, use_reactor=True, is_derived=True)
    res = client.post("/api/blend/records/continuous", json={
        "recipe_id": rid, "product_name": final,
        "work_date": "2026-07-02", "total_amount": 250, "reactor": 1,
        "lots": [
            [
                {"material_name": intermediate, "material_lot": stage1_lot,
                 "actual_amount": 150, "carried_over": True},
                {"material_name": "최종원료", "actual_amount": 100, "material_lot": "L9"},
            ],
        ],
    }, headers=csrf())
    assert res.status_code == 400
    assert res.json()["detail"] == "반응기 이월은 단일 배합 화면에서만 사용할 수 있습니다."


# ── 자재 LOT 필수 검증(POST /api/blend/records) — 추적성 ──────────
def test_blend_create_missing_lot_returns_400_with_names():
    """(a) 단건 저장에서 한 행의 material_lot 가 비어 있으면 400 + 자재명 노출."""
    client, csrf = _mgmt_client()
    prod = "LOTMISS" + __import__("uuid").uuid4().hex[:4]
    worker = "LOT작업"
    client.post("/api/workers", json={"name": worker}, headers=csrf())
    client.post("/api/blend/session/login", json={"worker": worker}, headers=csrf())
    res = client.post("/api/blend/records", json={
        "product_name": prod, "worker": worker, "work_date": "2026-07-10",
        "total_amount": 100,
        "details": [
            {"material_name": "채워진자재", "ratio": 50, "theory_amount": 50, "actual_amount": 50, "material_lot": "L1"},
            {"material_name": "빈LOT자재", "ratio": 50, "theory_amount": 50, "actual_amount": 50, "material_lot": ""},
        ],
    }, headers=csrf())
    assert res.status_code == 400, res.text
    detail = res.json()["detail"]
    assert "자재 LOT 를 입력하세요" in detail
    assert "빈LOT자재" in detail
    assert "채워진자재" not in detail   # LOT 가 있는 자재는 이름이 나오지 않는다


def test_blend_create_all_lots_returns_200():
    """(b) 모든 행에 material_lot 가 있으면 정상 저장 200(회귀 가드)."""
    client, csrf = _mgmt_client()
    prod = "LOTOK" + __import__("uuid").uuid4().hex[:4]
    worker = "LOT작업2"
    client.post("/api/workers", json={"name": worker}, headers=csrf())
    client.post("/api/blend/session/login", json={"worker": worker}, headers=csrf())
    res = client.post("/api/blend/records", json={
        "product_name": prod, "worker": worker, "work_date": "2026-07-10",
        "total_amount": 100,
        "details": [
            {"material_name": "자재A", "ratio": 60, "theory_amount": 60, "actual_amount": 60, "material_lot": "LA"},
            {"material_name": "자재B", "ratio": 40, "theory_amount": 40, "actual_amount": 40, "material_lot": "LB"},
        ],
    }, headers=csrf())
    assert res.status_code == 200, res.text
    assert res.json()["product_lot"]


def test_bulk_create_without_lots_still_works():
    """(d) POST /blend/records/bulk(일괄 재생성, 과거 데이터)는 LOT 필수 검증에서 제외.

    과거 이관 데이터는 LOT 를 모를 수 있어 — bulk 경로는 LOT 검사를 하지 않는다.
    bs.create_bulk 가 LOT 없는 details 로 정상 저장되는지 확인(서비스 계층 직접 호출).
    """
    conn = _make_db()
    rid = _seed_recipe(conn, weights=(60, 40))
    ids = bs.create_bulk(
        conn, recipe_id=rid, worker="홍", scale="M-65",
        entries=[
            {"work_date": "2026-06-24", "total_amount": 100},
        ],
        created_by="t", created_at="2026-06-24T00:00:00Z",
    )
    assert len(ids) == 1
    rec = bs.get_blend_record(conn, ids[0])
    # bulk 는 서버가 레시피에서 자재명·이론량을 채우되 material_lot 는 비워둔다.
    assert all(d["material_lot"] in (None, "") for d in rec["details"])


# ── 미등록 자가 반제품 LOT 서버 백업 검증(unregistered_product_lots) ──
def _seed_own_product(client, csrf, product, worker):
    """product 를 자가 반제품으로 만든다 — completed 배합 기록 1건 생성 → (id, product_lot).

    이후 다른 배합에서 product 를 원료(material_name)로 쓰면 서버가 자가 반제품으로
    인식해 material_lot 검증을 건다.
    """
    client.post("/api/workers", json={"name": worker}, headers=csrf())
    client.post("/api/blend/session/login", json={"worker": worker}, headers=csrf())
    res = client.post("/api/blend/records", json={
        "product_name": product, "worker": worker, "work_date": "2026-07-01",
        "total_amount": 100,
        "details": [{"material_name": "원료1", "ratio": 100, "theory_amount": 100,
                     "actual_amount": 100, "material_lot": "L1"}],
    }, headers=csrf())
    assert res.status_code == 200, res.text
    j = res.json()
    return j["id"], j["product_lot"]


def test_own_product_registered_lot_saves():
    """(a) 자가 반제품 행에 '등록된' LOT → 저장 성공."""
    client, csrf = _mgmt_client()
    import uuid as _uuid
    suffix = _uuid.uuid4().hex[:4]
    product = f"OWNP{suffix}"
    worker = "LOT작업"
    _pid, plot = _seed_own_product(client, csrf, product, worker)
    # product 를 원료로 쓰되 등록된 product_lot 를 material_lot 로 — 통과해야 한다.
    res = client.post("/api/blend/records", json={
        "product_name": f"FINAL{suffix}", "worker": worker, "work_date": "2026-07-02",
        "total_amount": 50,
        "details": [{"material_name": product, "ratio": 100, "theory_amount": 50,
                     "actual_amount": 50, "material_lot": plot}],
    }, headers=csrf())
    assert res.status_code == 200, res.text


def test_own_product_unregistered_lot_blocked_400():
    """(b) 자가 반제품 행에 미등록 LOT → 400 + name/LOT 노출."""
    client, csrf = _mgmt_client()
    import uuid as _uuid
    suffix = _uuid.uuid4().hex[:4]
    product = f"OWNP{suffix}"
    worker = "LOT작업"
    _seed_own_product(client, csrf, product, worker)
    bad_lot = "절대없는LOT"
    res = client.post("/api/blend/records", json={
        "product_name": f"FINAL{suffix}", "worker": worker, "work_date": "2026-07-02",
        "total_amount": 50,
        "details": [{"material_name": product, "ratio": 100, "theory_amount": 50,
                     "actual_amount": 50, "material_lot": bad_lot}],
    }, headers=csrf())
    assert res.status_code == 400, res.text
    detail = res.json()["detail"]
    assert "등록되지 않은 LOT" in detail
    assert f"{product}/{bad_lot}" in detail


def test_own_product_unregistered_lot_with_override_saves():
    """(c) 자가 반제품 미등록 LOT + matching lot_overrides(사유) → 저장 성공."""
    client, csrf = _mgmt_client()
    import uuid as _uuid
    suffix = _uuid.uuid4().hex[:4]
    product = f"OWNP{suffix}"
    worker = "LOT작업"
    _seed_own_product(client, csrf, product, worker)
    bad_lot = "절대없는LOT"
    res = client.post("/api/blend/records", json={
        "product_name": f"FINAL{suffix}", "worker": worker, "work_date": "2026-07-02",
        "total_amount": 50,
        "details": [{"material_name": product, "ratio": 100, "theory_amount": 50,
                     "actual_amount": 50, "material_lot": bad_lot}],
        "lot_overrides": [{"material_name": product, "material_lot": bad_lot,
                           "reason": "1차 배합 종이 기록만 있음"}],
    }, headers=csrf())
    assert res.status_code == 200, res.text


def test_raw_material_lot_unaffected():
    """(d) 일반 원료(완료 기록 없는 product_name)는 LOT 등록 검증 대상 아님 → 저장 성공."""
    client, csrf = _mgmt_client()
    import uuid as _uuid
    suffix = _uuid.uuid4().hex[:4]
    worker = "LOT작업"
    client.post("/api/workers", json={"name": worker}, headers=csrf())
    client.post("/api/blend/session/login", json={"worker": worker}, headers=csrf())
    # '일반원료' 는 completed 배합 기록이 없으므로 자가 반제품 아님 — 아무 LOT 나 OK.
    res = client.post("/api/blend/records", json={
        "product_name": f"FINAL{suffix}", "worker": worker, "work_date": "2026-07-02",
        "total_amount": 50,
        "details": [{"material_name": "일반원료", "ratio": 100, "theory_amount": 50,
                     "actual_amount": 50, "material_lot": "아무LOT"}],
    }, headers=csrf())
    assert res.status_code == 200, res.text


# ── 증량(rescale) 승인제 — 현장 인증·저장 검증 ────────────────────────────
def _rescale_client():
    """증량 라우트 테스트용 앱 클라이언트(매번 새 앱 + csrf 쿠키 확보)."""
    import importlib

    import src.config as cfg
    import src.main as mainmod

    importlib.reload(cfg)
    importlib.reload(mainmod)
    from fastapi.testclient import TestClient

    client = TestClient(mainmod.app)
    client.get("/api/blend/records")  # csrf 쿠키 확보
    return client


def _rescale_csrf(client):
    tok = client.cookies.get("csrftoken")
    return {"x-csrftoken": tok} if tok else {}


def _rescale_login_worker(client, worker):
    h = _rescale_csrf(client)
    client.post("/api/workers", json={"name": worker}, headers=h)
    client.post("/api/blend/session/login", json={"worker": worker}, headers=h)


def _rescale_record_payload(prod, worker, **extra):
    payload = {
        "product_name": prod,
        "worker": worker,
        "work_date": "2026-07-22",
        "total_amount": 100,
        "details": [
            {"material_name": "일반원료", "ratio": 100, "theory_amount": 100,
             "actual_amount": 100, "material_lot": "L1"},
        ],
    }
    payload.update(extra)
    return payload


def test_rescale_manager_verify_wrong_password_401_and_audit():
    import uuid

    client = _rescale_client()
    res = client.post(
        "/api/blend/manager-verify",
        json={"username": "admin", "password": "definitely-wrong-" + uuid.uuid4().hex[:6]},
        headers=_rescale_csrf(client),
    )
    assert res.status_code == 401, res.text

    from src.db import get_connection
    with get_connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM audit_logs WHERE action = 'blend_rescale_approve_denied'"
        ).fetchone()
    assert row["n"] >= 1


def test_manual_entry_verify_success_audits_new_action():
    """purpose='manual' 승인 성공 → 새 감사 action(blend_manual_entry_approved) + 토큰 반환."""
    client = _rescale_client()
    res = client.post(
        "/api/blend/manager-verify",
        json={"username": "admin", "password": "admin", "purpose": "manual"},
        headers=_rescale_csrf(client),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["approver"]
    assert body["approval_id"]

    from src.db import get_connection
    with get_connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM audit_logs WHERE action = 'blend_manual_entry_approved'"
        ).fetchone()
    assert row["n"] >= 1


def test_manual_entry_verify_wrong_password_401():
    """purpose='manual' 이어도 틀린 비밀번호는 401(승인 우회 불가)."""
    import uuid

    client = _rescale_client()
    res = client.post(
        "/api/blend/manager-verify",
        json={
            "username": "admin",
            "password": "definitely-wrong-" + uuid.uuid4().hex[:6],
            "purpose": "manual",
        },
        headers=_rescale_csrf(client),
    )
    assert res.status_code == 401, res.text


def test_rescale_verify_then_save_marks_columns_and_consumes_approval():
    import uuid

    client = _rescale_client()
    worker = "증량작업" + uuid.uuid4().hex[:6]
    prod = "RSC" + uuid.uuid4().hex[:4]
    _rescale_login_worker(client, worker)

    verify = client.post(
        "/api/blend/manager-verify",
        json={"username": "admin", "password": "admin"},
        headers=_rescale_csrf(client),
    )
    assert verify.status_code == 200, verify.text
    approval_id = verify.json()["approval_id"]
    assert verify.json()["approver"]

    created = client.post(
        "/api/blend/records",
        json=_rescale_record_payload(
            prod, worker,
            rescale_events=[{"before_total": 100, "after_total": 120, "approval_id": approval_id}],
        ),
        headers=_rescale_csrf(client),
    )
    assert created.status_code == 200, created.text
    rid = created.json()["id"]

    from src.db import get_connection
    with get_connection() as conn:
        rec = conn.execute(
            "SELECT rescale_count, rescale_unacked, rescale_events_json "
            "FROM blend_records WHERE id = ?",
            (rid,),
        ).fetchone()
        appr = conn.execute(
            "SELECT used, approver FROM blend_rescale_approvals WHERE id = ?",
            (approval_id,),
        ).fetchone()
    assert rec["rescale_count"] == 1
    assert rec["rescale_unacked"] == 0
    assert appr["used"] == 1
    # 정규화된 이벤트에 책임자 표시명이 채워졌다.
    assert appr["approver"] in (rec["rescale_events_json"] or "")


def test_rescale_approval_reuse_rejected():
    import uuid

    client = _rescale_client()
    worker = "재사용" + uuid.uuid4().hex[:6]
    prod = "RSU" + uuid.uuid4().hex[:4]
    _rescale_login_worker(client, worker)

    approval_id = client.post(
        "/api/blend/manager-verify",
        json={"username": "admin", "password": "admin"},
        headers=_rescale_csrf(client),
    ).json()["approval_id"]

    first = client.post(
        "/api/blend/records",
        json=_rescale_record_payload(
            prod, worker,
            rescale_events=[{"before_total": 100, "after_total": 130, "approval_id": approval_id}],
        ),
        headers=_rescale_csrf(client),
    )
    assert first.status_code == 200, first.text

    # 같은 approval_id 재사용 → 400
    second = client.post(
        "/api/blend/records",
        json=_rescale_record_payload(
            prod + "2", worker,
            rescale_events=[{"before_total": 100, "after_total": 130, "approval_id": approval_id}],
        ),
        headers=_rescale_csrf(client),
    )
    assert second.status_code == 400, second.text
    assert "증량 승인" in second.json()["detail"]


def test_rescale_absence_reason_saved_unacked():
    import uuid

    client = _rescale_client()
    worker = "부재진행" + uuid.uuid4().hex[:6]
    prod = "RAB" + uuid.uuid4().hex[:4]
    _rescale_login_worker(client, worker)

    created = client.post(
        "/api/blend/records",
        json=_rescale_record_payload(
            prod, worker,
            rescale_events=[{"before_total": 100, "after_total": 115,
                             "absence_reason": "책임자 부재 — 야간조 단독"}],
        ),
        headers=_rescale_csrf(client),
    )
    assert created.status_code == 200, created.text
    rid = created.json()["id"]

    from src.db import get_connection
    with get_connection() as conn:
        rec = conn.execute(
            "SELECT rescale_count, rescale_unacked, rescale_events_json "
            "FROM blend_records WHERE id = ?",
            (rid,),
        ).fetchone()
    assert rec["rescale_count"] == 1
    assert rec["rescale_unacked"] == 1
    assert "부재" in (rec["rescale_events_json"] or "")


def test_rescale_record_detail_exposes_rescale_fields():
    """get_blend_record 가 rescale_events_json/count/unacked 를 실어 준다(GAP-5 — DHR 소스)."""
    import uuid

    client = _rescale_client()
    worker = "이력노출" + uuid.uuid4().hex[:6]
    prod = "RSE" + uuid.uuid4().hex[:4]
    _rescale_login_worker(client, worker)

    created = client.post(
        "/api/blend/records",
        json=_rescale_record_payload(
            prod, worker,
            rescale_events=[{"before_total": 100, "after_total": 130,
                             "absence_reason": "책임자 부재"}],
        ),
        headers=_rescale_csrf(client),
    )
    assert created.status_code == 200, created.text
    rid = created.json()["id"]

    detail = client.get(f"/api/blend/records/{rid}").json()
    assert detail["rescale_count"] == 1
    assert detail["rescale_unacked"] == 1
    assert "부재" in (detail["rescale_events_json"] or "")


def test_rescale_three_events_rejected():
    import uuid

    client = _rescale_client()
    worker = "삼회증량" + uuid.uuid4().hex[:6]
    prod = "R3X" + uuid.uuid4().hex[:4]
    _rescale_login_worker(client, worker)

    created = client.post(
        "/api/blend/records",
        json=_rescale_record_payload(
            prod, worker,
            rescale_events=[
                {"before_total": 100, "after_total": 110, "absence_reason": "a"},
                {"before_total": 110, "after_total": 120, "absence_reason": "b"},
                {"before_total": 120, "after_total": 130, "absence_reason": "c"},
            ],
        ),
        headers=_rescale_csrf(client),
    )
    assert created.status_code == 400, created.text
    assert "3회 증량은 불가합니다" in created.json()["detail"]


def test_rescale_save_without_events_keeps_defaults():
    import uuid

    client = _rescale_client()
    worker = "무증량" + uuid.uuid4().hex[:6]
    prod = "RNO" + uuid.uuid4().hex[:4]
    _rescale_login_worker(client, worker)

    created = client.post(
        "/api/blend/records",
        json=_rescale_record_payload(prod, worker),
        headers=_rescale_csrf(client),
    )
    assert created.status_code == 200, created.text
    rid = created.json()["id"]

    from src.db import get_connection
    with get_connection() as conn:
        rec = conn.execute(
            "SELECT rescale_count, rescale_unacked, rescale_events_json "
            "FROM blend_records WHERE id = ?",
            (rid,),
        ).fetchone()
    assert rec["rescale_count"] == 0
    assert rec["rescale_unacked"] == 0
    assert rec["rescale_events_json"] is None


# ── GAP-2: 수정(PUT)도 create 통제(자재 LOT·레시피 파생·편차)를 강제한다 ──────────
def _g2_recipe_and_record(client, csrf, *, weights=(60, 40)):
    """레시피(원료A/원료B) + 그 레시피로 저장한 배합 기록 1건 → (recipe_id, prod, worker, record)."""
    import uuid as _uuid
    suffix = _uuid.uuid4().hex[:4]
    prod = f"G2P{suffix}"
    worker = "G2작업" + suffix
    rid = _import_recipe(client, csrf, prod, [("원료A", weights[0]), ("원료B", weights[1])])
    client.post("/api/workers", json={"name": worker}, headers=csrf())
    client.post("/api/blend/session/login", json={"worker": worker}, headers=csrf())
    total = float(weights[0] + weights[1])
    res = client.post("/api/blend/records", json={
        "recipe_id": rid, "product_name": prod, "worker": worker, "work_date": "2026-07-20",
        "total_amount": total,
        "details": [
            {"material_name": "원료A", "actual_amount": float(weights[0]), "material_lot": "LA"},
            {"material_name": "원료B", "actual_amount": float(weights[1]), "material_lot": "LB"},
        ],
    }, headers=csrf())
    assert res.status_code == 200, res.text
    return rid, prod, worker, res.json()


def test_blend_update_clearing_lot_returns_400():
    """PUT 로 자재 LOT 를 비우면 400 — create 와 동일 추적성 통제(GAP-2)."""
    client, csrf = _mgmt_client()
    rid, prod, worker, rec = _g2_recipe_and_record(client, csrf)
    res = client.request("PUT", f"/api/blend/records/{rec['id']}", json={
        "recipe_id": rid, "product_name": prod, "worker": worker, "work_date": "2026-07-20",
        "total_amount": 100,
        "details": [
            {"material_name": "원료A", "actual_amount": 60, "material_lot": "LA"},
            {"material_name": "원료B", "actual_amount": 40, "material_lot": ""},
        ],
    }, headers=csrf())
    assert res.status_code == 400, res.text
    assert "자재 LOT" in res.json()["detail"]


def test_blend_update_off_recipe_amount_returns_tolerance_400():
    """PUT 로 레시피에서 벗어난 실제량을 보내면 서버 재산출 이론량 기준 편차 400(GAP-2)."""
    client, csrf = _mgmt_client()
    rid, prod, worker, rec = _g2_recipe_and_record(client, csrf)
    # 원료A 이론량은 서버가 레시피에서 60 으로 재산출 — actual 80 은 편차 20g 초과.
    res = client.request("PUT", f"/api/blend/records/{rec['id']}", json={
        "recipe_id": rid, "product_name": prod, "worker": worker, "work_date": "2026-07-20",
        "total_amount": 100,
        "details": [
            {"material_name": "원료A", "actual_amount": 80, "material_lot": "LA",
             "ratio": 99, "theory_amount": 80},  # 클라이언트가 조작한 값은 무시돼야 한다
            {"material_name": "원료B", "actual_amount": 40, "material_lot": "LB"},
        ],
    }, headers=csrf())
    assert res.status_code == 400, res.text
    assert "허용 편차" in res.json()["detail"]


def test_blend_update_benign_metadata_edit_still_works():
    """작업자·비고 등 메타 정정은 그대로 허용된다 — 자재 LOT·정상 실제량이면 200(GAP-2)."""
    client, csrf = _mgmt_client()
    rid, prod, worker, rec = _g2_recipe_and_record(client, csrf)
    res = client.request("PUT", f"/api/blend/records/{rec['id']}", json={
        "recipe_id": rid, "product_name": prod, "worker": "새담당", "work_date": "2026-07-21",
        "total_amount": 100, "note": "메모수정",
        "details": [
            {"material_name": "원료A", "actual_amount": 60, "material_lot": "LA"},
            {"material_name": "원료B", "actual_amount": 40, "material_lot": "LB"},
        ],
    }, headers=csrf())
    assert res.status_code == 200, res.text
    j = res.json()
    assert j["worker"] == "새담당"
    assert j["note"] == "메모수정"
    assert j["product_lot"] == rec["product_lot"]  # LOT 보존


# ── GAP-1: 미등록 LOT 진행 사유가 감사에 구조화 보존된다 ─────────────────────────
def test_blend_create_lot_override_persisted_in_audit():
    """미등록 자가 반제품 LOT + 사유(lot_overrides)로 진행하면, 그 사유가
    blend_record_create 감사 details 에 구조화되어 남는다(GAP-1 belt-and-braces)."""
    import json as _json
    import uuid as _uuid

    client, csrf = _mgmt_client()
    suffix = _uuid.uuid4().hex[:4]
    product = f"OWNA{suffix}"
    worker = "감사작업"
    _seed_own_product(client, csrf, product, worker)
    bad_lot = "미등록LOT" + suffix
    res = client.post("/api/blend/records", json={
        "product_name": f"FIN{suffix}", "worker": worker, "work_date": "2026-07-02",
        "total_amount": 50,
        "details": [{"material_name": product, "ratio": 100, "theory_amount": 50,
                     "actual_amount": 50, "material_lot": bad_lot}],
        "lot_overrides": [{"material_name": product, "material_lot": bad_lot,
                           "reason": "1차 종이 기록만 있음"}],
    }, headers=csrf())
    assert res.status_code == 200, res.text
    rid = res.json()["id"]

    from src.db import get_connection
    with get_connection() as conn:
        row = conn.execute(
            "SELECT details_json FROM audit_logs "
            "WHERE action = 'blend_record_create' AND target_id = ?",
            (str(rid),),
        ).fetchone()
    assert row is not None
    details = _json.loads(row["details_json"])
    assert "lot_overrides" in details
    ov = details["lot_overrides"][0]
    assert ov["material_name"] == product
    assert ov["material_lot"] == bad_lot
    assert ov["reason"] == "1차 종이 기록만 있음"


# ── GAP-4: DHR 산출물 출력·다운로드가 감사된다 ─────────────────────────────────
def test_dhr_exports_are_audited():
    """단건 Excel/PDF·전체 Excel·일괄 PDF 출력이 dhr_exported 감사를 남긴다(GAP-4)."""
    import uuid as _uuid

    client, csrf = _mgmt_client()
    suffix = _uuid.uuid4().hex[:4]
    prod = f"EXP{suffix}"
    worker = "출력작업" + suffix
    client.post("/api/workers", json={"name": worker}, headers=csrf())
    client.post("/api/blend/session/login", json={"worker": worker}, headers=csrf())
    created = client.post("/api/blend/records", json={
        "product_name": prod, "worker": worker, "work_date": "2026-07-15",
        "total_amount": 100,
        "details": [{"material_name": "원료1", "ratio": 100, "theory_amount": 100,
                     "actual_amount": 100, "material_lot": "L1"}],
    }, headers=csrf())
    assert created.status_code == 200, created.text
    rid = created.json()["id"]

    from src.db import get_connection

    # 단건 Excel
    assert client.get(f"/api/blend/records/{rid}/export").status_code == 200
    # 단건 PDF
    assert client.get(f"/api/blend/records/{rid}/pdf").status_code == 200
    # 일괄 PDF
    assert client.get(f"/api/blend/records/dhr-batch?ids={rid}").status_code == 200
    # 전체 Excel 백업
    assert client.get("/api/blend/records/export-all").status_code == 200

    with get_connection() as conn:
        formats = {
            r["format"] for r in [
                __import__("json").loads(x["details_json"])
                for x in conn.execute(
                    "SELECT details_json FROM audit_logs WHERE action = 'dhr_exported'"
                ).fetchall()
            ]
        }
        single = conn.execute(
            "SELECT COUNT(*) AS n FROM audit_logs "
            "WHERE action = 'dhr_exported' AND target_id = ?",
            (str(rid),),
        ).fetchone()["n"]
    # 최소한 단건 xlsx·pdf 형식과 전체 백업 형식이 감사에 남는다.
    assert "xlsx" in formats
    assert "pdf" in formats
    assert "xlsx_all" in formats
    assert single >= 2  # 단건 Excel + 단건 PDF (같은 record 대상)


# ── 배합일지 ZIP (반제품명 폴더로 묶음) ─────────────────────────────────────────
def _make_blend_record(client, csrf, *, product, worker, work_date="2026-07-20", total=100):
    """작업자 세션 로그인 후 배합 실적 1건 생성 → (id, product_lot)."""
    client.post("/api/workers", json={"name": worker}, headers=csrf())
    client.post("/api/blend/session/login", json={"worker": worker}, headers=csrf())
    res = client.post("/api/blend/records", json={
        "product_name": product, "worker": worker, "work_date": work_date,
        "total_amount": total,
        "details": [{"material_name": "원료1", "ratio": 100, "theory_amount": total,
                     "actual_amount": total, "material_lot": "L1"}],
    }, headers=csrf())
    assert res.status_code == 200, res.text
    return res.json()["id"], res.json()["product_lot"]


def test_dhr_zip_groups_by_product_folder():
    """서로 다른 두 제품 2건 선택 → application/zip, 제품명 폴더별 {LOT}.pdf 구조,
    존재하지 않는 id 는 누락.txt 로 남고, 감사에 format=zip 이 기록된다."""
    import io as _io
    import json as _json
    import uuid as _uuid
    import zipfile as _zip

    client, csrf = _mgmt_client()
    suffix = _uuid.uuid4().hex[:4]
    # 한글 제품명 — Windows 압축 풀기 호환(UTF-8 플래그) 검증을 위해 non-ASCII 로.
    prod_a = f"묶음가{suffix}"
    prod_b = f"묶음나{suffix}"
    id_a, lot_a = _make_blend_record(client, csrf, product=prod_a, worker="집작업A" + suffix)
    id_b, lot_b = _make_blend_record(client, csrf, product=prod_b, worker="집작업B" + suffix)

    missing_id = 987654
    res = client.get(f"/api/blend/records/dhr-zip?ids={id_a},{id_b},{missing_id}")
    assert res.status_code == 200, res.text
    assert res.headers["content-type"] == "application/zip"
    assert "dhr-" in res.headers["content-disposition"]

    zf = _zip.ZipFile(_io.BytesIO(res.content))
    names = zf.namelist()
    # 제품명 폴더별로 {LOT}.pdf 가 담긴다(두 폴더).
    assert f"{prod_a}/{lot_a}.pdf" in names, names
    assert f"{prod_b}/{lot_b}.pdf" in names, names
    folders = {n.split("/", 1)[0] for n in names if "/" in n}
    assert prod_a in folders and prod_b in folders
    # 누락 id 는 전체를 실패시키지 않고 누락.txt 로 기록.
    assert "누락.txt" in names
    assert str(missing_id) in zf.read("누락.txt").decode("utf-8")
    # 한글 폴더명이 UTF-8 플래그로 기록돼 그대로 되읽힌다(0x800 = UTF-8 filename flag).
    info_a = zf.getinfo(f"{prod_a}/{lot_a}.pdf")
    assert info_a.flag_bits & 0x800
    assert info_a.filename == f"{prod_a}/{lot_a}.pdf"

    from src.db import get_connection
    with get_connection() as conn:
        formats = {
            _json.loads(x["details_json"])["format"]
            for x in conn.execute(
                "SELECT details_json FROM audit_logs WHERE action = 'dhr_exported'"
            ).fetchall()
        }
    assert "zip" in formats


def test_dhr_zip_sign_param_accepted_and_audited():
    """sign=1 도 200 이며 감사 format=zip_signed 로 남는다."""
    import json as _json
    import uuid as _uuid

    client, csrf = _mgmt_client()
    suffix = _uuid.uuid4().hex[:4]
    rid, _lot = _make_blend_record(client, csrf, product=f"ZSGN{suffix}", worker="서명집" + suffix)
    res = client.get(f"/api/blend/records/dhr-zip?ids={rid}&sign=1")
    assert res.status_code == 200, res.text
    assert res.headers["content-type"] == "application/zip"

    from src.db import get_connection
    with get_connection() as conn:
        formats = {
            _json.loads(x["details_json"])["format"]
            for x in conn.execute(
                "SELECT details_json FROM audit_logs WHERE action = 'dhr_exported'"
            ).fetchall()
        }
    assert "zip_signed" in formats


def test_dhr_zip_duplicate_lots_within_folder_deduped():
    """같은 폴더 안 LOT 이 겹치면 두 번째부터 _2 접미가 붙어 덮어쓰기되지 않는다."""
    import io as _io
    import uuid as _uuid
    import zipfile as _zip

    client, csrf = _mgmt_client()
    suffix = _uuid.uuid4().hex[:4]
    product = f"ZDUP{suffix}"
    worker = "중복집" + suffix
    client.post("/api/workers", json={"name": worker}, headers=csrf())
    client.post("/api/blend/session/login", json={"worker": worker}, headers=csrf())

    def make():
        r = client.post("/api/blend/records", json={
            "product_name": product, "worker": worker, "work_date": "2026-07-20",
            "total_amount": 100,
            "details": [{"material_name": "원료1", "ratio": 100, "theory_amount": 100,
                         "actual_amount": 100, "material_lot": "L1"}],
        }, headers=csrf())
        assert r.status_code == 200, r.text
        return r.json()["id"]

    id1 = make()
    id2 = make()
    # 두 기록은 서로 다른 LOT(채번 순번)이라 폴더 안에서 겹치지 않는 것이 정상이지만,
    # 어떤 경우에도 두 PDF 가 모두(덮어쓰기 없이) 담기는지 확인한다.
    res = client.get(f"/api/blend/records/dhr-zip?ids={id1},{id2}")
    assert res.status_code == 200, res.text
    zf = _zip.ZipFile(_io.BytesIO(res.content))
    pdfs = [n for n in zf.namelist() if n.startswith(f"{product}/")]
    assert len(pdfs) == 2, zf.namelist()


def test_dhr_zip_caps_ids_at_200():
    """id 는 앞에서부터 200개로 상한 — 201번째(실존 id)는 잘려 나가 반영되지 않는다."""
    import uuid as _uuid

    client, csrf = _mgmt_client()
    suffix = _uuid.uuid4().hex[:4]
    rid, _lot = _make_blend_record(client, csrf, product=f"ZCAP{suffix}", worker="상한집" + suffix)
    # 앞 200개는 존재하지 않는 id, 마지막(201번째)에만 실존 rid → 상한으로 잘리면 rid 제외.
    fillers = ",".join(str(n) for n in range(900000, 900200))
    res = client.get(f"/api/blend/records/dhr-zip?ids={fillers},{rid}")
    # 유효 200개가 모두 없는 id 이므로 실존 rid 가 잘려나가면 결과가 비어 404.
    assert res.status_code == 404, res.status_code
