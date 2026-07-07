"""자재 사용량(불출) 공개 API — 상위 재고 대시보드 연동용.

트레이용 공개 API 와 동일한 보호를 받는다(main.py InternalNetworkOnlyMiddleware):
개발/내부망은 사설 IP 허용, 운영(IRMS_ENV=production)은 X-IRMS-Tray-Token 헤더 필수.

Endpoints:
    GET /public/material-usage?start_date&end_date&group=total|day|month
        기간 내 완료 배합 기록의 자재별 실사용량(g) 집계.
        기본: 이번 달 1일 ~ 오늘, group=total(기간 합계).
        응답 items: {period, material_code, material_name,
                     total_actual, total_theory, batch_count}
"""

import re
import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from ..db import get_db, local_today_text
from ..services import blend_service

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _validate_date(value: str, field: str) -> str:
    if not _DATE_RE.fullmatch(value):
        raise HTTPException(status_code=400, detail=f"{field} 는 YYYY-MM-DD 형식이어야 합니다.")
    return value


def build_router() -> APIRouter:
    router = APIRouter(prefix="/public/material-usage", tags=["public-material-usage"])

    @router.get("")
    def material_usage(
        start_date: str | None = Query(default=None, max_length=10),
        end_date: str | None = Query(default=None, max_length=10),
        group: str = Query(default="total", pattern="^(total|day|month)$"),
        by_product: bool = Query(default=False),
        connection: sqlite3.Connection = Depends(get_db),
    ) -> dict[str, Any]:
        today = local_today_text()
        start = _validate_date(start_date, "start_date") if start_date else today[:8] + "01"
        end = _validate_date(end_date, "end_date") if end_date else today
        if start > end:
            raise HTTPException(status_code=400, detail="start_date 가 end_date 보다 늦습니다.")
        return blend_service.material_usage_periods(
            connection, start_date=start, end_date=end, group=group, by_product=by_product
        )

    return router
