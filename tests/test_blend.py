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
            status TEXT DEFAULT 'completed', created_at TEXT DEFAULT '2026-01-01'
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
            status TEXT NOT NULL DEFAULT 'completed', note TEXT,
            reviewed_by TEXT, reviewed_at TEXT, approved_by TEXT, approved_at TEXT,
            worker_sign TEXT, reviewed_sign TEXT, approved_sign TEXT,
            created_by TEXT, created_at TEXT NOT NULL, updated_at TEXT
        );
        CREATE TABLE blend_details (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            blend_record_id INTEGER NOT NULL, material_id INTEGER,
            material_code TEXT, material_name TEXT NOT NULL, material_lot TEXT,
            ratio REAL, theory_amount REAL, actual_amount REAL,
            sequence_order INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
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
