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

from ..auth import (
    authenticate_manager_worker,
    authenticate_user,
    get_current_user,
    has_access_level,
    require_access_level,
)
from ..blend_session import current_blend_worker, touch_worker_session
from ..db import get_db, utc_now_text, write_audit_log
from ..limiter import limiter
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

    @router.get("/blend/mistake-stats")
    def blend_mistake_stats(
        start_date: str = "",
        end_date: str = "",
        connection: sqlite3.Connection = Depends(get_db),
    ) -> dict[str, Any]:
        """작업자·자재별 이상(수동 입력·취소) 통계 — 편차 강제로 편차 대신 이 신호를 본다."""
        return blend_service.mistake_stats(connection, start_date or None, end_date or None)

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

    @router.get("/blend/recent-product-lots")
    def blend_recent_product_lots(
        names: str = Query(default=""),
        limit: int = Query(default=5),
        connection: sqlite3.Connection = Depends(get_db),
    ) -> dict[str, Any]:
        """반제품 원료 LOT 자동 제안용 — names 에 든 제품(반제품)명별 최근 product_lot.

        2단 제조(1차 중간체 → 2차 최종)에서 2차 배합의 원료 행(=1차 반제품명) 자재 LOT 칸에
        1차 배합의 제품 LOT 을 넣어 1차→2차 LOT 연결을 남기는 데 쓴다. 완료(completed) 기록만,
        최신순(id DESC), 반제품별 limit 개, NULL/빈 LOT·중복 제거. 기록 없는 이름은 키 자체 제외.

        각 항목은 {lot, total} — total 은 그 1차 배합 기록의 total_amount(1차 배치 총량)로,
        반응기 이월(carry-over) 입력이 채울 기준값으로 화면에 같이 보여준다.

        names: 쉼표 구분 제품명(빈 항목 제거, 최대 50 — 초과분 무시).
        limit: 반제품당 LOT 개수(1~20 클램프, 기본 5). 0 이하→1, 20 초과→20.
        """
        # limit 클램프 — FastAPI ge/le 가 422 로 거부하는 대신 1~20 으로 끌어온다(스펙 C.2).
        limit = max(1, min(20, limit))
        # names 파싱: strip → 빈 항목 제거 → 순서 보존 중복 제거 → 50개 초과분 무시.
        raw_names = [n.strip() for n in (names or "").split(",")]
        seen: set[str] = set()
        name_list: list[str] = []
        for n in raw_names:
            if n and n not in seen:
                seen.add(n)
                name_list.append(n)
            if len(name_list) >= 50:
                break
        items: dict[str, list[dict[str, Any]]] = {}
        if not name_list:
            return {"items": items}
        # IN (?, ?, ...) 자리표시자 — 제품명 수만큼. total_amount 까지 함께 가져온다(이월 채움용).
        placeholders = ",".join("?" for _ in name_list)
        rows = connection.execute(
            f"SELECT product_name, product_lot, total_amount FROM blend_records "
            f"WHERE product_name IN ({placeholders}) AND status = 'completed' "
            f"ORDER BY id DESC",
            name_list,
        ).fetchall()
        # 반제품별 최신순(id DESC 로 이미 정렬됨)로 순회하며 LOT 단위 중복 제거해 limit 개씩 채운다.
        for r in rows:
            lot_val = (r["product_lot"] or "").strip()
            if not lot_val:
                continue
            lots = items.setdefault(r["product_name"], [])
            if any(it["lot"] == lot_val for it in lots):
                continue
            if len(lots) < limit:
                lots.append({"lot": lot_val, "total": float(r["total_amount"] or 0)})
        return {"items": items}

    @router.get("/blend/product-lot-exists")
    def blend_product_lot_exists(
        name: str = Query(default=""),
        lot: str = Query(default=""),
        connection: sqlite3.Connection = Depends(get_db),
    ) -> dict[str, Any]:
        """반제품 원료 LOT 미등록 차단용 — 주어진 제품명/LOT 의 완료 기록 존재 여부.

        2단 제조(1차 중간체 → 2차 최종)에서 2차 배합의 원료 행(=1차 반제품명) 자재 LOT 칸에
        입력된 값이 실제 1차 배합의 완료(completed) 기록에 존재하는 LOT 인지 검증하여,
        등록되지 않은 LOT 의 입력을 막는 데 쓴다. 양쪽 값은 strip 후 정확히 일치하는
        product_name·product_lot 이며 status='completed' 인 행이 있어야 exists=true.

        name: 검증할 제품(반제품)명(빈 값 → exists=false).
        lot: 검증할 LOT(빈 값 → exists=false).
        """
        name = (name or "").strip()
        lot = (lot or "").strip()
        if not name or not lot:
            return {"exists": False}
        # 정확 일치 — strip 된 name/lot 으로 파라미터화 WHERE. status='completed' 한정.
        row = connection.execute(
            "SELECT 1 FROM blend_records "
            "WHERE product_name = ? AND product_lot = ? AND status = 'completed' LIMIT 1",
            (name, lot),
        ).fetchone()
        return {"exists": row is not None}

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

    # ── 증량(rescale) 책임자 현장 인증 — 세션 생성 없이 자격 증명만 확인 ──
    # 저장 시 approval_id 로 소비되는 1회용 승인 토큰을 발급한다. 비밀번호 검증은
    # 기존 authenticate_user 를 재사용(해시 로직 중복 금지). management-login 과 동일
    # slowapi 레이트리밋(5/분) 으로 무차별 대입을 막는다.
    @router.post("/blend/manager-verify")
    @limiter.limit("5/minute")
    def blend_manager_verify(
        request: Request,
        body: dict[str, Any],
        connection: sqlite3.Connection = Depends(get_db),
    ) -> dict[str, Any]:
        username = str(body.get("username") or "").strip()
        password = str(body.get("password") or "")
        # management-login 과 동일한 순서로 책임자 자격을 검증한다(해시 로직 중복 금지):
        # 이름 기반 책임자(workers.is_manager) 우선, 없으면 레거시 admin(users) 폴백.
        user = authenticate_manager_worker(username, password)
        non_manager = False
        denied_actor: dict[str, Any] | None = None
        if user is None:
            legacy = authenticate_user(username, password)
            if legacy is not None:
                if has_access_level(legacy, "manager"):
                    user = legacy
                else:
                    # 유효한 계정이지만 책임자 권한이 아님 → 403.
                    non_manager = True
                    denied_actor = legacy
        if user is None:
            write_audit_log(
                connection,
                action="blend_rescale_approve_denied",
                actor=denied_actor,
                target_type="rescale_approval",
                target_label=username or "(빈 이름)",
                details={"reason": "not_manager" if non_manager else "invalid_credentials"},
            )
            connection.commit()
            raise HTTPException(
                status_code=403 if non_manager else 401,
                detail="FORBIDDEN" if non_manager else "INVALID_CREDENTIALS",
            )
        # 통과 — 승인 토큰 발급(approver=표시명).
        approver = user.get("display_name") or user.get("username") or "책임자"
        result = blend_service.create_rescale_approval(connection, approver)
        write_audit_log(
            connection,
            action="blend_rescale_approved",
            actor=user,
            target_type="rescale_approval",
            target_id=str(result["approval_id"]),
            target_label=approver,
            details={},
        )
        connection.commit()
        return result

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
        # 반응기 이월(carry-over) 검증·강제 채움 — derive 보다 먼저. 이월 행의 actual_amount
        # 를 1차 배합 총량으로 덮어쓰므로, 그 뒤 derive 가 올바른 기준 실측값으로 이론·총량을
        # 산출하게 한다(잘못된 클라이언트 값이 편차·총량에 스며드는 것을 막는다).
        try:
            blend_service.enforce_carry_over(
                connection, body.recipe_id, body.product_name, details
            )
        except blend_service.CarryOverError as exc:
            raise HTTPException(status_code=400, detail=exc.detail) from exc
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

        # 자재 LOT 필수 — 추적성 핵심. enforce_carry_over·derive 이후 최종 행 상태로 검사.
        missing_lots = blend_service.missing_lot_names(details)
        if missing_lots:
            shown = missing_lots[:5]
            suffix = " …" if len(missing_lots) > 5 else ""
            raise HTTPException(
                status_code=400,
                detail="자재 LOT 를 입력하세요: " + ", ".join(shown) + suffix,
            )

        # 미등록 자가 반제품 LOT 서버 백업 검증 — 클라이언트 fail-open 구멍 방지.
        # 사유(lot_overrides) 전달 시 통과, 아니면 차단.
        unregistered = blend_service.unregistered_product_lots(
            connection, details, body.lot_overrides
        )
        if unregistered:
            shown = unregistered[:5]
            suffix = " …" if len(unregistered) > 5 else ""
            raise HTTPException(
                status_code=400,
                detail="등록되지 않은 LOT 입니다 (사유를 남기고 진행하거나 LOT 를 확인하세요): "
                + ", ".join(shown) + suffix,
            )

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
        # 증량(rescale) 이벤트 검증·승인 소비 — create 직전 확정(같은 트랜잭션).
        # 유효 승인 토큰(approval_id)은 used=1 로 소비되고, 부재 사유(absence_reason)는
        # 미확인으로 기록된다. 3회 이상·무효/재사용/만료 토큰은 400.
        try:
            rescale = blend_service.validate_rescale_events(connection, body.rescale_events)
        except blend_service.RescaleApprovalError as exc:
            raise HTTPException(status_code=400, detail=exc.detail) from exc
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
        # 증량 이벤트가 있으면 컬럼 기록 + 감사. 없으면(rescale=None) 기존 동작 유지(컬럼 기본값 0).
        if rescale is not None:
            blend_service.apply_rescale_to_record(connection, record_id, rescale)
            write_audit_log(
                connection,
                action="blend_rescale_saved",
                actor=current_user,
                target_type="blend_record",
                target_id=str(record_id),
                target_label=record["product_lot"],
                details={
                    "count": rescale["count"],
                    "unapproved": rescale["unapproved"],
                    "totals": rescale["totals"],
                },
            )
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
        details = [d.model_dump() for d in body.details]
        # 반응기 이월(carry-over) 검증·강제 채움 — create 경로와 대칭. carried_over=true 행은
        # 파생 레시피의 기준 자재 + 등록된 1차 LOT 이어야 하고, actual_amount 는 1차 총량으로
        # 강제 덮어써진다(변조 방지). 정정 저장에서도 이 불변식을 지킨다.
        try:
            blend_service.enforce_carry_over(
                connection, body.recipe_id, body.product_name, details
            )
        except blend_service.CarryOverError as exc:
            raise HTTPException(status_code=400, detail=exc.detail) from exc
        # 자재별 허용 편차 — 편차는 레시피(recipe_id) 에서 결정. 없으면 기본값 0.05g.
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
            details=details,
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
        # 반응기 이월(carry-over)은 단일 배합 화면 전용 — 연속(다중 로트) 화면에서는 거부.
        if any(d.carried_over for lot in body.lots for d in lot):
            raise HTTPException(
                status_code=400,
                detail="반응기 이월은 단일 배합 화면에서만 사용할 수 있습니다.",
            )
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
            # 자재 LOT 필수 — 추적성 핵심(단건과 동일 규칙).
            missing_lots = blend_service.missing_lot_names(derived)
            if missing_lots:
                shown = missing_lots[:5]
                suffix = " …" if len(missing_lots) > 5 else ""
                raise HTTPException(
                    status_code=400,
                    detail=f"로트 {lot_no}: 자재 LOT 를 입력하세요: " + ", ".join(shown) + suffix,
                )
            # 미등록 자가 반제품 LOT 서버 백업 검증(단건과 동일 규칙, 사유 전달 시 통과).
            unregistered = blend_service.unregistered_product_lots(
                connection, derived, body.lot_overrides
            )
            if unregistered:
                shown = unregistered[:5]
                suffix = " …" if len(unregistered) > 5 else ""
                raise HTTPException(
                    status_code=400,
                    detail=f"로트 {lot_no}: 등록되지 않은 LOT 입니다 (사유를 남기고 진행하거나 LOT 를 확인하세요): "
                    + ", ".join(shown) + suffix,
                )
            offenders = blend_service.weighing_tolerance_violations(derived, tolerance_g=tolerance)
            if offenders:
                raise HTTPException(
                    status_code=400,
                    detail=f"로트 {lot_no}: 허용 편차(±{tolerance}g) 초과 — " + ", ".join(offenders),
                )
            derived_lots.append(derived)
        # 증량(rescale) 이벤트 검증·승인 소비 — 로트별(lot_rescale_events[j]). 단건 blend_create
        # 와 동일한 validate_rescale_events 를 로트마다 호출한다. 유효 approval_id 는 used=1 로
        # 소비되고, absence_reason 은 그 로트만 미확인(rescale_unacked=1)으로 기록된다. 무효·재사용·
        # 만료·3회 초과는 400. 커밋은 맨 끝 한 번뿐이라, 여기서 400 이 나면 앞 로트에서 소비한
        # 승인 UPDATE 도 함께 롤백된다(get_db 가 미커밋 연결을 close → 자동 롤백 → 원자성).
        lot_rescale_events = body.lot_rescale_events or []
        lot_rescales: list[dict[str, Any] | None] = []
        try:
            for lot_no in range(1, len(body.lots) + 1):
                events = (
                    lot_rescale_events[lot_no - 1]
                    if lot_no - 1 < len(lot_rescale_events)
                    else None
                )
                lot_rescales.append(
                    blend_service.validate_rescale_events(connection, events)
                )
        except blend_service.RescaleApprovalError as exc:
            raise HTTPException(status_code=400, detail=f"로트 {lot_no}: {exc.detail}") from exc
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
        # 증량 이벤트가 있는 로트만 그 로트의 record 에 컬럼 기록 + 감사(단건과 동일 규칙).
        # lot_rescales[j] 는 create_continuous 가 저장한 ids[j] 와 같은 로트를 가리킨다(둘 다
        # body.lots 순서). 이벤트 없는 로트(None)는 건너뛰어 컬럼 기본값 0 을 유지한다.
        for lot_idx, rescale in enumerate(lot_rescales):
            if rescale is None:
                continue
            record_id = ids[lot_idx]
            blend_service.apply_rescale_to_record(connection, record_id, rescale)
            record = blend_service.get_blend_record(connection, record_id)
            write_audit_log(
                connection,
                action="blend_rescale_saved",
                actor=current_user,
                target_type="blend_record",
                target_id=str(record_id),
                target_label=record["product_lot"],
                details={
                    "count": rescale["count"],
                    "unapproved": rescale["unapproved"],
                    "totals": rescale["totals"],
                },
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
