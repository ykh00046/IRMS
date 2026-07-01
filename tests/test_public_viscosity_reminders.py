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
            created_at TEXT NOT NULL
        )
        """
    )
    # PB: 알림 대상, 오늘 측정 없음 → 알림 대상
    connection.execute(
        "INSERT INTO viscosity_products (code, name, remind_daily, is_active, created_at) "
        "VALUES ('PB', 'PB', 1, 1, '2026-01-01')"
    )
    # SBCT: 알림 대상이지만 오늘 측정 있음 → 제외
    connection.execute(
        "INSERT INTO viscosity_products (code, name, remind_daily, is_active, created_at) "
        "VALUES ('SBCT', 'SBCT', 1, 1, '2026-01-01')"
    )
    # SCRA: 알림 대상 아님(remind_daily=0), 오늘 측정 없어도 → 제외
    connection.execute(
        "INSERT INTO viscosity_products (code, name, remind_daily, is_active, created_at) "
        "VALUES ('SCRA', 'SCRA', 0, 1, '2026-01-01')"
    )
    connection.execute(
        """
        INSERT INTO viscosity_readings
            (product_id, lot_no, viscosity, measured_date, created_at)
        VALUES (2, '26070101', 204.0, '2026-07-01', '2026-07-01 09:00:00')
        """
    )
    connection.commit()
    return connection


def test_public_viscosity_reminder_returns_only_flagged_missing_items() -> None:
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
    # PB만: SBCT는 오늘 측정 완료, SCRA는 알림 대상 아님
    assert payload["total"] == 1
    assert payload["items"][0]["code"] == "PB"


def test_public_viscosity_reminder_is_internal_network_only() -> None:
    client = TestClient(create_app())

    response = client.get("/api/public/viscosity-reminders/due")

    assert response.status_code == 403
    assert response.json() == {"detail": "INTERNAL_NETWORK_ONLY"}
