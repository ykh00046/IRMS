"""배합 실적(잉크 계량 재구축) 라우트 — DHR Generator 이식.

접근: 점도 화면과 동일하게 로그인 없이 사내 공용 단말에서 사용. 작성자는 로그인
사용자가 있으면 그 이름, 없으면 '현장' 으로 기록.

Plan:   docs/01-plan/features/blend-overhaul.plan.md
Design: docs/02-design/features/blend-overhaul.design.md

Endpoints:
    GET    /blend/recipes                     배합용 레시피 목록 (최신 개정판만)
    GET    /blend/recipes/{id}?total=...      비율·이론량 환산 (개정 자동 귀결)
    GET    /blend/next-lot                    저장 시 부여될 제품 LOT 미리보기
    GET    /blend/workers                     작업자 목록(필터용)
    GET    /blend/material-usage              자재별 사용량 집계
    GET    /blend/product-usage               제품별 배치 빈도
    GET    /blend/batch-details[/export]      배치 상세(+Excel)
    POST   /blend/records                     배합 실적 저장 (작업자 세션 필요)
    POST   /blend/records/bulk                일괄 생성
    GET    /blend/records                     기록 조회(필터)
    GET    /blend/records/export-all          전체 Excel 백업
    GET    /blend/records/dhr-batch           배합일지 일괄 PDF
    GET    /blend/records/{id}                상세(배합상세+편차+점도)
    PUT    /blend/records/{id}                전체 수정 (책임자 전용)
    DELETE /blend/records/{id}                기록 취소/삭제(soft/hard 모두 책임자 전용)
    POST   /blend/records/{id}/restore        soft 취소 복원 (책임자 전용)
    POST   /blend/records/{id}/viscosity      점도 등록(배합 연계 — 점도 화면의 저장 경로)
    POST   /blend/records/{id}/approve        결재 기록 (책임자 전용, 현장 미사용)
    GET    /blend/records/{id}/export         실적서 Excel
    GET    /blend/records/{id}/pdf            배합일지 PDF(?sign=1 서명 합성)
"""

import io
import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from ..auth import get_current_user, has_access_level, require_access_level
from ..blend_session import current_blend_worker, touch_worker_session
from ..db import get_db, utc_now_text, write_audit_log
from ..services import blend_service, dhr_cache, dhr_excel, dhr_pdf, record_delete_service, viscosity_service
from .models import (
    BlendApprovalBody,
    BlendBulkBody,
    BlendContinuousBody,
    BlendCreateBody,
    BlendViscosityBody,
    actor_name,
)


