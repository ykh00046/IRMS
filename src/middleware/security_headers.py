"""Attach security-related response headers for external (cloudflared) exposure.

Headers applied to every response:
  - Strict-Transport-Security (production only)
  - X-Frame-Options: DENY
  - X-Content-Type-Options: nosniff
  - Referrer-Policy: same-origin
  - Permissions-Policy: minimal allowlist (no geolocation/camera/mic/payment)
  - Cross-Origin-Opener-Policy: same-origin

HSTS is intentionally skipped in development to avoid polluting the
browser cache with a long-lived production-only directive. Other headers
use ``setdefault`` so a router can explicitly override (e.g. an image
endpoint that needs a different X-Frame-Options policy).
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request


_DEFAULT_HSTS_MAX_AGE = 31_536_000  # 1 year


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Append response headers that harden the app for public exposure.

    Mounted as the outermost middleware so the headers are attached to
    every response — including 4xx/5xx and middleware-rejected requests
    such as CSRF failures.
    """

    def __init__(
        self,
        app,
        *,
        is_production: bool,
        hsts_max_age: int = _DEFAULT_HSTS_MAX_AGE,
    ) -> None:
        super().__init__(app)
        self._is_production = is_production
        self._hsts_max_age = hsts_max_age

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        headers = response.headers
        headers.setdefault("X-Frame-Options", "DENY")
        headers.setdefault("X-Content-Type-Options", "nosniff")
        headers.setdefault("Referrer-Policy", "same-origin")
        headers.setdefault(
            "Permissions-Policy",
            "geolocation=(), camera=(), microphone=(), payment=()",
        )
        headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        if self._is_production:
            headers.setdefault(
                "Strict-Transport-Security",
                f"max-age={self._hsts_max_age}",
            )
        return response
