import secrets
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from slowapi.util import get_remote_address

from ..auth import (
    authenticate_manager_worker,
    authenticate_user,
    get_current_user,
    has_access_level,
    login_manager_worker,
    login_user,
    logout_user,
    require_access_level,
)
from ..db import get_connection, write_audit_log
from ..limiter import limiter
from ..security import hash_password, refresh_csrf_cookie, verify_password
from .models import ChangePasswordBody, LoginRequest


def build_router() -> APIRouter:
    router = APIRouter()

    def _do_login(
        request: Request,
        response: Response,
        user: dict[str, Any],
        action: str,
        entry_point: str,
    ) -> dict[str, Any]:
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
        refresh_csrf_cookie(response)
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

    @router.post("/auth/management-login")
    @limiter.limit("5/minute")
    def auth_management_login(request: Request, response: Response, body: LoginRequest) -> dict[str, Any]:
        # 이름 기반 책임자(이용자 명단에서 지정된 사람) 우선, 없으면 레거시 admin 계정 폴백.
        user = authenticate_manager_worker(body.username, body.password)
        if user is None:
            legacy = authenticate_user(body.username, body.password)
            if legacy and has_access_level(legacy, "manager"):
                user = legacy
        if user is None:
            _log_failed_login(request, body.username, "management_login")
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="INVALID_CREDENTIALS")

        if user.get("is_worker_manager"):
            login_manager_worker(request, user)
            with get_connection() as connection:
                write_audit_log(
                    connection, action="management_login", actor=user,
                    target_type="session", target_id=user["id"], target_label=user["username"],
                    details={"entry_point": "management_login"},
                )
                connection.commit()
            refresh_csrf_cookie(response)
            return {"user": user}
        return _do_login(request, response, user, "management_login", "management_login")

    @router.post("/auth/change-password")
    @limiter.limit("5/minute")
    def auth_change_password(request: Request, body: ChangePasswordBody) -> dict[str, Any]:
        """로그인한 책임자 본인의 비밀번호 변경(현재 비밀번호 확인 + 세션 토큰 회전)."""
        user = get_current_user(request, required=True)
        if not has_access_level(user, "manager"):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="FORBIDDEN")
        new_token = secrets.token_urlsafe(32)
        with get_connection() as connection:
            if user.get("is_worker_manager"):
                row = connection.execute(
                    "SELECT password_hash FROM workers WHERE id = ?", (int(user["id"]),)
                ).fetchone()
                if not row or not row["password_hash"] or not verify_password(
                    body.current_password, row["password_hash"]
                ):
                    raise HTTPException(status_code=400, detail="INVALID_CURRENT_PASSWORD")
                connection.execute(
                    "UPDATE workers SET password_hash = ?, session_token = ? WHERE id = ?",
                    (hash_password(body.new_password), new_token, int(user["id"])),
                )
                request.session["mgr_token"] = new_token
                action = "worker_manager_password_changed"
            else:
                row = connection.execute(
                    "SELECT password_hash FROM users WHERE id = ?", (int(user["id"]),)
                ).fetchone()
                if not row or not verify_password(body.current_password, row["password_hash"]):
                    raise HTTPException(status_code=400, detail="INVALID_CURRENT_PASSWORD")
                connection.execute(
                    "UPDATE users SET password_hash = ?, session_token = ? WHERE id = ?",
                    (hash_password(body.new_password), new_token, int(user["id"])),
                )
                request.session["session_token"] = new_token
                action = "password_changed"
            write_audit_log(
                connection, action=action, actor=user, target_type="user",
                target_id=str(user["id"]), target_label=user["username"],
            )
            connection.commit()
        return {"ok": True}

    @router.post("/auth/logout")
    def auth_logout(request: Request) -> dict[str, str]:
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
    def auth_me(request: Request) -> dict[str, Any]:
        return {"user": get_current_user(request)}

    return router, auth_router
