"""Unit + route tests for blend-overhaul (배합 실적 / 잉크 계량 재구축).

Design: docs/02-design/features/blend-overhaul.design.md
"""

from __future__ import annotations

import sqlite3

from src.services import blend_service as bs


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
            created_by TEXT, created_at TEXT NOT NULL, updated_at TEXT
        );
        CREATE TABLE blend_details (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            blend_record_id INTEGER NOT NULL, material_id INTEGER,
            material_code TEXT, material_name TEXT NOT NULL, material_lot TEXT,
            ratio REAL, theory_amount REAL, actual_amount REAL,
            sequence_order INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
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
