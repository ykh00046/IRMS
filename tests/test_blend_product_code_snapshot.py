"""배합 기록의 품목코드 무결성 — 제품 코드 스냅샷 + 자재 코드 서버 재검증.

배경(2026-08-13):
 1. 제품(반제품) 품목코드는 blend_records 에 컬럼이 없어 recipe_id 조인으로만 얻어졌다.
    나중에 레시피의 product_code 를 고치면 과거 기록의 코드 해석이 소급으로 바뀐다
    — 제조기록(DHR) 관점에서 위험. → 저장 시점 스냅샷(blend_records.product_code),
    NULL 인 구 기록만 조인 폴백.
 2. blend_details.material_code 는 클라이언트가 보낸 값을 길이만 검증해 저장했다.
    → 자재를 특정할 수 있으면 서버가 materials.code 로 덮어쓴다.

픽스처는 tests/test_blend_material_code.py 의 인메모리 스키마 스타일을 따르되,
recipes.product_code · blend_records.product_code 를 추가한다.
"""

from __future__ import annotations

import sqlite3

from src.services import blend_service as bs


def _make_db(*, with_snapshot: bool = True, with_recipe_code: bool = True) -> sqlite3.Connection:
    """인메모리 배합 스키마. 컬럼 유무를 바꿔 구 스키마 폴백까지 검증한다."""
    snapshot_col = "product_code TEXT," if with_snapshot else ""
    recipe_code_col = "product_code TEXT," if with_recipe_code else ""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        f"""
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
            product_name TEXT, position TEXT, ink_name TEXT, {recipe_code_col}
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
            work_time TEXT, total_amount REAL NOT NULL, scale TEXT, {snapshot_col}
            status TEXT NOT NULL DEFAULT 'completed', note TEXT, reactor INTEGER,
            manual_entry INTEGER NOT NULL DEFAULT 0,
            is_bulk_regenerated INTEGER NOT NULL DEFAULT 0,
            rescale_events_json TEXT,
            rescale_count INTEGER NOT NULL DEFAULT 0,
            rescale_unacked INTEGER NOT NULL DEFAULT 0,
            manual_absence_reason TEXT,
            manual_unacked INTEGER NOT NULL DEFAULT 0,
            discard_events_json TEXT,
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


def _seed_material(conn, name, code) -> int:
    conn.execute(
        "INSERT INTO materials (name, unit_type, unit, category, code) "
        "VALUES (?, 'weight', 'g', '원료', ?)",
        (name, code),
    )
    return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])


def _seed_recipe(conn, product="반제품A", product_code="B0082", weights=(60.0, 40.0),
                 codes=("AS0001", "AS0002")):
    """레시피(품목코드 포함) + 자재 2종 시드."""
    has_code = "product_code" in {
        r["name"] for r in conn.execute("PRAGMA table_info(recipes)").fetchall()
    }
    if has_code:
        conn.execute(
            "INSERT INTO recipes (product_name, ink_name, product_code, status) "
            "VALUES (?, ?, ?, 'completed')",
            (product, f"{product}-반제품", product_code),
        )
    else:
        conn.execute(
            "INSERT INTO recipes (product_name, ink_name, status) VALUES (?, ?, 'completed')",
            (product, f"{product}-반제품"),
        )
    rid = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
    for i, w in enumerate(weights):
        mid = _seed_material(conn, f"원료{i+1}", codes[i])
        conn.execute(
            "INSERT INTO recipe_items (recipe_id, material_id, value_weight) VALUES (?, ?, ?)",
            (rid, mid, w),
        )
    return rid


def _create(conn, rid, product="반제품A", details=None, work_date="2026-08-13"):
    return bs.create_blend_record(
        conn,
        recipe_id=rid, product_name=product, ink_name=None, position=None,
        worker="홍길동", work_date=work_date, work_time=None,
        total_amount=200.0, scale="M-65", note=None,
        details=details if details is not None else [],
        created_by="현장", created_at=f"{work_date}T00:00:00Z",
    )


# ── ① 저장 시 제품 품목코드 스냅샷 ─────────────────────────────────
def test_create_snapshots_product_code():
    """저장 시 blend_records.product_code 에 그때의 레시피 코드가 박힌다."""
    conn = _make_db()
    rid = _seed_recipe(conn, product_code="B0082")
    record_id = _create(conn, rid)

    stored = conn.execute(
        "SELECT product_code FROM blend_records WHERE id = ?", (record_id,)
    ).fetchone()["product_code"]
    assert stored == "B0082"
    assert bs.get_blend_record(conn, record_id)["product_code"] == "B0082"


def test_create_without_recipe_snapshots_null():
    """레시피 없이 저장한 기록(수기 입력·이관)은 스냅샷 NULL — 조작하지 않는다."""
    conn = _make_db()
    record_id = bs.create_blend_record(
        conn,
        recipe_id=None, product_name="수기제품", ink_name=None, position=None,
        worker="홍길동", work_date="2026-08-13", work_time=None,
        total_amount=100.0, scale=None, note=None, details=[],
        created_by="현장", created_at="2026-08-13T00:00:00Z",
    )
    assert conn.execute(
        "SELECT product_code FROM blend_records WHERE id = ?", (record_id,)
    ).fetchone()["product_code"] is None
    assert bs.get_blend_record(conn, record_id)["product_code"] is None


# ── ② 레시피 코드가 바뀌어도 기존 기록은 불변 ─────────────────────
def test_recipe_code_change_does_not_rewrite_past_record():
    """레시피의 품목코드를 바꿔도 이미 저장된 기록의 제품 코드는 그대로다(소급 금지)."""
    conn = _make_db()
    rid = _seed_recipe(conn, product_code="B0082")
    old_id = _create(conn, rid, work_date="2026-08-01")

    conn.execute("UPDATE recipes SET product_code = 'B9999' WHERE id = ?", (rid,))
    new_id = _create(conn, rid, work_date="2026-08-13")

    assert bs.get_blend_record(conn, old_id)["product_code"] == "B0082"   # 옛 기록 불변
    assert bs.get_blend_record(conn, new_id)["product_code"] == "B9999"   # 새 기록은 새 코드
    # 목록 조회도 같은 해석(스냅샷 우선)
    by_id = {r["id"]: r for r in bs.list_blend_records(conn)}
    assert by_id[old_id]["product_code"] == "B0082"
    assert by_id[new_id]["product_code"] == "B9999"


def test_continuous_records_share_snapshot():
    """이어서 계량(로트별 다건)도 각 기록에 같은 스냅샷을 남긴다."""
    conn = _make_db()
    rid = _seed_recipe(conn, product_code="B0082")
    ids = bs.create_continuous(
        conn,
        recipe_id=rid, product_name="반제품A", ink_name=None, position=None,
        worker="홍길동", work_date="2026-08-13", work_time=None,
        total_amount=100.0, scale=None, note=None,
        lots_details=[[], []],
        created_by="현장", created_at="2026-08-13T00:00:00Z",
    )
    assert len(ids) == 2
    conn.execute("UPDATE recipes SET product_code = 'B9999' WHERE id = ?", (rid,))
    assert [bs.get_blend_record(conn, i)["product_code"] for i in ids] == ["B0082", "B0082"]


def test_bulk_regenerated_records_snapshot_current_recipe():
    """일괄 재생성은 원본 기록이 아니라 레시피로 새 문서를 만든다 → 현재 레시피 코드 스냅샷."""
    conn = _make_db()
    rid = _seed_recipe(conn, product_code="B0082")
    ids = bs.create_bulk(
        conn,
        recipe_id=rid, worker="홍길동", scale=None,
        entries=[{"work_date": "2026-08-13", "total_amount": 100.0}],
        created_by="현장", created_at="2026-08-13T00:00:00Z",
    )
    assert bs.get_blend_record(conn, ids[0])["product_code"] == "B0082"


# ── ③ 스냅샷 NULL 인 구 기록은 레시피 조인 폴백 ────────────────────
def test_legacy_null_snapshot_falls_back_to_recipe_join():
    """스냅샷 이전 기록(product_code NULL)은 레시피 조인으로 해석한다(하위호환)."""
    conn = _make_db()
    rid = _seed_recipe(conn, product_code="B0082")
    record_id = _create(conn, rid)
    # 스냅샷 도입 전에 저장된 기록을 재현 — 컬럼만 비운다.
    conn.execute("UPDATE blend_records SET product_code = NULL WHERE id = ?", (record_id,))

    assert bs.get_blend_record(conn, record_id)["product_code"] == "B0082"
    conn.execute("UPDATE recipes SET product_code = 'B9999' WHERE id = ?", (rid,))
    # 폴백이므로 구 기록은 레시피를 따라간다(스냅샷이 없는 기록의 한계 — 그래서 스냅샷).
    assert bs.get_blend_record(conn, record_id)["product_code"] == "B9999"


def test_old_schema_without_columns_returns_none():
    """product_code 컬럼이 아예 없는 구버전/최소 스키마도 죽지 않는다(None 폴백)."""
    conn = _make_db(with_snapshot=False, with_recipe_code=False)
    rid = _seed_recipe(conn)
    record_id = _create(conn, rid)
    assert bs.get_blend_record(conn, record_id)["product_code"] is None
    assert bs.list_blend_records(conn)[0]["product_code"] is None


# ── ④ 자재 코드는 서버가 덮어쓴다 ─────────────────────────────────
def test_material_code_overwritten_by_server_via_material_id():
    """클라이언트가 엉뚱한 코드를 보내도 material_id 로 특정되면 materials.code 로 저장."""
    conn = _make_db()
    rid = _seed_recipe(conn, codes=("AS0001", "AS0002"))
    mid = int(conn.execute("SELECT id FROM materials WHERE name = '원료1'").fetchone()[0])
    record_id = _create(conn, rid, details=[{
        "material_id": mid, "material_name": "원료1", "material_code": "위조코드",
        "ratio": 100, "theory_amount": 200, "actual_amount": 200, "material_lot": "L1",
    }])
    rec = bs.get_blend_record(conn, record_id)
    assert rec["details"][0]["material_code"] == "AS0001"


def test_material_code_overwritten_by_name_resolution():
    """material_id 가 없어도 자재명(또는 동의어)으로 특정되면 서버 코드가 이긴다."""
    conn = _make_db()
    _seed_material(conn, "HEMA", "AS0055")
    mid = int(conn.execute("SELECT id FROM materials WHERE name = 'HEMA'").fetchone()[0])
    conn.execute(
        "INSERT INTO material_aliases (material_id, alias_name) VALUES (?, 'PMA긴이름')",
        (mid,),
    )
    record_id = bs.create_blend_record(
        conn,
        recipe_id=None, product_name="수기제품", ink_name=None, position=None,
        worker="홍길동", work_date="2026-08-13", work_time=None,
        total_amount=200.0, scale=None, note=None,
        details=[
            {"material_name": "hema", "material_code": "옛코드",
             "actual_amount": 100, "theory_amount": 100},
            {"material_name": "PMA긴이름", "material_code": "RM0001",
             "actual_amount": 100, "theory_amount": 100},
        ],
        created_by="현장", created_at="2026-08-13T00:00:00Z",
    )
    codes = [d["material_code"] for d in bs.get_blend_record(conn, record_id)["details"]]
    assert codes == ["AS0055", "AS0055"]  # 이름 정규화 + 동의어 둘 다 서버 코드


def test_material_code_empty_when_master_has_no_code():
    """자재는 특정됐지만 품목코드 미부여면 ''(정직한 빈 값) — 옛 코드가 살아남지 않는다."""
    conn = _make_db()
    _seed_material(conn, "무코드자재", None)
    record_id = bs.create_blend_record(
        conn,
        recipe_id=None, product_name="수기제품", ink_name=None, position=None,
        worker="홍길동", work_date="2026-08-13", work_time=None,
        total_amount=100.0, scale=None, note=None,
        details=[{"material_name": "무코드자재", "material_code": "AS0031",
                  "actual_amount": 100, "theory_amount": 100}],
        created_by="현장", created_at="2026-08-13T00:00:00Z",
    )
    assert bs.get_blend_record(conn, record_id)["details"][0]["material_code"] == ""


# ── ⑤ 자재를 특정 못한 행은 클라이언트 값 보존 ─────────────────────
def test_unknown_material_keeps_client_code():
    """마스터에 없는 자재명 행은 클라이언트 값을 보존한다(기존 동작 — 코드 소실 금지)."""
    conn = _make_db()
    record_id = bs.create_blend_record(
        conn,
        recipe_id=None, product_name="수기제품", ink_name=None, position=None,
        worker="홍길동", work_date="2026-08-13", work_time=None,
        total_amount=100.0, scale=None, note=None,
        details=[{"material_name": "마스터에없는자재", "material_code": "AC0060",
                  "actual_amount": 100, "theory_amount": 100}],
        created_by="현장", created_at="2026-08-13T00:00:00Z",
    )
    assert bs.get_blend_record(conn, record_id)["details"][0]["material_code"] == "AC0060"


def test_update_record_also_overwrites_material_code():
    """책임자 전체 수정(상세 전량 교체)도 같은 규칙 — 스냅샷된 제품 코드는 보존."""
    conn = _make_db()
    rid = _seed_recipe(conn, codes=("AS0001", "AS0002"))
    mid = int(conn.execute("SELECT id FROM materials WHERE name = '원료1'").fetchone()[0])
    record_id = _create(conn, rid, details=[{
        "material_id": mid, "material_name": "원료1", "material_code": "AS0001",
        "ratio": 100, "theory_amount": 200, "actual_amount": 200, "material_lot": "L1",
    }])
    bs.update_blend_record(
        conn, record_id,
        product_name="반제품A", ink_name=None, position=None, worker="홍길동",
        work_date="2026-08-13", work_time=None, total_amount=200.0, scale=None,
        note="정정", details=[{
            "material_id": mid, "material_name": "원료1", "material_code": "위조코드",
            "ratio": 100, "theory_amount": 200, "actual_amount": 200, "material_lot": "L1",
        }],
        reactor=None, updated_at="2026-08-14T00:00:00Z",
    )
    rec = bs.get_blend_record(conn, record_id)
    assert rec["details"][0]["material_code"] == "AS0001"
    assert rec["product_code"] == "B0082"          # 수정이 스냅샷을 건드리지 않는다


# ── 폐기 경로 ─────────────────────────────────────────────────────
def test_discard_events_use_server_material_code():
    """계량 중 자재 폐기 기록의 자재 코드도 서버 값(특정 실패 행만 클라이언트 값)."""
    conn = _make_db()
    _seed_material(conn, "폐기자재", "AS0077")
    record_id = _create(conn, _seed_recipe(conn))
    events_json = bs.apply_discard_events_to_record(conn, record_id, [
        {"material_name": "폐기자재", "material_code": "옛코드", "amount_g": 12.5},
        {"material_name": "모르는자재", "material_code": "AC0060", "amount_g": 3.0},
    ])
    import json

    events = json.loads(events_json)
    assert [e["material_code"] for e in events] == ["AS0077", "AC0060"]


def test_migration_adds_product_code_column(tmp_path):
    """마이그레이션이 blend_records.product_code 를 추가한다(기존 DB 하위호환)."""
    import sqlite3 as sq

    from src.db.migrations import ensure_column

    db = sq.connect(str(tmp_path / "t.db"))
    db.row_factory = sq.Row
    db.execute(
        "CREATE TABLE blend_records (id INTEGER PRIMARY KEY, product_lot TEXT)"
    )
    ensure_column(db, "blend_records", "product_code", "TEXT")
    cols = {r["name"] for r in db.execute("PRAGMA table_info(blend_records)").fetchall()}
    assert "product_code" in cols
    db.close()
