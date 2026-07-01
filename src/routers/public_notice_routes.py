from __future__ import annotations

from fastapi import APIRouter, Query
from typing_extensions import TypedDict


class NoticeRoom(TypedDict):
    key: str
    name: str
    scope: str


class NoticeItem(TypedDict):
    id: int
    message_text: str
    created_by_display_name: str
    created_by_username: str
    created_at: str


class NoticePollResponse(TypedDict):
    room: NoticeRoom
    items: list[NoticeItem]
    latest_id: int
    total: int


class NoticePingResponse(TypedDict):
    status: str
    room: NoticeRoom


NOTICE_ROOM: NoticeRoom = {"key": "notice", "name": "공지", "scope": "notice"}


def build_router() -> APIRouter:
    router = APIRouter(prefix="/public/notice", tags=["public-notice-compat"])

    @router.get("/ping")
    def ping() -> NoticePingResponse:
        return {"status": "ok", "room": NOTICE_ROOM}

    @router.get("/poll")
    def poll(
        after_id: int = Query(default=0, ge=0),
        limit: int = Query(default=20, ge=1, le=100),
    ) -> NoticePollResponse:
        del limit
        return {
            "room": NOTICE_ROOM,
            "items": [],
            "latest_id": after_id,
            "total": 0,
        }

    return router
