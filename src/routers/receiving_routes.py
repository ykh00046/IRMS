"""발주 입고·검수 엔드포인트 (manager scope).

ERP 전송된 발주서(sent)에 대한 입고를 등록하면 LOT/유통기한과 재고가 동시에
반영된다(receiving_service). ERP 전송 status 와 직교한 receipt_status 로 추적.

Plan:   docs/01-plan/features/purchase-order-receiving.plan.md
Design: docs/02-design/features/purchase-order-receiving.design.md

Endpoints (manager):
    POST   /orders/{order_id}/receipts      입고 등록
    GET    /orders/{order_id}/receipts      입고 이력 조회
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from ..auth import get_current_user, require_access_level
from ..db import get_connection, write_audit_log
from ..services import order_service, receiving_service
from .models import ReceiptCreateBody, actor_name


def build_router() -> APIRouter:
    router = APIRouter(dependencies=[Depends(require_access_level("manager"))])

    def _require_order(connection: Any, order_id: int) -> dict[str, Any]:
        order = order_service.get_order(connection, order_id)
        if order is None:
            raise HTTPException(status_code=404, detail="발주서를 찾을 수 없습니다.")
        return order

    @router.post("/orders/{order_id}/receipts", status_code=201)
    def create_receipt(
        order_id: int, body: ReceiptCreateBody, request: Request
    ) -> dict[str, Any]:
        current_user = get_current_user(request)
        receiver = actor_name(current_user)
        with get_connection() as connection:
            order = _require_order(connection, order_id)
            try:
                result = receiving_service.receive_order(
                    connection,
                    order_id=order_id,
                    lines=[line.model_dump() for line in body.lines],
                    received_by=receiver,
                    actor=current_user,
                    note=body.note,
                )
            except receiving_service.ReceivingStateError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
            if result is None:
                raise HTTPException(status_code=404, detail="발주서를 찾을 수 없습니다.")
            write_audit_log(
                connection,
                action="order_receive",
                actor=current_user,
                target_type="purchase_order",
                target_id=str(order_id),
                target_label=order["order_no"],
                details={
                    "receipt_no": result["receipt_no"],
                    "item_count": result["item_count"],
                    "total_qty": result["total_qty"],
                    "receipt_status": result["receipt_status"],
                },
            )
            connection.commit()
        return result

    @router.get("/orders/{order_id}/receipts")
    def list_receipts(order_id: int) -> dict[str, Any]:
        with get_connection() as connection:
            _require_order(connection, order_id)
            receipts = receiving_service.list_receipts(connection, order_id)
        return {"receipts": receipts}

    return router
