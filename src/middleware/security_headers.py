"""Attach security-related response headers for external (cloudflared) exposure.

Headers applied to every response:
  - Strict-Transport-Security (production only)
  - X-Frame-Options: DENY
  - X-Content-Type-Options: nosniff
  - Referrer-Policy: same-origin
  - Permissions-Policy: minimal allowlist (no geolocation/camera/mic/payment)
  - Cross-Origin-Opener-Policy: same-origin
  - Content-Security-Policy: conservative same-origin lock

HSTS is intentionally skipped in development to avoid polluting the
browser cache with a long-lived production-only directive. Other headers
use ``setdefault`` so a router can explicitly override (e.g. an image
endpoint that needs a different X-Frame-Options policy).

CSP note: the Jinja2 templates carry inline code that a strict policy
would break — e.g. an inline ``<script>`` block in ``entry.html`` and
inline ``<style>`` / ``style="..."`` in ``status.html``,
``material_lots.html``, ``admin_users.html``. Removing inline code is out
of scope, so ``script-src``/``style-src`` keep ``'unsafe-inline'``. The
policy still hardens the app: it pins every origin to ``'self'``, forbids
framing (``frame-ancestors 'none'``), blocks plugins/objects
(``object-src 'none'``), and prevents ``<base>`` hijacking
(``base-uri 'self'``). Set via ``setdefault`` so it stays additive and a
router can override for a specific response if ever needed.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request


_DEFAULT_HSTS_MAX_AGE = 31_536_000  # 1 year

# Conservative same-origin CSP. 'unsafe-inline' is required by existing inline
# scripts/styles in the templates (see module docstring); img data: URIs are
# used by canvas-generated signatures and small inline assets.
_CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "frame-ancestors 'none'; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "img-src 'self' data:; "
    # 웹폰트는 2026-08-03 부터 자체 호스팅(static/fonts + static/css/fonts.css)이라
    # 외부 폰트 도메인 예외가 필요 없다. CDN 시절에는 인터넷이 막힌 현장 PC 에서
    # 폰트만 조용히 실패해 맑은 고딕으로 폴백 → 화면이 넓어지고 잘렸다.
    "style-src 'self' 'unsafe-inline'; "
    "font-src 'self'; "
    # 배합 화면(blend.js/blend_continuous.js)은 현장 PC 로컬 저울 에이전트
    # (http://127.0.0.1:8787, 현장 도우미 저울 토글)로 fetch 한다. connect-src 를
    # 명시하지 않으면 default-src 'self' 로 폴백돼 이 교차 출처 fetch 가 차단된다 —
    # 2026-07-31 CSP 도입 직후 전 현장 PC 저울 연동이 동시에 끊긴 실사고의 원인.
    "connect-src 'self' http://127.0.0.1:8787; "
    "script-src 'self' 'unsafe-inline'"
)


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
        headers.setdefault("Content-Security-Policy", _CONTENT_SECURITY_POLICY)
        if self._is_production:
            headers.setdefault(
                "Strict-Transport-Security",
                f"max-age={self._hsts_max_age}",
            )
        return response
