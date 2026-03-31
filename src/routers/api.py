import csv
import hashlib
import io
from datetime import date, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..auth import (
    ACCESS_LEVEL_LABEL,
    authenticate_user,
    get_current_user,
    get_user_for_selection,
    has_access_level,
    login_user,
    logout_user,
    require_access_level,
)
from ..database import (
    get_connection,
    list_audit_logs,
    normalize_token,
    row_to_dict,
    utc_now_text,
    write_audit_log,
)
from ..security import hash_password


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=1, max_length=100)


class OperatorSelectRequest(BaseModel):
    user_id: int = Field(gt=0)


class ImportRequest(BaseModel):
    raw_text: str = Field(min_length=1)
    created_by: str = Field(default="관리자")


class StatusUpdateRequest(BaseModel):
    action: str = Field(pattern="^(start|complete|cancel)$")
    reason: str | None = None


class WeighingStepRequest(BaseModel):
    recipe_id: int = Field(gt=0)
    material_id: int = Field(gt=0)
    measured_by: str = Field(default="작업자")


class WeighingRecipeCompleteRequest(BaseModel):
    recipe_id: int = Field(gt=0)


class AdminUserCreateRequest(BaseModel):
    username: str = Field(min_length=3, max_length=50, pattern=r"^[A-Za-z0-9._-]+$")
    display_name: str = Field(min_length=1, max_length=50)
    access_level: Literal["operator", "manager", "admin"]
    password: str = Field(min_length=6, max_length=100)


class AdminUserUpdateRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=50)
    access_level: Literal["operator", "manager", "admin"]
    is_active: bool


class AdminUserPasswordResetRequest(BaseModel):
    password: str = Field(min_length=6, max_length=100)


class ChatMessageCreateRequest(BaseModel):
    room_key: Literal["notice", "mass_response", "liquid_ink_response", "sample_mass_production"]
    message_text: str = Field(min_length=1, max_length=1000)
    stage: Literal["registered", "in_progress", "completed"] | None = None


CHAT_STAGE_OPTIONS = ("registered", "in_progress", "completed")


def actor_name(current_user: dict[str, Any]) -> str:
    return str(current_user.get("display_name") or current_user.get("username") or "사용자")


def role_for_access_level(access_level: str) -> str:
    return "admin" if access_level == "admin" else "user"


def serialize_admin_user(row: Any) -> dict[str, Any]:
    payload = row_to_dict(row)
    payload["role_label"] = ACCESS_LEVEL_LABEL.get(str(payload.get("access_level")), "User")
    payload["is_active"] = bool(payload.get("is_active"))
    return payload


def recipe_label(row: dict[str, Any]) -> str:
    return f"{row.get('product_name', '-')}/{row.get('ink_name', '-')}"


def serialize_chat_room(row: Any) -> dict[str, Any]:
    payload = row_to_dict(row)
    payload["is_active"] = bool(payload.get("is_active"))
    payload["stage_required"] = payload.get("scope") == "workflow"
    payload["stage_options"] = list(CHAT_STAGE_OPTIONS) if payload["stage_required"] else []
    return payload


def serialize_chat_message(row: Any) -> dict[str, Any]:
    return row_to_dict(row)


