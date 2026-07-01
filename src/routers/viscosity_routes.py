"""합성 점도 등록·추세·이상 분석 라우트.

접근: 로그인 없이 누구나 열람·등록·설정 가능(사내 공용 단말 운영 편의). 등록/변경
시 로그인 사용자가 있으면 그 이름으로, 없으면 '현장' 으로 created_by/audit 에 기록.
(op_router/mgr_router 둘 다 무인증 — 코드 구조 유지를 위해 두 라우터로 분리만 유지)

Plan:   docs/01-plan/features/viscosity-analysis.plan.md
Design: docs/02-design/features/viscosity-analysis.design.md

Endpoints (모두 무인증):
    GET    /viscosity/overview
    GET    /viscosity/products
    GET    /viscosity/products/{id}             분석 포함
    POST   /viscosity/readings
    POST   /viscosity/products
    PATCH  /viscosity/products/{id}
    DELETE /viscosity/readings/{id}
    GET    /viscosity/products/{id}/export      CSV
"""

import csv
import io
import sqlite3
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from ..auth import get_current_user
from ..db import get_db, utc_now_text, write_audit_log
from ..services import viscosity_service
from .models import (
    ViscosityProductCreateBody,
    ViscosityProductUpdateBody,
    ViscosityReadingBody,
    actor_name,
)


def _require_product(connection: sqlite3.Connection, product_id: int) -> dict[str, Any]:
    product = viscosity_service.get_product(connection, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="점도 제품을 찾을 수 없습니다.")
    return product


