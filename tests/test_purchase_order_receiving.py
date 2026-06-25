"""Unit + integration + route tests for purchase-order-receiving.

발주(sent) → 입고 → LOT(material_lots) + 재고(materials.stock_quantity) 동시 반영을
실제 스키마(stock_service/lot_service 가 기대하는 전체 컬럼)에 대해 검증한다.

Design: docs/02-design/features/purchase-order-receiving.design.md §7
"""

from __future__ import annotations

import sqlite3

import pytest

from src.services import lot_service, receiving_service


# ── 실제 런타임 스키마와 일치하는 in-memory DB ────────────────────────────────

def _make_db() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        CREATE TABLE materials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            category TEXT,
            stock_quantity REAL NOT NULL DEFAULT 0,
            stock_threshold REAL NOT NULL DEFAULT 0,
            is_active INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE material_stock_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            material_id INTEGER NOT NULL,
            delta REAL NOT NULL,
            balance_after REAL NOT NULL,
            reason TEXT NOT NULL,
            actor_id INTEGER,
            actor_name TEXT,
            recipe_id INTEGER,
            recipe_item_id INTEGER,
            note TEXT,
            created_at TEXT NOT NULL
        );
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
        );
        CREATE TABLE purchase_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_no TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL DEFAULT 'draft',
            receipt_status TEXT NOT NULL DEFAULT 'pending',
            window_days INTEGER NOT NULL,
            note TEXT,
            item_count INTEGER NOT NULL DEFAULT 0,
            total_qty REAL NOT NULL DEFAULT 0,
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT
        );
        CREATE TABLE purchase_order_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            material_id INTEGER NOT NULL,
            material_name TEXT NOT NULL,
            category TEXT,
            unit TEXT NOT NULL DEFAULT 'g',
            recommended_qty REAL NOT NULL DEFAULT 0,
            order_qty REAL NOT NULL DEFAULT 0,
            received_qty REAL NOT NULL DEFAULT 0,
            note TEXT
        );
        CREATE TABLE po_receipts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            receipt_no TEXT NOT NULL UNIQUE,
            order_id INTEGER NOT NULL,
            note TEXT,
            item_count INTEGER NOT NULL DEFAULT 0,
            total_qty REAL NOT NULL DEFAULT 0,
            received_by TEXT NOT NULL,
            received_at TEXT NOT NULL
        );
        CREATE TABLE po_receipt_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            receipt_id INTEGER NOT NULL,
            order_item_id INTEGER NOT NULL,
            material_id INTEGER NOT NULL,
            material_name TEXT NOT NULL,
            received_qty REAL NOT NULL,
            lot_no TEXT,
            expiry_date TEXT,
            lot_id INTEGER,
            stock_log_id INTEGER,
            note TEXT
        );
        """
    )
    return connection


ACTOR = {"id": 7, "display_name": "책임자", "username": "mgr"}


def _add_material(connection, name="잉크A", stock=0.0) -> int:
    cur = connection.execute(
        "INSERT INTO materials (name, category, stock_quantity) VALUES (?, '잉크', ?)",
        (name, stock),
    )
    return int(cur.lastrowid)


def _make_sent_order(connection, items, *, status="sent") -> int:
    """items: [(material_id, order_qty), ...]. 발주서 1건 + 항목 생성, status 지정."""
    cur = connection.execute(
        """
        INSERT INTO purchase_orders
            (order_no, status, window_days, item_count, total_qty, created_by, created_at)
        VALUES ('PO-20260606-001', ?, 30, ?, ?, '책임자', '2026-06-06T00:00:00Z')
        """,
        (status, len(items), sum(q for _, q in items)),
    )
    order_id = int(cur.lastrowid)
    for mid, qty in items:
        connection.execute(
            """
            INSERT INTO purchase_order_items
                (order_id, material_id, material_name, recommended_qty, order_qty)
            VALUES (?, ?, ?, ?, ?)
            """,
            (order_id, mid, f"자재{mid}", qty, qty),
        )
    return order_id


def _item_ids(connection, order_id):
    return [
        int(r["id"])
        for r in connection.execute(
            "SELECT id FROM purchase_order_items WHERE order_id = ? ORDER BY id", (order_id,)
        ).fetchall()
    ]


# ── R1: 채번 ───────────────────────────────────────────────────


def test_generate_receipt_no_increments_per_day():
    connection = _make_db()
    order_id = _make_sent_order(connection, [(_add_material(connection), 100)])
    iid = _item_ids(connection, order_id)[0]
    no1 = receiving_service.generate_receipt_no(connection)
    assert no1.startswith("RC-") and no1.endswith("-001")
    receiving_service.receive_order(
        connection, order_id=order_id,
        lines=[{"order_item_id": iid, "received_qty": 10}],
        received_by="책임자", actor=ACTOR,
    )
    no2 = receiving_service.generate_receipt_no(connection)
    assert no2.endswith("-002")


# ── R2: 전량 입고 → received + LOT + 재고 동시 반영 ─────────────


def test_full_receipt_sets_received_and_reflects_lot_and_stock():
    connection = _make_db()
    mid = _add_material(connection, stock=0.0)
    order_id = _make_sent_order(connection, [(mid, 100)])
    iid = _item_ids(connection, order_id)[0]

    result = receiving_service.receive_order(
        connection, order_id=order_id,
        lines=[{"order_item_id": iid, "received_qty": 100, "lot_no": "L1",
                "expiry_date": "2027-01-01"}],
        received_by="책임자", actor=ACTOR,
    )
    assert result["receipt_status"] == "received"
    assert result["item_count"] == 1
    assert result["total_qty"] == pytest.approx(100)

    # 재고 += 100
    stock = connection.execute(
        "SELECT stock_quantity FROM materials WHERE id = ?", (mid,)
    ).fetchone()["stock_quantity"]
    assert stock == pytest.approx(100)
    # LOT 1건 생성(잔여 100)
    lots = lot_service.list_lots(connection, material_id=mid)
    assert len(lots) == 1 and lots[0]["remaining_quantity"] == pytest.approx(100)
    # 재고 로그 restock 1건
    log = connection.execute(
        "SELECT reason, delta FROM material_stock_logs WHERE material_id = ?", (mid,)
    ).fetchone()
    assert log["reason"] == "restock" and log["delta"] == pytest.approx(100)
    # 발주 항목 received_qty 누적
    rq = connection.execute(
        "SELECT received_qty FROM purchase_order_items WHERE id = ?", (iid,)
    ).fetchone()["received_qty"]
    assert rq == pytest.approx(100)


# ── R3: 부분 입고 → partial ────────────────────────────────────


def test_partial_receipt_sets_partial():
    connection = _make_db()
    mid = _add_material(connection)
    order_id = _make_sent_order(connection, [(mid, 100)])
    iid = _item_ids(connection, order_id)[0]
    result = receiving_service.receive_order(
        connection, order_id=order_id,
        lines=[{"order_item_id": iid, "received_qty": 40}],
        received_by="책임자", actor=ACTOR,
    )
    assert result["receipt_status"] == "partial"
    status = connection.execute(
        "SELECT receipt_status FROM purchase_orders WHERE id = ?", (order_id,)
    ).fetchone()["receipt_status"]
    assert status == "partial"


# ── R4: 분할 입고 2회 → 누적 충족 시 received ──────────────────


def test_split_receipts_accumulate_to_received():
    connection = _make_db()
    mid = _add_material(connection)
    order_id = _make_sent_order(connection, [(mid, 100)])
    iid = _item_ids(connection, order_id)[0]
    r1 = receiving_service.receive_order(
        connection, order_id=order_id,
        lines=[{"order_item_id": iid, "received_qty": 60}],
        received_by="책임자", actor=ACTOR,
    )
    assert r1["receipt_status"] == "partial"
    r2 = receiving_service.receive_order(
        connection, order_id=order_id,
        lines=[{"order_item_id": iid, "received_qty": 40}],
        received_by="책임자", actor=ACTOR,
    )
    assert r2["receipt_status"] == "received"
    # 누적 received_qty == 100, 재고 == 100, LOT 2건
    rq = connection.execute(
        "SELECT received_qty FROM purchase_order_items WHERE id = ?", (iid,)
    ).fetchone()["received_qty"]
    assert rq == pytest.approx(100)
    assert connection.execute(
        "SELECT stock_quantity FROM materials WHERE id = ?", (mid,)
    ).fetchone()["stock_quantity"] == pytest.approx(100)
    assert len(lot_service.list_lots(connection, material_id=mid)) == 2


# ── R5: received_qty=0 라인 skip ───────────────────────────────


def test_zero_qty_lines_are_skipped():
    connection = _make_db()
    m1, m2 = _add_material(connection, "A"), _add_material(connection, "B")
    order_id = _make_sent_order(connection, [(m1, 50), (m2, 50)])
    i1, i2 = _item_ids(connection, order_id)
    result = receiving_service.receive_order(
        connection, order_id=order_id,
        lines=[{"order_item_id": i1, "received_qty": 50},
               {"order_item_id": i2, "received_qty": 0}],
        received_by="책임자", actor=ACTOR,
    )
    assert result["item_count"] == 1  # m2 는 skip
    assert result["receipt_status"] == "partial"  # m2 미입고
    assert len(lot_service.list_lots(connection, material_id=m2)) == 0
    assert connection.execute(
        "SELECT stock_quantity FROM materials WHERE id = ?", (m2,)
    ).fetchone()["stock_quantity"] == pytest.approx(0)


# ── R6: draft/cancelled 발주 입고 거부 ─────────────────────────


@pytest.mark.parametrize("status", ["draft", "cancelled", "failed"])
def test_receive_non_sent_order_raises(status):
    connection = _make_db()
    mid = _add_material(connection)
    order_id = _make_sent_order(connection, [(mid, 100)], status=status)
    iid = _item_ids(connection, order_id)[0]
    with pytest.raises(receiving_service.ReceivingStateError):
        receiving_service.receive_order(
            connection, order_id=order_id,
            lines=[{"order_item_id": iid, "received_qty": 10}],
            received_by="책임자", actor=ACTOR,
        )


# ── R7: 다른 발주 항목 혼입 무시 ───────────────────────────────


def test_foreign_order_item_is_ignored():
    connection = _make_db()
    mid = _add_material(connection)
    order_a = _make_sent_order(connection, [(mid, 100)])
    # 두 번째 발주(다른 order_no)
    connection.execute(
        "INSERT INTO purchase_orders (order_no, status, window_days, created_by, created_at) "
        "VALUES ('PO-20260606-002', 'sent', 30, '책임자', '2026-06-06T00:00:00Z')"
    )
    other_order = int(connection.execute("SELECT id FROM purchase_orders WHERE order_no='PO-20260606-002'").fetchone()["id"])
    connection.execute(
        "INSERT INTO purchase_order_items (order_id, material_id, material_name, recommended_qty, order_qty) "
        "VALUES (?, ?, '타발주', 10, 10)",
        (other_order, mid),
    )
    foreign_iid = int(connection.execute(
        "SELECT id FROM purchase_order_items WHERE order_id = ?", (other_order,)
    ).fetchone()["id"])
    iid = _item_ids(connection, order_a)[0]

    result = receiving_service.receive_order(
        connection, order_id=order_a,
        lines=[{"order_item_id": iid, "received_qty": 100},
               {"order_item_id": foreign_iid, "received_qty": 999}],
        received_by="책임자", actor=ACTOR,
    )
    assert result["item_count"] == 1  # foreign 무시
    # 타 발주 항목 received_qty 변동 없음
    assert connection.execute(
        "SELECT received_qty FROM purchase_order_items WHERE id = ?", (foreign_iid,)
    ).fetchone()["received_qty"] == pytest.approx(0)


# ── R8: 입고 수량 전부 0/없음 → ValueError ─────────────────────


def test_no_receivable_qty_raises():
    connection = _make_db()
    mid = _add_material(connection)
    order_id = _make_sent_order(connection, [(mid, 100)])
    iid = _item_ids(connection, order_id)[0]
    with pytest.raises(ValueError):
        receiving_service.receive_order(
            connection, order_id=order_id,
            lines=[{"order_item_id": iid, "received_qty": 0}],
            received_by="책임자", actor=ACTOR,
        )
    with pytest.raises(ValueError):
        receiving_service.receive_order(
            connection, order_id=order_id, lines=[],
            received_by="책임자", actor=ACTOR,
        )


# ── R9: 유통기한/LOT 미입력 입고 정상(no_expiry) ───────────────


def test_receipt_without_lot_or_expiry():
    connection = _make_db()
    mid = _add_material(connection)
    order_id = _make_sent_order(connection, [(mid, 30)])
    iid = _item_ids(connection, order_id)[0]
    result = receiving_service.receive_order(
        connection, order_id=order_id,
        lines=[{"order_item_id": iid, "received_qty": 30}],
        received_by="책임자", actor=ACTOR,
    )
    assert result["receipt_status"] == "received"
    lots = lot_service.list_lots(connection, material_id=mid)
    assert lots[0]["expiry_state"] == "no_expiry"
    assert lots[0]["lot_no"] is None


# ── R10: 없는 발주 → None ──────────────────────────────────────


def test_receive_unknown_order_returns_none():
    connection = _make_db()
    assert receiving_service.receive_order(
        connection, order_id=9999, lines=[{"order_item_id": 1, "received_qty": 1}],
        received_by="책임자", actor=ACTOR,
    ) is None


# ── R11: 입고 이력 조회 — LOT/재고로그 연결 ────────────────────


def test_list_receipts_links_lot_and_stock_log():
    connection = _make_db()
    mid = _add_material(connection)
    order_id = _make_sent_order(connection, [(mid, 100)])
    iid = _item_ids(connection, order_id)[0]
    receiving_service.receive_order(
        connection, order_id=order_id,
        lines=[{"order_item_id": iid, "received_qty": 100, "lot_no": "L9"}],
        received_by="책임자", actor=ACTOR,
    )
    receipts = receiving_service.list_receipts(connection, order_id)
    assert len(receipts) == 1
    line = receipts[0]["items"][0]
    assert line["lot_no"] == "L9"
    # lot_id / stock_log_id 가 실제 행을 가리킨다
    assert connection.execute(
        "SELECT 1 FROM material_lots WHERE id = ?", (line["lot_id"],)
    ).fetchone() is not None
    assert connection.execute(
        "SELECT 1 FROM material_stock_logs WHERE id = ?", (line["stock_log_id"],)
    ).fetchone() is not None


# ── R12: 초과 입고 허용(잔여 초과) ─────────────────────────────


def test_over_receipt_allowed_and_marks_received():
    connection = _make_db()
    mid = _add_material(connection)
    order_id = _make_sent_order(connection, [(mid, 100)])
    iid = _item_ids(connection, order_id)[0]
    result = receiving_service.receive_order(
        connection, order_id=order_id,
        lines=[{"order_item_id": iid, "received_qty": 130}],
        received_by="책임자", actor=ACTOR,
    )
    assert result["receipt_status"] == "received"
    assert connection.execute(
        "SELECT stock_quantity FROM materials WHERE id = ?", (mid,)
    ).fetchone()["stock_quantity"] == pytest.approx(130)


# ── R13: 라우트 권한 — 비인증 차단 ─────────────────────────────


def test_routes_open_without_login():
    """배합 단일 신뢰 — 비로그인(현장)도 접근 가능(401/403 아님)."""
    import importlib
    import src.config as cfg
    import src.main as mainmod
    from fastapi.testclient import TestClient

    importlib.reload(cfg)
    importlib.reload(mainmod)
    client = TestClient(mainmod.app)
    # 읽기는 비로그인(현장) 허용. POST 쓰기는 인증이 아니라 CSRF 로 보호(별개)라 검사 제외.
    assert client.get("/api/orders/1/receipts").status_code not in (401, 403)


# ── R14: 마이그레이션 — 입고 테이블/컬럼 생성 ──────────────────


def test_migration_creates_receiving_tables_and_columns():
    import importlib
    import src.config as cfg
    import src.main as mainmod
    from src.db import get_connection

    importlib.reload(cfg)
    importlib.reload(mainmod)
    with get_connection() as conn:
        tables = {
            r["name"]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name IN ('po_receipts', 'po_receipt_items')"
            ).fetchall()
        }
        po_cols = {r["name"] for r in conn.execute("PRAGMA table_info(purchase_orders)").fetchall()}
        item_cols = {r["name"] for r in conn.execute("PRAGMA table_info(purchase_order_items)").fetchall()}
    assert tables == {"po_receipts", "po_receipt_items"}
    assert "receipt_status" in po_cols
    assert "received_qty" in item_cols
