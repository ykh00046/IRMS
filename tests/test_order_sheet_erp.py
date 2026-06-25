"""Unit + route tests for order-sheet-erp.

Design: docs/02-design/features/order-sheet-erp.design.md §8
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from src.services import order_service


# ── In-memory DB matching the runtime schema (forecast + order tables) ────────

def _make_db() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(
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
        );
        CREATE TABLE material_stock_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            material_id INTEGER NOT NULL,
            delta REAL NOT NULL,
            reason TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE purchase_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_no TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL DEFAULT 'draft',
            window_days INTEGER NOT NULL,
            note TEXT,
            item_count INTEGER NOT NULL DEFAULT 0,
            total_qty REAL NOT NULL DEFAULT 0,
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT,
            sent_at TEXT,
            sent_by TEXT,
            erp_mode TEXT,
            erp_status_code INTEGER,
            erp_response TEXT
        );
        CREATE TABLE purchase_order_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            material_id INTEGER NOT NULL,
            material_name TEXT NOT NULL,
            category TEXT,
            unit TEXT NOT NULL DEFAULT 'g',
            stock_quantity REAL,
            avg_daily REAL,
            days_remaining REAL,
            predicted_stockout_date TEXT,
            urgency_status TEXT,
            recommended_qty REAL NOT NULL,
            order_qty REAL NOT NULL,
            note TEXT
        );
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


def _urgent_material(connection, name="검정", stock=10.0):
    """잔여일수가 리드타임 이내가 되도록 빠르게 소진되는 자재."""
    mid = _add_material(connection, name, stock=stock)
    for d in range(30):
        _consume(connection, mid, 10.0, days_ago=d)
    return mid


@dataclass
class _FakeResult:
    ok: bool
    mode: str
    status_code: int
    body: str


# ── Tests ─────────────────────────────────────────────────────────────────

def test_create_order_from_forecast():
    """T1 — urgent/soon 자재로 발주서 생성, order_qty==recommended_qty."""
    conn = _make_db()
    _urgent_material(conn)
    order = order_service.create_order_from_forecast(
        conn, window_days=30, created_by="책임자"
    )
    assert order["status"] == "draft"
    assert order["item_count"] >= 1
    assert order["order_no"].startswith("PO-")
    for it in order["items"]:
        assert it["order_qty"] == it["recommended_qty"]
        assert it["urgency_status"] in ("urgent", "soon")


def test_create_order_empty_raises():
    """T2 — 발주 권장 0건 → ValueError."""
    conn = _make_db()
    _add_material(conn, "여유", stock=100000.0)
    for d in range(30):
        _consume(conn, 1, 1.0, days_ago=d)
    with pytest.raises(ValueError):
        order_service.create_order_from_forecast(conn, window_days=30, created_by="x")


def test_order_no_sequence():
    """T3 — 같은 날 2건 → -001, -002."""
    conn = _make_db()
    _urgent_material(conn, name="a")
    _urgent_material(conn, name="b")
    today = date(2026, 6, 2)
    no1 = order_service.generate_order_no(conn, today)
    assert no1 == "PO-20260602-001"
    conn.execute(
        "INSERT INTO purchase_orders (order_no, window_days, created_by, created_at) "
        "VALUES (?, 30, 'x', 'now')",
        (no1,),
    )
    no2 = order_service.generate_order_no(conn, today)
    assert no2 == "PO-20260602-002"


def test_update_order_quantities():
    """T4 — draft 수량 수정, 0 입력은 합계 제외."""
    conn = _make_db()
    _urgent_material(conn, name="a")
    _urgent_material(conn, name="b")
    order = order_service.create_order_from_forecast(conn, window_days=30, created_by="x")
    items = order["items"]
    edits = [
        {"id": items[0]["id"], "order_qty": 50.0},
        {"id": items[1]["id"], "order_qty": 0.0},  # 제외
    ]
    updated = order_service.update_order(conn, order["id"], note="급함", items=edits)
    assert updated["note"] == "급함"
    assert updated["item_count"] == 1  # order_qty>0 만 집계
    assert updated["total_qty"] == pytest.approx(50.0)


def test_update_sent_order_rejected():
    """T5 — sent 상태 수정 시도 → OrderStateError."""
    conn = _make_db()
    _urgent_material(conn)
    order = order_service.create_order_from_forecast(conn, window_days=30, created_by="x")
    order_service.mark_sent(
        conn, order["id"],
        result=_FakeResult(True, "mock", 200, "{}"), sent_by="x",
    )
    with pytest.raises(order_service.OrderStateError):
        order_service.update_order(conn, order["id"], items=[])


def test_mark_sent_mock():
    """T6 — mock 전송 결과 반영 → status=sent."""
    conn = _make_db()
    _urgent_material(conn)
    order = order_service.create_order_from_forecast(conn, window_days=30, created_by="x")
    sent = order_service.mark_sent(
        conn, order["id"],
        result=_FakeResult(True, "mock", 200, '{"mock": true}'), sent_by="책임자",
    )
    assert sent["status"] == "sent"
    assert sent["erp_mode"] == "mock"
    assert sent["erp_status_code"] == 200
    assert sent["sent_by"] == "책임자"


def test_cancel_draft():
    """T8 — draft 취소 → cancelled."""
    conn = _make_db()
    _urgent_material(conn)
    order = order_service.create_order_from_forecast(conn, window_days=30, created_by="x")
    cancelled = order_service.cancel_order(conn, order["id"])
    assert cancelled["status"] == "cancelled"


def test_cancel_sent_rejected():
    """T9 — sent 취소 시도 → OrderStateError."""
    conn = _make_db()
    _urgent_material(conn)
    order = order_service.create_order_from_forecast(conn, window_days=30, created_by="x")
    order_service.mark_sent(
        conn, order["id"],
        result=_FakeResult(True, "mock", 200, "{}"), sent_by="x",
    )
    with pytest.raises(order_service.OrderStateError):
        order_service.cancel_order(conn, order["id"])


def test_build_workbook_bytes():
    """T11 — Excel 생성: 비어있지 않은 xlsx(zip) 바이트."""
    conn = _make_db()
    _urgent_material(conn)
    order = order_service.create_order_from_forecast(conn, window_days=30, created_by="x")
    data = order_service.build_workbook(order)
    assert isinstance(data, bytes) and len(data) > 0
    assert data[:2] == b"PK"  # xlsx 는 zip 컨테이너


def test_xlsx_formula_injection_guard():
    """수식 인젝션: 위험 문자로 시작하는 값은 ' 접두."""
    assert order_service._xlsx_safe("=SUM(A1)") == "'=SUM(A1)"
    assert order_service._xlsx_safe("정상") == "정상"


