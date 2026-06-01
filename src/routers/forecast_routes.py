"""Manager-scope material consumption forecast + reorder recommendation.

Reads consumption history to project stock-out dates and recommend reorder
quantities, and exports a reorder recommendation sheet as CSV. Per-material
lead time / coverage parameters are editable here.

Plan:   docs/01-plan/features/material-forecast.plan.md
Design: docs/02-design/features/material-forecast.design.md

Endpoints:
    GET    /forecast/materials
    GET    /forecast/export
    PATCH  /materials/{material_id}/forecast-params
"""

import csv
import io
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from ..auth import get_current_user, require_access_level
from ..db import get_connection, write_audit_log
from ..services import forecast_service
from ..services.recipe_helpers import ensure_material
from .models import ForecastParamsBody


def build_router() -> APIRouter:
    router = APIRouter(dependencies=[Depends(require_access_level("manager"))])

    @router.get("/forecast/materials")
    def forecast_materials(
        window_days: int = Query(forecast_service.DEFAULT_WINDOW_DAYS, ge=7, le=365),
    ) -> dict[str, Any]:
        with get_connection() as connection:
            return forecast_service.compute_forecast(connection, window_days=window_days)

    @router.get("/forecast/export")
    def forecast_export(
        window_days: int = Query(forecast_service.DEFAULT_WINDOW_DAYS, ge=7, le=365),
        only_reorder: bool = False,
    ) -> StreamingResponse:
        with get_connection() as connection:
            result = forecast_service.compute_forecast(connection, window_days=window_days)

        items = result["items"]
        if only_reorder:
            items = [it for it in items if it["status"] in ("urgent", "soon")]

        def _csv_safe(value: Any) -> Any:
            # 스프레드시트 수식 인젝션 방어: 위험 문자로 시작하는 텍스트는 ' 접두
            if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
                return "'" + value
            return value

        fieldnames = [
            "material_name", "category", "stock_quantity", "avg_daily",
            "days_remaining", "predicted_stockout_date", "recommended_order_qty", "status",
        ]
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for it in items:
            writer.writerow({
                "material_name": _csv_safe(it["name"]),
                "category": _csv_safe(it["category"]),
                "stock_quantity": it["stock_quantity"],
                "avg_daily": it["avg_daily"],
                "days_remaining": it["days_remaining"],
                "predicted_stockout_date": it["predicted_stockout_date"],
                "recommended_order_qty": it["recommended_order_qty"],
                "status": it["status"],
            })

        output.seek(0)
        filename = f"irms-forecast-{date.today().isoformat()}.csv"
        return StreamingResponse(
            output,
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @router.patch("/materials/{material_id}/forecast-params")
    def material_forecast_params(
        material_id: int, body: ForecastParamsBody, request: Request
    ) -> dict[str, Any]:
        current_user = get_current_user(request)
        with get_connection() as connection:
            material = ensure_material(connection, material_id)
            try:
                forecast_service.set_forecast_params(
                    connection,
                    material_id,
                    lead_time_days=body.lead_time_days,
                    reorder_cycle_days=body.reorder_cycle_days,
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
            write_audit_log(
                connection,
                action="material_forecast_params_set",
                actor=current_user,
                target_type="material",
                target_id=str(material_id),
                target_label=material["name"],
                details={
                    "lead_time_days": body.lead_time_days,
                    "reorder_cycle_days": body.reorder_cycle_days,
                },
            )
            connection.commit()
        return {
            "material_id": material_id,
            "lead_time_days": body.lead_time_days,
            "reorder_cycle_days": body.reorder_cycle_days,
        }

    return router
