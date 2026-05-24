"""Verify /health endpoint contract for cloudflared edge health checks."""

from __future__ import annotations

import importlib
import re

from fastapi.testclient import TestClient


_ISO_8601_UTC = re.compile(
    r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:?\d{2})?$"
)


def _reload_app(monkeypatch):
    monkeypatch.setenv("IRMS_ENV", "development")
    monkeypatch.setenv("IRMS_REQUIRE_SESSION_SECRET", "false")
    monkeypatch.setenv("IRMS_SESSION_SECRET", "0" * 64)
    monkeypatch.setenv("IRMS_SEED_DEMO_DATA", "false")

    import src.config as cfg
    import src.main as mainmod

    importlib.reload(cfg)
    importlib.reload(mainmod)
    return mainmod.app


def test_health_returns_ok_status(monkeypatch):
    """T-H1 — 200 with status=ok and ISO timestamp."""
    app = _reload_app(monkeypatch)
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert _ISO_8601_UTC.match(body["time"]), body["time"]


def test_health_is_unauthenticated(monkeypatch):
    """T-H2 — /health works without any cookie/header (cloudflared call)."""
    app = _reload_app(monkeypatch)
    client = TestClient(app)
    response = client.get("/health", cookies={})
    assert response.status_code == 200


def test_health_carries_security_headers(monkeypatch):
    """T-H3 — sanity check that SecurityHeaders middleware wraps /health."""
    app = _reload_app(monkeypatch)
    client = TestClient(app)
    response = client.get("/health")
    assert response.headers.get("X-Frame-Options") == "DENY"
    assert response.headers.get("X-Content-Type-Options") == "nosniff"
