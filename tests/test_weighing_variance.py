"""Unit + route tests for weighing-variance-analysis.

Design: docs/02-design/features/weighing-variance-analysis.design.md §7

목표=value_weight, 실측=actual_weight, 편차=실측-목표. 실측 NULL은 집계 시 목표로 폴백.
"""

from __future__ import annotations

import sqlite3

from fastapi.testclient import TestClient

from src.services import variance_service


FROM_TS = "2026-06-01T00:00:00Z"
TO_TS = "2026-06-30T23:59:59Z"
TS = "2026-06-10T10:00:00Z"          # 범위 내
TS_OUT = "2026-05-10T10:00:00Z"      # 범위 밖


def _make_db() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute(
        """
        CREATE TABLE materials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            category TEXT
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE recipes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_name TEXT,
            ink_name TEXT
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE recipe_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipe_id INTEGER NOT NULL,
            material_id INTEGER NOT NULL,
            value_weight REAL,
            actual_weight REAL,
            measured_at TEXT,
            measured_by TEXT
        )
        """
    )
    return connection


def _add_material(conn, name, *, category="잉크"):
    cur = conn.execute(
        "INSERT INTO materials (name, category) VALUES (?, ?)", (name, category)
    )
    return int(cur.lastrowid)


def _add_recipe(conn, product="제품", ink="잉크A"):
    cur = conn.execute(
        "INSERT INTO recipes (product_name, ink_name) VALUES (?, ?)", (product, ink)
    )
    return int(cur.lastrowid)


def _add_item(conn, recipe_id, material_id, value, actual, *, at=TS, by="홍길동"):
    conn.execute(
        "INSERT INTO recipe_items (recipe_id, material_id, value_weight, actual_weight, "
        "measured_at, measured_by) VALUES (?, ?, ?, ?, ?, ?)",
        (recipe_id, material_id, value, actual, at, by),
    )


# ── variance_summary ─────────────────────────────────────────────────────────

def test_summary_partial_actual_coverage():
    """V1 — 실측 일부만 입력: 커버리지/편차/폴백 합산이 정확하다."""
    conn = _make_db()
    r = _add_recipe(conn)
    m = _add_material(conn, "A")
    _add_item(conn, r, m, 100.0, 110.0)   # 편차 +10
    _add_item(conn, r, m, 200.0, 180.0)   # 편차 -20
    _add_item(conn, r, m, 50.0, None)     # 실측 미입력 → 목표로 폴백, 편차 0

    s = variance_service.variance_summary(conn, FROM_TS, TO_TS)
    assert s["measured_count"] == 3
    assert s["actual_count"] == 2
    assert s["coverage_pct"] == round(2 / 3 * 100, 2)
    assert s["target_total_g"] == 350.0
    assert s["actual_total_g"] == 340.0           # 110+180+50(폴백)
    assert s["deviation_total_g"] == -10.0
    assert s["absolute_deviation_total_g"] == 30.0  # 10+20+0
    assert s["deviation_pct"] == round(-10 / 350 * 100, 2)


def test_summary_no_actual():
    """V2 — 실측 0건: actual_count=0, coverage=0, 편차합=0."""
    conn = _make_db()
    r = _add_recipe(conn)
    m = _add_material(conn, "A")
    _add_item(conn, r, m, 100.0, None)
    _add_item(conn, r, m, 200.0, None)

    s = variance_service.variance_summary(conn, FROM_TS, TO_TS)
    assert s["measured_count"] == 2
    assert s["actual_count"] == 0
    assert s["coverage_pct"] == 0.0
    assert s["deviation_total_g"] == 0.0
    assert s["absolute_deviation_total_g"] == 0.0


def test_summary_ignores_out_of_range():
    """V3 — 범위 밖 measured_at은 집계에서 제외된다."""
    conn = _make_db()
    r = _add_recipe(conn)
    m = _add_material(conn, "A")
    _add_item(conn, r, m, 100.0, 130.0)              # 범위 내
    _add_item(conn, r, m, 100.0, 999.0, at=TS_OUT)   # 범위 밖 → 제외

    s = variance_service.variance_summary(conn, FROM_TS, TO_TS)
    assert s["measured_count"] == 1
    assert s["deviation_total_g"] == 30.0


