"""Unit + route tests for forecast-dashboard-alert.

Design: docs/02-design/features/forecast-dashboard-alert.design.md §7
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

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


def _add_material(connection, name, *, stock=0.0, lead=0.0, cycle=0.0, category="잉크"):
    cur = connection.execute(
        "INSERT INTO materials (name, unit_type, unit, category, stock_quantity, "
        "lead_time_days, reorder_cycle_days) VALUES (?, 'weight', 'g', ?, ?, ?, ?)",
        (name, category, stock, lead, cycle),
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


def _consume_30d(connection, material_id, daily):
    for d in range(30):
        _consume(connection, material_id, daily, days_ago=d)


def test_alert_collects_only_reorder():
    """A1 — urgent 1 + soon 1 + ok 1 → reorder_recommended=2, urgent 먼저."""
    conn = _make_db()
    u = _add_material(conn, "urgent", stock=10.0)      # 1일치 → urgent
    _consume_30d(conn, u, 10.0)
    s = _add_material(conn, "soon", stock=100.0)       # ~10일치, lead7 → soon
    _consume_30d(conn, s, 10.0)
    ok = _add_material(conn, "ok", stock=100000.0)
    _consume_30d(conn, ok, 5.0)

    alert = forecast_service.forecast_alert(conn, window_days=30)
    assert alert["reorder_recommended"] == 2
    assert len(alert["items"]) == 2
    assert alert["items"][0]["status"] == "urgent"     # urgent가 soon보다 앞
    assert alert["items"][1]["status"] == "soon"
    assert alert["shown"] == 2
    # ok 자재는 포함되지 않는다
    assert all(it["status"] in ("urgent", "soon") for it in alert["items"])


def test_alert_empty_when_no_reorder():
    """A2 — 발주 임박 0건 → reorder_recommended=0, items=[]."""
    conn = _make_db()
    ok = _add_material(conn, "ok", stock=100000.0)
    _consume_30d(conn, ok, 5.0)
    _add_material(conn, "none", stock=50.0)  # no_data (seeded for side effect)

    alert = forecast_service.forecast_alert(conn, window_days=30)
    assert alert["reorder_recommended"] == 0
    assert alert["items"] == []
    assert alert["shown"] == 0


def test_alert_respects_limit():
    """A3 — urgent 7건 중 limit=3 → items 3건, shown=3, 카운트는 전체."""
    conn = _make_db()
    for i in range(7):
        mid = _add_material(conn, f"urgent{i}", stock=5.0)
        _consume_30d(conn, mid, 10.0)

    alert = forecast_service.forecast_alert(conn, window_days=30, limit=3)
    assert alert["urgent"] == 7
    assert alert["reorder_recommended"] == 7
    assert len(alert["items"]) == 3
    assert alert["shown"] == 3


def test_alert_sorted_by_days_remaining():
    """A4 — 같은 status 내에서는 잔여일 오름차순(가장 임박한 자재가 위)."""
    conn = _make_db()
    a = _add_material(conn, "a", stock=10.0)   # 1일
    _consume_30d(conn, a, 10.0)
    b = _add_material(conn, "b", stock=20.0)   # 2일
    _consume_30d(conn, b, 10.0)

    items = forecast_service.forecast_alert(conn, window_days=30)["items"]
    days = [it["days_remaining"] for it in items]
    assert days == sorted(days)


def test_alert_route_open_without_login():
    """배합 단일 신뢰 — 비로그인(현장)도 접근 가능(401/403 아님)."""
    import importlib
    import src.config as cfg
    import src.main as mainmod

    importlib.reload(cfg)
    importlib.reload(mainmod)
    client = TestClient(mainmod.app)
    res = client.get("/api/dashboard/forecast-alert")
    assert res.status_code not in (401, 403)


def test_migration_creates_reason_created_index():
    """A6 — 마이그레이션이 idx_stock_logs_reason_created 인덱스를 생성한다."""
    import importlib
    import src.config as cfg
    import src.main as mainmod
    from src.db import get_connection

    importlib.reload(cfg)
    importlib.reload(mainmod)
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND name='idx_stock_logs_reason_created'"
        ).fetchall()
    assert len(rows) == 1
