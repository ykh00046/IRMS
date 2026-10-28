"""양산대응 배합 사용량 — Recipe-Intake 입구(쓰기)와 재고 대시보드용 조회(읽기).

쓰기와 읽기는 보호가 다르다:
    POST /intake/recipe-usage
        Recipe-Intake 가 일일 장부 스냅샷을 보낸다. 사설 IP 필수, 터널 경유(CF 헤더)는 403.
        전용 토큰(X-IRMS-Intake-Token = IRMS_RECIPE_INTAKE_TOKEN)은 설정했을 때만 필수
        (미설정 = 사내망이면 받음). 쿠키 세션을 쓰지 않는 기계 호출이라 CSRF 면제
        (main.py) — 토큰이 그 역할을 대신한다. 경로를 /public 밖에 둔 이유: 운영에서 /public 은
        트레이 토큰이 필수라 읽기 토큰까지 함께 요구하게 되기 때문.
    GET /public/recipe-usage?start_date&end_date&group=total|day|month&kind=A|B|all
    GET /public/recipe-usage/status
        material-usage 와 같은 내부망/트레이 토큰 보호(main.py protected_prefixes).

배합 기록·반제품제조 불출량(material-usage)은 건드리지 않는다.
`from __future__ import annotations` 사용 금지(FastAPI 본문 모델 해석).
"""

import hmac
import re
import sqlite3
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator, model_validator

from .. import config as cfg
from ..db import get_db, local_today_text
from ..middleware.internal_only import _is_private
from ..services import recipe_usage_service

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CODE_RE = re.compile(r"^[A-Za-z]{1,2}[A-Za-z0-9]{1,18}$")
_SOURCE_PATTERN = r"^[a-z][a-z0-9-]{1,39}$"
_CLOUDFLARE_HEADERS = ("cf-ray", "cf-connecting-ip")
MAX_ROWS = 20000


def _iso_day(value: str) -> str:
    if not _DATE_RE.fullmatch(value):
        raise ValueError("YYYY-MM-DD 형식이어야 합니다.")
    date.fromisoformat(value)  # 2026-02-30 같은 날짜를 거른다
    return value


class RecipeUsageRow(BaseModel):
    day: str
    material: str = Field(min_length=1, max_length=200)
    item_code: str | None = Field(default=None, max_length=20)
    used_g: float = Field(ge=0, le=10_000_000)
    batch_g: float = Field(default=0, ge=0, le=10_000_000)
    lines: int = Field(default=0, ge=0)
    images: int = Field(default=0, ge=0)

    @field_validator("day")
    @classmethod
    def _day(cls, v: str) -> str:
        return _iso_day(v)

    @field_validator("item_code")
    @classmethod
    def _code(cls, v: str | None) -> str | None:
        if v is None or v.strip() == "":
            return None
        if not _CODE_RE.fullmatch(v.strip()):
            raise ValueError("품목코드 형식이 아닙니다.")
        return v.strip().upper()


class RecipeUsageSnapshot(BaseModel):
    source: str = Field(default=recipe_usage_service.DEFAULT_SOURCE, pattern=_SOURCE_PATTERN)
    first_day: str
    through: str
    computed_at: str | None = Field(default=None, max_length=40)
    rows: list[RecipeUsageRow] = Field(max_length=MAX_ROWS)

    @field_validator("first_day", "through")
    @classmethod
    def _days(cls, v: str) -> str:
        return _iso_day(v)

    @model_validator(mode="after")
    def _consistent(self) -> "RecipeUsageSnapshot":
        if self.first_day > self.through:
            raise ValueError("first_day 가 through 보다 늦습니다.")
        seen = set()
        for r in self.rows:
            if not (self.first_day <= r.day <= self.through):
                raise ValueError(f"기간 밖 행: {r.day}")
            key = (r.day, r.material)
            if key in seen:
                raise ValueError(f"같은 날·자재 중복: {r.day} {r.material}")
            seen.add(key)
        return self


def _require_intake_token(request: Request) -> str:
    """입구 보호 — 터널 차단 → 사설 IP → (토큰이 설정돼 있으면) 토큰 일치 순.

    토큰 미설정이면 사내망 요청은 받는다(사용자 결정 2026-10-28: 열쇠 없이 운영). 보낸 IP 는
    recipe_usage_imports.client_ip 에 남는다. 토큰을 설정하면 그때부터 토큰이 필수다."""
    if any(h in request.headers for h in _CLOUDFLARE_HEADERS):
        raise HTTPException(status_code=403, detail="INTAKE_LAN_ONLY")
    host = request.client.host if request.client else ""
    if not _is_private(host):
        raise HTTPException(status_code=403, detail="INTAKE_LAN_ONLY")
    expected = cfg.RECIPE_INTAKE_TOKEN
    if expected and not hmac.compare_digest(request.headers.get("X-IRMS-Intake-Token", ""), expected):
        raise HTTPException(status_code=403, detail="INTAKE_TOKEN_REQUIRED")
    return host


def _validate_date(value: str, field: str) -> str:
    try:
        return _iso_day(value)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"{field} 는 YYYY-MM-DD 형식이어야 합니다.")


def build_router() -> APIRouter:
    router = APIRouter(tags=["recipe-usage"])

    @router.post("/intake/recipe-usage")
    def intake_recipe_usage(
        payload: RecipeUsageSnapshot,
        client_ip: str = Depends(_require_intake_token),
        connection: sqlite3.Connection = Depends(get_db),
    ) -> dict[str, Any]:
        return recipe_usage_service.replace_snapshot(
            connection,
            source=payload.source,
            first_day=payload.first_day,
            through=payload.through,
            computed_at=payload.computed_at,
            rows=[r.model_dump() for r in payload.rows],
            client_ip=client_ip,
        )

    @router.get("/public/recipe-usage")
    def recipe_usage(
        start_date: str | None = Query(default=None, max_length=10),
        end_date: str | None = Query(default=None, max_length=10),
        group: str = Query(default="total", pattern="^(total|day|month)$"),
        kind: str = Query(default="all", pattern="^(A|B|all)$"),
        source: str = Query(default=recipe_usage_service.DEFAULT_SOURCE, pattern=_SOURCE_PATTERN),
        connection: sqlite3.Connection = Depends(get_db),
    ) -> dict[str, Any]:
        today = local_today_text()
        start = _validate_date(start_date, "start_date") if start_date else today[:8] + "01"
        end = _validate_date(end_date, "end_date") if end_date else today
        if start > end:
            raise HTTPException(status_code=400, detail="start_date 가 end_date 보다 늦습니다.")
        return recipe_usage_service.usage_periods(
            connection, start_date=start, end_date=end, group=group, kind=kind, source=source
        )

    @router.get("/public/recipe-usage/status")
    def recipe_usage_status(
        source: str = Query(default=recipe_usage_service.DEFAULT_SOURCE, pattern=_SOURCE_PATTERN),
        connection: sqlite3.Connection = Depends(get_db),
    ) -> dict[str, Any]:
        return recipe_usage_service.import_status(connection, source=source)

    return router
