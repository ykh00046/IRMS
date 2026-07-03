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


def _make_blend_db() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:", check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute(
        """
        CREATE TABLE recipes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_name TEXT NOT NULL,
            position TEXT,
            ink_name TEXT,
            status TEXT NOT NULL,
            is_dhr INTEGER NOT NULL DEFAULT 0,
            revision_of INTEGER,
            created_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE recipe_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipe_id INTEGER NOT NULL,
            value_weight REAL NOT NULL
        )
        """
    )
    connection.execute(
        """
        INSERT INTO recipes (product_name, position, ink_name, status, is_dhr, created_at)
        VALUES ('Injected DB Recipe', 'P1', 'Ink', 'approved', 0, '2026-01-01')
        """
    )
    recipe_id = connection.execute("SELECT id FROM recipes").fetchone()["id"]
    connection.execute("INSERT INTO recipe_items (recipe_id, value_weight) VALUES (?, 100)", (recipe_id,))
    connection.commit()
    return connection


def _make_blend_session_db() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:", check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute(
        """
        CREATE TABLE workers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action TEXT NOT NULL,
            actor_user_id INTEGER,
            actor_username TEXT,
            actor_display_name TEXT,
            actor_access_level TEXT,
            target_type TEXT,
            target_id TEXT,
            target_label TEXT,
            details_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        "INSERT INTO workers (name, is_active, created_at) VALUES ('김도현', 1, '2026-01-01')"
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


def test_blend_route_uses_overridable_db_dependency(monkeypatch):
    import src.main as mainmod

    importlib.reload(mainmod)
    connection = _make_blend_db()

    def override_db():
        yield connection

    mainmod.app.dependency_overrides[get_db] = override_db
    try:
        client = TestClient(mainmod.app)
        response = client.get("/api/blend/recipes")
    finally:
        mainmod.app.dependency_overrides.clear()
        connection.close()

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["items"]) == 1
    assert payload["items"][0]["product_name"] == "Injected DB Recipe"


def test_blend_page_requires_worker_session(monkeypatch):
    import src.main as mainmod

    importlib.reload(mainmod)
    connection = _make_blend_session_db()

    def override_db():
        yield connection

    mainmod.app.dependency_overrides[get_db] = override_db
    try:
        client = TestClient(mainmod.app)
        first_response = client.get("/blend", follow_redirects=False)
        login_response = client.post("/api/blend/session/login", json={"worker": "김도현"})
        second_response = client.get("/blend")
    finally:
        mainmod.app.dependency_overrides.clear()
        connection.close()

    assert first_response.status_code == 303
    assert first_response.headers["location"].startswith("/blend/login")
    assert login_response.status_code == 200
    assert second_response.status_code == 200
    assert 'id="blend-worker" value="김도현"' in second_response.text
