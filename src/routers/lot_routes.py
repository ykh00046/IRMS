"""Material LOT / expiry tracking endpoints (operator reads + manager writes).

Returns a tuple of (operator_router, manager_router), mirroring stock_routes:
LOT lists are readable by operators (FIFO decisions on the floor) while
registration / consume / discard / export are manager-only.

LOT tracking is independent of materials.stock_quantity (see Plan §3).

Plan:   docs/01-plan/features/lot-expiry-tracking.plan.md
Design: docs/02-design/features/lot-expiry-tracking.design.md

Endpoints:
    GET    /materials/lots                          (operator)
    GET    /materials/{material_id}/lots            (operator)
    POST   /materials/{material_id}/lots            (manager)
    POST   /lots/{lot_id}/consume                   (manager)
    POST   /lots/{lot_id}/discard                   (manager)
    GET    /lots/export                             (manager)
"""

import csv
import io
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from ..auth import get_current_user, require_access_level
from ..db import get_connection, write_audit_log
from ..services import lot_service
from ..services.recipe_helpers import ensure_material
from .models import LotConsumeBody, LotCreateBody, LotDiscardBody


def _csv_safe(value: Any) -> Any:
    # 스프레드시트 수식 인젝션 방어: 위험 문자로 시작하는 텍스트는 ' 접두
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def build_router() -> tuple[APIRouter, APIRouter]:
    operator_router = APIRouter(dependencies=[Depends(require_access_level("operator"))])
    manager_router = APIRouter(dependencies=[Depends(require_access_level("manager"))])

    @operator_router.get("/materials/lots")
    def get_all_lots(
        alert_days: int = Query(lot_service.DEFAULT_ALERT_DAYS, ge=1, le=365),
    ) -> dict[str, Any]:
        with get_connection() as connection:
            items = lot_service.list_lots(connection, alert_days=alert_days)
        return {"items": items, "total": len(items)}

    @operator_router.get("/materials/{material_id}/lots")
    def get_material_lots(
        material_id: int,
        include_inactive: bool = False,
        alert_days: int = Query(lot_service.DEFAULT_ALERT_DAYS, ge=1, le=365),
    ) -> dict[str, Any]:
        with get_connection() as connection:
            ensure_material(connection, material_id)
            items = lot_service.list_lots(
                connection,
                material_id=material_id,
                include_inactive=include_inactive,
                alert_days=alert_days,
            )
        return {"items": items, "total": len(items)}

    @manager_router.post("/materials/{material_id}/lots")
    def register_lot(material_id: int, body: LotCreateBody, request: Request) -> dict[str, Any]:
        current_user = get_current_user(request)
        with get_connection() as connection:
            material = ensure_material(connection, material_id)
            try:
                result = lot_service.register_lot(
                    connection,
                    material_id=material_id,
                    lot_no=body.lot_no,
                    quantity=body.quantity,
                    received_at=body.received_at,
                    expiry_date=body.expiry_date,
                    actor=current_user,
                    note=body.note,
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
            write_audit_log(
                connection,
                action="material_lot_register",
                actor=current_user,
                target_type="material",
                target_id=str(material_id),
                target_label=material["name"],
                details={
                    "lot_no": body.lot_no,
                    "quantity": body.quantity,
                    "expiry_date": result["expiry_date"],
                    "lot_id": result["lot_id"],
                },
            )
            connection.commit()
        return result

    @manager_router.post("/lots/{lot_id}/consume")
    def consume_lot(lot_id: int, body: LotConsumeBody, request: Request) -> dict[str, Any]:
        current_user = get_current_user(request)
        with get_connection() as connection:
            try:
                result = lot_service.consume_lot(
                    connection, lot_id=lot_id, amount=body.amount,
                    actor=current_user, note=body.note,
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
            write_audit_log(
                connection,
                action="material_lot_consume",
                actor=current_user,
                target_type="material_lot",
                target_id=str(lot_id),
                details={"amount": body.amount, "note": body.note, **result},
            )
            connection.commit()
        return result

    @manager_router.post("/lots/{lot_id}/discard")
    def discard_lot(lot_id: int, body: LotDiscardBody, request: Request) -> dict[str, Any]:
        current_user = get_current_user(request)
        with get_connection() as connection:
            try:
                result = lot_service.discard_lot(
                    connection, lot_id=lot_id, actor=current_user, note=body.note,
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
            write_audit_log(
                connection,
                action="material_lot_discard",
                actor=current_user,
                target_type="material_lot",
                target_id=str(lot_id),
                details={"note": body.note, **result},
            )
            connection.commit()
        return result

    @manager_router.get("/lots/export")
    def export_lots(
        include_inactive: bool = False,
        alert_days: int = Query(lot_service.DEFAULT_ALERT_DAYS, ge=1, le=365),
    ) -> StreamingResponse:
        with get_connection() as connection:
            items = lot_service.list_lots(
                connection, include_inactive=include_inactive, alert_days=alert_days
            )

        state_label = {
            "expired": "만료",
            "expiring_soon": "임박",
            "ok": "정상",
            "no_expiry": "무기한",
        }
        status_label = {"active": "유효", "depleted": "소진", "discarded": "폐기"}
        fieldnames = [
            "material_name", "category", "lot_no", "remaining_quantity",
            "received_at", "expiry_date", "days_until", "expiry_state", "status",
        ]
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for it in items:
            writer.writerow({
                "material_name": _csv_safe(it["material_name"]),
                "category": _csv_safe(it["category"]),
                "lot_no": _csv_safe(it["lot_no"]),
                "remaining_quantity": it["remaining_quantity"],
                "received_at": it["received_at"],
                "expiry_date": it["expiry_date"],
                "days_until": it["days_until"],
                "expiry_state": state_label.get(it["expiry_state"], it["expiry_state"]),
                "status": status_label.get(it["status"], it["status"]),
            })

        output.seek(0)
        filename = f"irms-lots-{date.today().isoformat()}.csv"
        return StreamingResponse(
            output,
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    return operator_router, manager_router
