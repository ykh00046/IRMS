"""Attendance view API endpoints.

All attendance endpoints share a single router under ``/api/attendance``:

- ``POST /login``            - Sa-beon + password. Auto-creates the account
                               on first sight if sa-beon exists in Excel.
- ``POST /change-password``  - Current + new password.
- ``POST /logout``           - Clear the attendance session.
- ``GET  /me?month=``        - Logged-in employee's own attendance.
- ``GET  /admin/employees?month=`` - List employees for admin drop-down.
- ``GET  /admin/view?emp_id=&month=`` - Admin views any employee.
- ``POST /admin/reset-password`` - Admin resets password back to sa-beon.
"""

from __future__ import annotations

import secrets
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from itsdangerous import URLSafeSerializer
from pydantic import BaseModel, Field

from .. import attendance_auth
from ..attendance_auth import (
    AttendanceAuthError,
    current_attendance_emp_id,
    is_admin_mode,
    login_session,
    logout_session,
    require_irms_manager,
    require_view_context,
    touch_session,
)
from ..config import IS_DEVELOPMENT, SESSION_SECRET
from ..database import get_connection, write_audit_log
from ..services import attendance_excel as excel_service


class LoginRequest(BaseModel):
    emp_id: str = Field(min_length=1, max_length=20)
    password: str = Field(min_length=1, max_length=200)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=attendance_auth.MIN_PASSWORD_LENGTH, max_length=200)


class ResetPasswordRequest(BaseModel):
    emp_id: str = Field(min_length=1, max_length=20)


def _resolve_month(month: str | None) -> str:
    if month:
        value = month.strip()
        if len(value) == 7 and value[4] == "-":
            return value
    return excel_service.current_year_month()


def _load_attendance_response(year_month: str, emp_id: str) -> dict[str, Any]:
    try:
        profile, rows, summary = excel_service.load_month_for_employee(year_month, emp_id)
        annual_summary = excel_service.load_year_summary_for_employee(
            int(year_month[:4]), emp_id
        )
    except excel_service.MonthFileNotFound:
        raise HTTPException(status_code=404, detail="MONTH_FILE_NOT_FOUND")
    except excel_service.FileLocked:
        raise HTTPException(status_code=503, detail="FILE_LOCKED_RETRY")
    except excel_service.FileFormatInvalid:
        raise HTTPException(status_code=500, detail="FILE_FORMAT_INVALID")

    return {
        "month": year_month,
        "profile": excel_service.serialize_profile(profile),
        "summary": excel_service.serialize_summary(summary),
        "annual_summary": excel_service.serialize_annual_summary(annual_summary),
        "rows": excel_service.serialize_rows(rows),
        "available_months": excel_service.available_months(),
    }


