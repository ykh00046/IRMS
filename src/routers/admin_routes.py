from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ..auth import get_current_user, require_access_level
from ..database import get_connection, list_audit_logs, row_to_dict, utc_now_text, write_audit_log
from ..security import hash_password
from .models import (
    AdminUserCreateRequest,
    AdminUserPasswordResetRequest,
    AdminUserUpdateRequest,
    role_for_access_level,
    serialize_admin_user,
)


def build_router() -> APIRouter:
    router = APIRouter(dependencies=[Depends(require_access_level("manager"))])

    @router.get("/admin/users")
    async def admin_list_users() -> dict[str, Any]:
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
            "managers": sum(1 for item in items if item["access_level"] == "manager"),
            "operators": sum(1 for item in items if item["access_level"] == "operator"),
        }
        return {"items": items, "summary": summary, "total": len(items)}

    @router.get("/admin/audit-logs")
    async def admin_list_audit_logs(
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
        action: str | None = None,
    ) -> dict[str, Any]:
        with get_connection() as connection:
            items = list_audit_logs(connection, limit=limit, offset=offset, action=action)
        return {"items": items, "total": len(items)}

    @router.post("/admin/users")
    async def admin_create_user(body: AdminUserCreateRequest, request: Request) -> dict[str, Any]:
        current_user = get_current_user(request)
        username = body.username.strip()
        display_name = body.display_name.strip()
        now = utc_now_text()

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
    async def admin_update_user(
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
    async def admin_reset_user_password(
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

            connection.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?",
                (hash_password(body.password), user_id),
            )
            target_user = row_to_dict(target_row)
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
                },
            )
            connection.commit()

        return {"status": "ok"}

    return router
