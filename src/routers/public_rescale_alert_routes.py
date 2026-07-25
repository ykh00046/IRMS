"""증량 승인제 — 내부망 공개 트레이 알림 라우터 (rescale-approval 2026-07-22).

트레이 앱이 주기 폴링해 '책임자 미확인 증량'이 남아 있는 동안 반복 알림을 띄운다.
public_attendance_alert_routes / public_viscosity_reminder_routes 와 동일한
내부망 공개(internal_only) 패턴을 따른다: 개발/내부망은 사설 IP 허용, 운영
(IRMS_ENV=production)은 X-IRMS-Tray-Token 헤더 필수(main.py protected_prefixes).

계약:
    GET /public/rescale-alerts   {count, items: [{id, product_name, product_lot,
                                   work_date, worker, kind}]}  — 최신순, 최대 20건.

kind = "rescale"(증량 부재 진행, rescale_unacked=1) | "manual"(수기 입력 부재 진행,
manual_unacked=1). 2026-07-25 에 수기 입력 부재도 같은 사후 확인 대상으로 격상하면서,
새 채널을 만들지 않고 이 채널을 확장했다 — 트레이를 한 번만 설치해도 두 종류가 모두
알림에 뜨게 하려는 것(구 버전 트레이는 kind 를 몰라도 목록·건수는 그대로 동작한다).
한 기록이 두 종류 모두 미확인이면 kind="both" 로 한 건만 보낸다(중복 알림 방지).
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
            SELECT id, product_name, product_lot, work_date, worker,
                   rescale_unacked, manual_unacked
            FROM blend_records
            WHERE (rescale_unacked = 1 OR manual_unacked = 1)
              AND status != 'canceled'
            ORDER BY id DESC
            LIMIT ?
            """,
            (_MAX_ITEMS,),
        ).fetchall()
        items = []
        for row in rows:
            has_rescale = bool(row["rescale_unacked"])
            has_manual = bool(row["manual_unacked"])
            kind = "both" if (has_rescale and has_manual) else ("rescale" if has_rescale else "manual")
            items.append(
                {
                    "id": row["id"],
                    "product_name": row["product_name"],
                    "product_lot": row["product_lot"],
                    "work_date": row["work_date"],
                    "worker": row["worker"],
                    "kind": kind,
                }
            )
        return {"count": len(items), "items": items}

    return router
