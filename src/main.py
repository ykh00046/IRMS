import re

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from starlette.middleware.sessions import SessionMiddleware
from starlette_csrf import CSRFMiddleware

from .config import (
    BASE_DIR,
    IS_DEVELOPMENT,
    REQUIRE_TRAY_API_TOKEN,
    SESSION_COOKIE_NAME,
    SESSION_MAX_AGE,
    SESSION_SECRET,
    TRAY_API_TOKEN,
    TUNNEL_REQUIRE_LOGIN,
)
from .db import init_db, utc_now_text
from .limiter import limiter
from .middleware.internal_only import InternalNetworkOnlyMiddleware
from .middleware.login_origin import LoginOriginMiddleware
from .middleware.security_headers import SecurityHeadersMiddleware
from .middleware.tunnel_auth import TunnelAuthMiddleware
from .routers.api import build_router as build_api_router
from .routers.pages import build_router as build_pages_router


def create_app() -> FastAPI:
    init_db()
    # 취소 보존기한 경과분 정리 — 기동마다 1회(운영은 자동 업데이트로 자주 재시작).
    # serve.py 가 일일 백업 '이후'에도 돌리므로 삭제분은 항상 백업에 남아 있다.
    try:
        from .db import get_connection
        from .services.record_delete_service import purge_expired_canceled
        with get_connection() as _conn:
            _purged = purge_expired_canceled(_conn)
            _conn.commit()
        if _purged:
            print(f"[정리] 취소 보존기한 경과 배합 기록 {len(_purged)}건 삭제")
    except Exception as _exc:  # noqa: BLE001 — 정리는 부가 기능, 기동을 막지 않는다
        print(f"[정리] 취소 기록 정리 실패(무시): {_exc}")
    app = FastAPI(title="IRMS", version="0.1.0")
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    # SessionMiddleware 보다 **먼저** 등록해야 안쪽(세션이 채워진 뒤)에서 돈다.
    # 터널로 들어온 요청에만 책임자 로그인을 요구한다 — 사내망 요청은 그대로 통과.
    app.add_middleware(
        TunnelAuthMiddleware,
        enabled=TUNNEL_REQUIRE_LOGIN,
    )
    app.add_middleware(
        SessionMiddleware,
        secret_key=SESSION_SECRET,
        session_cookie=SESSION_COOKIE_NAME,
        max_age=SESSION_MAX_AGE,
        same_site="lax" if IS_DEVELOPMENT else "strict",
        https_only=not IS_DEVELOPMENT,
    )

    app.add_middleware(
        CSRFMiddleware,
        secret=SESSION_SECRET,
        cookie_name="csrftoken",
        cookie_secure=not IS_DEVELOPMENT,
        exempt_urls=[
            re.compile(r"^/health$"),
            # 로그인은 토큰을 받기 전에 호출되므로 CSRF 토큰 검사에서 면제된다.
            # 그 대신 LoginOriginMiddleware 가 교차 출처 POST 를 막는다(감사 F-10).
            re.compile(r"^/api/auth/management-login$"),
            re.compile(r"^/api/attendance/login$"),
            re.compile(r"^/api/blend/session/login$"),
            # sendBeacon cannot attach custom headers; logout is idempotent
            # so a forced-logout CSRF attack causes only minor inconvenience.
            re.compile(r"^/api/attendance/logout$"),
            re.compile(r"^/api/blend/session/logout$"),
        ],
    )
    # CSRF 면제로 뚫린 로그인 경로를 Origin 검사로 막는다 (감사 F-10).
    app.add_middleware(LoginOriginMiddleware)
    app.add_middleware(
        InternalNetworkOnlyMiddleware,
        protected_prefixes=(
            "/api/public/attendance-alerts",
            "/api/public/material-usage",
            "/api/public/viscosity-reminders",
            "/api/public/rescale-alerts",
        ),
        api_token=TRAY_API_TOKEN,
        require_api_token=REQUIRE_TRAY_API_TOKEN,
    )
    app.add_middleware(
        SecurityHeadersMiddleware,
        is_production=not IS_DEVELOPMENT,
    )

    templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
    app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

    app.include_router(build_pages_router(templates))
    app.include_router(build_api_router())

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "time": utc_now_text()}

    return app


app = create_app()
