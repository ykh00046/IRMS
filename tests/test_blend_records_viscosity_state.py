"""list_blend_records 의 reactor·viscosity_state 서버 필드 단위 테스트.

viscosity_state 4상태(na/done/skipped/missing)는 목록 SELECT 의 CASE+EXISTS 로
한 번에 계산된다. 여기선 service 레이어(list_blend_records)를 직접 부른다 —
제품 판정(name/code 일치, is_active)과 구버전 스키마 폴백(테이블 없으면 전 행 'na')이
관심 대상이라 라우트·HTTP 레이어는 끼워지지 않는다.
"""

from __future__ import annotations

import sqlite3

from src.services import blend_service as bs

# tests/test_blend.py _make_db 와 같은 스키마 + viscosity_skips(거기엔 없는 테이블).
_BASE_SCHEMA = """
CREATE TABLE blend_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_lot TEXT NOT NULL, recipe_id INTEGER, product_name TEXT NOT NULL,
    ink_name TEXT, position TEXT, worker TEXT NOT NULL, work_date TEXT NOT NULL,
    work_time TEXT, total_amount REAL NOT NULL, scale TEXT,
    status TEXT NOT NULL DEFAULT 'completed', note TEXT, reactor INTEGER,
    manual_entry INTEGER NOT NULL DEFAULT 0,
    is_bulk_regenerated INTEGER NOT NULL DEFAULT 0,
    rescale_events_json TEXT,
    rescale_count INTEGER NOT NULL DEFAULT 0,
    rescale_unacked INTEGER NOT NULL DEFAULT 0,
    manual_absence_reason TEXT,
    manual_unacked INTEGER NOT NULL DEFAULT 0,
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
"""

_VISCOSITY_SCHEMA = """
CREATE TABLE viscosity_products (
    id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT UNIQUE, name TEXT,
    target REAL, lower_limit REAL, upper_limit REAL, sigma_k REAL DEFAULT 3,
    is_active INTEGER DEFAULT 1, created_at TEXT
);
CREATE TABLE viscosity_readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT, product_id INTEGER, lot_no TEXT,
    viscosity REAL, measured_date TEXT, memo TEXT,
    created_by TEXT, created_at TEXT, blend_record_id INTEGER
);
CREATE TABLE viscosity_skips (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    blend_record_id INTEGER NOT NULL UNIQUE,
    reason TEXT NOT NULL, created_by TEXT, created_at TEXT NOT NULL
);
"""

_lot_seq = 0


def _make_db(with_viscosity: bool = True) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_BASE_SCHEMA + (_VISCOSITY_SCHEMA if with_viscosity else ""))
    return conn


def _add_record(conn: sqlite3.Connection, product: str, *, reactor: int | None = None) -> int:
    global _lot_seq
    _lot_seq += 1
    return conn.execute(
        "INSERT INTO blend_records (product_lot, product_name, worker, work_date,"
        " total_amount, status, reactor, created_at)"
        " VALUES (?, ?, '테스트작업자', '2026-09-01', 100.0, 'completed', ?, '2026-09-01 09:00')",
        (f"{product}-LOT{_lot_seq:03d}", product, reactor),
    ).lastrowid


def _add_viscosity_product(
    conn: sqlite3.Connection, *, name: str, code: str | None, is_active: int = 1
) -> int:
    return conn.execute(
        "INSERT INTO viscosity_products (code, name, is_active, created_at)"
        " VALUES (?, ?, ?, '2026-09-01')",
        (code, name, is_active),
    ).lastrowid


def _state_of(conn: sqlite3.Connection, record_id: int) -> str | None:
    rows = {r["id"]: r for r in bs.list_blend_records(conn)}
    return rows[record_id].get("viscosity_state")


# ── 4상태 ───────────────────────────────────────────────────────
def test_tracked_product_with_reading_is_done():
    conn = _make_db()
    _add_viscosity_product(conn, name="PB", code="PB")
    rid = _add_record(conn, "PB")
    conn.execute(
        "INSERT INTO viscosity_readings (product_id, lot_no, viscosity, created_at,"
        " blend_record_id) VALUES ((SELECT id FROM viscosity_products WHERE name='PB'),"
        " 'PBLOT', 50.0, '2026-09-01', ?)",
        (rid,),
    )
    assert _state_of(conn, rid) == "done"


def test_tracked_product_with_skip_is_skipped():
    conn = _make_db()
    _add_viscosity_product(conn, name="PB", code="PB")
    rid = _add_record(conn, "PB")
    conn.execute(
        "INSERT INTO viscosity_skips (blend_record_id, reason, created_at)"
        " VALUES (?, '시료 소진', '2026-09-01')",
        (rid,),
    )
    assert _state_of(conn, rid) == "skipped"


def test_tracked_product_with_neither_is_missing():
    conn = _make_db()
    _add_viscosity_product(conn, name="PB", code="PB")
    rid = _add_record(conn, "PB")
    assert _state_of(conn, rid) == "missing"


def test_untracked_product_is_na():
    conn = _make_db()
    _add_viscosity_product(conn, name="PB", code="PB")
    rid = _add_record(conn, "잉크A")  # 점도 관리 대상 아님
    assert _state_of(conn, rid) == "na"


def test_inactive_viscosity_product_is_na():
    conn = _make_db()
    _add_viscosity_product(conn, name="PB", code="PB", is_active=0)  # 비활성
    rid = _add_record(conn, "PB")
    assert _state_of(conn, rid) == "na"


def test_matching_by_code_as_well_as_name():
    """제품명이 아니라 viscosity_products.code 로 걸려도 관리 대상으로 판정한다."""
    conn = _make_db()
    _add_viscosity_product(conn, name="폴리우레탄 수지 A", code="PB")
    rid = _add_record(conn, "PB")  # 기록의 product_name 이 코드와 일치
    conn.execute(
        "INSERT INTO viscosity_readings (product_id, lot_no, viscosity, created_at,"
        " blend_record_id) VALUES ((SELECT id FROM viscosity_products WHERE code='PB'),"
        " 'PBLOT', 50.0, '2026-09-01', ?)",
        (rid,),
    )
    assert _state_of(conn, rid) == "done"


# ── reactor 필드 ────────────────────────────────────────────────
def test_reactor_value_is_returned_for_a_record_that_has_one():
    conn = _make_db()
    rid = _add_record(conn, "PB", reactor=2)
    rows = {r["id"]: r for r in bs.list_blend_records(conn)}
    assert rows[rid]["reactor"] == 2
    # reactor 없는 기록은 여전히 null.
    rid_none = _add_record(conn, "PB")
    rows = {r["id"]: r for r in bs.list_blend_records(conn)}
    assert rows[rid_none]["reactor"] is None


# ── 구버전 스키마 폴백 ──────────────────────────────────────────
def test_old_schema_without_viscosity_tables_returns_na_without_raising():
    conn = _make_db(with_viscosity=False)
    ids = [_add_record(conn, name) for name in ("PB", "SBCT", "잉크A")]
    items = bs.list_blend_records(conn)  # 예외 없이
    by_id = {r["id"]: r for r in items}
    assert len(items) == 3
    for rid in ids:
        assert by_id[rid]["viscosity_state"] == "na"


def test_partial_schema_missing_only_skips_also_falls_back_to_na():
    """viscosity_products/readings 는 있지만 skips 만 없는 스키마(단위테스트 실제 사례)도 폴백."""
    conn = _make_db()
    conn.execute("DROP TABLE viscosity_skips")
    _add_viscosity_product(conn, name="PB", code="PB")
    rid = _add_record(conn, "PB")
    assert _state_of(conn, rid) == "na"