# ── top_material_variances ───────────────────────────────────────────────────

def test_materials_sorted_and_excludes_no_actual():
    """V4 — |편차| 내림차순 + 실측 0건 자재 제외."""
    conn = _make_db()
    r = _add_recipe(conn)
    big = _add_material(conn, "큰편차")
    small = _add_material(conn, "작은편차")
    none = _add_material(conn, "실측없음")
    _add_item(conn, r, big, 100.0, 130.0)    # |편차| 30
    _add_item(conn, r, small, 100.0, 105.0)  # |편차| 5
    _add_item(conn, r, none, 100.0, None)    # 실측 없음 → 제외

    items = variance_service.top_material_variances(conn, FROM_TS, TO_TS)
    names = [it["material_name"] for it in items]
    assert names == ["큰편차", "작은편차"]      # |편차| DESC, none 제외
    assert items[0]["absolute_deviation_g"] == 30.0
    assert items[0]["deviation_g"] == 30.0
    assert items[1]["deviation_g"] == 5.0


def test_materials_respects_limit():
    """V5 — limit 적용 시 상위 N건만 반환."""
    conn = _make_db()
    r = _add_recipe(conn)
    for i in range(3):
        mid = _add_material(conn, f"자재{i}")
        _add_item(conn, r, mid, 100.0, 100.0 + (i + 1) * 10)  # 편차 10/20/30

    items = variance_service.top_material_variances(conn, FROM_TS, TO_TS, limit=2)
    assert len(items) == 2
    assert items[0]["absolute_deviation_g"] == 30.0  # 가장 큰 편차 우선


# ── material_variance_recipes ────────────────────────────────────────────────

def test_recipes_deviation_and_order():
    """V6 — 자재별 레시피 편차/편차율 정확 + |편차| 내림차순, 실측 입력행만."""
    conn = _make_db()
    m = _add_material(conn, "A")
    r1 = _add_recipe(conn, "제품1", "잉크1")
    r2 = _add_recipe(conn, "제품2", "잉크2")
    r3 = _add_recipe(conn, "제품3", "잉크3")
    _add_item(conn, r1, m, 100.0, 105.0)   # 편차 +5
    _add_item(conn, r2, m, 100.0, 120.0)   # 편차 +20
    _add_item(conn, r3, m, 100.0, None)    # 실측 없음 → 제외

    rows = variance_service.material_variance_recipes(conn, m, FROM_TS, TO_TS)
    assert len(rows) == 2                          # 실측 입력행만
    assert rows[0]["product_name"] == "제품2"      # |편차| DESC
    assert rows[0]["deviation_g"] == 20.0
    assert rows[0]["deviation_pct"] == 20.0
    assert rows[1]["deviation_g"] == 5.0


def test_recipes_zero_target_pct_none():
    """V7 — 목표 0이면 편차율은 None(0분모 방지)."""
    conn = _make_db()
    m = _add_material(conn, "A")
    r = _add_recipe(conn)
    _add_item(conn, r, m, 0.0, 5.0)

    rows = variance_service.material_variance_recipes(conn, m, FROM_TS, TO_TS)
    assert len(rows) == 1
    assert rows[0]["target_weight_g"] == 0.0
    assert rows[0]["deviation_g"] == 5.0
    assert rows[0]["deviation_pct"] is None


def test_recipes_unknown_material_empty():
    """V9(서비스) — 미존재 자재는 빈 리스트(라우터 404는 비인증 차단과 QA로 검증)."""
    conn = _make_db()
    assert variance_service.material_variance_recipes(conn, 999, FROM_TS, TO_TS) == []


# ── 라우트 권한 ──────────────────────────────────────────────────────────────

def test_variance_routes_require_auth():
    """V8 — 비인증 접근은 차단(manager scope)."""
    import importlib
    import src.config as cfg
    import src.main as mainmod

    importlib.reload(cfg)
    importlib.reload(mainmod)
    client = TestClient(mainmod.app)
    for path in (
        "/api/dashboard/variance/summary",
        "/api/dashboard/variance/materials",
        "/api/dashboard/variance/materials/1/recipes",
    ):
        res = client.get(path)
        assert res.status_code in (401, 403), path