def build_router() -> APIRouter:
    router = APIRouter(prefix="/api")
    public_router = APIRouter()
    operator_router = APIRouter(dependencies=[Depends(require_access_level("operator"))])
    manager_router = APIRouter(dependencies=[Depends(require_access_level("manager"))])
    admin_router = APIRouter(dependencies=[Depends(require_access_level("admin"))])

    @public_router.post("/auth/login")
    async def auth_login(request: Request, body: LoginRequest) -> dict[str, Any]:
        user = authenticate_user(body.username, body.password)
        if not user:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="INVALID_CREDENTIALS")
        login_user(request, user)
        with get_connection() as connection:
            write_audit_log(
                connection,
                action="auth_login",
                actor=user,
                target_type="session",
                target_id=user["id"],
                target_label=user["username"],
                details={"entry_point": "legacy_login"},
            )
            connection.commit()
        return {"user": user}

    @public_router.post("/auth/management-login")
    async def auth_management_login(request: Request, body: LoginRequest) -> dict[str, Any]:
        user = authenticate_user(body.username, body.password)
        if not user or not has_access_level(user, "manager"):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="INVALID_CREDENTIALS")
        login_user(request, user)
        with get_connection() as connection:
            write_audit_log(
                connection,
                action="management_login",
                actor=user,
                target_type="session",
                target_id=user["id"],
                target_label=user["username"],
                details={"entry_point": "management_login"},
            )
            connection.commit()
        return {"user": user}

    @public_router.post("/auth/operator-select")
    async def auth_operator_select(request: Request, body: OperatorSelectRequest) -> dict[str, Any]:
        user = get_user_for_selection(body.user_id, ("operator",))
        if not user:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="OPERATOR_NOT_FOUND")
        login_user(request, user)
        with get_connection() as connection:
            write_audit_log(
                connection,
                action="operator_select",
                actor=user,
                target_type="session",
                target_id=user["id"],
                target_label=user["username"],
                details={"entry_point": "weighing_select"},
            )
            connection.commit()
        return {"user": user}

    @public_router.post("/auth/logout")
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

    @operator_router.get("/auth/me")
    async def auth_me(request: Request) -> dict[str, Any]:
        return {"user": get_current_user(request)}

    @operator_router.get("/notifications/recipe-imports")
    async def recipe_import_notifications(
        after_id: int = Query(default=0, ge=0),
        limit: int = Query(default=10, ge=1, le=100),
        latest: bool = Query(default=False),
    ) -> dict[str, Any]:
        with get_connection() as connection:
            items = list_audit_logs(
                connection,
                limit=limit,
                action="recipes_imported",
                after_id=after_id,
                ascending=not latest,
            )

        latest_id = max((int(item["id"]) for item in items), default=after_id)
        return {"items": items, "total": len(items), "latest_id": latest_id}

    @admin_router.get("/admin/users")
    async def admin_list_users() -> dict[str, Any]:
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT id, username, display_name, role, access_level, is_active, created_at
                FROM users
                ORDER BY
                    CASE access_level
                        WHEN 'admin' THEN 0
                        WHEN 'manager' THEN 1
                        ELSE 2
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

    @admin_router.get("/admin/audit-logs")
    async def admin_list_audit_logs(
        limit: int = Query(default=100, ge=1, le=500),
        action: str | None = None,
    ) -> dict[str, Any]:
        with get_connection() as connection:
            items = list_audit_logs(connection, limit=limit, action=action)
        return {"items": items, "total": len(items)}

    @manager_router.get("/recipes/progress")
    async def recipe_progress(
        status_filter: str = Query(default="active"),
    ) -> dict[str, Any]:
        allowed_filters = {"active", "all", "pending", "in_progress", "completed", "canceled"}
        normalized_filter = status_filter.strip().lower()
        if normalized_filter not in allowed_filters:
            raise HTTPException(status_code=400, detail="INVALID_STATUS_FILTER")

        where_parts: list[str] = []
        params: list[Any] = []
        if normalized_filter == "active":
            where_parts.append("r.status IN ('pending', 'in_progress')")
        elif normalized_filter != "all":
            where_parts.append("r.status = ?")
            params.append(normalized_filter)

        where_sql = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""

        with get_connection() as connection:
            recipe_rows = connection.execute(
                f"""
                SELECT
                    r.id,
                    r.product_name,
                    r.position,
                    r.ink_name,
                    r.status,
                    r.created_by,
                    r.created_at,
                    r.completed_at,
                    r.started_by,
                    r.started_at
                FROM recipes r
                {where_sql}
                ORDER BY
                    CASE r.status
                        WHEN 'in_progress' THEN 0
                        WHEN 'pending' THEN 1
                        WHEN 'completed' THEN 2
                        ELSE 3
                    END,
                    COALESCE(r.started_at, r.created_at) DESC,
                    r.id DESC
                """,
                params,
            ).fetchall()

            recipe_ids = [int(row["id"]) for row in recipe_rows]
            item_rows = connection.execute(
                """
                SELECT
                    ri.recipe_id,
                    ri.material_id,
                    m.name AS material_name,
                    m.unit,
                    m.color_group,
                    COALESCE(ri.value_weight, ri.value_text) AS target_value,
                    ri.measured_at,
                    ri.measured_by
                FROM recipe_items ri
                JOIN materials m ON m.id = ri.material_id
                WHERE ri.recipe_id IN ({ids})
                ORDER BY
                    ri.recipe_id ASC,
                    CASE m.color_group
                        WHEN 'black' THEN 1
                        WHEN 'red' THEN 2
                        WHEN 'blue' THEN 3
                        WHEN 'yellow' THEN 4
                        ELSE 5
                    END,
                    m.name ASC
                """.format(
                    ids=", ".join("?" for _ in recipe_ids) if recipe_ids else "NULL"
                ),
                recipe_ids,
            ).fetchall() if recipe_ids else []

        item_map: dict[int, list[dict[str, Any]]] = {}
        for item_row in item_rows:
            item_map.setdefault(int(item_row["recipe_id"]), []).append(row_to_dict(item_row))

        items: list[dict[str, Any]] = []
        summary = {
            "total_recipes": len(recipe_rows),
            "active_recipes": 0,
            "in_progress_recipes": 0,
            "pending_recipes": 0,
            "completed_recipes": 0,
            "remaining_steps": 0,
            "open_positions": 0,
        }
        active_positions: set[str] = set()

        for recipe_row in recipe_rows:
            payload = row_to_dict(recipe_row)
            recipe_items = item_map.get(int(recipe_row["id"]), [])
            total_steps = len(recipe_items)
            completed_items = [item for item in recipe_items if item.get("measured_at")]
            remaining_items = [item for item in recipe_items if not item.get("measured_at")]
            next_item = remaining_items[0] if remaining_items else None
            last_completed = completed_items[-1] if completed_items else None
            completed_steps = len(completed_items)
            remaining_steps = len(remaining_items)
            progress_pct = round((completed_steps / total_steps) * 100, 1) if total_steps else 0.0

            payload.update(
                {
                    "total_steps": total_steps,
                    "completed_steps": completed_steps,
                    "remaining_steps": remaining_steps,
                    "progress_pct": progress_pct,
                    "next_item": next_item,
                    "remaining_materials": [
                        item["material_name"] for item in remaining_items
                    ],
                    "last_completed_item": last_completed,
                }
            )
            items.append(payload)

            if payload["status"] in {"pending", "in_progress"}:
                summary["active_recipes"] += 1
                summary["remaining_steps"] += remaining_steps
                if payload.get("position"):
                    active_positions.add(str(payload["position"]))
            if payload["status"] == "in_progress":
                summary["in_progress_recipes"] += 1
            elif payload["status"] == "pending":
                summary["pending_recipes"] += 1
            elif payload["status"] == "completed":
                summary["completed_recipes"] += 1

        summary["open_positions"] = len(active_positions)
        return {"status_filter": normalized_filter, "summary": summary, "items": items}

    @operator_router.get("/chat/rooms")
    async def list_chat_rooms() -> dict[str, Any]:
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT
                    cr.key,
                    cr.name,
                    cr.scope,
                    cr.sort_order,
                    cr.is_active,
                    COUNT(cm.id) AS message_count,
                    MAX(cm.created_at) AS latest_message_at
                FROM chat_rooms cr
                LEFT JOIN chat_messages cm ON cm.room_key = cr.key
                WHERE cr.is_active = 1
                GROUP BY cr.key, cr.name, cr.scope, cr.sort_order, cr.is_active
                ORDER BY cr.sort_order ASC, cr.name ASC
                """
            ).fetchall()

        items = [serialize_chat_room(row) for row in rows]
        return {"items": items, "total": len(items)}

    @operator_router.get("/chat/messages")
    async def list_chat_messages(
        room_key: str = Query(..., min_length=1),
        limit: int = Query(default=60, ge=1, le=200),
        after_id: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        safe_room_key = room_key.strip()
        with get_connection() as connection:
            room_row = connection.execute(
                """
                SELECT key, name, scope, sort_order, is_active
                FROM chat_rooms
                WHERE key = ? AND is_active = 1
                """,
                (safe_room_key,),
            ).fetchone()
            if not room_row:
                raise HTTPException(status_code=404, detail="CHAT_ROOM_NOT_FOUND")

            if after_id > 0:
                rows = connection.execute(
                    """
                    SELECT
                        id,
                        room_key,
                        message_text,
                        stage,
                        created_by_user_id,
                        created_by_username,
                        created_by_display_name,
                        created_at
                    FROM chat_messages
                    WHERE room_key = ? AND id > ?
                    ORDER BY id ASC
                    LIMIT ?
                    """,
                    (safe_room_key, after_id, limit),
                ).fetchall()
            else:
                recent_rows = connection.execute(
                    """
                    SELECT
                        id,
                        room_key,
                        message_text,
                        stage,
                        created_by_user_id,
                        created_by_username,
                        created_by_display_name,
                        created_at
                    FROM chat_messages
                    WHERE room_key = ?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (safe_room_key, limit),
                ).fetchall()
                rows = list(reversed(recent_rows))

            latest_id = int(
                connection.execute(
                    "SELECT COALESCE(MAX(id), 0) AS max_id FROM chat_messages WHERE room_key = ?",
                    (safe_room_key,),
                ).fetchone()["max_id"]
            )

        items = [serialize_chat_message(row) for row in rows]
        return {
            "room": serialize_chat_room(room_row),
            "items": items,
            "total": len(items),
            "latest_id": latest_id,
        }

    @operator_router.post("/chat/messages")
    async def create_chat_message(
        body: ChatMessageCreateRequest,
        request: Request,
    ) -> dict[str, Any]:
        current_user = get_current_user(request)
        message_text = body.message_text.strip()
        stage = body.stage
        if not message_text:
            raise HTTPException(status_code=400, detail="CHAT_MESSAGE_REQUIRED")

        with get_connection() as connection:
            room_row = connection.execute(
                """
                SELECT key, name, scope, sort_order, is_active
                FROM chat_rooms
                WHERE key = ? AND is_active = 1
                """,
                (body.room_key,),
            ).fetchone()
            if not room_row:
                raise HTTPException(status_code=404, detail="CHAT_ROOM_NOT_FOUND")

            room = serialize_chat_room(room_row)
            if room["stage_required"] and not stage:
                raise HTTPException(status_code=400, detail="CHAT_STAGE_REQUIRED")
            if not room["stage_required"]:
                stage = None

            created_at = utc_now_text()
            cursor = connection.execute(
                """
                INSERT INTO chat_messages (
                    room_key,
                    message_text,
                    stage,
                    created_by_user_id,
                    created_by_username,
                    created_by_display_name,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    room["key"],
                    message_text,
                    stage,
                    current_user["id"],
                    current_user["username"],
                    actor_name(current_user),
                    created_at,
                ),
            )
            message_id = cursor.lastrowid
            row = connection.execute(
                """
                SELECT
                    id,
                    room_key,
                    message_text,
                    stage,
                    created_by_user_id,
                    created_by_username,
                    created_by_display_name,
                    created_at
                FROM chat_messages
                WHERE id = ?
                """,
                (message_id,),
            ).fetchone()
            write_audit_log(
                connection,
                action="chat_message_posted",
                actor=current_user,
                target_type="chat_room",
                target_id=room["key"],
                target_label=room["name"],
                details={
                    "message_id": message_id,
                    "stage": stage,
                },
            )
            connection.commit()

        return {"room": room, "message": serialize_chat_message(row)}

    @admin_router.post("/admin/users")
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

    @admin_router.patch("/admin/users/{user_id}")
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
            removing_admin_access = target["access_level"] == "admin" and (
                body.access_level != "admin" or not body.is_active
            )

            if int(target["id"]) == int(current_user["id"]) and removing_admin_access:
                raise HTTPException(status_code=400, detail="SELF_ADMIN_LOCKOUT")

            if removing_admin_access:
                active_admin_count = int(
                    connection.execute(
                        """
                        SELECT COUNT(*) AS count
                        FROM users
                        WHERE access_level = 'admin' AND is_active = 1
                        """
                    ).fetchone()["count"]
                )
                if active_admin_count <= 1:
                    raise HTTPException(status_code=400, detail="LAST_ACTIVE_ADMIN_REQUIRED")

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

    @admin_router.post("/admin/users/{user_id}/password")
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

    @operator_router.get("/materials")
    async def list_materials() -> dict[str, Any]:
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT id, name, unit_type, unit, color_group, category, is_active
                FROM materials
                WHERE is_active = 1
                ORDER BY name
                """
            ).fetchall()

            alias_rows = connection.execute(
                """
                SELECT material_id, alias_name
                FROM material_aliases
                ORDER BY alias_name
                """
            ).fetchall()

        alias_map: dict[int, list[str]] = {}
        for alias_row in alias_rows:
            alias_map.setdefault(alias_row["material_id"], []).append(alias_row["alias_name"])

        items = []
        for row in rows:
            payload = row_to_dict(row)
            payload["aliases"] = alias_map.get(row["id"], [])
            items.append(payload)

        return {"items": items, "total": len(items)}

    @operator_router.get("/recipes")
    async def list_recipes(
        status: str | None = None,
        search: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> dict[str, Any]:
        where_parts = []
        params: list[Any] = []

        if status:
            where_parts.append("r.status = ?")
            params.append(status)

        if search:
            where_parts.append("(r.product_name LIKE ? OR r.ink_name LIKE ?)")
            token = f"%{search.strip()}%"
            params.extend([token, token])

        if date_from:
            where_parts.append("date(r.created_at) >= ?")
            params.append(str(date_from))

        if date_to:
            where_parts.append("date(r.created_at) <= ?")
            params.append(str(date_to))

        where_sql = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""

        with get_connection() as connection:
            recipe_rows = connection.execute(
                f"""
                SELECT r.id, r.product_name, r.position, r.ink_name, r.status, r.created_by, r.created_at, r.completed_at
                FROM recipes r
                {where_sql}
                ORDER BY r.created_at DESC, r.id DESC
                """,
                params,
            ).fetchall()

            recipe_ids = [row["id"] for row in recipe_rows]
            item_rows = connection.execute(
                """
                SELECT
                    ri.recipe_id,
                    ri.material_id,
                    m.name AS material_name,
                    m.unit_type,
                    m.unit,
                    m.color_group,
                    COALESCE(ri.value_weight, ri.value_text) AS value
                FROM recipe_items ri
                JOIN materials m ON m.id = ri.material_id
                WHERE ri.recipe_id IN ({ids})
                ORDER BY m.name ASC
                """.format(
                    ids=", ".join("?" for _ in recipe_ids) if recipe_ids else "NULL"
                ),
                recipe_ids,
            ).fetchall() if recipe_ids else []

        item_map: dict[int, list[dict[str, Any]]] = {}
        for item_row in item_rows:
            entry = {
                "material_id": item_row["material_id"],
                "material_name": item_row["material_name"],
                "unit_type": item_row["unit_type"],
                "unit": item_row["unit"],
                "color_group": item_row["color_group"],
                "value": item_row["value"],
            }
            item_map.setdefault(item_row["recipe_id"], []).append(entry)

        items = []
        for recipe_row in recipe_rows:
            payload = row_to_dict(recipe_row)
            payload["items"] = item_map.get(recipe_row["id"], [])
            items.append(payload)

        return {"items": items, "total": len(items)}

    @operator_router.patch("/recipes/{recipe_id}/status")
    async def update_recipe_status(
        recipe_id: int,
        body: StatusUpdateRequest,
        request: Request,
    ) -> dict[str, Any]:
        current_user = get_current_user(request)
        transition_map = {
            "start": ("pending", "in_progress"),
            "complete": ("in_progress", "completed"),
            "cancel": ("pending", "canceled"),
        }
        actor = actor_name(current_user)

        with get_connection() as connection:
            row = connection.execute(
                "SELECT id, product_name, ink_name, status FROM recipes WHERE id = ?",
                (recipe_id,),
            ).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Recipe not found")

            current_status = row["status"]
            if body.action == "complete" and current_status == "pending":
                allowed = True
                next_status = "completed"
            elif body.action == "cancel" and current_status == "in_progress":
                allowed = True
                next_status = "canceled"
            else:
                from_status, next_status = transition_map[body.action]
                allowed = current_status == from_status

            if not allowed:
                raise HTTPException(status_code=409, detail="INVALID_STATUS_TRANSITION")

            now = utc_now_text()
            completed_at = now if next_status == "completed" else None

            # C-2: pending -> completed 시 started_by/at 자동 기록 (추적성 보전)
            if body.action == "complete" and current_status == "pending":
                connection.execute(
                    "UPDATE recipes SET started_by = ?, started_at = ? WHERE id = ? AND started_at IS NULL",
                    (actor, now, recipe_id),
                )

            # C-2: start 시 started_by/at 기록
            if body.action == "start":
                connection.execute(
                    "UPDATE recipes SET started_by = ?, started_at = ? WHERE id = ? AND started_at IS NULL",
                    (actor, now, recipe_id),
                )

            connection.execute(
                "UPDATE recipes SET status = ?, completed_at = ? WHERE id = ?",
                (next_status, completed_at, recipe_id),
            )

            # cancel 시 reason 저장
            if next_status == "canceled" and body.reason:
                connection.execute(
                    "UPDATE recipes SET cancel_reason = ? WHERE id = ?",
                    (body.reason, recipe_id),
                )

            write_audit_log(
                connection,
                action="recipe_status_updated",
                actor=current_user,
                target_type="recipe",
                target_id=recipe_id,
                target_label=recipe_label(row_to_dict(row)),
                details={
                    "action": body.action,
                    "from_status": current_status,
                    "to_status": next_status,
                    "reason": body.reason,
                },
            )

            connection.commit()

            updated = connection.execute(
                """
                SELECT id, product_name, position, ink_name, status, created_by, created_at, completed_at
                FROM recipes
                WHERE id = ?
                """,
                (recipe_id,),
            ).fetchone()

        return row_to_dict(updated)

    @operator_router.get("/weighing/queue")
    async def get_weighing_queue(
        color_group: str | None = Query(default=None),
    ) -> dict[str, Any]:
        allowed_groups = {"all", "black", "red", "blue", "yellow", "none"}
        group_filter = (color_group or "all").strip().lower()
        if group_filter not in allowed_groups:
            raise HTTPException(status_code=400, detail="INVALID_COLOR_GROUP")

        where_parts = [
            "r.status IN ('pending', 'in_progress')",
            "ri.measured_at IS NULL",
        ]
        params: list[Any] = []
        if group_filter != "all":
            where_parts.append("m.color_group = ?")
            params.append(group_filter)

        with get_connection() as connection:
            rows = connection.execute(
                f"""
                SELECT
                    r.id AS recipe_id,
                    r.product_name,
                    r.position,
                    r.ink_name,
                    r.status AS recipe_status,
                    r.created_at,
                    ri.material_id,
                    m.name AS material_name,
                    m.unit_type,
                    m.unit,
                    m.color_group,
                    COALESCE(ri.value_weight, ri.value_text) AS target_value
                FROM recipes r
                JOIN recipe_items ri ON ri.recipe_id = r.id
                JOIN materials m ON m.id = ri.material_id
                WHERE {" AND ".join(where_parts)}
                ORDER BY
                    CASE m.color_group
                        WHEN 'black' THEN 1
                        WHEN 'red' THEN 2
                        WHEN 'blue' THEN 3
                        WHEN 'yellow' THEN 4
                        ELSE 5
                    END,
                    m.name ASC,
                    r.created_at ASC,
                    r.id ASC
                """,
                params,
            ).fetchall()

        items: list[dict[str, Any]] = []
        by_color = {"black": 0, "red": 0, "blue": 0, "yellow": 0, "none": 0}
        recipe_ids: set[int] = set()

        for index, row in enumerate(rows, start=1):
            payload = row_to_dict(row)
            payload["sequence"] = index
            items.append(payload)
            recipe_ids.add(row["recipe_id"])
            color = row["color_group"] or "none"
            if color not in by_color:
                by_color["none"] += 1
            else:
                by_color[color] += 1

        return {
            "items": items,
            "summary": {
                "total_steps": len(items),
                "recipe_count": len(recipe_ids),
                "by_color": by_color,
            },
            "color_group": group_filter,
        }

    @operator_router.post("/weighing/step/complete")
    async def complete_weighing_step(
        body: WeighingStepRequest,
        request: Request,
    ) -> dict[str, Any]:
        current_user = get_current_user(request)
        measured_by = actor_name(current_user)
        with get_connection() as connection:
            recipe_row = connection.execute(
                """
                SELECT id, product_name, ink_name, status
                FROM recipes
                WHERE id = ?
                """,
                (body.recipe_id,),
            ).fetchone()
            if not recipe_row:
                raise HTTPException(status_code=404, detail="Recipe not found")

            if recipe_row["status"] not in {"pending", "in_progress"}:
                raise HTTPException(status_code=409, detail="RECIPE_NOT_ACTIVE")

            item_row = connection.execute(
                """
                SELECT
                    ri.id,
                    m.name AS material_name,
                    COALESCE(ri.value_weight, ri.value_text) AS target_value
                FROM recipe_items ri
                JOIN materials m ON m.id = ri.material_id
                WHERE ri.recipe_id = ? AND ri.material_id = ?
                """,
                (body.recipe_id, body.material_id),
            ).fetchone()
            if not item_row:
                raise HTTPException(status_code=404, detail="Recipe item not found")

            measured_at = utc_now_text()

            if recipe_row["status"] == "pending":
                connection.execute(
                    """
                    UPDATE recipes
                    SET status = 'in_progress', completed_at = NULL
                    WHERE id = ?
                    """,
                    (body.recipe_id,),
                )

            update_cursor = connection.execute(
                """
                UPDATE recipe_items
                SET measured_at = ?, measured_by = ?
                WHERE recipe_id = ? AND material_id = ? AND measured_at IS NULL
                """,
                (measured_at, measured_by, body.recipe_id, body.material_id),
            )
            if update_cursor.rowcount == 0:
                raise HTTPException(status_code=409, detail="STEP_ALREADY_COMPLETED")

            remaining_in_recipe = int(
                connection.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM recipe_items
                    WHERE recipe_id = ? AND measured_at IS NULL
                    """,
                    (body.recipe_id,),
                ).fetchone()["count"]
            )

            remaining_total = int(
                connection.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM recipe_items ri
                    JOIN recipes r ON r.id = ri.recipe_id
                    WHERE r.status IN ('pending', 'in_progress') AND ri.measured_at IS NULL
                    """
                ).fetchone()["count"]
            )

            recipe_payload = row_to_dict(recipe_row)
            item_payload = row_to_dict(item_row)
            write_audit_log(
                connection,
                action="weighing_step_completed",
                actor=current_user,
                target_type="recipe_item",
                target_id=f"{body.recipe_id}:{body.material_id}",
                target_label=f"{recipe_label(recipe_payload)} · {item_payload['material_name']}",
                details={
                    "recipe_id": body.recipe_id,
                    "material_id": body.material_id,
                    "material_name": item_payload["material_name"],
                    "target_value": item_payload["target_value"],
                    "measured_by": measured_by,
                    "measured_at": measured_at,
                    "remaining_in_recipe": remaining_in_recipe,
                    "remaining_total": remaining_total,
                },
            )

            connection.commit()

        return {
            "recipe_id": body.recipe_id,
            "material_id": body.material_id,
            "measured_at": measured_at,
            "remaining_in_recipe": remaining_in_recipe,
            "remaining_total": remaining_total,
            "ready_for_recipe_completion": remaining_in_recipe == 0,
        }

    @operator_router.post("/weighing/recipe/complete")
    async def complete_weighing_recipe(
        body: WeighingRecipeCompleteRequest,
        request: Request,
    ) -> dict[str, Any]:
        current_user = get_current_user(request)
        with get_connection() as connection:
            row = connection.execute(
                """
                SELECT id, product_name, position, ink_name, status, created_by, created_at, completed_at
                FROM recipes
                WHERE id = ?
                """,
                (body.recipe_id,),
            ).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Recipe not found")
            if row["status"] in {"completed", "canceled"}:
                raise HTTPException(status_code=409, detail="RECIPE_ALREADY_CLOSED")

            remaining_count = int(
                connection.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM recipe_items
                    WHERE recipe_id = ? AND measured_at IS NULL
                    """,
                    (body.recipe_id,),
                ).fetchone()["count"]
            )
            if remaining_count > 0:
                raise HTTPException(
                    status_code=409,
                    detail={"code": "REMAINING_WEIGHING_STEPS", "remaining_count": remaining_count},
                )

            completed_at = utc_now_text()
            connection.execute(
                """
                UPDATE recipes
                SET status = 'completed', completed_at = ?
                WHERE id = ?
                """,
                (completed_at, body.recipe_id),
            )
            write_audit_log(
                connection,
                action="recipe_weighing_completed",
                actor=current_user,
                target_type="recipe",
                target_id=body.recipe_id,
                target_label=recipe_label(row_to_dict(row)),
                details={
                    "completed_at": completed_at,
                    "remaining_count": remaining_count,
                },
            )
            connection.commit()

            updated = connection.execute(
                """
                SELECT id, product_name, position, ink_name, status, created_by, created_at, completed_at
                FROM recipes
                WHERE id = ?
                """,
                (body.recipe_id,),
            ).fetchone()

        return row_to_dict(updated)

    @manager_router.post("/recipes/import/preview")
    async def import_preview(body: ImportRequest) -> dict[str, Any]:
        with get_connection() as connection:
            result = parse_import_text(connection, body.raw_text)
        return result

    @manager_router.post("/recipes/import")
    async def import_recipes(
        body: ImportRequest,
        request: Request,
    ) -> dict[str, Any]:
        current_user = get_current_user(request)
        creator_name = actor_name(current_user)
        with get_connection() as connection:
            parsed = parse_import_text(connection, body.raw_text)
            if parsed["errors"]:
                raise HTTPException(status_code=400, detail={"errors": parsed["errors"]})

            created_ids = []
            now = utc_now_text()
            raw_hash = hashlib.sha256(body.raw_text.encode()).hexdigest()

            for parsed_row in parsed["parsed_rows"]:
                cursor = connection.execute(
                    """
                    INSERT INTO recipes (
                        product_name, position, ink_name, status, created_by, created_at, completed_at,
                        raw_input_hash, raw_input_text
                    ) VALUES (?, ?, ?, 'pending', ?, ?, NULL, ?, ?)
                    """,
                    (
                        parsed_row["product_name"],
                        parsed_row["position"],
                        parsed_row["ink_name"],
                        creator_name,
                        now,
                        raw_hash,
                        body.raw_text,
                    ),
                )
                recipe_id = cursor.lastrowid
                created_ids.append(recipe_id)

                for item in parsed_row["items"]:
                    connection.execute(
                        """
                        INSERT INTO recipe_items (recipe_id, material_id, value_weight, value_text)
                        VALUES (?, ?, ?, ?)
                        """,
                        (recipe_id, item["material_id"], item["value_weight"], item["value_text"]),
                    )

            write_audit_log(
                connection,
                action="recipes_imported",
                actor=current_user,
                target_type="recipe_batch",
                target_label=f"{len(created_ids)} recipes",
                details={
                    "created_count": len(created_ids),
                    "created_ids": created_ids,
                    "warnings_count": len(parsed["warnings"]),
                    "raw_hash": raw_hash,
                },
            )

            connection.commit()

        return {
            "created_count": len(created_ids),
            "created_ids": created_ids,
            "warnings": parsed["warnings"],
        }

    @manager_router.get("/stats/consumption")
    async def stats_consumption(
        date_from: date = Query(...),
        date_to: date = Query(...),
        color_group: str | None = None,
        category: str | None = None,
    ) -> dict[str, Any]:
        where_parts = ["r.status = 'completed'", "date(r.completed_at) >= ?", "date(r.completed_at) <= ?"]
        params: list[Any] = [str(date_from), str(date_to)]

        if color_group:
            where_parts.append("m.color_group = ?")
            params.append(color_group)

        if category:
            where_parts.append("m.category = ?")
            params.append(category)

        where_sql = " AND ".join(where_parts)

        with get_connection() as connection:
            rows = connection.execute(
                f"""
                SELECT
                    m.id AS material_id,
                    m.name AS material_name,
                    m.unit_type,
                    m.unit,
                    m.color_group,
                    m.category,
                    SUM(CASE WHEN m.unit_type = 'weight' THEN COALESCE(ri.value_weight, 0) ELSE 0 END) AS total_weight,
                    SUM(CASE WHEN m.unit_type = 'count' AND ri.value_text IS NOT NULL THEN 1 ELSE 0 END) AS total_count,
                    COUNT(DISTINCT r.id) AS recipe_count
                FROM recipes r
                JOIN recipe_items ri ON ri.recipe_id = r.id
                JOIN materials m ON m.id = ri.material_id
                WHERE {where_sql}
                GROUP BY m.id, m.name, m.unit_type, m.unit, m.color_group, m.category
                ORDER BY m.name
                """,
                params,
            ).fetchall()

        items = [row_to_dict(row) for row in rows]
        total_weight = float(sum((row.get("total_weight") or 0) for row in items))
        total_count = float(sum((row.get("total_count") or 0) for row in items))

        with get_connection() as connection:
            summary_where_parts = ["r.status = 'completed'", "date(r.completed_at) >= ?", "date(r.completed_at) <= ?"]
            summary_params: list[Any] = [str(date_from), str(date_to)]
            if color_group:
                summary_where_parts.append("m.color_group = ?")
                summary_params.append(color_group)
            if category:
                summary_where_parts.append("m.category = ?")
                summary_params.append(category)

            summary_row = connection.execute(
                f"""
                SELECT COUNT(DISTINCT r.id) AS completed_recipes
                FROM recipes r
                JOIN recipe_items ri ON ri.recipe_id = r.id
                JOIN materials m ON m.id = ri.material_id
                WHERE {' AND '.join(summary_where_parts)}
                """,
                summary_params,
            ).fetchone()

        return {
            "period": {"from": str(date_from), "to": str(date_to)},
            "summary": {
                "completed_recipes": int(summary_row["completed_recipes"]) if summary_row else 0,
                "active_materials": len(items),
                "total_weight": total_weight,
                "total_count": total_count,
            },
            "items": items,
        }

    @manager_router.get("/stats/export")
    async def stats_export(
        date_from: date = Query(...),
        date_to: date = Query(...),
        color_group: str | None = None,
        category: str | None = None,
    ) -> StreamingResponse:
        response = await stats_consumption(date_from, date_to, color_group, category)
        rows = response["items"]

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["material_name", "color_group", "category", "unit_type", "unit", "total_weight", "total_count", "recipe_count"])
        for row in rows:
            writer.writerow(
                [
                    row["material_name"],
                    row["color_group"],
                    row["category"],
                    row["unit_type"],
                    row["unit"],
                    row["total_weight"],
                    row["total_count"],
                    row["recipe_count"],
                ]
            )

        output.seek(0)
        filename = f"irms-stats-{date_from}-{date_to}.csv"
        return StreamingResponse(
            output,
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @public_router.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "time": utc_now_text()}

    router.include_router(public_router)
    router.include_router(operator_router)
    router.include_router(manager_router)
    router.include_router(admin_router)
    return router


def parse_import_text(connection, raw_text: str) -> dict[str, Any]:
    lines = [line.rstrip() for line in raw_text.splitlines() if line.strip()]
    errors = []
    warnings = []

    if not lines:
        return {
            "status": "error",
            "errors": [{"level": 1, "message": "데이터가 없습니다.", "row": 0}],
            "warnings": [],
            "preview": {"headers": [], "rows": []},
            "parsed_rows": [],
        }

    material_rows = connection.execute(
        """
        SELECT m.id, m.name, m.unit_type, m.unit, m.color_group, m.category, a.alias_name
        FROM materials m
        LEFT JOIN material_aliases a ON a.material_id = m.id
        WHERE m.is_active = 1
        """
    ).fetchall()

    token_to_material = {}
    for row in material_rows:
        base_payload = {
            "id": row["id"],
            "name": row["name"],
            "unit_type": row["unit_type"],
            "unit": row["unit"],
            "color_group": row["color_group"],
            "category": row["category"],
        }
        token_to_material[normalize_token(row["name"])] = base_payload
        if row["alias_name"]:
            token_to_material[normalize_token(row["alias_name"])] = base_payload

    required_map = {
        "product_name": ["제품명", "PRODUCTNAME"],
        "position": ["위치", "POSITION"],
        "ink_name": ["잉크명", "INKNAME"],
    }
    required_tokens = {
        key: {normalize_token(candidate) for candidate in candidates}
        for key, candidates in required_map.items()
    }

    def get_header_config(row_cells, current_row_index, previous_config):
        normalized_headers = [normalize_token(c) for c in row_cells]

        explicit_req_indexes = {}
        for key, norm_cand in required_tokens.items():
            idx = next((i for i, val in enumerate(normalized_headers) if val in norm_cand), -1)
            if idx >= 0:
                explicit_req_indexes[key] = idx

        if not explicit_req_indexes:
            return None

        req_indexes = dict(explicit_req_indexes)
        prev_req = previous_config["required_indexes"] if previous_config else {}

        # 부분/하이브리드 헤더에서도 누락된 필수 인덱스를 추론한다.
        if "product_name" not in req_indexes:
            if "product_name" in prev_req:
                req_indexes["product_name"] = prev_req["product_name"]
            elif "position" in req_indexes and req_indexes["position"] > 0:
                req_indexes["product_name"] = req_indexes["position"] - 1
            elif "ink_name" in req_indexes and req_indexes["ink_name"] >= 2:
                req_indexes["product_name"] = req_indexes["ink_name"] - 2

        if "position" not in req_indexes:
            if "position" in prev_req:
                req_indexes["position"] = prev_req["position"]
            elif "ink_name" in req_indexes and req_indexes["ink_name"] >= 1:
                req_indexes["position"] = req_indexes["ink_name"] - 1
            elif "product_name" in req_indexes and req_indexes["product_name"] + 1 < len(row_cells):
                req_indexes["position"] = req_indexes["product_name"] + 1

        if "ink_name" not in req_indexes and "ink_name" in prev_req:
            req_indexes["ink_name"] = prev_req["ink_name"]

        if set(req_indexes.keys()) != {"product_name", "position", "ink_name"}:
            return None

        if len(set(req_indexes.values())) < 3:
            return None

        ink_index = req_indexes["ink_name"]
        trailing_non_empty = [
            idx for idx, value in enumerate(row_cells)
            if idx > ink_index and value.strip()
        ]
        non_empty_before_ink = [
            idx for idx, value in enumerate(row_cells)
            if idx < ink_index and value.strip()
        ]

        explicit_count = len(explicit_req_indexes)
        has_partial_pair = "position" in explicit_req_indexes and "ink_name" in explicit_req_indexes
        is_hybrid = explicit_count == 1 and "ink_name" in explicit_req_indexes

        is_header = False
        if explicit_count >= 3:
            is_header = True
        elif has_partial_pair and trailing_non_empty:
            is_header = True
        elif is_hybrid and trailing_non_empty and (len(non_empty_before_ink) >= 2 or previous_config):
            is_header = True

        if not is_header:
            return None

        mat_cols = []
        header_warnings = []
        req_index_set = set(req_indexes.values())

        for idx, header in enumerate(row_cells):
            if idx in req_index_set:
                continue
            if idx <= ink_index:
                continue
            if not header.strip():
                continue

            mat = token_to_material.get(normalize_token(header))
            if not mat:
                header_warnings.append({"level": 3, "message": f"미등록 원재료(무시됨): {header}", "row": current_row_index})
                continue

            mat_cols.append({"index": idx, "header": header, "material": mat})

        return {
            "required_indexes": req_indexes,
            "material_columns": mat_cols,
            "warnings": header_warnings,
            "headers": row_cells,
            "seed_values": {
                key: row_cells[index]
                for key, index in req_indexes.items()
                if index < len(row_cells)
                and row_cells[index].strip()
                and normalize_token(row_cells[index]) not in required_tokens[key]
            },
            "reset_carry": "product_name" in explicit_req_indexes,
        }

    parsed_rows = []
    preview_rows = []

    current_config = None
    last_product_name = ""
    last_position = ""
    global_headers = []

    for row_index, line in enumerate(lines, start=1):
        cells = [cell.strip() for cell in line.split("\t")]

        new_config = get_header_config(cells, row_index, current_config)
        if new_config:
            current_config = new_config
            if new_config["reset_carry"]:
                last_product_name = ""
                last_position = ""
            if new_config["seed_values"].get("product_name"):
                last_product_name = new_config["seed_values"]["product_name"].strip()
            if new_config["seed_values"].get("position"):
                last_position = new_config["seed_values"]["position"].strip()
            warnings.extend(new_config["warnings"])
            if not global_headers:
                global_headers = new_config["headers"]
            continue

        if not current_config:
            continue

        req_idx = current_config["required_indexes"]
        prod = cells[req_idx["product_name"]] if req_idx["product_name"] < len(cells) else ""
        pos = cells[req_idx["position"]] if req_idx["position"] < len(cells) else ""
        ink = cells[req_idx["ink_name"]] if req_idx["ink_name"] < len(cells) else ""

        if not prod:
            prod = last_product_name
        else:
            last_product_name = prod

        if not pos:
            pos = last_position
        else:
            last_position = pos

        if not pos and not ink:
            continue

        if not prod or not pos or not ink:
            errors.append({"level": 2, "message": "필드 누락 (제품명, 위치, 잉크명)", "row": row_index})

        row_items = []
        preview_items = []

        for col in current_config["material_columns"]:
            idx = col["index"]
            val = cells[idx] if idx < len(cells) else ""
            if not val:
                continue

            mat = col["material"]
            norm_val = val.replace(",", "")

            numeric_value = None
            try:
                numeric_value = float(norm_val)
            except ValueError:
                pass

            if mat["unit_type"] == "weight" and numeric_value is None:
                errors.append({"level": 2, "message": f"숫자 입력 필요: {mat['name']} ({val})", "row": row_index})
                continue

            if numeric_value is not None and numeric_value < 0:
                errors.append({"level": 2, "message": f"음수 값 불가: {mat['name']}", "row": row_index})
                continue

            if numeric_value is not None and numeric_value > 10000:
                warnings.append({"level": 3, "message": f"이상치 의심: {mat['name']}={numeric_value}", "row": row_index})

            row_items.append({
                "material_id": mat["id"],
                "value_weight": float(numeric_value) if numeric_value is not None else None,
                "value_text": val if numeric_value is None else None,
            })

            preview_items.append({
                "material_id": mat["id"],
                "material_name": mat["name"],
                "value": numeric_value if numeric_value is not None else val,
            })

        parsed_rows.append({
            "product_name": prod or "(미입력)",
            "position": pos or "-",
            "ink_name": ink or "(미입력)",
            "items": row_items,
        })

        preview_rows.append({
            "product_name": prod or "(미입력)",
            "position": pos or "-",
            "ink_name": ink or "(미입력)",
            "items": preview_items,
        })

    if not global_headers:
        errors.append({"level": 1, "message": "유효한 헤더를 찾을 수 없습니다.", "row": 0})

    status = "error" if errors else "ok"
    return {
        "status": status,
        "errors": errors,
        "warnings": warnings,
        "preview": {"headers": global_headers, "rows": preview_rows},
        "parsed_rows": parsed_rows,
    }
