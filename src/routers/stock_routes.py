"""Material stock tracking endpoints (operator reads + manager writes).

Returns a tuple of (operator_router, manager_router). See section 11.2 of
the design for why stock_routes is the one router file that combines two
authorization scopes instead of splitting by role.

Split from src/routers/recipe_routes.py during the split-large-files
PDCA cycle (2026-05).

Endpoints:
    GET    /materials/stock                              (operator)
    GET    /materials/{material_id}/stock-log            (operator)
    POST   /materials/{material_id}/stock/restock        (manager)
    POST   /materials/{material_id}/stock/adjust         (manager)
    POST   /materials/{material_id}/stock/discard        (manager)
    PATCH  /materials/{material_id}/stock-threshold      (manager)
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ..auth import get_current_user, require_access_level
from ..database import get_connection, write_audit_log
from ..services import stock_service
from ..services.recipe_helpers import ensure_material
from .models import (
    StockAdjustBody,
    StockAmountBody,
    StockDiscardBody,
    StockThresholdBody,
)


def build_router() -> tuple[APIRouter, APIRouter]:
    operator_router = APIRouter(dependencies=[Depends(require_access_level("operator"))])
    manager_router = APIRouter(dependencies=[Depends(require_access_level("manager"))])

    @operator_router.get("/materials/stock")
    async def get_material_stock() -> dict[str, Any]:
        with get_connection() as connection:
            items = stock_service.list_stock(connection)
        return {"items": items, "total": len(items)}

    @operator_router.get("/materials/{material_id}/stock-log")
    async def get_material_stock_log(material_id: int, limit: int = Query(50, ge=1, le=500)) -> dict[str, Any]:
        with get_connection() as connection:
            ensure_material(connection, material_id)
            logs = stock_service.list_logs(connection, material_id, limit=limit)
        return {"items": logs, "total": len(logs)}

    @manager_router.post("/materials/{material_id}/stock/restock")
    async def material_stock_restock(material_id: int, body: StockAmountBody, request: Request) -> dict[str, Any]:
        current_user = get_current_user(request)
        with get_connection() as connection:
            material = ensure_material(connection, material_id)
            try:
                result = stock_service.restock(
                    connection,
                    material_id=material_id,
                    amount=body.amount,
                    actor=current_user,
                    note=body.note,
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
            write_audit_log(
                connection,
                action="material_stock_restock",
                actor=current_user,
                target_type="material",
                target_id=str(material_id),
                target_label=material["name"],
                details={"amount": body.amount, "note": body.note, **result},
            )
            connection.commit()
        return result

    @manager_router.post("/materials/{material_id}/stock/adjust")
    async def material_stock_adjust(material_id: int, body: StockAdjustBody, request: Request) -> dict[str, Any]:
        current_user = get_current_user(request)
        with get_connection() as connection:
            material = ensure_material(connection, material_id)
            try:
                result = stock_service.adjust(
                    connection,
                    material_id=material_id,
                    new_quantity=body.new_quantity,
                    actor=current_user,
                    note=body.note,
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
            write_audit_log(
                connection,
                action="material_stock_adjust",
                actor=current_user,
                target_type="material",
                target_id=str(material_id),
                target_label=material["name"],
                details={"new_quantity": body.new_quantity, "note": body.note, **result},
            )
            connection.commit()
        return result

    @manager_router.post("/materials/{material_id}/stock/discard")
    async def material_stock_discard(material_id: int, body: StockDiscardBody, request: Request) -> dict[str, Any]:
        current_user = get_current_user(request)
        with get_connection() as connection:
            material = ensure_material(connection, material_id)
            try:
                result = stock_service.discard(
                    connection,
                    material_id=material_id,
                    amount=body.amount,
                    actor=current_user,
                    note=body.note,
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
            write_audit_log(
                connection,
                action="material_stock_discard",
                actor=current_user,
                target_type="material",
                target_id=str(material_id),
                target_label=material["name"],
                details={"amount": body.amount, "note": body.note, **result},
            )
            connection.commit()
        return result

    @manager_router.patch("/materials/{material_id}/stock-threshold")
    async def material_stock_threshold(material_id: int, body: StockThresholdBody, request: Request) -> dict[str, Any]:
        current_user = get_current_user(request)
        with get_connection() as connection:
            material = ensure_material(connection, material_id)
            try:
                stock_service.set_threshold(connection, material_id, body.threshold)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
            write_audit_log(
                connection,
                action="material_stock_threshold_set",
                actor=current_user,
                target_type="material",
                target_id=str(material_id),
                target_label=material["name"],
                details={"threshold": body.threshold},
            )
            connection.commit()
        return {"material_id": material_id, "threshold": body.threshold}

    return operator_router, manager_router
