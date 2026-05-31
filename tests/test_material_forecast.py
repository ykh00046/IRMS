"""Unit + route tests for material-forecast.

Design: docs/02-design/features/material-forecast.design.md §7
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from src.db import utc_now_text
from src.services import forecast_service


def _make_db() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute(
        """
        CREATE TABLE materials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            unit_type TEXT NOT NULL,
            unit TEXT NOT NULL,
            category TEXT,
            is_active INTEGER NOT NULL DEFAULT 1,
            stock_quantity REAL NOT NULL DEFAULT 0,
            lead_time_days REAL NOT NULL DEFAULT 0,
            reorder_cycle_days REAL NOT NULL DEFAULT 0
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE material_stock_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            material_id INTEGER NOT NULL,
            delta REAL NOT NULL,
            reason TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    return connection


def _add_material(connection, name, *, unit_type="weight", stock=0.0,
                  lead=0.0, cycle=0.0, category="잉크"):
    cur = connection.execute(
        "INSERT INTO materials (name, unit_type, unit, category, stock_quantity, "
        "lead_time_days, reorder_cycle_days) VALUES (?, ?, 'g', ?, ?, ?, ?)",
        (name, unit_type, category, stock, lead, cycle),
    )
    return int(cur.lastrowid)


def _consume(connection, material_id, weight, *, days_ago=0):
    ts = (datetime.now(timezone.utc) - timedelta(days=days_ago)).replace(
        microsecond=0
    ).isoformat().replace("+00:00", "Z")
    connection.execute(
        "INSERT INTO material_stock_logs (material_id, delta, reason, created_at) "
        "VALUES (?, ?, 'measurement', ?)",
        (material_id, -abs(weight), ts),
    )


def _find(items, material_id):
    return next(it for it in items if it["material_id"] == material_id)


def test_avg_daily_and_days_remaining():
    """T1 — 30일간 300g 소모, 현재고 100g → avg=10, days_remaining=10."""
    conn = _make_db()
    mid = _add_material(conn, "검정", stock=100.0)
    for d in range(30):
        _consume(conn, mid, 10.0, days_ago=d)

    result = forecast_service.compute_forecast(conn, window_days=30)
    item = _find(result["items"], mid)
    assert item["avg_daily"] == pytest.approx(10.0, abs=0.5)
    assert item["days_remaining"] == pytest.approx(10.0, abs=0.5)
    # lead=7 기본, 10 <= 7*1.5=10.5 → soon
    assert item["status"] in ("urgent", "soon")


def test_no_data_avoids_division():
    """T2 — 소모 이력 없음 → no_data, days_remaining=None (0 나눗셈 없음)."""
    conn = _make_db()
    mid = _add_material(conn, "미사용", stock=500.0)
    result = forecast_service.compute_forecast(conn, window_days=30)
    item = _find(result["items"], mid)
    assert item["status"] == "no_data"
    assert item["days_remaining"] is None
    assert item["recommended_order_qty"] == 0.0
    assert item["predicted_stockout_date"] is None


def test_plenty_of_stock_is_ok():
    """T3 — 재고가 소모량 대비 충분 → ok, 권장 발주량 0."""
    conn = _make_db()
    mid = _add_material(conn, "여유", stock=100000.0)
    for d in range(30):
        _consume(conn, mid, 5.0, days_ago=d)
    result = forecast_service.compute_forecast(conn, window_days=30)
    item = _find(result["items"], mid)
    assert item["status"] == "ok"
    assert item["recommended_order_qty"] == 0.0


def test_count_type_excluded():
    """T4 — unit_type='count' 자재는 예측 대상에서 제외."""
    conn = _make_db()
    _add_material(conn, "라벨", unit_type="count", stock=10.0)
    result = forecast_service.compute_forecast(conn, window_days=30)
    assert result["items"] == []
    assert result["summary"]["total_materials"] == 0


def test_set_params_changes_recommendation():
    """T5 — 커버리지를 늘리면 권장 발주량이 증가."""
    conn = _make_db()
    mid = _add_material(conn, "파랑", stock=100.0)
    for d in range(30):
        _consume(conn, mid, 10.0, days_ago=d)

    before = _find(forecast_service.compute_forecast(conn, window_days=30)["items"], mid)
    forecast_service.set_forecast_params(conn, mid, lead_time_days=7, reorder_cycle_days=60)
    after = _find(forecast_service.compute_forecast(conn, window_days=30)["items"], mid)

    assert after["reorder_cycle_days"] == 60
    assert after["recommended_order_qty"] > before["recommended_order_qty"]


