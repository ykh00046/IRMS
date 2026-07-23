"""운영 대시보드 — 배합 실적(blend_records) + 점도 현황 기반.

구 계량 워크플로(recipe_items.measured_at·recipes.completed_at·계량 편차) 지표는
/blend 전환 이후 데이터가 더 이상 쌓이지 않아 2026-07 전면 재구축했다. 모든
지표는 배합 실적과 점도에서 나온다. 배합은 편차 0 강제 저장이라 편차 지표는 없다.

Endpoints (무로그인 개방, 조회 전용):
    GET /dashboard/summary   기간 KPI + 현재 상태(점도 이상·오늘 점도 미입력)
    GET /dashboard/trend     일별 배합 건수·총량
    GET /dashboard/products  반제품별 배합 TOP
    GET /dashboard/workers   작업자별 실적
    GET /dashboard/recent    최근 배합 기록 (점도·결재 여부 포함)
    GET /dashboard/export    Excel 보고서
"""

import io
from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from ..db import get_connection
from ..services import dashboard_export, viscosity_service


def _parse_range(from_: str | None, to_: str | None) -> tuple[str, str]:
    today = date.today()
    if from_ is None and to_ is None:
        return (today - timedelta(days=6)).isoformat(), today.isoformat()
    try:
        from_date = date.fromisoformat(from_) if from_ else today - timedelta(days=6)
        to_date = date.fromisoformat(to_) if to_ else today
    except ValueError:
        raise HTTPException(status_code=400, detail="INVALID_DATE")
    if from_date > to_date:
        raise HTTPException(status_code=400, detail="INVALID_RANGE")
    return from_date.isoformat(), to_date.isoformat()


def _daterange(from_date: str, to_date: str) -> list[str]:
    cur = date.fromisoformat(from_date)
    end = date.fromisoformat(to_date)
    out: list[str] = []
    while cur <= end:
        out.append(cur.isoformat())
        cur += timedelta(days=1)
    return out


