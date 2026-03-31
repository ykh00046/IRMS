from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from .config import BASE_DIR, SESSION_COOKIE_NAME, SESSION_MAX_AGE, SESSION_SECRET
from .database import init_db, utc_now_text
from .routers.api import build_router as build_api_router
from .routers.pages import build_router as build_pages_router


def create_app() -> FastAPI:
    init_db()
    app = FastAPI(title="IRMS", version="0.1.0")
    app.add_middleware(
        SessionMiddleware,
        secret_key=SESSION_SECRET,
        session_cookie=SESSION_COOKIE_NAME,
        max_age=SESSION_MAX_AGE,
        same_site="lax",
        https_only=False,
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
