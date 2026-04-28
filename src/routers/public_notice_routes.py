"""Unauthenticated notice-polling endpoints for the tray client.

The tray client installed on field PCs polls ``/poll`` every few seconds and
plays newly received notice-room messages via TTS. These endpoints are read
only; network access is restricted by the InternalNetworkOnlyMiddleware so
only private-LAN clients can reach them.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from ..database import get_connection, utc_cutoff_text, utc_now_text
from .models import serialize_chat_message, serialize_chat_room

NOTICE_ROOM_KEY = "notice"
# On the first poll (after_id == 0) we replay at most one message that was
# posted within this freshness window. Catches the "PC reinstalled while
# admin posted urgent notice" case without flooding TTS with old history.
INITIAL_SYNC_FRESHNESS_SECONDS = 5 * 60


def build_router() -> APIRouter:
    router = APIRouter(prefix="/public/notice", tags=["public-notice"])

    @router.get("/ping")
    async def ping() -> dict[str, str]:
        return {"status": "ok", "time": utc_now_text()}

    @router.get("/poll")
    async def poll(
        after_id: int = Query(default=0, ge=0),
        limit: int = Query(default=20, ge=1, le=100),
    ) -> dict[str, Any]:
        with get_connection() as connection:
            room_row = connection.execute(
                """
                SELECT key, name, scope, sort_order, is_active
                FROM chat_rooms
                WHERE key = ? AND is_active = 1
                """,
                (NOTICE_ROOM_KEY,),
            ).fetchone()
            if not room_row:
                raise HTTPException(status_code=404, detail="NOTICE_ROOM_NOT_FOUND")

            latest_id = int(
                connection.execute(
                    "SELECT COALESCE(MAX(id), 0) AS max_id "
                    "FROM chat_messages WHERE room_key = ?",
                    (NOTICE_ROOM_KEY,),
                ).fetchone()["max_id"]
            )

            if after_id == 0:
                # Initial sync: replay at most one recent message inside
                # INITIAL_SYNC_FRESHNESS_SECONDS so a freshly installed PC
                # still catches an urgent notice posted moments earlier.
                # Anything older is suppressed to avoid history flooding.
                cutoff = utc_cutoff_text(utc_now_text(), INITIAL_SYNC_FRESHNESS_SECONDS)
                recent = connection.execute(
                    """
                    SELECT
                        id, room_key, message_text, stage,
                        created_by_user_id, created_by_username,
                        created_by_display_name, created_at
                    FROM chat_messages
                    WHERE room_key = ? AND created_at >= ?
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (NOTICE_ROOM_KEY, cutoff),
                ).fetchall()
                rows = list(recent)
            else:
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
                    (NOTICE_ROOM_KEY, after_id, limit),
                ).fetchall()

        items = [serialize_chat_message(row) for row in rows]
        return {
            "room": serialize_chat_room(room_row),
            "items": items,
            "total": len(items),
            "latest_id": latest_id,
        }

    return router
