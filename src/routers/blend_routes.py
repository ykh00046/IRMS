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
from ..services import blend_service, dhr_cache, dhr_excel, dhr_pdf, viscosity_service
from .models import (
    BlendApprovalBody,
    BlendBulkBody,
    BlendCreateBody,
    BlendViscosityBody,
    actor_name,
)


def build_router() -> APIRouter:
    router = APIRouter()

    @router.get("/blend/recipes")
    def blend_recipes(
        dhr: bool = Query(default=False),
        connection: sqlite3.Connection = Depends(get_db),
    ) -> dict[str, Any]:
        # dhr=True: DHR 전용 레시피(일괄 배합일지 생성용). 기본은 일반 레시피.
        return {"items": blend_service.list_blend_recipes(connection, dhr=dhr)}

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

    @router.get("/blend/material-lots")
    def blend_material_lots(
        material_ids: str = "",
        connection: sqlite3.Connection = Depends(get_db),
    ) -> dict[str, Any]:
        ids = [int(x) for x in material_ids.split(",") if x.strip().isdigit()]
        return {"map": blend_service.list_material_lots_map(connection, ids)}

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

    @router.get("/blend/records/export-all")
    def blend_export_all(
        start_date: str | None = None,
        end_date: str | None = None,
        worker: str | None = None,
        search: str | None = None,
        connection: sqlite3.Connection = Depends(get_db),
    ) -> StreamingResponse:
        """전체(필터) 배합 기록을 한 시트로 — 데이터 백업·이관용."""
        records = blend_service.list_blend_records(
            connection, start_date=start_date, end_date=end_date,
            worker=worker, search=search, limit=10000,
        )
        from openpyxl import Workbook
        from openpyxl.styles import Font

        wb = Workbook()
        ws = wb.active
        ws.title = "배합기록"
        headers = ["작업일", "제품LOT", "제품", "잉크", "작업자", "총량(g)", "저울", "상태", "비고"]
        ws.append(headers)
        for c in range(1, len(headers) + 1):
            ws.cell(row=1, column=c).font = Font(bold=True)
        for r in records:
            ws.append([
                r["work_date"], r["product_lot"], r["product_name"], r.get("ink_name") or "",
                r["worker"], r["total_amount"], r.get("scale") or "", r["status"], r.get("note") or "",
            ])
        widths = [12, 18, 16, 14, 10, 10, 10, 10, 24]
        for col, w in enumerate(widths, start=1):
            ws.column_dimensions[chr(64 + col)].width = w
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        from datetime import date as _date
        filename = f"blend-records-{_date.today().isoformat()}.xlsx"
        return StreamingResponse(
            buf,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @router.get("/blend/records/dhr-batch")
    def blend_dhr_batch(
        ids: str = Query(...),
        connection: sqlite3.Connection = Depends(get_db),
    ) -> StreamingResponse:
        """선택한(또는 전체) 배합 기록의 배합일지를 한 PDF로 일괄 출력(최대 200건)."""
        id_list = [int(x) for x in ids.split(",") if x.strip().isdigit()][:200]
        records = [
            r for r in (blend_service.get_blend_record(connection, i) for i in id_list) if r
        ]
        if not records:
            raise HTTPException(status_code=404, detail="배합 기록을 찾을 수 없습니다.")
        pdf_bytes = dhr_pdf.build_batch_dhr_pdf(records)
        from urllib.parse import quote
        utf8_name = quote(f"배합일지-{len(records)}건.pdf")
        disposition = (
            f"attachment; filename=\"dhr-batch.pdf\"; filename*=UTF-8''{utf8_name}"
        )
        return StreamingResponse(
            io.BytesIO(pdf_bytes),
            media_type="application/pdf",
            headers={"Content-Disposition": disposition},
        )

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
        current_user = get_current_user(request, required=False)
        actor = actor_name(current_user) if current_user else "현장"
        now = utc_now_text()
        # 제품(레시피)명으로 점도 제품을 자동 확보 — 사용자는 점도 숫자만 입력.
        product = viscosity_service.ensure_product_by_code(
            connection, record["product_name"], record["product_name"], now
        )
        if not product:
            raise HTTPException(status_code=400, detail="제품명이 없어 점도를 등록할 수 없습니다.")
        first_lot = record["details"][0]["material_lot"] if record["details"] else None
        try:
            viscosity_service.add_reading(
                connection,
                product_id=product["id"],
                lot_no=record["product_lot"],
                viscosity=body.viscosity,
                measured_date=record["work_date"],
                memo=body.memo,
                recipe_material=record["product_name"],
                material_lot=first_lot,
                created_by=actor,
                created_at=now,
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
            worker_sign=body.worker_sign,
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

    @router.post("/blend/records/{record_id}/approve")
    def blend_approve(
        record_id: int,
        body: BlendApprovalBody,
        request: Request,
        connection: sqlite3.Connection = Depends(get_db),
    ) -> dict[str, Any]:
        record = blend_service.get_blend_record(connection, record_id)
        if not record:
            raise HTTPException(status_code=404, detail="배합 기록을 찾을 수 없습니다.")
        current_user = get_current_user(request, required=False)
        now = utc_now_text()
        col = "reviewed" if body.role == "review" else "approved"
        connection.execute(
            f"UPDATE blend_records SET {col}_by = ?, {col}_at = ?, {col}_sign = ?, updated_at = ? WHERE id = ?",
            (body.name.strip(), now, body.signature, now, record_id),
        )
        write_audit_log(
            connection,
            action=f"blend_record_{body.role}",
            actor=current_user,
            target_type="blend_record",
            target_id=str(record_id),
            target_label=record["product_lot"],
            details={"name": body.name},
        )
        connection.commit()
        result = blend_service.get_blend_record(connection, record_id)
        result["viscosity"] = viscosity_service.list_readings_for_blend(connection, record_id)
        return result

    @router.get("/blend/records/{record_id}/export")
    def blend_export(
        record_id: int,
        connection: sqlite3.Connection = Depends(get_db),
    ) -> StreamingResponse:
        record = blend_service.get_blend_record(connection, record_id)
        if not record:
            raise HTTPException(status_code=404, detail="배합 기록을 찾을 수 없습니다.")
        xlsx_bytes = dhr_excel.build_official_dhr_xlsx(record)
        buf = io.BytesIO(xlsx_bytes)
        buf.seek(0)
        # 한글 product_lot 대응: ASCII 폴백 + RFC 5987 filename* (UTF-8)
        from urllib.parse import quote
        ascii_name = f"blend-{record_id}.xlsx"
        utf8_name = quote(f"원료배합일지-{record['product_lot']}.xlsx")
        disposition = (
            f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{utf8_name}"
        )
        return StreamingResponse(
            buf,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": disposition},
        )

    @router.get("/blend/records/{record_id}/pdf")
    def blend_pdf(
        record_id: int,
        connection: sqlite3.Connection = Depends(get_db),
    ) -> StreamingResponse:
        """배합일지 스캔효과 PDF(서명 합성 포함)."""
        record = blend_service.get_blend_record(connection, record_id)
        if not record:
            raise HTTPException(status_code=404, detail="배합 기록을 찾을 수 없습니다.")
        # 캐시(레코드·서명설정 변경 시 자동 무효화) → 없으면 생성 후 저장
        pdf_bytes = dhr_cache.get(record)
        if pdf_bytes is None:
            pdf_bytes = dhr_pdf.build_scanned_dhr_pdf(record)
            dhr_cache.put(record, pdf_bytes)
        from urllib.parse import quote
        utf8_name = quote(f"원료배합일지-{record['product_lot']}.pdf")
        disposition = (
            f"attachment; filename=\"blend-{record_id}.pdf\"; filename*=UTF-8''{utf8_name}"
        )
        return StreamingResponse(
            io.BytesIO(pdf_bytes),
            media_type="application/pdf",
            headers={"Content-Disposition": disposition},
        )

    @router.post("/blend/records/bulk")
    def blend_create_bulk(
        body: BlendBulkBody,
        request: Request,
        connection: sqlite3.Connection = Depends(get_db),
    ) -> dict[str, Any]:
        if not body.entries:
            raise HTTPException(status_code=400, detail="생성할 항목이 없습니다.")
        current_user = get_current_user(request, required=False)
        actor = actor_name(current_user) if current_user else "현장"
        now = utc_now_text()
        try:
            ids = blend_service.create_bulk(
                connection,
                recipe_id=body.recipe_id,
                worker=body.worker,
                scale=body.scale,
                entries=[e.model_dump() for e in body.entries],
                created_by=actor,
                created_at=now,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        if body.deduct_stock:
            for rid in ids:
                blend_service.deduct_blend_stock(
                    connection, rid,
                    actor_id=(current_user or {}).get("id"),
                    actor_name=actor, created_at=now,
                )
        lots = [blend_service.get_blend_record(connection, rid)["product_lot"] for rid in ids]
        write_audit_log(
            connection,
            action="blend_record_bulk_create",
            actor=current_user,
            target_type="blend_record",
            target_id=",".join(str(i) for i in ids),
            target_label=f"{len(ids)}건",
            details={"recipe_id": body.recipe_id, "count": len(ids)},
        )
        connection.commit()
        return {"created": len(ids), "ids": ids, "product_lots": lots}

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
