"""item-code P4 — materials.code 우선 자재코드 전환 (blend_service).

자재 '코드'가 분류(category) 가 아닌 진짜 ERP 품목코드(materials.code) 가 되는지,
그리고 /public/material-usage 의 erp_code 가 materials.code 를 최우선으로 쓰는지.

Spec: scratchpad/item-code-p4-spec.md (docs/01-plan/features/item-code.plan.md §1·§4).
기존 blend 테스트 픽스처(test_blend.py 의 _make_db/_seed_recipe) 스타일을 따르되,
스키마에 materials.code · material_aliases 를 추가한다(test_import_parser 선례).
"""

from __future__ import annotations

import sqlite3

from src.services import blend_service as bs


def _make_db() -> sqlite3.Connection:
    """P4 검증용 인메모리 DB — materials.code·material_aliases·recipe_steps 포함."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE materials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL, unit_type TEXT, unit TEXT DEFAULT 'g',
            category TEXT, code TEXT, is_active INTEGER DEFAULT 1
        );
        CREATE TABLE material_aliases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            material_id INTEGER NOT NULL REFERENCES materials(id) ON DELETE CASCADE,
            alias_name TEXT NOT NULL UNIQUE
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
        CREATE TABLE recipe_steps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipe_id INTEGER, position INTEGER, note TEXT
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
            manual_entry INTEGER NOT NULL DEFAULT 0,
            carried_over INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
        );
        CREATE TABLE viscosity_products (
            id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT UNIQUE, name TEXT,
            target REAL, lower_limit REAL, upper_limit REAL, sigma_k REAL DEFAULT 3,
            rpm REAL, temperature REAL, remind_daily INTEGER DEFAULT 0,
            use_reactor INTEGER DEFAULT 0, is_active INTEGER DEFAULT 1, created_at TEXT
        );
        """
    )
    return conn


def _seed_recipe(conn, product="잉크A", weights=(60.0, 40.0), codes=(None, None)):
    """레시피 + 자재(코드·category 옵션) 시드. codes[i] 가 materials.code 칸."""
    rid = conn.execute(
        "INSERT INTO recipes (product_name, ink_name, status) VALUES (?, ?, 'completed')",
        (product, f"{product}-ink"),
    ).lastrowid
    for i, w in enumerate(weights):
        conn.execute(
            "INSERT INTO materials (name, unit_type, unit, category, code) "
            "VALUES (?, 'weight', 'g', ?, ?)",
            (f"원료{i+1}", f"CAT0{i+1}", codes[i]),
        )
        mid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO recipe_items (recipe_id, material_id, value_weight) VALUES (?, ?, ?)",
            (rid, mid, w),
        )
    return rid


# ── get_recipe_for_blend: 자재코드가 진짜 materials.code ─────────
def test_get_recipe_for_blend_uses_real_material_code():
    """materials.code 가 있으면 items[].material_code == 그 코드(category 아님)."""
    conn = _make_db()
    rid = _seed_recipe(conn, weights=(60, 40), codes=("AS0001", "AS0002"))
    result = bs.get_recipe_for_blend(conn, rid)
    codes = [it["material_code"] for it in result["items"]]
    assert codes == ["AS0001", "AS0002"]


def test_get_recipe_for_blend_code_null_is_none():
    """코드 미부여(code NULL) 자재는 material_code None — 정직한 빈 값(빈문자/폴백 금지)."""
    conn = _make_db()
    rid = _seed_recipe(conn, weights=(60, 40), codes=("AS0001", None))
    result = bs.get_recipe_for_blend(conn, rid)
    codes = [it["material_code"] for it in result["items"]]
    assert codes[0] == "AS0001"
    assert codes[1] is None