def test_negative_stock_is_urgent():
    """T8 — 음수 재고 → urgent, 권장 발주량 > 0."""
    conn = _make_db()
    mid = _add_material(conn, "소진", stock=-20.0)
    for d in range(30):
        _consume(conn, mid, 10.0, days_ago=d)
    item = _find(forecast_service.compute_forecast(conn, window_days=30)["items"], mid)
    assert item["status"] == "urgent"
    assert item["recommended_order_qty"] > 0
    # 음수 재고 → 잔여일수<0 → 소진 예상일은 오늘(UTC)로 고정
    today_utc = datetime.now(timezone.utc).date().isoformat()
    assert item["predicted_stockout_date"] == today_utc


def test_set_params_rejects_negative():
    conn = _make_db()
    mid = _add_material(conn, "x", stock=1.0)
    with pytest.raises(ValueError):
        forecast_service.set_forecast_params(conn, mid, lead_time_days=-1, reorder_cycle_days=10)


def test_summary_counts():
    conn = _make_db()
    # urgent
    u = _add_material(conn, "urgent", stock=10.0)
    for d in range(30):
        _consume(conn, u, 10.0, days_ago=d)
    # ok
    o = _add_material(conn, "ok", stock=100000.0)
    for d in range(30):
        _consume(conn, o, 5.0, days_ago=d)
    # no_data
    _add_material(conn, "none", stock=50.0)

    summary = forecast_service.compute_forecast(conn, window_days=30)["summary"]
    assert summary["total_materials"] == 3
    assert summary["no_data"] == 1
    assert summary["reorder_recommended"] == summary["urgent"] + summary["soon"]


def test_window_excludes_old_consumption():
    """분석기간 밖(40일 전)의 소모는 집계되지 않는다."""
    conn = _make_db()
    mid = _add_material(conn, "구형", stock=100.0)
    _consume(conn, mid, 1000.0, days_ago=40)  # 30일 창 밖
    item = _find(forecast_service.compute_forecast(conn, window_days=30)["items"], mid)
    assert item["status"] == "no_data"


# ── Route-level permission test ──────────────────────────────────────────────

def test_forecast_requires_authentication():
    """T6 — 비인증/operator 접근은 차단(manager scope)."""
    import importlib
    import src.config as cfg
    import src.main as mainmod

    importlib.reload(cfg)
    importlib.reload(mainmod)
    client = TestClient(mainmod.app)
    res = client.get("/api/forecast/materials")
    assert res.status_code in (401, 403)


def test_export_only_reorder_filters_rows(monkeypatch):
    """T7 — /forecast/export?only_reorder=true 는 urgent/soon 행만 CSV에 포함."""
    import importlib
    import src.auth as auth
    import src.config as cfg
    import src.main as mainmod
    from src.db import get_connection

    importlib.reload(cfg)
    importlib.reload(mainmod)

    # manager 인증 우회 (require_access_level 내부의 get_current_user를 대체)
    monkeypatch.setattr(
        auth, "get_current_user",
        lambda request, required=True: {"id": 1, "username": "t", "access_level": "admin"},
    )

    urgent_name = "ZZ_긴급테스트자재"
    ok_name = "ZZ_여유테스트자재"
    with get_connection() as conn:
        conn.execute("DELETE FROM materials WHERE name IN (?, ?)", (urgent_name, ok_name))
        u = int(conn.execute(
            "INSERT INTO materials (name, unit_type, unit, color_group, category, "
            "stock_quantity) VALUES (?, 'weight', 'g', 'none', '잉크', 10)", (urgent_name,)
        ).lastrowid)
        o = int(conn.execute(
            "INSERT INTO materials (name, unit_type, unit, color_group, category, "
            "stock_quantity) VALUES (?, 'weight', 'g', 'none', '잉크', 100000)", (ok_name,)
        ).lastrowid)
        for d in range(30):
            ts = (datetime.now(timezone.utc) - timedelta(days=d)).replace(
                microsecond=0).isoformat().replace("+00:00", "Z")
            conn.execute(
                "INSERT INTO material_stock_logs (material_id, delta, balance_after, "
                "reason, created_at) VALUES (?, -10, 0, 'measurement', ?)", (u, ts))
            conn.execute(
                "INSERT INTO material_stock_logs (material_id, delta, balance_after, "
                "reason, created_at) VALUES (?, -5, 0, 'measurement', ?)", (o, ts))
        conn.commit()

    try:
        client = TestClient(mainmod.app)
        res = client.get("/api/forecast/export?window_days=30&only_reorder=true")
        assert res.status_code == 200
        assert "text/csv" in res.headers["content-type"]
        body = res.text
        assert urgent_name in body       # urgent → 포함
        assert ok_name not in body       # ok → 제외
    finally:
        with get_connection() as conn:
            conn.execute("DELETE FROM material_stock_logs WHERE material_id IN (?, ?)", (u, o))
            conn.execute("DELETE FROM materials WHERE id IN (?, ?)", (u, o))
            conn.commit()
