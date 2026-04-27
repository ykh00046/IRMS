"""Unauthenticated attendance-alert polling for the tray client.

Each field PC's tray app polls ``/month`` on the configured schedule;
when the response has items, a silent popup is raised. Access is
restricted to the internal LAN by the InternalNetworkOnlyMiddleware;
no login is required because the endpoint is read-only and the data
it returns is already visible via the main attendance page for anyone
on the same network.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from ..services import attendance_excel as excel_service


def build_router() -> APIRouter:
    router = APIRouter(prefix="/public/attendance-alerts", tags=["public-attendance-alerts"])

    @router.get("/today")
    async def today() -> dict[str, Any]:
        year_month = excel_service.current_year_month()
        target_date = excel_service.current_date()
        try:
            day_type, items = excel_service.detect_today_anomalies(
                year_month, target_date
            )
        except excel_service.MonthFileNotFound:
            raise HTTPException(status_code=404, detail="MONTH_FILE_NOT_FOUND")
        except excel_service.FileLocked:
            raise HTTPException(status_code=503, detail="FILE_LOCKED_RETRY")
        except excel_service.FileFormatInvalid:
            raise HTTPException(status_code=500, detail="FILE_FORMAT_INVALID")

        return {
            "date": target_date,
            "day_type": day_type,
            "total": len(items),
            "items": items,
        }

    @router.get("/month")
    async def month() -> dict[str, Any]:
        year_month = excel_service.current_year_month()
        try:
            items = excel_service.detect_month_anomalies(year_month)
        except excel_service.MonthFileNotFound:
            raise HTTPException(status_code=404, detail="MONTH_FILE_NOT_FOUND")
        except excel_service.FileLocked:
            raise HTTPException(status_code=503, detail="FILE_LOCKED_RETRY")
        except excel_service.FileFormatInvalid:
            raise HTTPException(status_code=500, detail="FILE_FORMAT_INVALID")

        return {
            "month": year_month,
            "date": excel_service.current_date(),
            "total": len(items),
            "items": items,
        }

    return router
