"""stock_service 단위 테스트 - 재고 차감/입고/조정/폐기 (계량 핵심 도메인).

순수 함수 `stock_status`는 직접, 나머지 DB 함수는 in-memory SQLite로 검증한다.
"""
import sqlite3

import pytest

from src.services import stock_service


def _make_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE materials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            category TEXT,
            is_active INTEGER NOT NULL DEFAULT 1,
            stock_quantity REAL NOT NULL DEFAULT 0,
            stock_threshold REAL NOT NULL DEFAULT 0
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
        """
    )
    return conn


def _add_material(conn, name="잉크A", *, stock=0.0, threshold=0.0, category="잉크"):
    cur = conn.execute(
        "INSERT INTO materials (name, category, stock_quantity, stock_threshold) "
        "VALUES (?, ?, ?, ?)",
        (name, category, stock, threshold),
    )
    return int(cur.lastrowid)


ACTOR = {"id": 7, "username": "tester", "display_name": "테스터"}


class TestStockStatus:
    def test_negative(self):
        assert stock_service.stock_status(-1, 10) == "negative"

    def test_low_at_threshold(self):
        assert stock_service.stock_status(5, 5) == "low"

    def test_low_below_threshold(self):
        assert stock_service.stock_status(3, 5) == "low"

    def test_ok_above_threshold(self):
        assert stock_service.stock_status(10, 5) == "ok"

    def test_ok_when_threshold_zero(self):
        # 임계치 0이면 low 판정 안 함
        assert stock_service.stock_status(0, 0) == "ok"


class TestRestock:
    def test_increases_stock_and_logs(self):
        conn = _make_db()
        mid = _add_material(conn, stock=100)
        result = stock_service.restock(conn, material_id=mid, amount=40, actor=ACTOR)
        assert result["balance_before"] == 100.0
        assert result["balance_after"] == 140.0
        assert result["delta"] == 40
        qty = conn.execute("SELECT stock_quantity FROM materials WHERE id=?", (mid,)).fetchone()[0]
        assert qty == 140.0
        log = conn.execute("SELECT reason, actor_name FROM material_stock_logs WHERE material_id=?", (mid,)).fetchone()
        assert log["reason"] == "restock"
        assert log["actor_name"] == "테스터"

    def test_rejects_non_positive(self):
        conn = _make_db()
        mid = _add_material(conn, stock=10)
        with pytest.raises(ValueError):
            stock_service.restock(conn, material_id=mid, amount=0, actor=ACTOR)


class TestDiscard:
    def test_decreases_stock(self):
        conn = _make_db()
        mid = _add_material(conn, stock=50)
        result = stock_service.discard(conn, material_id=mid, amount=20, actor=ACTOR, note="오염")
        assert result["balance_after"] == 30.0
        assert result["delta"] == -20

    def test_requires_note(self):
        conn = _make_db()
        mid = _add_material(conn, stock=50)
        with pytest.raises(ValueError):
            stock_service.discard(conn, material_id=mid, amount=5, actor=ACTOR, note="")

    def test_rejects_non_positive(self):
        conn = _make_db()
        mid = _add_material(conn, stock=50)
        with pytest.raises(ValueError):
            stock_service.discard(conn, material_id=mid, amount=-1, actor=ACTOR, note="x")


class TestAdjust:
    def test_sets_absolute_quantity(self):
        conn = _make_db()
        mid = _add_material(conn, stock=30)
        result = stock_service.adjust(conn, material_id=mid, new_quantity=100, actor=ACTOR, note="실사")
        assert result["balance_after"] == 100.0
        assert result["delta"] == 70.0

    def test_requires_note(self):
        conn = _make_db()
        mid = _add_material(conn, stock=30)
        with pytest.raises(ValueError):
            stock_service.adjust(conn, material_id=mid, new_quantity=10, actor=ACTOR, note="")


class TestDeductForMeasurement:
    def test_deducts_and_logs(self):
        conn = _make_db()
        mid = _add_material(conn, stock=100)
        result = stock_service.deduct_for_measurement(
            conn, material_id=mid, weight=25, recipe_id=1, recipe_item_id=11, actor=ACTOR
        )
        assert result["balance_after"] == 75.0
        assert result["negative"] is False

    def test_zero_weight_skipped(self):
        conn = _make_db()
        mid = _add_material(conn, stock=100)
        assert stock_service.deduct_for_measurement(
            conn, material_id=mid, weight=0, recipe_id=1, recipe_item_id=11, actor=ACTOR
        ) is None

    def test_idempotent_per_recipe_item(self):
        conn = _make_db()
        mid = _add_material(conn, stock=100)
        first = stock_service.deduct_for_measurement(
            conn, material_id=mid, weight=10, recipe_id=1, recipe_item_id=11, actor=ACTOR
        )
        second = stock_service.deduct_for_measurement(
            conn, material_id=mid, weight=10, recipe_id=1, recipe_item_id=11, actor=ACTOR
        )
        assert first is not None
        assert second is None  # 중복 차감 방지
        qty = conn.execute("SELECT stock_quantity FROM materials WHERE id=?", (mid,)).fetchone()[0]
        assert qty == 90.0

    def test_negative_balance_flagged(self):
        conn = _make_db()
        mid = _add_material(conn, stock=5)
        result = stock_service.deduct_for_measurement(
            conn, material_id=mid, weight=20, recipe_id=1, recipe_item_id=11, actor=ACTOR
        )
        assert result["balance_after"] == -15.0
        assert result["negative"] is True


class TestReverseMeasurement:
    def test_credits_back_and_removes_log(self):
        conn = _make_db()
        mid = _add_material(conn, stock=100)
        stock_service.deduct_for_measurement(
            conn, material_id=mid, weight=30, recipe_id=1, recipe_item_id=11, actor=ACTOR
        )
        stock_service.reverse_measurement(conn, recipe_item_id=11)
        qty = conn.execute("SELECT stock_quantity FROM materials WHERE id=?", (mid,)).fetchone()[0]
        assert qty == 100.0
        cnt = conn.execute(
            "SELECT COUNT(*) FROM material_stock_logs WHERE recipe_item_id=11 AND reason='measurement'"
        ).fetchone()[0]
        assert cnt == 0

    def test_noop_when_absent(self):
        conn = _make_db()
        # 예외 없이 조용히 통과
        stock_service.reverse_measurement(conn, recipe_item_id=999)


class TestSetThreshold:
    def test_sets_value(self):
        conn = _make_db()
        mid = _add_material(conn)
        stock_service.set_threshold(conn, mid, 12.5)
        thr = conn.execute("SELECT stock_threshold FROM materials WHERE id=?", (mid,)).fetchone()[0]
        assert thr == 12.5

    def test_rejects_negative(self):
        conn = _make_db()
        mid = _add_material(conn)
        with pytest.raises(ValueError):
            stock_service.set_threshold(conn, mid, -1)


class TestListStockAndLogs:
    def test_list_stock_with_status(self):
        conn = _make_db()
        _add_material(conn, name="여유", stock=100, threshold=10)
        _add_material(conn, name="부족", stock=5, threshold=10)
        rows = {r["name"]: r for r in stock_service.list_stock(conn)}
        assert rows["여유"]["status"] == "ok"
        assert rows["부족"]["status"] == "low"

    def test_list_logs_returns_recent(self):
        conn = _make_db()
        mid = _add_material(conn, stock=100)
        stock_service.restock(conn, material_id=mid, amount=10, actor=ACTOR)
        stock_service.discard(conn, material_id=mid, amount=5, actor=ACTOR, note="x")
        logs = stock_service.list_logs(conn, mid)
        assert len(logs) == 2
        assert {log["reason"] for log in logs} == {"restock", "discard"}