def build_router() -> APIRouter:
    router = APIRouter(prefix="/attendance", tags=["attendance"])
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

    @router.post("/login")
    async def login(
        body: LoginRequest, request: Request, response: Response
    ) -> dict[str, Any]:
        emp_id = body.emp_id.strip()
        try:
            record = attendance_auth.authenticate(emp_id, body.password)
        except AttendanceAuthError as exc:
            raise exc.to_http() from exc

        reset_required = bool(record.get("password_reset_required"))
        login_session(request, emp_id, reset_required)
        _refresh_csrf_cookie(response)
        return {
            "emp_id": emp_id,
            "password_reset_required": reset_required,
        }

    @router.post("/logout")
    async def logout(request: Request) -> dict[str, str]:
        logout_session(request)
        return {"status": "ok"}

    @router.post("/change-password")
    async def change_password(
        body: ChangePasswordRequest, request: Request
    ) -> dict[str, str]:
        emp_id = current_attendance_emp_id(request)
        if not emp_id:
            raise HTTPException(
                status_code=401, detail="ATTENDANCE_LOGIN_REQUIRED"
            )
        try:
            attendance_auth.change_password(
                emp_id, body.current_password, body.new_password
            )
        except AttendanceAuthError as exc:
            raise exc.to_http() from exc
        sess = request.session.get(attendance_auth.SESSION_KEY) or {}
        sess["password_reset_required"] = False
        request.session[attendance_auth.SESSION_KEY] = sess
        touch_session(request)
        return {"status": "ok"}

    @router.get("/me")
    async def me(
        request: Request, month: str | None = Query(default=None, max_length=7)
    ) -> dict[str, Any]:
        context = require_view_context(request)
        if context.admin_mode and not context.emp_id:
            # Pure admin without own sa-beon session: cannot query /me.
            raise HTTPException(
                status_code=400, detail="ATTENDANCE_EMP_REQUIRED_IN_ADMIN_MODE"
            )
        payload = _load_attendance_response(_resolve_month(month), context.emp_id or "")
        payload["admin_mode"] = context.admin_mode
        payload["password_reset_required"] = (
            context.password_reset_required and bool(context.emp_id)
        )
        return payload

    @router.get("/admin/employees")
    async def admin_employees(
        request: Request, month: str | None = Query(default=None, max_length=7)
    ) -> dict[str, Any]:
        require_irms_manager(request)
        year_month = _resolve_month(month)
        try:
            items = excel_service.employee_list(year_month)
        except excel_service.MonthFileNotFound:
            items = []
        except excel_service.FileLocked:
            raise HTTPException(status_code=503, detail="FILE_LOCKED_RETRY")
        return {
            "month": year_month,
            "items": items,
            "available_months": excel_service.available_months(),
        }

    @router.get("/admin/view")
    async def admin_view(
        request: Request,
        emp_id: str = Query(..., min_length=1, max_length=20),
        month: str | None = Query(default=None, max_length=7),
    ) -> dict[str, Any]:
        user = require_irms_manager(request)
        year_month = _resolve_month(month)
        payload = _load_attendance_response(year_month, emp_id.strip())
        profile = payload.get("profile") or {}
        with get_connection() as connection:
            write_audit_log(
                connection,
                action="attendance_viewed_by_admin",
                actor=user,
                target_type="attendance",
                target_id=emp_id.strip(),
                target_label=(
                    f"{profile.get('name', '-')} (사번 {emp_id.strip()}) {year_month}"
                ),
                details={"month": year_month},
            )
            connection.commit()
        payload["admin_mode"] = True
        return payload

    @router.post("/admin/reset-password")
    async def admin_reset_password(
        body: ResetPasswordRequest, request: Request
    ) -> dict[str, str]:
        user = require_irms_manager(request)
        emp_id = body.emp_id.strip()
        try:
            attendance_auth.reset_password_to_empid(emp_id)
        except AttendanceAuthError as exc:
            raise exc.to_http() from exc
        profile = excel_service.employee_profile_from_any_month(emp_id)
        target_label = (
            f"{profile.name} (사번 {emp_id})"
            if profile and profile.name
            else f"사번 {emp_id}"
        )
        with get_connection() as connection:
            write_audit_log(
                connection,
                action="attendance_password_reset",
                actor=user,
                target_type="attendance",
                target_id=emp_id,
                target_label=target_label,
                details={},
            )
            connection.commit()
        return {"status": "ok"}

    @router.get("/admin/users")
    async def admin_users(request: Request) -> dict[str, Any]:
        require_irms_manager(request)
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT emp_id, password_reset_required, failed_attempts,
                       locked_until, last_login_at, created_at
                FROM attendance_users
                ORDER BY emp_id ASC
                """
            ).fetchall()
        items = [
            {
                "emp_id": row["emp_id"],
                "password_reset_required": bool(row["password_reset_required"]),
                "failed_attempts": int(row["failed_attempts"] or 0),
                "locked_until": row["locked_until"],
                "last_login_at": row["last_login_at"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]
        return {"items": items, "total": len(items)}

    @router.get("/session")
    async def session_status(request: Request) -> dict[str, Any]:
        emp_id = current_attendance_emp_id(request)
        admin = is_admin_mode(request)
        if emp_id:
            touch_session(request)
        sess = request.session.get(attendance_auth.SESSION_KEY) or {}
        return {
            "authenticated": bool(emp_id),
            "admin_mode": admin,
            "emp_id": emp_id,
            "password_reset_required": bool(sess.get("password_reset_required")),
        }

    return router
