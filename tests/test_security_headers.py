"""Verify SecurityHeadersMiddleware behaviour across environments.

Covers FR-03 (5+ standard headers), FR-04 (HSTS production-only),
and FR-06 (InternalNetworkOnlyMiddleware still rejects external IPs
through TestClient — its default ``testclient`` host is non-private,
so the protected prefix returns 403).
"""

from __future__ import annotations

import importlib

from fastapi.testclient import TestClient


_BASE_HEADERS = (
    ("X-Frame-Options", "DENY"),
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "same-origin"),
    (
        "Permissions-Policy",
        "geolocation=(), camera=(), microphone=(), payment=()",
    ),
    ("Cross-Origin-Opener-Policy", "same-origin"),
)


def _reload_app(monkeypatch, env_value: str | None):
    """Reload src.config and src.main with the given IRMS_ENV value.

    Sets IRMS_REQUIRE_SESSION_SECRET=false and a deterministic secret
    so production-mode init does not fail.
    """
    if env_value is None:
        monkeypatch.delenv("IRMS_ENV", raising=False)
    else:
        monkeypatch.setenv("IRMS_ENV", env_value)
    monkeypatch.setenv("IRMS_REQUIRE_SESSION_SECRET", "false")
    monkeypatch.setenv("IRMS_SESSION_SECRET", "0" * 64)
    monkeypatch.setenv("IRMS_SEED_DEMO_DATA", "false")

    import src.config as cfg
    import src.main as mainmod

    importlib.reload(cfg)
    importlib.reload(mainmod)
    return mainmod.app


def test_base_security_headers_present_in_development(monkeypatch):
    """T-S3 — base 5 headers present on every response."""
    app = _reload_app(monkeypatch, "development")
    client = TestClient(app)
    response = client.get("/health")
    for name, value in _BASE_HEADERS:
        assert response.headers.get(name) == value, name


def test_hsts_absent_in_development(monkeypatch):
    """T-S1 — HSTS should NOT be sent in development."""
    app = _reload_app(monkeypatch, "development")
    client = TestClient(app)
    response = client.get("/health")
    assert "Strict-Transport-Security" not in response.headers


def test_hsts_present_in_production(monkeypatch):
    """T-S2 — HSTS sent with 1-year max-age in production."""
    app = _reload_app(monkeypatch, "production")
    client = TestClient(app)
    response = client.get("/health")
    assert response.headers.get("Strict-Transport-Security") == "max-age=31536000"


def test_security_headers_on_404(monkeypatch):
    """T-S4 — headers attached even on 404 (outermost middleware)."""
    app = _reload_app(monkeypatch, "development")
    client = TestClient(app)
    response = client.get("/this-path-does-not-exist")
    assert response.status_code == 404
    for name, value in _BASE_HEADERS:
        assert response.headers.get(name) == value, name


def test_internal_network_only_blocks_external_through_testclient(monkeypatch):
    """T-I1 — FR-06 regression guard.

    TestClient reports ``request.client.host == 'testclient'`` which is
    not a valid IP. ``InternalNetworkOnlyMiddleware._is_private`` returns
    False for invalid IPs, so the protected prefix should return 403.
    This guarantees the LAN-only invariant is preserved even when
    cloudflared exposes the app externally.
    """
    app = _reload_app(monkeypatch, "development")
    client = TestClient(app)
    response = client.get("/api/public/attendance-alerts/anything")
    assert response.status_code == 403
    body = response.json()
    assert body == {"detail": "INTERNAL_NETWORK_ONLY"}


def test_attendance_alerts_require_tray_token_even_from_loopback(monkeypatch):
    """Tunnel origin traffic can appear as 127.0.0.1, so token must win in production."""
    monkeypatch.setenv("IRMS_ENV", "production")
    monkeypatch.setenv("IRMS_REQUIRE_SESSION_SECRET", "false")
    monkeypatch.setenv("IRMS_SESSION_SECRET", "0" * 64)
    monkeypatch.setenv("IRMS_SEED_DEMO_DATA", "false")
    monkeypatch.setenv("IRMS_REQUIRE_TRAY_API_TOKEN", "true")
    monkeypatch.setenv("IRMS_TRAY_API_TOKEN", "test-tray-token")

    import src.config as cfg
    import src.main as mainmod

    importlib.reload(cfg)
    importlib.reload(mainmod)

    client = TestClient(mainmod.app, client=("127.0.0.1", 50000))
    response = client.get("/api/public/attendance-alerts/anything")
    assert response.status_code == 403
    assert response.json() == {"detail": "TRAY_TOKEN_REQUIRED"}

    response = client.get(
        "/api/public/attendance-alerts/anything",
        headers={"X-IRMS-Tray-Token": "test-tray-token"},
    )
    assert response.status_code == 404
