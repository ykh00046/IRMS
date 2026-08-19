from __future__ import annotations

import sqlite3

from fastapi.testclient import TestClient

from src.db import get_db
from src.main import create_app


def _make_viscosity_db() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:", check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute(
        """
        CREATE TABLE viscosity_products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            target REAL,
            lower_limit REAL,
            upper_limit REAL,
            sigma_k REAL NOT NULL DEFAULT 3,
            rpm REAL,
            temperature REAL,
            remind_daily INTEGER NOT NULL DEFAULT 0,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE viscosity_readings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL,
            lot_no TEXT NOT NULL,
            viscosity REAL NOT NULL,
            measured_date TEXT,
            memo TEXT,
            recipe_material TEXT,
            material_lot TEXT,
            created_by TEXT,
            created_at TEXT NOT NULL,
            blend_record_id INTEGER
        )
        """
    )
    # LOT 단위 알림(2026-08-19) 판정에 필요한 최소 배합 스키마 — pending LOT 조건은
    # status/is_bulk_regenerated/등록 연계(blend_record_id)/측정 불가(viscosity_skips).
    connection.execute(
        """
        CREATE TABLE blend_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_name TEXT NOT NULL,
            product_lot TEXT NOT NULL,
            work_date TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'completed',
            is_bulk_regenerated INTEGER NOT NULL DEFAULT 0,
            reactor INTEGER
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE viscosity_skips (
            blend_record_id INTEGER NOT NULL UNIQUE,
            reason TEXT NOT NULL
        )
        """
    )
    # PB: 알림 대상 + 2026-06-30 배합 LOT 미등록 → 알림 대상(pending LOT 1건).
    connection.execute(
        "INSERT INTO viscosity_products (code, name, remind_daily, is_active, created_at) "
        "VALUES ('PB', 'PB', 1, 1, '2026-01-01')"
    )
    # SBCT: 알림 대상 + 2026-06-30 배합 LOT 을 이미 등록(reading 이 blend_record_id
    # 로 연결) → 2026-07-01 측정이 없어도 알림 없음.
    connection.execute(
        "INSERT INTO viscosity_products (code, name, remind_daily, is_active, created_at) "
        "VALUES ('SBCT', 'SBCT', 1, 1, '2026-01-01')"
    )
    # SCRA: 알림 대상 아님(remind_daily=0) — 미등록 LOT 이 있어도 제외.
    connection.execute(
        "INSERT INTO viscosity_products (code, name, remind_daily, is_active, created_at) "
        "VALUES ('SCRA', 'SCRA', 0, 1, '2026-01-01')"
    )
    # 배합 LOT: PB/SCRA 는 미등록, SBCT 는 아래 reading 이 blend_record_id 로 등록 연결.
    connection.execute(
        "INSERT INTO blend_records "
        "(product_name, product_lot, work_date, status, is_bulk_regenerated, reactor) "
        "VALUES ('PB', '26063001', '2026-06-30', 'completed', 0, 2)"
    )
    connection.execute(
        "INSERT INTO blend_records "
        "(product_name, product_lot, work_date, status, is_bulk_regenerated, reactor) "
        "VALUES ('SBCT', '26063001', '2026-06-30', 'completed', 0, NULL)"
    )
    connection.execute(
        "INSERT INTO blend_records "
        "(product_name, product_lot, work_date, status, is_bulk_regenerated, reactor) "
        "VALUES ('SCRA', '26063001', '2026-06-30', 'completed', 0, NULL)"
    )
    # SBCT 등록 — 측정일은 2026-07-01(대상일)이 아니어야 한다: '오늘 측정 없음'
    # 규칙이 아니라 LOT 등록 상태가 알림을 끈다는 것을 증명하기 때문.
    connection.execute(
        """
        INSERT INTO viscosity_readings
            (product_id, lot_no, viscosity, measured_date, created_at, blend_record_id)
        VALUES (2, '26063001', 204.0, '2026-06-30', '2026-06-30 09:00:00', 2)
        """
    )
    connection.commit()
    return connection


def test_public_viscosity_reminder_lists_products_with_pending_lots() -> None:
    app = create_app()
    connection = _make_viscosity_db()

    def override_db():
        yield connection

    app.dependency_overrides[get_db] = override_db
    try:
        client = TestClient(app, client=("192.168.11.108", 50000))
        # 트레이는 품목을 지정하지 않는다 — 알림 대상은 서버(remind_daily)가 정한다.
        response = client.get(
            "/api/public/viscosity-reminders/due?target_date=2026-07-01"
        )
    finally:
        app.dependency_overrides.clear()
        connection.close()

    assert response.status_code == 200
    payload = response.json()
    assert payload["date"] == "2026-07-01"
    # PB만: SBCT는 2026-06-30 LOT 을 이미 등록(대상일 측정이 없어도 조용),
    # SCRA는 알림 대상 아님(미등록 LOT 이 있어도 remind_daily=0).
    assert payload["total"] == 1
    item = payload["items"][0]
    assert item["code"] == "PB"
    assert item["pending_count"] == 1
    assert item["pending_lots"] == [
        {
            "blend_record_id": 1,
            "product_lot": "26063001",
            "work_date": "2026-06-30",
            "reactor": 2,
        }
    ]
    # 기존 키 유지 — PB 는 측정 이력이 없으므로 최종 측정은 없음.
    assert item["latest_value"] is None
    assert item["latest_date"] is None


def test_public_viscosity_reminder_is_internal_network_only() -> None:
    client = TestClient(create_app())

    response = client.get("/api/public/viscosity-reminders/due")

    assert response.status_code == 403
    assert response.json() == {"detail": "INTERNAL_NETWORK_ONLY"}
