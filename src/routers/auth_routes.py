from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
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
from ..database import get_connection, write_audit_log
from .models import LoginRequest, actor_name


def build_router() -> APIRouter:
    router = APIRouter()
    limiter = Limiter(key_func=get_remote_address)

    def _do_login(request: Request, user: dict[str, Any], action: str, entry_point: str) -> dict[str, Any]:
        login_user(request, user)
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
        return {"user": user}

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
    async def auth_login(request: Request, body: LoginRequest) -> dict[str, Any]:
        user = authenticate_user(body.username, body.password)
        if not user:
            _log_failed_login(request, body.username, "legacy_login")
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="INVALID_CREDENTIALS")
        return _do_login(request, user, "auth_login", "legacy_login")

    @router.post("/auth/management-login")
    @limiter.limit("5/minute")
    async def auth_management_login(request: Request, body: LoginRequest) -> dict[str, Any]:
        user = authenticate_user(body.username, body.password)
        if not user or not has_access_level(user, "manager"):
            _log_failed_login(request, body.username, "management_login")
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="INVALID_CREDENTIALS")
        return _do_login(request, user, "management_login", "management_login")

    @router.post("/auth/operator-login")
    @limiter.limit("5/minute")
    async def auth_operator_login(request: Request, body: LoginRequest) -> dict[str, Any]:
        user = authenticate_user(body.username, body.password)
        if not user:
            _log_failed_login(request, body.username, "operator_login")
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="INVALID_CREDENTIALS")
        login_user(request, user, max_level="operator")
        with get_connection() as connection:
            write_audit_log(
                connection,
                action="operator_select",
                actor=user,
                target_type="session",
                target_id=user["id"],
                target_label=user["username"],
                details={"entry_point": "operator_login"},
            )
            connection.commit()
        return {"user": user}

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
