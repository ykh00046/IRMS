"""증량 승인제 — 내부망 공개 트레이 알림 라우터 (rescale-approval 2026-07-22).

트레이 앱이 주기 폴링해 '책임자 미확인 증량'이 남아 있는 동안 반복 알림을 띄운다.
public_attendance_alert_routes / public_viscosity_reminder_routes 와 동일한
내부망 공개(internal_only) 패턴을 따른다: 개발/내부망은 사설 IP 허용, 운영
(IRMS_ENV=production)은 X-IRMS-Tray-Token 헤더 필수(main.py protected_prefixes).

계약(구현은 이 파일에 채운다):
    GET /public/rescale-alerts   {count, items: [{id, product_name, product_lot,
                                   work_date, worker}]}  — rescale_unacked=1 인 기록
                                   최신순, 최대 20건.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, Depends

from ..db import get_db

_MAX_ITEMS = 20


def build_router() -> APIRouter:
    router = APIRouter(prefix="/public/rescale-alerts", tags=["public-rescale-alerts"])

    @router.get("")
    def rescale_alerts(
        connection: sqlite3.Connection = Depends(get_db),
    ) -> dict[str, Any]:
        rows = connection.execute(
            """
            SELECT id, product_name, product_lot, work_date, worker
            FROM blend_records
            WHERE rescale_unacked = 1
            ORDER BY id DESC
            LIMIT ?
            """,
            (_MAX_ITEMS,),
        ).fetchall()
        items = [
            {
                "id": row["id"],
                "product_name": row["product_name"],
                "product_lot": row["product_lot"],
                "work_date": row["work_date"],
                "worker": row["worker"],
            }
            for row in rows
        ]
        return {"count": len(items), "items": items}

    return router
