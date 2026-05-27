"""Restrict selected URL paths to internal (private) network IPs only.

Used for the unauthenticated notice-polling endpoints that the tray client
consumes from field PCs on the internal LAN. External requests are rejected
with HTTP 403 before hitting the router.
"""

from __future__ import annotations

import ipaddress
import hmac
from collections.abc import Iterable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

_PRIVATE_NETWORKS: tuple[ipaddress._BaseNetwork, ...] = (
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
)


def _is_private(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    return any(ip in net for net in _PRIVATE_NETWORKS)


class InternalNetworkOnlyMiddleware(BaseHTTPMiddleware):
    """Reject requests to protected paths when the client IP is not private.

    We intentionally ignore ``X-Forwarded-For`` because IRMS has no reverse
    proxy in its current deployment. If a proxy is added later, the trust
    configuration needs to be added explicitly.
    """

    def __init__(
        self,
        app,
        protected_prefixes: Iterable[str],
        *,
        api_token: str = "",
        require_api_token: bool = False,
    ):
        super().__init__(app)
        self._prefixes = tuple(protected_prefixes)
        self._api_token = api_token
        self._require_api_token = require_api_token

    def _has_valid_token(self, request: Request) -> bool:
        if not self._api_token:
            return False
        supplied = request.headers.get("X-IRMS-Tray-Token", "")
        return hmac.compare_digest(supplied, self._api_token)

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if not path.startswith(self._prefixes):
            return await call_next(request)

        if self._require_api_token:
            if self._has_valid_token(request):
                return await call_next(request)
            return JSONResponse(
                status_code=403,
                content={"detail": "TRAY_TOKEN_REQUIRED"},
            )

        client = request.client
        client_host = client.host if client else ""
        if not (_is_private(client_host) or self._has_valid_token(request)):
            return JSONResponse(
                status_code=403,
                content={"detail": "INTERNAL_NETWORK_ONLY"},
            )
        return await call_next(request)
