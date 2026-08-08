"""운영 대시보드 — 배합 실적(blend_records) + 점도 현황 기반.

구 계량 워크플로(recipe_items.measured_at·recipes.completed_at·계량 편차) 지표는
/blend 전환 이후 데이터가 더 이상 쌓이지 않아 2026-07 전면 재구축했다. 모든
지표는 배합 실적과 점도에서 나온다. 배합은 편차 0 강제 저장이라 편차 지표는 없다.

역할 분리(2026-08-08): 이 화면은 **지금 해야 할 일**을 본다 — 오늘·최근 며칠의 배합,
점도 미입력, 확인 대기 중인 증량·수기 입력, 최근 기록. 기간을 잡고 경향·성과·자재 소비를
분석하는 일은 배합 분석(/insight, GET /blend/analysis)이 맡는다. 예전에는 반제품 TOP·
작업자별 실적이 양쪽에 다 있었고, 정작 세는 기준이 서로 달랐다(여기 `status != 'canceled'`
= 초안 포함, 저기 `status = 'completed'`). 지금은 양쪽 다 완료·비-bulk 기준이다.

Endpoints (무로그인 개방, 조회 전용):
    GET /dashboard/summary   기간 KPI + 현재 상태(점도 이상·오늘 점도 미입력)
    GET /dashboard/trend     일별 배합 건수·총량
    GET /dashboard/attention 지금 조치 필요(점도 미입력·미확인·자재 LOT 파일 경과·오늘 실적)
    GET /dashboard/recent    최근 배합 기록 (점도·결재 여부 포함)
    GET /dashboard/export    Excel 보고서(운영 스냅샷 — 기간 분석은 /insight 리포트)
"""

import io
from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from ..db import get_connection
from ..services import dashboard_export, erp_lot_service, viscosity_service


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
                WHERE status = 'completed' AND COALESCE(is_bulk_regenerated, 0) = 0
                  AND work_date BETWEEN ? AND ?
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
                WHERE status = 'completed' AND COALESCE(is_bulk_regenerated, 0) = 0
                  AND work_date BETWEEN ? AND ?
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

    @router.get("/attention")
    def dashboard_attention() -> dict[str, Any]:
        """지금 조치가 필요한 것들 — 기간 필터와 무관한 '현재 상태' 신호.

        기간 지표(건수·총량)와 섞이면 안 되는 값들이다: 기간을 30일로 바꿔도 '오늘
        점도 미입력'은 그대로여야 하는데, 한 줄에 나란히 있으면 같은 기준으로 읽힌다.

        자재 LOT 기준 파일이 며칠 지났는지는 여기서 처음 나온다. 그 파일이 낡으면
        배합 화면의 원료 LOT 검증이 낡은 재고로 판정하는데, 그 사실을 알려면 자재 LOT
        화면까지 들어가야 했다(2026-08-08).
        """
        today = date.today().isoformat()
        with get_connection() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS cnt,
                       MAX(work_date || ' ' || COALESCE(work_time, '')) AS last_at
                FROM blend_records
                WHERE status = 'completed' AND COALESCE(is_bulk_regenerated, 0) = 0
                """
            ).fetchone()
            today_row = connection.execute(
                """
                SELECT COUNT(*) AS cnt FROM blend_records
                WHERE status = 'completed' AND COALESCE(is_bulk_regenerated, 0) = 0
                  AND work_date = ?
                """,
                (today,),
            ).fetchone()
            due = viscosity_service.daily_reading_reminders(connection, target_date=today)
            unacked = connection.execute(
                """
                SELECT COUNT(*) AS cnt FROM blend_records
                WHERE (rescale_unacked = 1 OR manual_unacked = 1) AND status != 'canceled'
                """
            ).fetchone()

        erp = erp_lot_service.latest_file_summary()
        stale_days = None
        if erp.get("file_date"):
            try:
                stale_days = (
                    date.today() - date.fromisoformat(erp["file_date"])
                ).days
            except ValueError:
                stale_days = None

        return {
            "today": today,
            "today_blend_count": int(today_row["cnt"] or 0),
            "last_blend_at": (row["last_at"] or "").strip() or None,
            "viscosity_due_today": [item["code"] for item in due],
            "unacked_count": int(unacked["cnt"] or 0),
            "material_lot_file": {
                "file_name": erp.get("file_name"),
                "file_date": erp.get("file_date"),
                "found": bool(erp.get("found")),
                "stale_days": stale_days,
            },
        }

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
                WHERE br.status = 'completed'
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