def build_router() -> APIRouter:
    router = APIRouter()

    def require_blend_worker(request: Request) -> str:
        worker = current_blend_worker(request)
        if not worker:
            raise HTTPException(status_code=401, detail="BLEND_WORKER_REQUIRED")
        touch_worker_session(request)
        return worker

    def _mask_manual_entry(request: Request, record: dict[str, Any]) -> dict[str, Any]:
        """수동 입력 표시(manual_entry)는 책임자 전용 — 비책임자 응답에서는 False 로 가린다.

        화면 가림이 아니라 응답 자체를 가려 API 직접 조회로도 노출되지 않는다.
        저장·감사 로그의 원본 값은 불변(조회 표시만 제한)."""
        user = get_current_user(request, required=False)
        if user and has_access_level(user, "manager"):
            return record
        record["manual_entry"] = False
        for d in record.get("details", []) or []:
            d["manual_entry"] = False
        return record

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

    @router.get("/blend/next-lot")
    def blend_next_lot(
        product: str = Query(..., min_length=1),
        date: str | None = Query(default=None),
        connection: sqlite3.Connection = Depends(get_db),
    ) -> dict[str, Any]:
        """저장 시 실제 부여될 product_lot 미리보기({제품명}{YYMMDD}{순번:02d})."""
        work_date = date or utc_now_text()[:10]
        return {"next_lot": blend_service.generate_product_lot(connection, product, work_date)}

    @router.get("/blend/material-usage")
    def blend_material_usage(
        start_date: str = "",
        end_date: str = "",
        connection: sqlite3.Connection = Depends(get_db),
    ) -> dict[str, Any]:
        """배합 기록 기반 자재 사용 분석(기간별 자재별 실제/이론 사용량·건수)."""
        return blend_service.material_usage(connection, start_date or None, end_date or None)

    @router.get("/blend/product-usage")
    def blend_product_usage(
        start_date: str = "",
        end_date: str = "",
        connection: sqlite3.Connection = Depends(get_db),
    ) -> dict[str, Any]:
        """제품별 배합 빈도 분석(기간 내 제품별 배치 수·총 배합량·최근 작업일)."""
        return blend_service.product_usage(connection, start_date or None, end_date or None)

    @router.get("/blend/batch-details")
    def blend_batch_details(
        start_date: str = "",
        end_date: str = "",
        product: str = "",
        limit: int = Query(default=2000, ge=1, le=10000),
        connection: sqlite3.Connection = Depends(get_db),
    ) -> dict[str, Any]:
        """배치 상세(자재별 비율·이론량·실제량·편차 평면 목록, 작업일 역순)."""
        return blend_service.batch_details(
            connection, start_date or None, end_date or None, product or None, limit,
        )

    @router.get("/blend/batch-details/export")
    def blend_batch_details_export(
        start_date: str = "",
        end_date: str = "",
        product: str = "",
        connection: sqlite3.Connection = Depends(get_db),
    ) -> StreamingResponse:
        """배치 상세를 Excel 한 시트로 — 배합 분석·데이터 이관용."""
        result = blend_service.batch_details(
            connection, start_date or None, end_date or None, product or None, limit=10000,
        )
        from openpyxl import Workbook
        from openpyxl.styles import Font

        wb = Workbook()
        ws = wb.active
        ws.title = "배합 상세"
        headers = [
            "작업일", "제품LOT", "제품", "작업자", "자재코드", "자재명",
            "자재LOT", "비율(%)", "이론량(g)", "실제량(g)", "편차(g)",
        ]
        ws.append(headers)
        for c in range(1, len(headers) + 1):
            ws.cell(row=1, column=c).font = Font(bold=True)
        for it in result["items"]:
            ws.append([
                it["work_date"], it["product_lot"], it["product_name"], it["worker"],
                it.get("material_code") or "", it["material_name"], it.get("material_lot") or "",
                it["ratio"], it["theory_amount"], it["actual_amount"], it["variance"],
            ])
        widths = [12, 18, 16, 10, 12, 18, 14, 9, 11, 11, 9]
        for col, w in enumerate(widths, start=1):
            ws.column_dimensions[chr(64 + col)].width = w
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        from datetime import date as _date
        filename = f"blend-batch-details-{_date.today().isoformat()}.xlsx"
        return StreamingResponse(
            buf,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @router.get("/blend/workers")
    def blend_workers(connection: sqlite3.Connection = Depends(get_db)) -> dict[str, Any]:
        return {"items": blend_service.list_workers(connection)}

    @router.get("/blend/material-lot-trace")
    def blend_material_lot_trace(
        lot: str = Query(..., min_length=1, max_length=100),
        limit: int = Query(default=500, ge=1, le=2000),
        connection: sqlite3.Connection = Depends(get_db),
    ) -> dict[str, Any]:
        """자재 LOT 역추적 — 이 LOT 이 들어간 배합 기록·상세(리콜 추적)."""
        return blend_service.trace_material_lot(connection, lot, limit=limit)

    @router.get("/blend/records")
    def blend_records(
        request: Request,
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
        for item in items:
            _mask_manual_entry(request, item)
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
        sign: bool = Query(default=False),
        connection: sqlite3.Connection = Depends(get_db),
    ) -> StreamingResponse:
        """선택한(또는 전체) 배합 기록의 배합일지를 한 PDF로 일괄 출력(최대 200건).

        sign=True 면 서명 합성, 기본은 빈 결재칸(서명 없음).
        """
        id_list = [int(x) for x in ids.split(",") if x.strip().isdigit()][:200]
        records = [
            r for r in (blend_service.get_blend_record(connection, i) for i in id_list) if r
        ]
        if not records:
            raise HTTPException(status_code=404, detail="배합 기록을 찾을 수 없습니다.")
        pdf_bytes = dhr_pdf.build_batch_dhr_pdf(records, sign=sign)
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
        request: Request,
        connection: sqlite3.Connection = Depends(get_db),
    ) -> dict[str, Any]:
        record = blend_service.get_blend_record(connection, record_id)
        if not record:
            raise HTTPException(status_code=404, detail="배합 기록을 찾을 수 없습니다.")
        record["viscosity"] = viscosity_service.list_readings_for_blend(connection, record_id)
        # 공정 설명 줄 — 기록 당시 레시피(recipe_id 원본 보존)의 설명을 함께 표시
        record["steps"] = []
        if record.get("recipe_id"):
            try:
                rows = connection.execute(
                    "SELECT position, note FROM recipe_steps WHERE recipe_id = ? "
                    "ORDER BY position, id",
                    (record["recipe_id"],),
                ).fetchall()
                record["steps"] = [
                    {"position": int(r["position"]), "note": r["note"]} for r in rows
                ]
            except sqlite3.OperationalError:
                pass
        return _mask_manual_entry(request, record)

    @router.post("/blend/records/{record_id}/viscosity")
    def blend_add_viscosity(
        record_id: int,
        body: BlendViscosityBody,
        request: Request,
        connection: sqlite3.Connection = Depends(get_db),
    ) -> dict[str, Any]:
        """점도 등록(배합 실적 연계). UI 는 점도 관리 화면 한 곳 — 이 라우트가 그 화면의
        저장 경로다(blend_record_id 연계 포함). 배합/기록 화면에는 입력 폼을 두지 않는다."""
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
                reactor=record.get("reactor"),
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
        return _mask_manual_entry(request, record)

    @router.post("/blend/records")
    def blend_create(
        body: BlendCreateBody,
        request: Request,
        connection: sqlite3.Connection = Depends(get_db),
    ) -> dict[str, Any]:
        if not body.details:
            raise HTTPException(status_code=400, detail="배합 상세가 비어 있습니다.")
        # 반응기 진행 반제품은 실적 저장 시 반응기(1~4) 지정 필수.
        if blend_service.product_uses_reactor(connection, body.product_name) and body.reactor is None:
            raise HTTPException(status_code=400, detail="반응기를 선택하세요.")

        # 비율·이론량은 서버가 레시피에서 직접 산출한다 — 클라이언트 값은 쓰지 않는다(감사 F-5).
        # 레시피 없이 저장되는 경로(옛 데이터 이관·수동 입력)는 대조할 근거가 없어 그대로 둔다.
        details = [d.model_dump() for d in body.details]
        total_amount = body.total_amount
        if body.recipe_id:
            # 화면이 열려 있는 사이 개정됐으면 옛 배합비다 — 조용히 저장하지 않고 되돌린다.
            if blend_service.resolve_chain_tip(connection, body.recipe_id) != body.recipe_id:
                raise HTTPException(
                    status_code=409,
                    detail="레시피가 개정되었습니다. 화면을 새로고침한 뒤 다시 확인하세요.",
                )
            try:
                details, total_amount = blend_service.derive_details_from_recipe(
                    connection, body.recipe_id, body.total_amount, details
                )
            except blend_service.RecipeMismatchError as exc:
                raise HTTPException(status_code=400, detail=exc.detail) from exc

        # 자재별 허용 편차 검사 — 합계 편차는 제한 없음. 편차는 레시피에서 결정
        # (recipe_id 가 없으면 기본값 0.05g). 메시지는 실제 적용된 편차를 표시.
        tolerance = blend_service.recipe_tolerance_g(connection, body.recipe_id)
        offenders = blend_service.weighing_tolerance_violations(
            details, tolerance_g=tolerance
        )
        if offenders:
            raise HTTPException(
                status_code=400,
                detail=f"허용 편차(±{tolerance}g)를 초과한 자재: "
                + ", ".join(offenders),
            )
        worker = require_blend_worker(request)
        current_user = get_current_user(request, required=False)
        actor = actor_name(current_user) if current_user else "현장"
        record_id = blend_service.create_blend_record(
            connection,
            recipe_id=body.recipe_id,
            product_name=body.product_name,
            ink_name=body.ink_name,
            position=body.position,
            worker=worker,
            work_date=body.work_date,
            work_time=body.work_time,
            total_amount=total_amount,
            scale=body.scale,
            note=body.note,
            details=details,
            created_by=actor,
            created_at=utc_now_text(),
            worker_sign=body.worker_sign,
            reactor=body.reactor,
            manual_entry=body.manual_entry,
        )
        record = blend_service.get_blend_record(connection, record_id)
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
                "manual_entry": body.manual_entry,
            },
        )
        connection.commit()
        return _mask_manual_entry(request, record)

    @router.put(
        "/blend/records/{record_id}",
        dependencies=[Depends(require_access_level("manager"))],
    )
    def blend_update(
        record_id: int,
        body: BlendCreateBody,
        request: Request,
        connection: sqlite3.Connection = Depends(get_db),
    ) -> dict[str, Any]:
        """배합 실적 전체 수정 — 책임자 이상만(현장 무로그인은 401). 드문 정정용.

        product_lot·상태·생성정보·서명(담당/검토/승인)은 보존하고 헤더·상세만 교체.
        """
        record = blend_service.get_blend_record(connection, record_id)
        if not record:
            raise HTTPException(status_code=404, detail="배합 기록을 찾을 수 없습니다.")
        # 제품명은 수정 불가 — product_lot 이 {제품명}{YYMMDD}{순번} 이라 제품명만 바꾸면
        # LOT 접두사가 옛 제품명으로 남아 기록과 어긋난다(감사 F-8). LOT 을 새로 채번하면
        # 이미 출력·보관된 DHR 문서와 어긋나므로(F-1 이 막으려던 바로 그 오염) 재채번도 안 한다.
        # 제품을 잘못 등록했으면 이 기록을 취소하고 새로 등록한다.
        if (body.product_name or "").strip() != (record.get("product_name") or "").strip():
            raise HTTPException(
                status_code=400,
                detail="제품명은 수정할 수 없습니다(제품 LOT 이 제품명으로 채번됩니다). "
                "잘못 등록했다면 이 기록을 취소하고 새로 등록하세요.",
            )
        if not body.details:
            raise HTTPException(status_code=400, detail="배합 상세가 비어 있습니다.")
        if blend_service.product_uses_reactor(connection, body.product_name) and body.reactor is None:
            raise HTTPException(status_code=400, detail="반응기를 선택하세요.")
        # 자재별 허용 편차 — 편차는 레시피(recipe_id) 에서 결정. 없으면 기본값 0.05g.
        tolerance = blend_service.recipe_tolerance_g(connection, body.recipe_id)
        offenders = blend_service.weighing_tolerance_violations(
            [d.model_dump() for d in body.details], tolerance_g=tolerance
        )
        if offenders:
            raise HTTPException(
                status_code=400,
                detail=f"허용 편차(±{tolerance}g)를 초과한 자재: "
                + ", ".join(offenders),
            )
        current_user = get_current_user(request, required=False)
        blend_service.update_blend_record(
            connection,
            record_id,
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
            reactor=body.reactor,
            updated_at=utc_now_text(),
        )
        updated = blend_service.get_blend_record(connection, record_id)
        write_audit_log(
            connection,
            action="blend_record_update",
            actor=current_user,
            target_type="blend_record",
            target_id=str(record_id),
            target_label=updated["product_lot"],
            details={
                "product_name": body.product_name,
                "total_amount": body.total_amount,
                "items": len(body.details),
            },
        )
        connection.commit()
        updated["viscosity"] = viscosity_service.list_readings_for_blend(connection, record_id)
        return updated

    @router.post(
        "/blend/records/{record_id}/approve",
        dependencies=[Depends(require_access_level("manager"))],
    )
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
        sign: bool = Query(default=False),
        connection: sqlite3.Connection = Depends(get_db),
    ) -> StreamingResponse:
        """배합일지 PDF. 기본은 서명 없이(빈 결재칸), sign=True 면 서명 합성."""
        record = blend_service.get_blend_record(connection, record_id)
        if not record:
            raise HTTPException(status_code=404, detail="배합 기록을 찾을 수 없습니다.")
        if sign:
            # 서명본은 캐시하지 않음(기본 비서명본만 캐시)
            pdf_bytes = dhr_pdf.build_scanned_dhr_pdf(record, sign=True)
        else:
            # 캐시(레코드·서명설정 변경 시 자동 무효화) → 없으면 생성 후 저장
            pdf_bytes = dhr_cache.get(record)
            if pdf_bytes is None:
                pdf_bytes = dhr_pdf.build_scanned_dhr_pdf(record, sign=False)
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
        worker = require_blend_worker(request)
        current_user = get_current_user(request, required=False)
        actor = actor_name(current_user) if current_user else "현장"
        now = utc_now_text()
        try:
            ids = blend_service.create_bulk(
                connection,
                recipe_id=body.recipe_id,
                worker=worker,
                scale=body.scale,
                entries=[e.model_dump() for e in body.entries],
                created_by=actor,
                created_at=now,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
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

    @router.post("/blend/records/continuous")
    def blend_create_continuous(
        body: BlendContinuousBody,
        request: Request,
        connection: sqlite3.Connection = Depends(get_db),
    ) -> dict[str, Any]:
        """이어서 계량: 한 레시피 · 동일 총량으로 N개 로트를 한 번에 저장.

        각 로트를 기존 단건 저장과 동일하게 서버 도출·편차검사한 뒤, 모두 통과하면 순차
        저장한다(하나라도 실패하면 아무것도 저장하지 않음). product_lot 은 로트마다 연속 채번.
        """
        if not body.lots:
            raise HTTPException(status_code=400, detail="저장할 로트가 없습니다.")
        if any(not lot for lot in body.lots):
            raise HTTPException(status_code=400, detail="자재 상세가 비어 있는 로트가 있습니다.")
        # 반응기 진행 반제품은 반응기(1~4) 지정 필수 (전 로트 공통).
        if blend_service.product_uses_reactor(connection, body.product_name) and body.reactor is None:
            raise HTTPException(status_code=400, detail="반응기를 선택하세요.")
        # 화면이 열린 사이 개정됐으면 옛 배합비 — 저장 거부(감사 F-5 / 단건과 동일).
        if blend_service.resolve_chain_tip(connection, body.recipe_id) != body.recipe_id:
            raise HTTPException(
                status_code=409,
                detail="레시피가 개정되었습니다. 화면을 새로고침한 뒤 다시 확인하세요.",
            )
        tolerance = blend_service.recipe_tolerance_g(connection, body.recipe_id)
        # 저장 전 전 로트 도출·편차검사 (원자성: 하나라도 실패하면 중단, 저장 없음)
        # lot_totals 가 있으면 그 로트의 총량 오버라이드를 사용(초과 계량 증량).
        derived_lots: list[list[dict[str, Any]]] = []
        lot_totals = body.lot_totals or []
        for lot_no, lot in enumerate(body.lots, start=1):
            lot_total = lot_totals[lot_no - 1] if lot_totals and lot_totals[lot_no - 1] else body.total_amount
            details = [d.model_dump() for d in lot]
            try:
                derived, _total = blend_service.derive_details_from_recipe(
                    connection, body.recipe_id, lot_total, details
                )
            except blend_service.RecipeMismatchError as exc:
                raise HTTPException(status_code=400, detail=f"로트 {lot_no}: {exc.detail}") from exc
            offenders = blend_service.weighing_tolerance_violations(derived, tolerance_g=tolerance)
            if offenders:
                raise HTTPException(
                    status_code=400,
                    detail=f"로트 {lot_no}: 허용 편차(±{tolerance}g) 초과 — " + ", ".join(offenders),
                )
            derived_lots.append(derived)
        worker = require_blend_worker(request)
        current_user = get_current_user(request, required=False)
        actor = actor_name(current_user) if current_user else "현장"
        ids = blend_service.create_continuous(
            connection,
            recipe_id=body.recipe_id,
            product_name=body.product_name,
            ink_name=body.ink_name,
            position=body.position,
            worker=worker,
            work_date=body.work_date,
            work_time=body.work_time,
            total_amount=body.total_amount,
            scale=body.scale,
            note=body.note,
            lots_details=derived_lots,
            created_by=actor,
            created_at=utc_now_text(),
            worker_sign=body.worker_sign,
            reactor=body.reactor,
            lot_totals=body.lot_totals,
        )
        lots = [blend_service.get_blend_record(connection, rid)["product_lot"] for rid in ids]
        write_audit_log(
            connection,
            action="blend_record_continuous_create",
            actor=current_user,
            target_type="blend_record",
            target_id=",".join(str(i) for i in ids),
            target_label=f"{len(ids)}건",
            details={"recipe_id": body.recipe_id, "count": len(ids), "total_amount": body.total_amount},
        )
        connection.commit()
        return {"created": len(ids), "ids": ids, "product_lots": lots}

    @router.delete("/blend/records/{record_id}")
    def blend_cancel(
        record_id: int,
        request: Request,
        hard: bool = Query(default=False),
        reason: str | None = Query(default=None, max_length=500),
        connection: sqlite3.Connection = Depends(get_db),
    ) -> dict[str, Any]:
        # 감사 F-2: soft/hard 모두 책임자 전용. soft 취소도 DHR 기록을 목록·출력·
        # 대시보드에서 숨기는 행위다(종전에는 soft 가 무인증이었다).
        # 인증을 404 조회보다 먼저 — 비인증 호출자에게 기록 존재 여부를 흘리지 않는다.
        # 상태 코드는 기존 hard 분기 관례를 보존: 미로그인·비책임자 모두 403.
        # (get_current_user(required=True)의 401 로 바꾸면 기존
        #  test_blend_hard_delete_requires_manager 의 403 기대가 깨진다.)
        current_user = get_current_user(request, required=False)
        if current_user is None or not has_access_level(current_user, "manager"):
            raise HTTPException(status_code=403, detail="FORBIDDEN")
        record = blend_service.get_blend_record(connection, record_id)
        if not record:
            raise HTTPException(status_code=404, detail="배합 기록을 찾을 수 없습니다.")
        if hard:
            result = record_delete_service.delete_blend_record(connection, record_id)
            if result is None:
                raise HTTPException(status_code=404, detail="배합 기록을 찾을 수 없습니다.")
            write_audit_log(
                connection,
                action="blend_record_deleted",
                actor=current_user,
                target_type="blend_record",
                target_id=str(result.record_id),
                target_label=result.product_lot,
                details={"reason": reason} if reason else None,
            )
            connection.commit()
            return {"deleted": result.record_id}

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
            details={"reason": reason} if reason else None,
        )
        connection.commit()
        return {"canceled": record_id}

    @router.post(
        "/blend/records/{record_id}/restore",
        dependencies=[Depends(require_access_level("manager"))],
    )
    def blend_restore(
        record_id: int,
        request: Request,
        connection: sqlite3.Connection = Depends(get_db),
    ) -> dict[str, Any]:
        """소프트 취소된 배합 기록 복원 (책임자 전용, 감사 F-2)."""
        record = blend_service.get_blend_record(connection, record_id)
        if not record:
            raise HTTPException(status_code=404, detail="배합 기록을 찾을 수 없습니다.")
        if record["status"] != "canceled":
            raise HTTPException(status_code=400, detail="취소 상태의 기록이 아닙니다.")
        current_user = get_current_user(request, required=False)
        connection.execute(
            "UPDATE blend_records SET status = 'completed', updated_at = ? WHERE id = ?",
            (utc_now_text(), record_id),
        )
        write_audit_log(
            connection,
            action="blend_record_restore",
            actor=current_user,
            target_type="blend_record",
            target_id=str(record_id),
            target_label=record["product_lot"],
        )
        connection.commit()
        return {"restored": record_id}

    return router