def build_router() -> APIRouter:
    router = APIRouter(prefix="/dashboard")

    @router.get("/summary")
    def dashboard_summary(
        from_: str | None = Query(default=None, alias="from"),
        to: str | None = Query(default=None),
    ) -> dict[str, Any]:
        from_date, to_date = _parse_range(from_, to)
        today = date.today().isoformat()
        with get_connection() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS cnt,
                       COALESCE(SUM(total_amount), 0) AS total_weight,
                       COUNT(DISTINCT product_name) AS products,
                       COUNT(DISTINCT worker) AS workers
                FROM blend_records
                WHERE status != 'canceled' AND work_date BETWEEN ? AND ?
                """,
                (from_date, to_date),
            ).fetchone()
            # 현재 상태 스냅샷 (기간과 무관한 '지금 해야 할 일').
            # NOTE: 결재 대기(approval_pending)는 2026-07-15 카드 제거 + 2026-07-23 페이로드
            # 에서도 제거했다(결재 현장 미사용 — 죽은 값을 매 호출 계산·반환하지 않음).
            due = viscosity_service.daily_reading_reminders(connection, target_date=today)
            viscosity_anomaly = viscosity_service.overview(connection)["total_anomaly"]

        return {
            "range": {"from": from_date, "to": to_date},
            "blend_count": int(row["cnt"] or 0),
            "total_weight_g": round(float(row["total_weight"] or 0.0), 2),
            "product_count": int(row["products"] or 0),
            "worker_count": int(row["workers"] or 0),
            "viscosity_anomaly": int(viscosity_anomaly or 0),
            "viscosity_due_today": [item["code"] for item in due],
        }

    @router.get("/trend")
    def dashboard_trend(
        from_: str | None = Query(default=None, alias="from"),
        to: str | None = Query(default=None),
    ) -> dict[str, Any]:
        from_date, to_date = _parse_range(from_, to)
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT work_date AS d, COUNT(*) AS cnt,
                       COALESCE(SUM(total_amount), 0) AS total_weight
                FROM blend_records
                WHERE status != 'canceled' AND work_date BETWEEN ? AND ?
                GROUP BY work_date
                """,
                (from_date, to_date),
            ).fetchall()
        by_day = {r["d"]: r for r in rows}
        points = [
            {
                "date": d,
                "blend_count": int(by_day[d]["cnt"]) if d in by_day else 0,
                "total_weight_g": round(float(by_day[d]["total_weight"]), 2) if d in by_day else 0.0,
            }
            for d in _daterange(from_date, to_date)
        ]
        return {"range": {"from": from_date, "to": to_date}, "points": points}

    @router.get("/products")
    def dashboard_products(
        from_: str | None = Query(default=None, alias="from"),
        to: str | None = Query(default=None),
        limit: int = Query(default=10, ge=1, le=100),
    ) -> dict[str, Any]:
        from_date, to_date = _parse_range(from_, to)
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT product_name, COUNT(*) AS cnt,
                       COALESCE(SUM(total_amount), 0) AS total_weight
                FROM blend_records
                WHERE status != 'canceled' AND work_date BETWEEN ? AND ?
                GROUP BY product_name
                ORDER BY total_weight DESC
                LIMIT ?
                """,
                (from_date, to_date, limit),
            ).fetchall()
        items = [
            {
                "product_name": r["product_name"],
                "blend_count": int(r["cnt"]),
                "total_weight_g": round(float(r["total_weight"] or 0.0), 2),
            }
            for r in rows
        ]
        return {"range": {"from": from_date, "to": to_date}, "items": items}

    @router.get("/workers")
    def dashboard_workers(
        from_: str | None = Query(default=None, alias="from"),
        to: str | None = Query(default=None),
    ) -> dict[str, Any]:
        from_date, to_date = _parse_range(from_, to)
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT COALESCE(NULLIF(worker, ''), '(미기록)') AS worker,
                       COUNT(*) AS cnt,
                       COALESCE(SUM(total_amount), 0) AS total_weight,
                       COUNT(DISTINCT product_name) AS products
                FROM blend_records
                WHERE status != 'canceled' AND work_date BETWEEN ? AND ?
                GROUP BY worker
                ORDER BY cnt DESC
                """,
                (from_date, to_date),
            ).fetchall()
        items = [
            {
                "worker": r["worker"],
                "blend_count": int(r["cnt"]),
                "total_weight_g": round(float(r["total_weight"] or 0.0), 2),
                "product_count": int(r["products"]),
            }
            for r in rows
        ]
        return {"range": {"from": from_date, "to": to_date}, "items": items}

    @router.get("/recent")
    def dashboard_recent(
        limit: int = Query(default=10, ge=1, le=50),
    ) -> dict[str, Any]:
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT br.id, br.product_lot, br.product_name, br.work_date, br.worker,
                       br.total_amount, br.reactor,
                       CASE WHEN br.approved_by IS NOT NULL AND br.approved_by != ''
                            THEN 1 ELSE 0 END AS approved,
                       EXISTS(
                           SELECT 1 FROM viscosity_readings vr
                           WHERE vr.blend_record_id = br.id
                       ) AS has_viscosity
                FROM blend_records br
                WHERE br.status != 'canceled'
                ORDER BY br.id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        items = [
            {
                "id": int(r["id"]),
                "product_lot": r["product_lot"],
                "product_name": r["product_name"],
                "work_date": r["work_date"],
                "worker": r["worker"],
                "total_amount": round(float(r["total_amount"] or 0.0), 2),
                "reactor": r["reactor"],
                "approved": bool(r["approved"]),
                "has_viscosity": bool(r["has_viscosity"]),
            }
            for r in rows
        ]
        return {"items": items}

    @router.get("/export")
    def dashboard_export_excel(
        from_: str | None = Query(default=None, alias="from"),
        to: str | None = Query(default=None),
    ) -> StreamingResponse:
        """운영 대시보드 보고서 Excel(배합 요약·반제품 TOP·작업자·점도 상태)."""
        from_date, to_date = _parse_range(from_, to)
        with get_connection() as connection:
            xlsx = dashboard_export.build_dashboard_excel(
                connection, from_date=from_date, to_date=to_date,
            )
        from urllib.parse import quote
        utf8_name = quote(f"대시보드보고서_{from_date}_{to_date}.xlsx")
        disposition = (
            f"attachment; filename=\"dashboard-{from_date}.xlsx\"; filename*=UTF-8''{utf8_name}"
        )
        return StreamingResponse(
            io.BytesIO(xlsx),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": disposition},
        )

    return router