# ── material_usage_periods: erp_code 우선순위 ────────────────────
def _seed_usage(conn, material_name, *, material_code="AS0001"):
    """한 자재에 대해 배합 실적 1건을 시드(완료). material_code=blend_details 스냅."""
    conn.execute(
        "INSERT INTO blend_records (product_lot, product_name, worker, work_date, "
        "total_amount, status, created_at) "
        "VALUES (?, 'P', 'w', '2026-07-01', 100, 'completed', '2026-07-01')",
        (f"{material_name}26070101",),
    )
    brid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO blend_details (blend_record_id, material_code, material_name, "
        "ratio, theory_amount, actual_amount, sequence_order, created_at) "
        "VALUES (?, ?, ?, 100, 100, 100, 1, '2026-07-01')",
        (brid, material_code, material_name),
    )


def test_material_usage_periods_prefers_materials_code_over_alias():
    """materials.code 보유 자재 → erp_code == materials.code(별칭보다 우선).

    자재명 '골드펄' 에 materials.code='AS0099' 와 RM 별칭 'RM0099' 가 같이 있어도
    materials.code 가 이긴다.
    """
    conn = _make_db()
    conn.execute(
        "INSERT INTO materials (name, unit_type, unit, category, code) "
        "VALUES ('골드펄', 'weight', 'g', '기타', 'AS0099')"
    )
    mid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO material_aliases (material_id, alias_name) VALUES (?, 'RM0099')",
        (mid,),
    )
    _seed_usage(conn, "골드펄", material_code="")

    res = bs.material_usage_periods(
        conn, start_date="2026-07-01", end_date="2026-07-31"
    )
    assert len(res["items"]) == 1
    assert res["items"][0]["erp_code"] == "AS0099"  # code > RM 별칭
    # 응답 구조(Dashboard 계약)는 불변
    assert set(res["items"][0]) == {
        "period", "erp_code", "material_code", "material_name",
        "total_actual", "total_theory", "batch_count",
    }


def test_material_usage_periods_falls_back_to_alias_when_no_code():
    """code 없고 RM 별칭만 있으면 별칭(기존 동작 보존)."""
    conn = _make_db()
    conn.execute(
        "INSERT INTO materials (name, unit_type, unit, category, code) "
        "VALUES ('실버펄', 'weight', 'g', '기타', NULL)"
    )
    mid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO material_aliases (material_id, alias_name) VALUES (?, 'RM0123')",
        (mid,),
    )
    _seed_usage(conn, "실버펄", material_code="")

    res = bs.material_usage_periods(
        conn, start_date="2026-07-01", end_date="2026-07-31"
    )
    assert res["items"][0]["erp_code"] == "RM0123"  # code 없음 → 별칭 폴백


# ── 새 배합 기록: blend_details.material_code 에 진짜 코드 저장 ──
def test_new_blend_record_carries_real_material_code():
    """derive_details_from_recipe → create_blend_record 경로로 저장된 새 기록의
    blend_details.material_code 가 진짜 materials.code(AS…). 서버 도출 경로 1건."""
    conn = _make_db()
    rid = _seed_recipe(conn, product="신제품", weights=(60, 40), codes=("AS0001", "AS0002"))

    # 작업자가 보낸 상세(실측값·LOT 만 — ratio/theory 는 서버가 산출)
    incoming = [
        {"material_name": "원료1", "actual_amount": 120.0, "material_lot": "L1"},
        {"material_name": "원료2", "actual_amount": 80.0, "material_lot": "L2"},
    ]
    derived, total = bs.derive_details_from_recipe(conn, rid, 200.0, incoming)
    record_id = bs.create_blend_record(
        conn,
        recipe_id=rid, product_name="신제품", ink_name=None, position=None,
        worker="홍", work_date="2026-07-01", work_time=None,
        total_amount=total, scale="M-65", note=None,
        details=derived,
        created_by="현장", created_at="2026-07-01T00:00:00Z",
    )
    rec = bs.get_blend_record(conn, record_id)
    codes = {d["material_name"]: d["material_code"] for d in rec["details"]}
    assert codes == {"원료1": "AS0001", "원료2": "AS0002"}
