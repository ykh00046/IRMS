import logging
import logging.handlers
import mimetypes
import re
import subprocess
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from starlette.middleware.sessions import SessionMiddleware
from starlette_csrf import CSRFMiddleware

from .config import (
    BASE_DIR,
    DATA_DIR,
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


def _compute_server_version() -> str:
    """서버 프로세스의 버전 마커를 한 번만 계산한다.

    운영 PC(serve.py)는 주기적으로 git pull 후 서버를 재시작하므로,
    git HEAD sha 가 배포마다 바뀐다 → 클라이언트가 이 값의 변화를 감지해
    자동 새로고침한다. git 이 없거나 저장소가 아니면 프로세스 기동 시각(UTC)으로
    폴백한다(재시작 = 새 값 → 여전히 동작).

    반환값은 모듈 로드 시점에 SERVER_VERSION 상수로 고정되어, 재시작 전까지
    모든 /api/version 응답이 동일하다.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
            timeout=3,
            check=True,
        )
        sha = result.stdout.strip()
        if sha:
            return sha
    except Exception:  # noqa: BLE001 — git 부재/비저장소는 폴백으로 처리
        pass
    return datetime.now(timezone.utc).isoformat()


# 프로세스 수명 동안 고정되는 버전 마커(모듈 import 시 1회 계산).
SERVER_VERSION = _compute_server_version()


_error_logger: logging.Logger | None = None


def _get_error_logger() -> logging.Logger | None:
    """미처리 500 예외를 <IRMS_DATA_DIR>/errors.log 에 남기는 회전 로거.

    serve.py 의 update-status.json 과 같은 취지 — 콘솔 로그를 놓쳐도 파일 하나로
    무엇이 터졌는지 사후 확인할 수 있다. 응답에는 트레이스백을 싣지 않고(기존 500
    동작 유지) 여기 파일에만 예외 종류·메시지·요청 경로를 기록한다.

    핸들러 구성 실패(폴더 권한 등)는 부가 기능이므로 삼키고 None 을 반환한다 —
    로깅이 요청 처리를 절대 막지 않는다.
    """
    global _error_logger
    if _error_logger is not None:
        return _error_logger
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        logger = logging.getLogger("irms.errors")
        logger.setLevel(logging.ERROR)
        logger.propagate = False
        if not logger.handlers:
            handler = logging.handlers.RotatingFileHandler(
                DATA_DIR / "errors.log",
                maxBytes=1_000_000,
                backupCount=5,
                encoding="utf-8",
            )
            handler.setFormatter(
                logging.Formatter("%(asctime)s %(levelname)s %(message)s")
            )
            logger.addHandler(handler)
        _error_logger = logger
    except Exception:  # noqa: BLE001 — 로거 구성 실패는 치명적 아님
        return None
    return _error_logger


def _log_unhandled_exception(method: str, path: str, exc: BaseException) -> None:
    """미처리 예외 한 건을 errors.log 에 기록. 자체적으로는 절대 raise 하지 않는다."""
    try:
        logger = _get_error_logger()
        if logger is None:
            return
        logger.error(
            "unhandled %s %s -> %s: %s",
            method,
            path,
            type(exc).__name__,
            exc,
        )
    except Exception:  # noqa: BLE001 — 로깅 실패가 요청/응답을 망치면 안 된다
        pass


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

    # 미처리 500 예외를 파일로 남긴다. call_next 를 감싸 예외를 로깅한 뒤 **그대로
    # 다시 던져** Starlette 의 기본 500 처리(클라이언트에는 일반 오류만)를 보존한다.
    # HTTPException 은 여기 도달 전에 응답으로 변환되므로 잡히지 않는다.
    @app.middleware("http")
    async def _persist_unhandled_errors(request: Request, call_next):
        try:
            return await call_next(request)
        except Exception as exc:  # noqa: BLE001 — 로깅 후 재던지기(동작 불변)
            _log_unhandled_exception(request.method, request.url.path, exc)
            raise

    templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
    # 자체 호스팅 웹폰트(static/fonts) — Python 의 mimetypes 는 woff2 를 모른다.
    # 등록하지 않으면 StaticFiles 가 application/octet-stream 으로 내보낸다.
    mimetypes.add_type("font/woff2", ".woff2")
    mimetypes.add_type("font/woff", ".woff")
    app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

    app.include_router(build_pages_router(templates))
    app.include_router(build_api_router())

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "time": utc_now_text()}

    # 배포 감지용 버전 마커 — 인증 불필요, GET 이라 CSRF 면제, 상수 반환이라 저비용.
    # 클라이언트(core.js)가 이 값을 폴링해 변하면 자동 새로고침한다.
    @app.get("/api/version")
    def api_version() -> dict[str, str]:
        return {"version": SERVER_VERSION}

    return app


app = create_app()
