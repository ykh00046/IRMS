from __future__ import annotations

import sqlite3
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends

from ..db import get_db
from ..services import viscosity_service


def _parse_codes(raw_codes: str) -> list[str]:
    codes: list[str] = []
    seen_codes = set()
    for part in raw_codes.replace("\n", ",").split(","):
        code = part.strip().upper()
        if code and code not in seen_codes:
            codes.append(code)
            seen_codes.add(code)
    return codes


def build_router() -> APIRouter:
    router = APIRouter(prefix="/public/viscosity-reminders", tags=["public-viscosity-reminders"])

    @router.get("/due")
    def due(
        codes: str = "",
        target_date: str | None = None,
        connection: sqlite3.Connection = Depends(get_db),
    ) -> dict[str, Any]:
        reminder_date = target_date or date.today().isoformat()
        items = viscosity_service.daily_reading_reminders(
            connection,
            codes=_parse_codes(codes),
            target_date=reminder_date,
        )
        return {
            "date": reminder_date,
            "total": len(items),
            "items": items,
        }

    return router
