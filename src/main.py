import re

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.middleware.sessions import SessionMiddleware
from starlette_csrf import CSRFMiddleware

from .config import BASE_DIR, IS_DEVELOPMENT, SESSION_COOKIE_NAME, SESSION_MAX_AGE, SESSION_SECRET
from .database import init_db, utc_now_text
from .middleware.internal_only import InternalNetworkOnlyMiddleware
from .routers.api import build_router as build_api_router
from .routers.pages import build_router as build_pages_router


limiter = Limiter(key_func=get_remote_address)


def create_app() -> FastAPI:
    init_db()
    app = FastAPI(title="IRMS", version="0.1.0")
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
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
            re.compile(r"^/api/auth/login$"),
            re.compile(r"^/api/auth/management-login$"),
            re.compile(r"^/api/auth/operator-login$"),
            re.compile(r"^/api/public/notice/.*$"),
            re.compile(r"^/api/attendance/login$"),
        ],
    )
    app.add_middleware(
        InternalNetworkOnlyMiddleware,
        protected_prefixes=(
            "/api/public/notice",
            "/api/public/attendance-alerts",
        ),
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
