from typing import Any

import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from itsdangerous import URLSafeSerializer
from slowapi import Limiter
from slowapi.util import get_remote_address

from ..auth import (
    authenticate_user,
    get_current_user,
    has_access_level,
    login_user,
    logout_user,
    require_access_level,
)
from ..config import IS_DEVELOPMENT, SESSION_SECRET
from ..database import get_connection, write_audit_log
from .models import LoginRequest, actor_name


def build_router() -> APIRouter:
    router = APIRouter()
    limiter = Limiter(key_func=get_remote_address)
    csrf_serializer = URLSafeSerializer(SESSION_SECRET, "csrftoken")

    def _refresh_csrf_cookie(response: Response) -> None:
        response.set_cookie(
            "csrftoken",
            csrf_serializer.dumps(secrets.token_urlsafe(128)),
            path="/",
            secure=not IS_DEVELOPMENT,
            httponly=False,
            samesite="lax",
        )

    def _do_login(
        request: Request,
        response: Response,
        user: dict[str, Any],
        action: str,
        entry_point: str,
        *,
        max_level: str | None = None,
    ) -> dict[str, Any]:
        login_user(request, user, max_level=max_level)
        with get_connection() as connection:
            write_audit_log(
                connection,
                action=action,
                actor=user,
                target_type="session",
                target_id=user["id"],
                target_label=user["username"],
                details={"entry_point": entry_point},
            )
            connection.commit()
        _refresh_csrf_cookie(response)
        return {"user": user}

    def _authenticate_or_fail(
        request: Request,
        body: LoginRequest,
        entry_point: str,
        *,
        required_level: str | None = None,
    ) -> dict[str, Any]:
        user = authenticate_user(body.username, body.password)
        if not user or (required_level and not has_access_level(user, required_level)):
            _log_failed_login(request, body.username, entry_point)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="INVALID_CREDENTIALS",
            )
        return user

    def _log_failed_login(request: Request, username: str, entry_point: str) -> None:
        with get_connection() as connection:
            write_audit_log(
                connection,
                action="login_failed",
                actor={"id": 0, "username": username},
                target_type="session",
                target_id=0,
                target_label=username,
                details={"entry_point": entry_point, "ip": get_remote_address(request)},
            )
            connection.commit()

    @router.post("/auth/login")
    @limiter.limit("5/minute")
    async def auth_login(request: Request, response: Response, body: LoginRequest) -> dict[str, Any]:
        user = _authenticate_or_fail(request, body, "legacy_login")
        return _do_login(request, response, user, "auth_login", "legacy_login")

    @router.post("/auth/management-login")
    @limiter.limit("5/minute")
    async def auth_management_login(request: Request, response: Response, body: LoginRequest) -> dict[str, Any]:
        user = _authenticate_or_fail(request, body, "management_login", required_level="manager")
        return _do_login(request, response, user, "management_login", "management_login")

    @router.post("/auth/operator-login")
    @limiter.limit("5/minute")
    async def auth_operator_login(request: Request, response: Response, body: LoginRequest) -> dict[str, Any]:
        user = _authenticate_or_fail(request, body, "operator_login")
        return _do_login(
            request,
            response,
            user,
            "operator_select",
            "operator_login",
            max_level="operator",
        )

    @router.post("/auth/logout")
    async def auth_logout(request: Request) -> dict[str, str]:
        current_user = get_current_user(request, required=False)
        if current_user:
            with get_connection() as connection:
                write_audit_log(
                    connection,
                    action="logout",
                    actor=current_user,
                    target_type="session",
                    target_id=current_user["id"],
                    target_label=current_user["username"],
                )
                connection.commit()
        logout_user(request)
        return {"status": "ok"}

    auth_router = APIRouter(dependencies=[Depends(require_access_level("operator"))])

    @auth_router.get("/auth/me")
    async def auth_me(request: Request) -> dict[str, Any]:
        return {"user": get_current_user(request)}

    return router, auth_router