def build_router() -> tuple[APIRouter, APIRouter]:
    # 점도 화면은 로그인 없이 누구나 열람·등록·설정 가능(사내 공용 단말 운영 편의).
    # 등록/변경 시 로그인 사용자가 있으면 그 이름으로, 없으면 '현장' 으로 기록.
    op_router = APIRouter()
    mgr_router = APIRouter()

    # ---- 조회 + 등록 -----------------------------------------------------
    @op_router.get("/viscosity/overview")
    def viscosity_overview(
        connection: sqlite3.Connection = Depends(get_db),
    ) -> dict[str, Any]:
        return viscosity_service.overview(connection)

    @op_router.get("/viscosity/products")
    def viscosity_products(
        connection: sqlite3.Connection = Depends(get_db),
    ) -> dict[str, Any]:
        return {"items": viscosity_service.list_products(connection)}

    @op_router.get("/viscosity/products/{product_id}")
    def viscosity_product_detail(
        product_id: int,
        granularity: str = "quarter",
        year: int | None = None,
        connection: sqlite3.Connection = Depends(get_db),
    ) -> dict[str, Any]:
        product = _require_product(connection, product_id)
        if granularity not in ("month", "quarter", "year"):
            granularity = "quarter"
        return viscosity_service.analyze_product(
            connection, product, granularity=granularity, year=year
        )

    @op_router.post("/viscosity/readings")
    def viscosity_add_reading(
        body: ViscosityReadingBody,
        request: Request,
        connection: sqlite3.Connection = Depends(get_db),
    ) -> dict[str, Any]:
        current_user = get_current_user(request, required=False)
        product = _require_product(connection, body.product_id)
        # 입력 전 '같은 연도' 표본 기준으로 판정 (연도별 기준 + 자기 자신 평균 오염 방지)
        resolved_date = (
            body.measured_date
            or viscosity_service.parse_lot_date(body.lot_no)
            or utc_now_text()[:10]
        )
        reading_year = int(resolved_date[:4]) if resolved_date[:4].isdigit() else None
        verdict = viscosity_service.classify_value(
            connection, product, body.viscosity, year=reading_year
        )
        try:
            reading_id = viscosity_service.add_reading(
                connection,
                product_id=product["id"],
                lot_no=body.lot_no,
                viscosity=body.viscosity,
                measured_date=body.measured_date,
                memo=body.memo,
                recipe_material=body.recipe_material,
                material_lot=body.material_lot,
                created_by=actor_name(current_user) if current_user else "현장",
                created_at=utc_now_text(),
            )
        except sqlite3.IntegrityError:
            raise HTTPException(
                status_code=409,
                detail=f"이미 등록된 LOT 입니다: {body.lot_no}",
            )
        write_audit_log(
            connection,
            action="viscosity_reading_add",
            actor=current_user,
            target_type="viscosity_reading",
            target_id=str(reading_id),
            target_label=f"{product['code']}/{body.lot_no}",
            details={"viscosity": body.viscosity, "lot_no": body.lot_no},
        )
        connection.commit()
        result = viscosity_service.analyze_product(
            connection, product, year=reading_year
        )
        result["new_reading"] = {
            "lot_no": body.lot_no,
            "viscosity": body.viscosity,
            "year": reading_year,
            "status": verdict["status"],
            "side": verdict["side"],
            "reasons": verdict["reasons"],
        }
        return result

    # ---- manager: 제품 설정 + 측정 삭제 + export -------------------------
    @mgr_router.post("/viscosity/products")
    def viscosity_create_product(
        body: ViscosityProductCreateBody,
        request: Request,
        connection: sqlite3.Connection = Depends(get_db),
    ) -> dict[str, Any]:
        current_user = get_current_user(request, required=False)
        if viscosity_service.get_product_by_code(connection, body.code):
            raise HTTPException(status_code=409, detail=f"이미 존재하는 코드입니다: {body.code}")
        cur = connection.execute(
            """
            INSERT INTO viscosity_products
                (code, name, target, lower_limit, upper_limit, sigma_k, is_active, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 1, ?)
            """,
            (
                body.code.strip(),
                body.name.strip(),
                body.target,
                body.lower_limit,
                body.upper_limit,
                body.sigma_k,
                utc_now_text(),
            ),
        )
        product_id = int(cur.lastrowid)
        write_audit_log(
            connection,
            action="viscosity_product_create",
            actor=current_user,
            target_type="viscosity_product",
            target_id=str(product_id),
            target_label=body.code,
            details={"name": body.name},
        )
        connection.commit()
        return viscosity_service.get_product(connection, product_id)

    @mgr_router.patch("/viscosity/products/{product_id}")
    def viscosity_update_product(
        product_id: int,
        body: ViscosityProductUpdateBody,
        request: Request,
        connection: sqlite3.Connection = Depends(get_db),
    ) -> dict[str, Any]:
        current_user = get_current_user(request, required=False)
        product = _require_product(connection, product_id)
        connection.execute(
            """
            UPDATE viscosity_products
            SET name = ?, target = ?, lower_limit = ?, upper_limit = ?,
                sigma_k = ?, rpm = ?, temperature = ?, remind_daily = ?, is_active = ?
            WHERE id = ?
            """,
            (
                body.name.strip(),
                body.target,
                body.lower_limit,
                body.upper_limit,
                body.sigma_k,
                body.rpm,
                body.temperature,
                1 if body.remind_daily else 0,
                1 if body.is_active else 0,
                product_id,
            ),
        )
        write_audit_log(
            connection,
            action="viscosity_product_update",
            actor=current_user,
            target_type="viscosity_product",
            target_id=str(product_id),
            target_label=product["code"],
            details={
                "target": body.target,
                "lower_limit": body.lower_limit,
                "upper_limit": body.upper_limit,
                "sigma_k": body.sigma_k,
                "remind_daily": body.remind_daily,
                "is_active": body.is_active,
            },
        )
        connection.commit()
        return viscosity_service.get_product(connection, product_id)

    @mgr_router.delete("/viscosity/readings/{reading_id}")
    def viscosity_delete_reading(
        reading_id: int,
        request: Request,
        connection: sqlite3.Connection = Depends(get_db),
    ) -> dict[str, Any]:
        current_user = get_current_user(request, required=False)
        row = connection.execute(
            "SELECT id, product_id, lot_no FROM viscosity_readings WHERE id = ?",
            (reading_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="측정 기록을 찾을 수 없습니다.")
        connection.execute("DELETE FROM viscosity_readings WHERE id = ?", (reading_id,))
        write_audit_log(
            connection,
            action="viscosity_reading_delete",
            actor=current_user,
            target_type="viscosity_reading",
            target_id=str(reading_id),
            target_label=str(row["lot_no"]),
        )
        connection.commit()
        return {"deleted": reading_id}

    @mgr_router.get("/viscosity/products/{product_id}/export")
    def viscosity_export(
        product_id: int,
        connection: sqlite3.Connection = Depends(get_db),
    ) -> StreamingResponse:
        product = _require_product(connection, product_id)
        analysis = viscosity_service.analyze_product(connection, product)

        def _csv_safe(value: Any) -> Any:
            if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
                return "'" + value
            return value

        fieldnames = [
            "lot_no", "viscosity", "measured_date", "status",
            "memo", "recipe_material", "material_lot",
        ]
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for it in analysis["readings"]:
            writer.writerow({
                "lot_no": _csv_safe(it["lot_no"]),
                "viscosity": it["viscosity"],
                "measured_date": it["measured_date"],
                "status": it["status"],
                "memo": _csv_safe(it["memo"] or ""),
                "recipe_material": _csv_safe(it["recipe_material"] or ""),
                "material_lot": _csv_safe(it["material_lot"] or ""),
            })
        output.seek(0)
        filename = f"viscosity-{product['code']}-{date.today().isoformat()}.csv"
        return StreamingResponse(
            output,
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    return op_router, mgr_router
