from __future__ import annotations

import importlib
import sqlite3

from fastapi.testclient import TestClient

from src.db import get_db


class TrackingConnection(sqlite3.Connection):
    closed: bool

    def close(self) -> None:
        self.closed = True
        super().close()


def _make_forecast_db() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:", check_same_thread=False)
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
    connection.execute(
        """
        INSERT INTO materials (
            name, unit_type, unit, category, stock_quantity, lead_time_days, reorder_cycle_days
        )
        VALUES ('Injected DB Material', 'weight', 'g', 'test', 100, 7, 30)
        """
    )
    connection.commit()
    return connection


def test_get_db_closes_connection(monkeypatch):
    connection = sqlite3.connect(":memory:", factory=TrackingConnection)
    connection.closed = False

    import src.db.connection as connection_module

    monkeypatch.setattr(connection_module, "get_connection", lambda: connection)
    dependency = get_db()
    assert next(dependency) is connection

    try:
        next(dependency)
    except StopIteration:
        pass

    assert connection.closed is True


def test_forecast_route_uses_overridable_db_dependency(monkeypatch):
    import src.auth as auth
    import src.main as mainmod

    importlib.reload(mainmod)
    connection = _make_forecast_db()

    monkeypatch.setattr(
        auth,
        "get_current_user",
        lambda request, required=True: {"id": 1, "username": "test", "access_level": "admin"},
    )

    def override_db():
        yield connection

    mainmod.app.dependency_overrides[get_db] = override_db
    try:
        client = TestClient(mainmod.app)
        response = client.get("/api/forecast/materials?window_days=30")
    finally:
        mainmod.app.dependency_overrides.clear()
        connection.close()

    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["total_materials"] == 1
    assert payload["items"][0]["name"] == "Injected DB Material"
