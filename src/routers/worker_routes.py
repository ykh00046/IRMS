"""작업자 명단 라우트.

접근: 조회·등록은 무로그인 개방(현장에서 이름 입력). 명단 정리(비활성/이름변경)는
관리자(admin) 전용.

Endpoints:
    GET    /workers                 활성 작업자 목록 (무로그인)
    POST   /workers                 이름 등록(처음 보는 이름) (무로그인)
    GET    /workers/all             전체 목록 (admin, 정리용)
    PATCH  /workers/{id}            비활성/이름변경 (admin)
"""

import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from ..auth import get_current_user, require_access_level
from ..db import get_db, utc_now_text, write_audit_log
from ..services import worker_service
from .models import WorkerCreateBody, WorkerUpdateBody


def build_router() -> tuple[APIRouter, APIRouter]:
    open_router = APIRouter()
    admin_router = APIRouter(dependencies=[Depends(require_access_level("manager"))])

    @open_router.get("/workers")
    def list_workers(connection: sqlite3.Connection = Depends(get_db)) -> dict[str, Any]:
        return {"items": worker_service.list_workers(connection)}

    @open_router.post("/workers")
    def register_worker(
        body: WorkerCreateBody,
        request: Request,
        connection: sqlite3.Connection = Depends(get_db),
    ) -> dict[str, Any]:
        try:
            result = worker_service.register(connection, body.name, utc_now_text())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        if result["created"]:
            write_audit_log(
                connection,
                action="worker_register",
                actor=get_current_user(request, required=False),
                target_type="worker",
                target_label=result["name"],
            )
        connection.commit()
        return result

    @admin_router.get("/workers/all")
    def list_all_workers(connection: sqlite3.Connection = Depends(get_db)) -> dict[str, Any]:
        return {"items": worker_service.list_workers(connection, active_only=False)}

    @admin_router.patch("/workers/{worker_id}")
    def update_worker(
        worker_id: int,
        body: WorkerUpdateBody,
        request: Request,
        connection: sqlite3.Connection = Depends(get_db),
    ) -> dict[str, Any]:
        if body.name is not None:
            try:
                worker_service.rename(connection, worker_id, body.name)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
        if body.is_active is not None:
            worker_service.set_active(connection, worker_id, body.is_active)
        write_audit_log(
            connection,
            action="worker_update",
            actor=get_current_user(request, required=False),
            target_type="worker",
            target_id=str(worker_id),
            details={"name": body.name, "is_active": body.is_active},
        )
        connection.commit()
        return {"updated": worker_id}

    return open_router, admin_router
