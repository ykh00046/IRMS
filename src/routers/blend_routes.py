"""배합 실적(잉크 계량 재구축) 라우트 — DHR Generator 이식.

접근: 점도 화면과 동일하게 로그인 없이 사내 공용 단말에서 사용. 작성자는 로그인
사용자가 있으면 그 이름, 없으면 '현장' 으로 기록.

Plan:   docs/01-plan/features/blend-overhaul.plan.md
Design: docs/02-design/features/blend-overhaul.design.md

Endpoints:
    GET    /blend/recipes                     배합용 레시피 목록
    GET    /blend/recipes/{id}?total=...      비율·이론량 환산
    POST   /blend/records                     배합 실적 저장
    GET    /blend/records                     기록조회(필터)
    GET    /blend/records/{id}                상세(배합상세+편차)
    DELETE /blend/records/{id}                기록 취소
    GET    /blend/workers                     작업자 목록(필터용)
"""

import io
import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from ..auth import get_current_user
from ..db import get_db, utc_now_text, write_audit_log
from ..services import blend_service, viscosity_service
from .models import BlendCreateBody, BlendViscosityBody, actor_name


def build_router() -> APIRouter:
    router = APIRouter()

    @router.get("/blend/recipes")
    def blend_recipes(connection: sqlite3.Connection = Depends(get_db)) -> dict[str, Any]:
        return {"items": blend_service.list_blend_recipes(connection)}

    @router.get("/blend/recipes/{recipe_id}")
    def blend_recipe_detail(
        recipe_id: int,
        total: float | None = Query(default=None, gt=0),
        connection: sqlite3.Connection = Depends(get_db),
    ) -> dict[str, Any]:
        result = blend_service.get_recipe_for_blend(connection, recipe_id, total)
        if not result:
            raise HTTPException(status_code=404, detail="레시피를 찾을 수 없습니다.")
        return result

    @router.get("/blend/workers")
    def blend_workers(connection: sqlite3.Connection = Depends(get_db)) -> dict[str, Any]:
        return {"items": blend_service.list_workers(connection)}

    @router.get("/blend/records")
    def blend_records(
        start_date: str | None = None,
        end_date: str | None = None,
        worker: str | None = None,
        search: str | None = None,
        limit: int = Query(default=200, ge=1, le=1000),
        connection: sqlite3.Connection = Depends(get_db),
    ) -> dict[str, Any]:
        items = blend_service.list_blend_records(
            connection,
            start_date=start_date,
            end_date=end_date,
            worker=worker,
            search=search,
            limit=limit,
        )
        return {"items": items, "total": len(items)}

    @router.get("/blend/records/{record_id}")
    def blend_record_detail(
        record_id: int,
        connection: sqlite3.Connection = Depends(get_db),
    ) -> dict[str, Any]:
        record = blend_service.get_blend_record(connection, record_id)
        if not record:
            raise HTTPException(status_code=404, detail="배합 기록을 찾을 수 없습니다.")
        record["viscosity"] = viscosity_service.list_readings_for_blend(connection, record_id)
        return record

    @router.post("/blend/records/{record_id}/viscosity")
    def blend_add_viscosity(
        record_id: int,
        body: BlendViscosityBody,
        request: Request,
        connection: sqlite3.Connection = Depends(get_db),
    ) -> dict[str, Any]:
        record = blend_service.get_blend_record(connection, record_id)
        if not record:
            raise HTTPException(status_code=404, detail="배합 기록을 찾을 수 없습니다.")
        product = viscosity_service.get_product(connection, body.product_id)
        if not product:
            raise HTTPException(status_code=404, detail="점도 제품을 찾을 수 없습니다.")
        current_user = get_current_user(request, required=False)
        actor = actor_name(current_user) if current_user else "현장"
        first_lot = record["details"][0]["material_lot"] if record["details"] else None
        try:
            viscosity_service.add_reading(
                connection,
                product_id=body.product_id,
                lot_no=record["product_lot"],
                viscosity=body.viscosity,
                measured_date=record["work_date"],
                memo=body.memo,
                recipe_material=record["product_name"],
                material_lot=first_lot,
                created_by=actor,
                created_at=utc_now_text(),
                blend_record_id=record_id,
            )
        except sqlite3.IntegrityError:
            raise HTTPException(
                status_code=409,
                detail=f"이미 등록된 점도(LOT {record['product_lot']})가 있습니다.",
            )
        write_audit_log(
            connection,
            action="blend_viscosity_link",
            actor=current_user,
            target_type="blend_record",
            target_id=str(record_id),
            target_label=record["product_lot"],
            details={"product_code": product["code"], "viscosity": body.viscosity},
        )
        connection.commit()
        record["viscosity"] = viscosity_service.list_readings_for_blend(connection, record_id)
        return record

    @router.post("/blend/records")
    def blend_create(
        body: BlendCreateBody,
        request: Request,
        connection: sqlite3.Connection = Depends(get_db),
    ) -> dict[str, Any]:
        if not body.details:
            raise HTTPException(status_code=400, detail="배합 상세가 비어 있습니다.")
        current_user = get_current_user(request, required=False)
        actor = actor_name(current_user) if current_user else "현장"
        record_id = blend_service.create_blend_record(
            connection,
            recipe_id=body.recipe_id,
            product_name=body.product_name,
            ink_name=body.ink_name,
            position=body.position,
            worker=body.worker,
            work_date=body.work_date,
            work_time=body.work_time,
            total_amount=body.total_amount,
            scale=body.scale,
            note=body.note,
            details=[d.model_dump() for d in body.details],
            created_by=actor,
            created_at=utc_now_text(),
        )
        deducted = 0
        if body.deduct_stock:
            deducted = blend_service.deduct_blend_stock(
                connection, record_id,
                actor_id=(current_user or {}).get("id"),
                actor_name=actor,
                created_at=utc_now_text(),
            )
        record = blend_service.get_blend_record(connection, record_id)
        record["stock_deducted"] = deducted
        write_audit_log(
            connection,
            action="blend_record_create",
            actor=current_user,
            target_type="blend_record",
            target_id=str(record_id),
            target_label=record["product_lot"],
            details={
                "product_name": body.product_name,
                "total_amount": body.total_amount,
                "items": len(body.details),
            },
        )
        connection.commit()
        return record

    @router.get("/blend/records/{record_id}/export")
    def blend_export(
        record_id: int,
        connection: sqlite3.Connection = Depends(get_db),
    ) -> StreamingResponse:
        record = blend_service.get_blend_record(connection, record_id)
        if not record:
            raise HTTPException(status_code=404, detail="배합 기록을 찾을 수 없습니다.")
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Border, Font, Side

        wb = Workbook()
        ws = wb.active
        ws.title = "배합실적서"
        bold = Font(bold=True)
        thin = Side(style="thin", color="CCCCCC")
        border = Border(left=thin, right=thin, top=thin, bottom=thin)

        ws["A1"] = "배합 실적서"
        ws["A1"].font = Font(bold=True, size=14)
        ws.merge_cells("A1:G1")
        ws["A1"].alignment = Alignment(horizontal="center")

        meta = [
            ("제품 LOT", record["product_lot"], "제품", f"{record['product_name']}"
             + (f" / {record['ink_name']}" if record.get("ink_name") else "")),
            ("작업자", record["worker"], "작업일시",
             f"{record['work_date']} {record.get('work_time') or ''}".strip()),
            ("총 배합량(g)", record["total_amount"], "저울", record.get("scale") or "-"),
        ]
        row = 3
        for k1, v1, k2, v2 in meta:
            ws.cell(row=row, column=1, value=k1).font = bold
            ws.cell(row=row, column=2, value=v1)
            ws.cell(row=row, column=4, value=k2).font = bold
            ws.cell(row=row, column=5, value=v2)
            row += 1

        header_row = row + 1
        headers = ["#", "품목", "자재 LOT", "비율(%)", "이론량(g)", "실제량(g)", "편차(g)"]
        for col, h in enumerate(headers, start=1):
            cell = ws.cell(row=header_row, column=col, value=h)
            cell.font = bold
            cell.border = border
            cell.alignment = Alignment(horizontal="center")

        r = header_row + 1
        for i, d in enumerate(record["details"], start=1):
            values = [
                i, d["material_name"], d.get("material_lot") or "",
                d.get("ratio"), d.get("theory_amount"), d.get("actual_amount"), d.get("variance"),
            ]
            for col, val in enumerate(values, start=1):
                cell = ws.cell(row=r, column=col, value=val)
                cell.border = border
            r += 1

        v = record.get("variance", {})
        ws.cell(row=r, column=1, value="합계").font = bold
        ws.cell(row=r, column=5, value=v.get("theory_total")).font = bold
        ws.cell(row=r, column=6, value=v.get("actual_total")).font = bold
        ws.cell(row=r, column=7, value=v.get("net_variance")).font = bold

        widths = [5, 22, 16, 10, 12, 12, 10]
        for col, w in enumerate(widths, start=1):
            ws.column_dimensions[chr(64 + col)].width = w
        if record.get("note"):
            ws.cell(row=r + 2, column=1, value=f"비고: {record['note']}")

        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        # 한글 product_lot 대응: ASCII 폴백 + RFC 5987 filename* (UTF-8)
        from urllib.parse import quote
        ascii_name = f"blend-{record_id}.xlsx"
        utf8_name = quote(f"배합실적서-{record['product_lot']}.xlsx")
        disposition = (
            f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{utf8_name}"
        )
        return StreamingResponse(
            buf,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": disposition},
        )

    @router.delete("/blend/records/{record_id}")
    def blend_cancel(
        record_id: int,
        request: Request,
        connection: sqlite3.Connection = Depends(get_db),
    ) -> dict[str, Any]:
        record = blend_service.get_blend_record(connection, record_id)
        if not record:
            raise HTTPException(status_code=404, detail="배합 기록을 찾을 수 없습니다.")
        current_user = get_current_user(request, required=False)
        restored = blend_service.reverse_blend_stock(connection, record_id)
        connection.execute(
            "UPDATE blend_records SET status = 'canceled', updated_at = ? WHERE id = ?",
            (utc_now_text(), record_id),
        )
        write_audit_log(
            connection,
            action="blend_record_cancel",
            actor=current_user,
            target_type="blend_record",
            target_id=str(record_id),
            target_label=record["product_lot"],
        )
        connection.commit()
        return {"canceled": record_id, "stock_restored": restored}

    return router