def test_order_payload_filters_zero():
    """T12 — order_payload 는 order_qty>0 항목만 포함."""
    conn = _make_db()
    _urgent_material(conn, name="a")
    _urgent_material(conn, name="b")
    order = order_service.create_order_from_forecast(conn, window_days=30, created_by="x")
    order_service.update_order(
        conn, order["id"],
        items=[{"id": order["items"][0]["id"], "order_qty": 0.0}],
    )
    refreshed = order_service.get_order(conn, order["id"])
    payload = order_service.order_payload(refreshed)
    assert all(i["order_qty"] > 0 for i in payload["items"])
    assert payload["order_no"].startswith("PO-")
    assert "items" in payload and "total_qty" in payload


def test_erp_client_mock_mode(monkeypatch):
    """엔드포인트 미설정 → Mock 성공(외부 호출 없음)."""
    import src.services.erp_client as erp

    monkeypatch.setattr(erp, "ERP_ENDPOINT", "")
    result = erp.send_order({"order_no": "PO-x", "items": []})
    assert result.ok is True
    assert result.mode == "mock"
    assert result.status_code == 200


# ── Route-level permission test ──────────────────────────────────────────────

def test_orders_open_without_login():
    """배합 단일 신뢰 — 비로그인(현장)도 접근 가능(401/403 아님)."""
    import importlib
    import src.config as cfg
    import src.main as mainmod

    importlib.reload(cfg)
    importlib.reload(mainmod)
    client = TestClient(mainmod.app)
    res = client.get("/api/orders")
    assert res.status_code not in (401, 403)
