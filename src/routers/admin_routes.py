import io
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from ..auth import get_current_user, require_access_level
from ..attendance_auth import AttendanceAuthError, validate_password_strength
from ..db import get_connection, list_audit_logs, row_to_dict, utc_now_text, write_audit_log
from ..security import hash_password
from ..services import dhr_pdf, signature_config
from .models import (
    AdminUserCreateRequest,
    AdminUserPasswordResetRequest,
    AdminUserUpdateRequest,
    role_for_access_level,
    serialize_admin_user,
)


PASSWORD_EXPIRATION_NOTICE = "초기화된 비밀번호는 임시 비밀번호입니다. 다음 로그인 시 반드시 변경해주세요."


def build_router() -> APIRouter:
    router = APIRouter(dependencies=[Depends(require_access_level("admin"))])

    @router.get("/admin/users")
    def admin_list_users() -> dict[str, Any]:
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT id, username, display_name, role, access_level, is_active, created_at
                FROM users
                ORDER BY
                    CASE access_level
                        WHEN 'manager' THEN 0
                        ELSE 1
                    END,
                    is_active DESC,
                    display_name ASC,
                    username ASC
                """
            ).fetchall()

        items = [serialize_admin_user(row) for row in rows]
        summary = {
            "total": len(items),
            "active": sum(1 for item in items if item["is_active"]),
            "admins": sum(1 for item in items if item["access_level"] == "admin"),
            "managers": sum(1 for item in items if item["access_level"] == "manager"),
            "operators": sum(1 for item in items if item["access_level"] == "operator"),
        }
        return {"items": items, "summary": summary, "total": len(items)}

    @router.post("/admin/deactivate-others")
    def admin_deactivate_others(request: Request) -> dict[str, Any]:
        """admin 을 제외한 모든 로그인 계정을 비활성화(작업자는 이름 입력으로 운영).

        삭제가 아니라 비활성화이므로 필요 시 사용자 관리에서 되돌릴 수 있다.
        """
        current_user = get_current_user(request)
        with get_connection() as connection:
            cursor = connection.execute(
                "UPDATE users SET is_active = 0, session_token = NULL "
                "WHERE username != 'admin' AND is_active = 1"
            )
            count = cursor.rowcount
            write_audit_log(
                connection,
                action="admin_deactivate_others",
                actor=current_user,
                target_type="users",
                details={"count": count},
            )
            connection.commit()
        return {"deactivated": count}

    @router.get("/admin/audit-logs")
    def admin_list_audit_logs(
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
        action: str | None = None,
    ) -> dict[str, Any]:
        with get_connection() as connection:
            items = list_audit_logs(connection, limit=limit, offset=offset, action=action)
        return {"items": items, "total": len(items)}

    @router.post("/admin/users")
    def admin_create_user(body: AdminUserCreateRequest, request: Request) -> dict[str, Any]:
        current_user = get_current_user(request)
        username = body.username.strip()
        display_name = body.display_name.strip()
        now = utc_now_text()
        try:
            validate_password_strength(body.password, username)
        except AttendanceAuthError as exc:
            raise exc.to_http() from exc

        with get_connection() as connection:
            existing = connection.execute(
                "SELECT id FROM users WHERE username = ?",
                (username,),
            ).fetchone()
            if existing:
                raise HTTPException(status_code=409, detail="USERNAME_ALREADY_EXISTS")

            cursor = connection.execute(
                """
                INSERT INTO users (username, password_hash, display_name, role, access_level, is_active, created_at)
                VALUES (?, ?, ?, ?, ?, 1, ?)
                """,
                (
                    username,
                    hash_password(body.password),
                    display_name,
                    role_for_access_level(body.access_level),
                    body.access_level,
                    now,
                ),
            )
            user_id = cursor.lastrowid
            created_row = connection.execute(
                """
                SELECT id, username, display_name, role, access_level, is_active, created_at
                FROM users
                WHERE id = ?
                """,
                (user_id,),
            ).fetchone()
            created_user = serialize_admin_user(created_row)
            write_audit_log(
                connection,
                action="user_created",
                actor=current_user,
                target_type="user",
                target_id=created_user["id"],
                target_label=created_user["username"],
                details={
                    "display_name": created_user["display_name"],
                    "access_level": created_user["access_level"],
                    "is_active": created_user["is_active"],
                },
            )
            connection.commit()

        return {"user": created_user}

    @router.patch("/admin/users/{user_id}")
    def admin_update_user(
        user_id: int,
        body: AdminUserUpdateRequest,
        request: Request,
    ) -> dict[str, Any]:
        current_user = get_current_user(request)
        display_name = body.display_name.strip()
        is_active_value = 1 if body.is_active else 0

        with get_connection() as connection:
            target_row = connection.execute(
                """
                SELECT id, username, display_name, role, access_level, is_active, created_at
                FROM users
                WHERE id = ?
                """,
                (user_id,),
            ).fetchone()
            if not target_row:
                raise HTTPException(status_code=404, detail="USER_NOT_FOUND")

            target = serialize_admin_user(target_row)

            if int(current_user["id"]) == int(user_id) and (
                not body.is_active or body.access_level != current_user["access_level"]
            ):
                raise HTTPException(status_code=400, detail="CANNOT_CHANGE_SELF_ACCESS")

            removes_admin = (
                target["access_level"] == "admin"
                and target["is_active"]
                and (not body.is_active or body.access_level != "admin")
            )
            if removes_admin:
                remaining_admins = connection.execute(
                    "SELECT COUNT(*) AS c FROM users WHERE access_level = 'admin' AND is_active = 1 AND id != ?",
                    (user_id,),
                ).fetchone()
                if int(remaining_admins["c"]) == 0:
                    raise HTTPException(status_code=400, detail="LAST_ADMIN")

            removes_privileged = (
                target["access_level"] in ("manager", "admin")
                and target["is_active"]
                and (not body.is_active or body.access_level == "operator")
            )
            if removes_privileged:
                remaining_privileged = connection.execute(
                    "SELECT COUNT(*) AS c FROM users WHERE access_level IN ('manager', 'admin') AND is_active = 1 AND id != ?",
                    (user_id,),
                ).fetchone()
                if int(remaining_privileged["c"]) == 0:
                    raise HTTPException(status_code=400, detail="LAST_MANAGER")

            connection.execute(
                """
                UPDATE users
                SET display_name = ?, role = ?, access_level = ?, is_active = ?
                WHERE id = ?
                """,
                (
                    display_name,
                    role_for_access_level(body.access_level),
                    body.access_level,
                    is_active_value,
                    user_id,
                ),
            )
            updated_row = connection.execute(
                """
                SELECT id, username, display_name, role, access_level, is_active, created_at
                FROM users
                WHERE id = ?
                """,
                (user_id,),
            ).fetchone()
            updated_user = serialize_admin_user(updated_row)
            write_audit_log(
                connection,
                action="user_updated",
                actor=current_user,
                target_type="user",
                target_id=updated_user["id"],
                target_label=updated_user["username"],
                details={
                    "before": {
                        "display_name": target["display_name"],
                        "access_level": target["access_level"],
                        "is_active": target["is_active"],
                    },
                    "after": {
                        "display_name": updated_user["display_name"],
                        "access_level": updated_user["access_level"],
                        "is_active": updated_user["is_active"],
                    },
                },
            )
            connection.commit()

        return {"user": updated_user}

    @router.post("/admin/users/{user_id}/password")
    def admin_reset_user_password(
        user_id: int,
        body: AdminUserPasswordResetRequest,
        request: Request,
    ) -> dict[str, str]:
        current_user = get_current_user(request)
        with get_connection() as connection:
            target_row = connection.execute(
                "SELECT id, username, display_name, access_level, is_active FROM users WHERE id = ?",
                (user_id,),
            ).fetchone()
            if not target_row:
                raise HTTPException(status_code=404, detail="USER_NOT_FOUND")
            target_user = row_to_dict(target_row)
            try:
                validate_password_strength(body.password, str(target_user["username"]))
            except AttendanceAuthError as exc:
                raise exc.to_http() from exc

            connection.execute(
                "UPDATE users SET password_hash = ?, session_token = NULL WHERE id = ?",
                (hash_password(body.password), user_id),
            )
            write_audit_log(
                connection,
                action="user_password_reset",
                actor=current_user,
                target_type="user",
                target_id=target_user["id"],
                target_label=str(target_user["username"]),
                details={
                    "target_access_level": target_user["access_level"],
                    "target_is_active": bool(target_user["is_active"]),
                    "password_expiration_notice": PASSWORD_EXPIRATION_NOTICE,
                },
            )
            connection.commit()

        return {"status": "ok", "password_expiration_notice": PASSWORD_EXPIRATION_NOTICE}

    @router.delete("/admin/users/{user_id}")
    def admin_delete_user(user_id: int, request: Request) -> dict[str, str]:
        current_user = get_current_user(request)
        if int(current_user["id"]) == int(user_id):
            raise HTTPException(status_code=400, detail="CANNOT_DELETE_SELF")

        with get_connection() as connection:
            target_row = connection.execute(
                "SELECT id, username, display_name, access_level, is_active FROM users WHERE id = ?",
                (user_id,),
            ).fetchone()
            if not target_row:
                raise HTTPException(status_code=404, detail="USER_NOT_FOUND")

            target_user = row_to_dict(target_row)

            if target_user["access_level"] in ("manager", "admin"):
                if target_user["access_level"] == "admin":
                    remaining_admins = connection.execute(
                        "SELECT COUNT(*) AS c FROM users WHERE access_level = 'admin' AND is_active = 1 AND id != ?",
                        (user_id,),
                    ).fetchone()
                    if int(remaining_admins["c"]) == 0:
                        raise HTTPException(status_code=400, detail="LAST_ADMIN")

                remaining = connection.execute(
                    "SELECT COUNT(*) AS c FROM users WHERE access_level IN ('manager', 'admin') AND is_active = 1 AND id != ?",
                    (user_id,),
                ).fetchone()
                if int(remaining["c"]) == 0:
                    raise HTTPException(status_code=400, detail="LAST_MANAGER")

            connection.execute(
                "UPDATE users SET is_active = 0, session_token = NULL WHERE id = ?",
                (user_id,),
            )
            write_audit_log(
                connection,
                action="user_deleted",
                actor=current_user,
                target_type="user",
                target_id=target_user["id"],
                target_label=str(target_user["username"]),
                details={
                    "display_name": target_user["display_name"],
                    "access_level": target_user["access_level"],
                },
            )
            connection.commit()

        return {"status": "ok"}

    @router.get("/admin/signature-config")
    def admin_get_signature_config() -> dict[str, Any]:
        """배합일지 서명 합성·스캔 파라미터(관리자 튜닝)."""
        return {
            "config": signature_config.load(),
            "defaults": signature_config.DEFAULTS,
            "ranges": {k: list(v) for k, v in signature_config.RANGES.items()},
        }

    @router.put("/admin/signature-config")
    def admin_save_signature_config(body: dict[str, Any], request: Request) -> dict[str, Any]:
        current_user = get_current_user(request)
        cfg = signature_config.save(body or {})
        with get_connection() as connection:
            write_audit_log(
                connection,
                action="signature_config_updated",
                actor=current_user,
                target_type="signature_config",
                target_label="배합일지 서명 설정",
                details=cfg,
            )
            connection.commit()
        return {"config": cfg}

    @router.get("/admin/signature-preview")
    def admin_signature_preview(worker: str | None = Query(default=None)) -> StreamingResponse:
        """현재 설정으로 합성한 샘플 배합일지 미리보기 PNG."""
        png = dhr_pdf.build_signature_preview_png(worker)
        return StreamingResponse(io.BytesIO(png), media_type="image/png")

    return router
