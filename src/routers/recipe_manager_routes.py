"""Manager-scope recipe endpoints: deletion + progress dashboards.

Provides endpoints accessible to manager-level users for deleting recipes
and viewing aggregate progress/operator dashboards.

Split from src/routers/recipe_routes.py during the split-large-files
PDCA cycle (2026-05).

Endpoints:
    DELETE /recipes/{recipe_id}
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth import require_access_level
from ..db import get_connection, write_audit_log
from ..services import record_delete_service


def build_router() -> APIRouter:
    router = APIRouter()

    @router.delete("/recipes/{recipe_id}")
    def delete_recipe(
        recipe_id: int,
        current_user: dict[str, Any] = Depends(require_access_level("manager")),
        delete_blend_records: bool = Query(default=False),
    ) -> dict[str, Any]:
        with get_connection() as connection:
            result = record_delete_service.delete_recipe(
                connection,
                recipe_id,
                delete_linked_records=delete_blend_records,
            )
            if result is None:
                raise HTTPException(status_code=404, detail="RECIPE_NOT_FOUND")

            write_audit_log(
                connection,
                action="recipe_deleted",
                actor=current_user,
                target_type="recipe",
                target_id=result.recipe_id,
                target_label=result.product_name,
                details={
                    "linked_record_count": result.linked_record_count,
                    "deleted_linked_records": result.deleted_linked_records,
                },
            )
            connection.commit()

        return {
            "status": "ok",
            "deleted_recipe_id": result.recipe_id,
            "linked_record_count": result.linked_record_count,
            "deleted_linked_records": result.deleted_linked_records,
        }

    @router.put("/recipes/{recipe_id}/anchor")
    def set_recipe_anchor(
        recipe_id: int,
        body: dict[str, Any],
        current_user: dict[str, Any] = Depends(require_access_level("manager")),
    ) -> dict[str, Any]:
        """레시피의 기준 자재(anchor_material_id) 지정/해제 — 책임자 전용.

        body: {"material_id": int | None}. None 이면 기준 자재 해제.
        material_id 를 지정할 때는 이 레시피의 recipe_items 중 하나여야 한다.
        배합 화면은 이 자재를 먼저 계량하고 그 실측값으로 다른 자재들의 이론량을 산출한다.
        """
        raw_material_id = body.get("material_id")
        material_id: int | None
        if raw_material_id is None:
            material_id = None
        else:
            try:
                material_id = int(raw_material_id)
            except (TypeError, ValueError):
                raise HTTPException(
                    status_code=400, detail="material_id 는 정수 또는 null 이어야 합니다."
                )

        with get_connection() as connection:
            recipe_row = connection.execute(
                "SELECT id, product_name FROM recipes WHERE id = ?", (recipe_id,)
            ).fetchone()
            if not recipe_row:
                raise HTTPException(status_code=404, detail="레시피를 찾을 수 없습니다.")

            if material_id is not None:
                # 지정 자재가 이 레시피의 recipe_items 중 하나인지 검증
                member = connection.execute(
                    "SELECT 1 FROM recipe_items WHERE recipe_id = ? AND material_id = ? LIMIT 1",
                    (recipe_id, material_id),
                ).fetchone()
                if not member:
                    raise HTTPException(
                        status_code=400,
                        detail="지정한 자재가 이 레시피의 구성 자재가 아닙니다.",
                    )

            connection.execute(
                "UPDATE recipes SET anchor_material_id = ? WHERE id = ?",
                (material_id, recipe_id),
            )
            write_audit_log(
                connection,
                action="recipe_anchor_set",
                actor=current_user,
                target_type="recipe",
                target_id=recipe_id,
                target_label=str(recipe_row["product_name"]),
                details={"anchor_material_id": material_id},
            )
            connection.commit()

        return {
            "status": "ok",
            "recipe_id": recipe_id,
            "anchor_material_id": material_id,
        }

    return router
