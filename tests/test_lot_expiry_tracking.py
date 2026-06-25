"""Unit + route tests for lot-expiry-tracking.

Design: docs/02-design/features/lot-expiry-tracking.design.md §7
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from src.services import lot_service

TODAY = date(2026, 6, 2)


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
        CREATE TABLE material_lots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            material_id INTEGER NOT NULL,
            lot_no TEXT,
            received_quantity REAL NOT NULL,
            remaining_quantity REAL NOT NULL,
            received_at TEXT NOT NULL,
            expiry_date TEXT,
            status TEXT NOT NULL DEFAULT 'active',
            note TEXT,
            actor_id INTEGER,
            actor_name TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    return connection


def _add_material(connection, name="잉크A", category="잉크") -> int:
    cur = connection.execute(
        "INSERT INTO materials (name, category) VALUES (?, ?)", (name, category)
    )
    return int(cur.lastrowid)


def _iso(days_from_today: int) -> str:
    return (TODAY + timedelta(days=days_from_today)).isoformat()


# ── L1: 순수 상태 판정 ─────────────────────────────────────────


def test_expiry_state_boundaries():
    assert lot_service.expiry_state(_iso(-1), TODAY) == "expired"
    assert lot_service.expiry_state(_iso(0), TODAY) == "expiring_soon"
    assert lot_service.expiry_state(_iso(30), TODAY) == "expiring_soon"  # 경계 포함
    assert lot_service.expiry_state(_iso(31), TODAY) == "ok"
    assert lot_service.expiry_state(None, TODAY) == "no_expiry"


def test_days_until():
    assert lot_service.days_until(_iso(5), TODAY) == 5
    assert lot_service.days_until(_iso(-3), TODAY) == -3
    assert lot_service.days_until(None, TODAY) is None


# ── L2: 등록 ───────────────────────────────────────────────────


def test_register_lot_initializes_remaining():
    conn = _make_db()
    mid = _add_material(conn)
    res = lot_service.register_lot(
        conn, material_id=mid, lot_no="L1", quantity=500.0,
        received_at=_iso(0), expiry_date=_iso(60), actor=None,
    )
    assert res["remaining_quantity"] == 500.0
    assert res["status"] == "active"
    row = conn.execute("SELECT * FROM material_lots WHERE id = ?", (res["lot_id"],)).fetchone()
    assert row["remaining_quantity"] == 500.0 and row["received_quantity"] == 500.0


def test_register_lot_rejects_bad_input():
    conn = _make_db()
    mid = _add_material(conn)
    with pytest.raises(ValueError):
        lot_service.register_lot(conn, material_id=mid, lot_no=None, quantity=0,
                                 received_at=None, expiry_date=None, actor=None)
    with pytest.raises(ValueError):  # 유통기한 < 입고일
        lot_service.register_lot(conn, material_id=mid, lot_no=None, quantity=10,
                                 received_at=_iso(0), expiry_date=_iso(-5), actor=None)
    with pytest.raises(ValueError):  # 잘못된 날짜 형식
        lot_service.register_lot(conn, material_id=mid, lot_no=None, quantity=10,
                                 received_at="2026/06/02", expiry_date=None, actor=None)


# ── L3/L4: 소진 ────────────────────────────────────────────────


def test_consume_partial_and_full():
    conn = _make_db()
    mid = _add_material(conn)
    lot = lot_service.register_lot(conn, material_id=mid, lot_no=None, quantity=100.0,
                                   received_at=_iso(0), expiry_date=_iso(60), actor=None)
    r1 = lot_service.consume_lot(conn, lot_id=lot["lot_id"], amount=40.0)
    assert r1["remaining_quantity"] == 60.0 and r1["status"] == "active"
    r2 = lot_service.consume_lot(conn, lot_id=lot["lot_id"], amount=60.0)
    assert r2["remaining_quantity"] == 0.0 and r2["status"] == "depleted"


def test_consume_over_remaining_raises():
    conn = _make_db()
    mid = _add_material(conn)
    lot = lot_service.register_lot(conn, material_id=mid, lot_no=None, quantity=10.0,
                                   received_at=_iso(0), expiry_date=None, actor=None)
    with pytest.raises(ValueError):
        lot_service.consume_lot(conn, lot_id=lot["lot_id"], amount=11.0)


def test_consume_depleted_lot_raises():
    conn = _make_db()
    mid = _add_material(conn)
    lot = lot_service.register_lot(conn, material_id=mid, lot_no=None, quantity=10.0,
                                   received_at=_iso(0), expiry_date=None, actor=None)
    lot_service.consume_lot(conn, lot_id=lot["lot_id"], amount=10.0)
    with pytest.raises(ValueError):
        lot_service.consume_lot(conn, lot_id=lot["lot_id"], amount=1.0)


# ── L5: 폐기 ───────────────────────────────────────────────────


def test_discard_requires_note():
    conn = _make_db()
    mid = _add_material(conn)
    lot = lot_service.register_lot(conn, material_id=mid, lot_no=None, quantity=10.0,
                                   received_at=_iso(0), expiry_date=None, actor=None)
    with pytest.raises(ValueError):
        lot_service.discard_lot(conn, lot_id=lot["lot_id"], note="")
    res = lot_service.discard_lot(conn, lot_id=lot["lot_id"], note="변질")
    assert res["status"] == "discarded" and res["remaining_quantity"] == 0.0


# ── L6: 목록 정렬/필터 ─────────────────────────────────────────


def test_list_lots_sorting_and_filter():
    conn = _make_db()
    mid = _add_material(conn)
    lot_service.register_lot(conn, material_id=mid, lot_no="ok", quantity=10,
                             received_at=_iso(0), expiry_date=_iso(90), actor=None)
    lot_service.register_lot(conn, material_id=mid, lot_no="exp", quantity=10,
                             received_at=_iso(-10), expiry_date=_iso(-1), actor=None)
    lot_service.register_lot(conn, material_id=mid, lot_no="soon", quantity=10,
                             received_at=_iso(0), expiry_date=_iso(5), actor=None)
    discarded = lot_service.register_lot(conn, material_id=mid, lot_no="gone", quantity=10,
                                         received_at=_iso(0), expiry_date=_iso(2), actor=None)
    lot_service.discard_lot(conn, lot_id=discarded["lot_id"], note="x")

    active = lot_service.list_lots(conn, today=TODAY)
    states = [it["expiry_state"] for it in active]
    assert states == ["expired", "expiring_soon", "ok"]  # discarded 제외, 위험 우선
    assert all(it["status"] == "active" for it in active)

    everything = lot_service.list_lots(conn, include_inactive=True, today=TODAY)
    assert any(it["status"] == "discarded" for it in everything)


# ── L7: 대시보드 집계 ──────────────────────────────────────────


def test_expiry_alert_collects_only_at_risk():
    conn = _make_db()
    mid = _add_material(conn)
    lot_service.register_lot(conn, material_id=mid, lot_no="exp", quantity=10,
                             received_at=_iso(-10), expiry_date=_iso(-2), actor=None)
    lot_service.register_lot(conn, material_id=mid, lot_no="soon", quantity=10,
                             received_at=_iso(0), expiry_date=_iso(10), actor=None)
    lot_service.register_lot(conn, material_id=mid, lot_no="ok", quantity=10,
                             received_at=_iso(0), expiry_date=_iso(90), actor=None)
    lot_service.register_lot(conn, material_id=mid, lot_no="none", quantity=10,
                             received_at=_iso(0), expiry_date=None, actor=None)
    # 잔여 0(소진)은 제외돼야 함
    depl = lot_service.register_lot(conn, material_id=mid, lot_no="depl", quantity=10,
                                    received_at=_iso(0), expiry_date=_iso(1), actor=None)
    lot_service.consume_lot(conn, lot_id=depl["lot_id"], amount=10)

    alert = lot_service.expiry_alert(conn, today=TODAY)
    assert alert["expired"] == 1
    assert alert["expiring_soon"] == 1
    assert alert["total_alert"] == 2
    assert [it["expiry_state"] for it in alert["items"]] == ["expired", "expiring_soon"]


def test_expiry_alert_respects_limit():
    conn = _make_db()
    mid = _add_material(conn)
    for i in range(7):
        lot_service.register_lot(conn, material_id=mid, lot_no=f"e{i}", quantity=10,
                                 received_at=_iso(-10), expiry_date=_iso(-1 - i), actor=None)
    alert = lot_service.expiry_alert(conn, limit=3, today=TODAY)
    assert alert["expired"] == 7
    assert alert["total_alert"] == 7
    assert len(alert["items"]) == 3
    assert alert["shown"] == 3


# ── L8: 라우트 권한 ────────────────────────────────────────────


def test_lot_routes_open_without_login():
    """배합 단일 신뢰 — 비로그인(현장)도 접근 가능(401/403 아님)."""
    import importlib
    import src.config as cfg
    import src.main as mainmod

    importlib.reload(cfg)
    importlib.reload(mainmod)
    client = TestClient(mainmod.app)
    assert client.get("/api/materials/lots").status_code not in (401, 403)
    assert client.get("/api/dashboard/expiry-alert").status_code not in (401, 403)
    # POST 쓰기는 인증이 아니라 CSRF 로 보호(토큰 없으면 403) — 별개 보호라 여기선 검사하지 않음


# ── L9: 마이그레이션 ───────────────────────────────────────────


def test_migration_creates_lot_table_and_indexes():
    import importlib
    import src.config as cfg
    import src.main as mainmod
    from src.db import get_connection

    importlib.reload(cfg)
    importlib.reload(mainmod)
    with get_connection() as conn:
        tbl = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='material_lots'"
        ).fetchall()
        idx = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND name IN ('idx_material_lots_material', 'idx_material_lots_expiry')"
        ).fetchall()
    assert len(tbl) == 1
    assert len(idx) == 2
