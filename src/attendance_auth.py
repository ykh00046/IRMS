"""Authentication + session management for the attendance view.

This is a separate credential space from the main IRMS login:
- Identity is the ERP employee number (사번), stored in ``attendance_users``.
- First login uses the sa-beon as the initial password and shows a change
  reminder; attendance lookup remains available.
- Brute force guard: 5 failures within the counter window locks the account
  for 5 minutes.
- Idle session timeout: 5 minutes of inactivity clears the session.

Managers / admins already authenticated via the main IRMS login bypass the
sa-beon gate and drop into "admin mode" where they can view any employee.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, Request, status

from .auth import has_access_level
from .database import get_connection, utc_now_text
from .security import hash_password, verify_password

SESSION_KEY = "att_user"
IDLE_TIMEOUT_SECONDS = 5 * 60
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_SECONDS = 5 * 60
MIN_PASSWORD_LENGTH = 4


@dataclass
class AttendanceAuthError(Exception):
    code: str
    status_code: int = status.HTTP_400_BAD_REQUEST
    extra: dict[str, Any] | None = None

    def to_http(self) -> HTTPException:
        detail: dict[str, Any] = {"detail": self.code}
        if self.extra:
            detail.update(self.extra)
        return HTTPException(status_code=self.status_code, detail=detail)


def _utc_now() -> _dt.datetime:
    return _dt.datetime.now(tz=_dt.timezone.utc)


def _parse_utc(text: str | None) -> _dt.datetime | None:
    if not text:
        return None
    try:
        return _dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _format_utc(when: _dt.datetime) -> str:
    return when.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _fetch(emp_id: str) -> dict[str, Any] | None:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT emp_id, password_hash, password_reset_required,
                   failed_attempts, locked_until, last_login_at, created_at
            FROM attendance_users
            WHERE emp_id = ?
            """,
            (emp_id,),
        ).fetchone()
    if not row:
        return None
    return dict(row)


def _create(emp_id: str, password: str, reset_required: int = 1) -> None:
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO attendance_users
                (emp_id, password_hash, password_reset_required, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (emp_id, hash_password(password), reset_required, utc_now_text()),
        )
        connection.commit()


def _update_failed(emp_id: str, count: int, locked_until: str | None) -> None:
    with get_connection() as connection:
        connection.execute(
            "UPDATE attendance_users "
            "SET failed_attempts = ?, locked_until = ? WHERE emp_id = ?",
            (count, locked_until, emp_id),
        )
        connection.commit()


def _update_on_success(emp_id: str) -> None:
    with get_connection() as connection:
        connection.execute(
            "UPDATE attendance_users "
            "SET failed_attempts = 0, locked_until = NULL, last_login_at = ? "
            "WHERE emp_id = ?",
            (utc_now_text(), emp_id),
        )
        connection.commit()


