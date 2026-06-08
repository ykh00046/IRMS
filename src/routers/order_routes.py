"""Manager-scope 발주서 생성·출력·ERP 전송.

material-forecast 의 발주 권장 산출물을 입력으로 발주서를 스냅샷 저장하고,
Excel(.xlsx)/인쇄용 HTML 로 출력하며 ERP 로 전송한다.

Plan:   docs/01-plan/features/order-sheet-erp.plan.md
Design: docs/02-design/features/order-sheet-erp.design.md

Endpoints (manager):
    POST   /orders
    GET    /orders
    GET    /orders/{order_id}
    PATCH  /orders/{order_id}
    POST   /orders/{order_id}/send
    POST   /orders/{order_id}/cancel
    GET    /orders/{order_id}/export.xlsx
    GET    /orders/{order_id}/print
"""

import io
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from ..auth import get_current_user, require_access_level
from ..config import BASE_DIR
from ..db import get_connection, utc_now_text, write_audit_log
from ..services import erp_client, order_service
from .models import OrderCreateBody, OrderUpdateBody, actor_name

_templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

_XLSX_MEDIA = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def build_router() -> APIRouter:
    router = APIRouter(dependencies=[Depends(require_access_level("manager"))])

    def _require_order(connection: Any, order_id: int) -> dict[str, Any]:
        order = order_service.get_order(connection, order_id)
        if order is None:
            raise HTTPException(status_code=404, detail="발주서를 찾을 수 없습니다.")
        return order

    @router.post("/orders", status_code=201)
    def create_order(body: OrderCreateBody, request: Request) -> dict[str, Any]:
        current_user = get_current_user(request)
        creator = actor_name(current_user)
        with get_connection() as connection:
            try:
                order = order_service.create_order_from_forecast(
                    connection, window_days=body.window_days, created_by=creator
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
            write_audit_log(
                connection,
                action="order_create",
                actor=current_user,
                target_type="purchase_order",
                target_id=str(order["id"]),
                target_label=order["order_no"],
                details={"item_count": order["item_count"], "window_days": body.window_days},
            )
            connection.commit()
        return order

    @router.get("/orders")
    def list_orders() -> dict[str, Any]:
        with get_connection() as connection:
            return {"orders": order_service.list_orders(connection)}

    @router.get("/orders/{order_id}")
    def get_order(order_id: int) -> dict[str, Any]:
        with get_connection() as connection:
            return _require_order(connection, order_id)

    @router.patch("/orders/{order_id}")
    def update_order(
        order_id: int, body: OrderUpdateBody, request: Request
    ) -> dict[str, Any]:
        current_user = get_current_user(request)
        with get_connection() as connection:
            _require_order(connection, order_id)
            try:
                order = order_service.update_order(
                    connection,
                    order_id,
                    note=body.note,
                    items=[item.model_dump() for item in body.items],
                )
            except order_service.OrderStateError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
            write_audit_log(
                connection,
                action="order_update",
                actor=current_user,
                target_type="purchase_order",
                target_id=str(order_id),
                target_label=order["order_no"],
                details={"item_count": order["item_count"], "total_qty": order["total_qty"]},
            )
            connection.commit()
        return order

    @router.post("/orders/{order_id}/send")
    def send_order(order_id: int, request: Request) -> dict[str, Any]:
        current_user = get_current_user(request)
        sender = actor_name(current_user)
        with get_connection() as connection:
            order = _require_order(connection, order_id)
            if order["status"] == "sent":
                raise HTTPException(status_code=400, detail="이미 전송된 발주서입니다.")
            if order["status"] == "cancelled":
                raise HTTPException(status_code=400, detail="취소된 발주서는 전송할 수 없습니다.")
            payload = order_service.order_payload(order)
            if not payload["items"]:
                raise HTTPException(status_code=400, detail="발주 수량이 있는 항목이 없습니다.")

            result = erp_client.send_order(payload)
            order = order_service.mark_sent(
                connection, order_id, result=result, sent_by=sender
            )
            write_audit_log(
                connection,
                action="order_send",
                actor=current_user,
                target_type="purchase_order",
                target_id=str(order_id),
                target_label=order["order_no"],
                details={
                    "erp_mode": result.mode,
                    "erp_status_code": result.status_code,
                    "ok": result.ok,
                },
            )
            connection.commit()
        if not result.ok:
            raise HTTPException(
                status_code=502,
                detail=f"ERP 전송 실패 (코드 {result.status_code}). 상태가 '실패'로 기록되었습니다.",
            )
        return {
            "status": order["status"],
            "erp_mode": result.mode,
            "erp_status_code": result.status_code,
        }

    @router.post("/orders/{order_id}/cancel")
    def cancel_order(order_id: int, request: Request) -> dict[str, Any]:
        current_user = get_current_user(request)
        with get_connection() as connection:
            _require_order(connection, order_id)
            try:
                order = order_service.cancel_order(connection, order_id)
            except order_service.OrderStateError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
            write_audit_log(
                connection,
                action="order_cancel",
                actor=current_user,
                target_type="purchase_order",
                target_id=str(order_id),
                target_label=order["order_no"],
            )
            connection.commit()
        return order

    @router.get("/orders/{order_id}/export.xlsx")
    def export_order(order_id: int) -> StreamingResponse:
        with get_connection() as connection:
            order = _require_order(connection, order_id)
        data = order_service.build_workbook(order)
        filename = f"{order['order_no']}.xlsx"
        return StreamingResponse(
            io.BytesIO(data),
            media_type=_XLSX_MEDIA,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @router.get("/orders/{order_id}/print", response_class=HTMLResponse)
    def print_order(order_id: int, request: Request) -> HTMLResponse:
        with get_connection() as connection:
            order = _require_order(connection, order_id)
        rows = [it for it in order["items"] if (it["order_qty"] or 0) > 0]
        return _templates.TemplateResponse(
            request,
            "order_print.html",
            {"order": order, "rows": rows, "now": utc_now_text()},
        )

    return router
