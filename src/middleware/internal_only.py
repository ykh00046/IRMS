"""Restrict selected URL paths to internal (private) network IPs only.

Used for the unauthenticated notice-polling endpoints that the tray client
consumes from field PCs on the internal LAN. External requests are rejected
with HTTP 403 before hitting the router.
"""

from __future__ import annotations

import ipaddress
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

    def __init__(self, app, protected_prefixes: Iterable[str]):
        super().__init__(app)
        self._prefixes = tuple(protected_prefixes)

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if not path.startswith(self._prefixes):
            return await call_next(request)

        client = request.client
        client_host = client.host if client else ""
        if not _is_private(client_host):
            return JSONResponse(
                status_code=403,
                content={"detail": "INTERNAL_NETWORK_ONLY"},
            )
        return await call_next(request)
