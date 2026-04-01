from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ..auth import get_current_user, require_access_level
from ..database import get_connection, utc_now_text, write_audit_log
from .models import ChatMessageCreateRequest, actor_name, serialize_chat_message, serialize_chat_room


def build_router() -> APIRouter:
    router = APIRouter(dependencies=[Depends(require_access_level("operator"))])

    @router.get("/chat/rooms")
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

    @router.get("/chat/messages")
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
                        id, room_key, message_text, stage,
                        created_by_user_id, created_by_username,
                        created_by_display_name, created_at
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
                        id, room_key, message_text, stage,
                        created_by_user_id, created_by_username,
                        created_by_display_name, created_at
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

    @router.post("/chat/messages")
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
                    room_key, message_text, stage,
                    created_by_user_id, created_by_username,
                    created_by_display_name, created_at
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
                    id, room_key, message_text, stage,
                    created_by_user_id, created_by_username,
                    created_by_display_name, created_at
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
                details={"message_id": message_id, "stage": stage},
            )
            connection.commit()

        return {"room": room, "message": serialize_chat_message(row)}

    return router