def _set_password(emp_id: str, password: str, reset_required: int) -> None:
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE attendance_users
            SET password_hash = ?, password_reset_required = ?,
                failed_attempts = 0, locked_until = NULL
            WHERE emp_id = ?
            """,
            (hash_password(password), reset_required, emp_id),
        )
        connection.commit()


def ensure_account(emp_id: str) -> dict[str, Any]:
    """Return (possibly auto-created) account row for sa-beon."""
    record = _fetch(emp_id)
    if record is not None:
        return record
    _create(emp_id, emp_id, reset_required=1)
    record = _fetch(emp_id)
    assert record is not None
    return record


def authenticate(emp_id: str, password: str) -> dict[str, Any]:
    """Verify credentials, applying lockout rules.

    Raises ``AttendanceAuthError`` with the public error code on failure.
    """
    from .services.attendance_excel import employee_exists_in_any_month

    emp_id = (emp_id or "").strip()
    password = password or ""
    if not emp_id or not password:
        raise AttendanceAuthError(
            code="INVALID_CREDENTIALS", status_code=status.HTTP_401_UNAUTHORIZED
        )

    record = _fetch(emp_id)
    if record is None:
        # First ever login: sa-beon must exist in some month's Excel file.
        if not employee_exists_in_any_month(emp_id):
            raise AttendanceAuthError(
                code="EMP_NOT_IN_EXCEL", status_code=status.HTTP_404_NOT_FOUND
            )
        _create(emp_id, emp_id, reset_required=1)
        record = _fetch(emp_id)
        assert record is not None

    locked_until = _parse_utc(record.get("locked_until"))
    now = _utc_now()
    if locked_until and locked_until > now:
        raise AttendanceAuthError(
            code="LOCKED",
            status_code=status.HTTP_423_LOCKED,
            extra={"locked_until": _format_utc(locked_until)},
        )

    if not verify_password(password, record["password_hash"]):
        attempts = int(record.get("failed_attempts") or 0) + 1
        if attempts >= MAX_FAILED_ATTEMPTS:
            lock_until = now + _dt.timedelta(seconds=LOCKOUT_SECONDS)
            _update_failed(emp_id, 0, _format_utc(lock_until))
            raise AttendanceAuthError(
                code="LOCKED",
                status_code=status.HTTP_423_LOCKED,
                extra={"locked_until": _format_utc(lock_until)},
            )
        _update_failed(emp_id, attempts, None)
        raise AttendanceAuthError(
            code="INVALID_CREDENTIALS",
            status_code=status.HTTP_401_UNAUTHORIZED,
            extra={"remaining": MAX_FAILED_ATTEMPTS - attempts},
        )

    _update_on_success(emp_id)
    return _fetch(emp_id) or record


def change_password(emp_id: str, current_password: str, new_password: str) -> None:
    record = _fetch(emp_id)
    if record is None:
        raise AttendanceAuthError(
            code="NOT_AUTHENTICATED", status_code=status.HTTP_401_UNAUTHORIZED
        )
    if not verify_password(current_password, record["password_hash"]):
        raise AttendanceAuthError(
            code="CURRENT_PASSWORD_WRONG",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    if len(new_password) < MIN_PASSWORD_LENGTH:
        raise AttendanceAuthError(
            code="PASSWORD_TOO_SHORT",
            status_code=status.HTTP_400_BAD_REQUEST,
            extra={"min_length": MIN_PASSWORD_LENGTH},
        )
    if new_password == emp_id:
        raise AttendanceAuthError(
            code="PASSWORD_SAME_AS_EMPID",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    _set_password(emp_id, new_password, reset_required=0)


def reset_password_to_empid(emp_id: str) -> None:
    from .services.attendance_excel import employee_exists_in_any_month

    emp_id = (emp_id or "").strip()
    if not emp_id or not employee_exists_in_any_month(emp_id):
        raise AttendanceAuthError(
            code="EMP_NOT_IN_EXCEL", status_code=status.HTTP_404_NOT_FOUND
        )

    record = _fetch(emp_id)
    if record is None:
        _create(emp_id, emp_id, reset_required=1)
        return
    _set_password(emp_id, emp_id, reset_required=1)


def login_session(request: Request, emp_id: str, password_reset_required: bool) -> None:
    request.session[SESSION_KEY] = {
        "emp_id": emp_id,
        "authenticated_at": _format_utc(_utc_now()),
        "last_activity": _format_utc(_utc_now()),
        "password_reset_required": bool(password_reset_required),
    }


def logout_session(request: Request) -> None:
    if SESSION_KEY in request.session:
        request.session.pop(SESSION_KEY, None)


def touch_session(request: Request) -> None:
    sess = request.session.get(SESSION_KEY)
    if sess:
        sess["last_activity"] = _format_utc(_utc_now())
        request.session[SESSION_KEY] = sess


def current_attendance_emp_id(request: Request) -> str | None:
    """Return the sa-beon of the logged-in attendance user, applying idle timeout."""
    sess = request.session.get(SESSION_KEY)
    if not sess:
        return None
    last = _parse_utc(sess.get("last_activity"))
    if last is None:
        logout_session(request)
        return None
    if (_utc_now() - last).total_seconds() > IDLE_TIMEOUT_SECONDS:
        logout_session(request)
        return None
    emp_id = sess.get("emp_id")
    return str(emp_id) if emp_id else None


def is_admin_mode(request: Request) -> bool:
    from .auth import get_current_user

    user = get_current_user(request, required=False)
    if not user:
        return False
    return has_access_level(user, "manager")


@dataclass
class AttendanceViewContext:
    emp_id: str | None          # logged-in sa-beon (None for pure admin mode without attendance session)
    admin_mode: bool            # IRMS manager/admin is viewing
    password_reset_required: bool


def require_view_context(request: Request) -> AttendanceViewContext:
    """Authentication gate for viewing attendance. Admins bypass the sa-beon login."""
    emp_id = current_attendance_emp_id(request)
    admin = is_admin_mode(request)
    if emp_id is None and not admin:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="ATTENDANCE_LOGIN_REQUIRED"
        )
    touch_session(request)
    sess = request.session.get(SESSION_KEY) or {}
    return AttendanceViewContext(
        emp_id=emp_id,
        admin_mode=admin,
        password_reset_required=bool(sess.get("password_reset_required")),
    )


def require_irms_manager(request: Request) -> dict[str, Any]:
    """Dependency for admin-only endpoints (password reset, list employees)."""
    from .auth import get_current_user

    user = get_current_user(request, required=True)
    if not has_access_level(user, "manager"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="FORBIDDEN")
    return user
