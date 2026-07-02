from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from ..blend_session import (
    current_blend_worker,
    login_worker_session,
    logout_worker_session,
    touch_worker_session,
)
from ..db import get_db, write_audit_log
from ..services import worker_service


class BlendWorkerSessionRequest(BaseModel):
    worker: str = Field(min_length=1, max_length=100)


def build_router() -> APIRouter:
    router = APIRouter(prefix="/blend/session", tags=["blend-session"])

    @router.get("/me")
    def me(request: Request) -> dict[str, str]:
        worker = current_blend_worker(request)
        if not worker:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="BLEND_WORKER_REQUIRED",
            )
        touch_worker_session(request)
        return {"worker": worker}

    @router.post("/login")
    def login(
        body: BlendWorkerSessionRequest,
        request: Request,
        connection: sqlite3.Connection = Depends(get_db),
    ) -> dict[str, str]:
        worker_name = body.worker.strip()
        if not worker_name:
            raise HTTPException(status_code=400, detail="WORKER_REQUIRED")
        if not worker_service.exists(connection, worker_name):
            raise HTTPException(status_code=404, detail="WORKER_NOT_REGISTERED")
        login_worker_session(request, worker_name)
        write_audit_log(
            connection,
            action="blend_worker_login",
            target_type="worker",
            target_label=worker_name,
            details={"worker": worker_name},
        )
        connection.commit()
        return {"worker": worker_name}

    @router.post("/logout")
    def logout(request: Request) -> dict[str, str]:
        logout_worker_session(request)
        return {"status": "ok"}

    return router
