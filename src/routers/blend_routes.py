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

import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

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
            },
        )
        connection.commit()
        return record

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
        return {"canceled": record_id}

    return router
