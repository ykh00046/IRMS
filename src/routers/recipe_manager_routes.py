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


def _stage1_would_cycle(
    connection, recipe_id: int, stage1_recipe_id: int, *, max_depth: int = 50
) -> bool:
    """GAP 4: stage1_recipe_id 링크를 따라 올라가며 recipe_id 로 되돌아오면 True(순환).

    A→B→A(2노드 상호 지정)·자기참조·더 긴 순환을 유한 걸음(visited-set + 깊이 상한)으로
    검출한다. recipe_id 가 stage1 을 지정하려 할 때, 대상 체인이 이미 recipe_id 를
    (직·간접으로) 1차로 가리키면 순환이 된다.
    """
    seen: set[int] = set()
    current: int | None = stage1_recipe_id
    depth = 0
    while current is not None and depth < max_depth:
        if current == recipe_id:
            return True
        if current in seen:
            break
        seen.add(current)
        row = connection.execute(
            "SELECT stage1_recipe_id FROM recipes WHERE id = ?", (current,)
        ).fetchone()
        if row is None:
            break
        current = row["stage1_recipe_id"]
        depth += 1
    return False


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
                    "relinked_child_ids": list(result.relinked_child_ids),
                    "stage1_cleared_recipe_ids": list(result.stage1_cleared_recipe_ids),
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

    @router.put("/recipes/{recipe_id}/tolerance")
    def set_recipe_tolerance(
        recipe_id: int,
        body: dict[str, Any],
        current_user: dict[str, Any] = Depends(require_access_level("manager")),
    ) -> dict[str, Any]:
        """레시피의 계량 허용 편차(tolerance_g) 지정/해제 — 책임자 전용.

        body: {"tolerance_g": float | None}. None 이면 기준값(0.05g) 으로 되돌림(clear).
        값은 0 < v <= 1000 이어야 한다. recipe_anchor_set 와 동일한 헬퍼/패턴 사용.
        """
        raw = body.get("tolerance_g")
        tolerance_g: float | None
        if raw is None:
            tolerance_g = None
        else:
            try:
                tolerance_g = float(raw)
            except (TypeError, ValueError):
                raise HTTPException(
                    status_code=400, detail="tolerance_g 는 숫자 또는 null 이어야 합니다."
                )
            if not (0 < tolerance_g <= 1000):
                raise HTTPException(
                    status_code=400,
                    detail="허용 편차는 0 초과 1000 이하여야 합니다.",
                )

        with get_connection() as connection:
            recipe_row = connection.execute(
                "SELECT id, product_name FROM recipes WHERE id = ?", (recipe_id,)
            ).fetchone()
            if not recipe_row:
                raise HTTPException(status_code=404, detail="레시피를 찾을 수 없습니다.")

            connection.execute(
                "UPDATE recipes SET tolerance_g = ? WHERE id = ?",
                (tolerance_g, recipe_id),
            )
            write_audit_log(
                connection,
                action="recipe_tolerance_set",
                actor=current_user,
                target_type="recipe",
                target_id=recipe_id,
                target_label=str(recipe_row["product_name"]),
                details={"tolerance_g": tolerance_g},
            )
            connection.commit()

        return {
            "status": "ok",
            "recipe_id": recipe_id,
            "tolerance_g": tolerance_g,
        }

    @router.put("/recipes/{recipe_id}/category")
    def set_recipe_category(
        recipe_id: int,
        body: dict[str, Any],
        current_user: dict[str, Any] = Depends(require_access_level("manager")),
    ) -> dict[str, Any]:
        """레시피 분류(약품/합성/잉크/용수) 지정/해제 — 책임자 전용.

        body: {"category": "약품"|"합성"|"잉크"|"용수"|null}. null 이면 미분류로 되돌림.
        recipe_tolerance_set 와 동일한 헬퍼/패턴 사용.
        """
        ALLOWED = {"약품", "합성", "잉크", "용수"}
        raw = body.get("category")
        category: str | None
        if raw is None or raw == "":
            category = None
        else:
            category = str(raw).strip()
            if category not in ALLOWED:
                raise HTTPException(
                    status_code=400,
                    detail="분류는 약품·합성·잉크·용수 중 하나이거나 null 이어야 합니다.",
                )

        with get_connection() as connection:
            recipe_row = connection.execute(
                "SELECT id, product_name FROM recipes WHERE id = ?", (recipe_id,)
            ).fetchone()
            if not recipe_row:
                raise HTTPException(status_code=404, detail="레시피를 찾을 수 없습니다.")

            connection.execute(
                "UPDATE recipes SET category = ? WHERE id = ?", (category, recipe_id)
            )
            write_audit_log(
                connection,
                action="recipe_category_set",
                actor=current_user,
                target_type="recipe",
                target_id=recipe_id,
                target_label=str(recipe_row["product_name"]),
                details={"category": category},
            )
            connection.commit()

        return {"status": "ok", "recipe_id": recipe_id, "category": category}

    @router.put("/recipes/{recipe_id}/use-reactor")
    def set_recipe_use_reactor(
        recipe_id: int,
        body: dict[str, Any],
        current_user: dict[str, Any] = Depends(require_access_level("manager")),
    ) -> dict[str, Any]:
        """레시피 반응기 진행 여부(use_reactor) 지정 — 책임자 전용.

        body: {"use_reactor": true|false}. recipe_category_set 와 동일한 헬퍼/패턴.
        반응기 사용 여부의 소유가 recipes 로 이전되어 배합 반응기 강제·점도 화면 모두
        이 값을 따른다.
        """
        raw = body.get("use_reactor")
        if raw is None or not isinstance(raw, bool):
            raise HTTPException(
                status_code=400,
                detail="use_reactor 는 true 또는 false 이어야 합니다.",
            )
        use_reactor = 1 if raw else 0

        with get_connection() as connection:
            recipe_row = connection.execute(
                "SELECT id, product_name FROM recipes WHERE id = ?", (recipe_id,)
            ).fetchone()
            if not recipe_row:
                raise HTTPException(status_code=404, detail="레시피를 찾을 수 없습니다.")

            connection.execute(
                "UPDATE recipes SET use_reactor = ? WHERE id = ?", (use_reactor, recipe_id)
            )
            write_audit_log(
                connection,
                action="recipe_use_reactor_set",
                actor=current_user,
                target_type="recipe",
                target_id=recipe_id,
                target_label=str(recipe_row["product_name"]),
                details={"use_reactor": bool(use_reactor)},
            )
            connection.commit()

        return {"status": "ok", "recipe_id": recipe_id, "use_reactor": bool(use_reactor)}

    @router.put("/recipes/{recipe_id}/derived")
    def set_recipe_is_derived(
        recipe_id: int,
        body: dict[str, Any],
        current_user: dict[str, Any] = Depends(require_access_level("manager")),
    ) -> dict[str, Any]:
        """레시피 파생 여부(is_derived) 지정 — 책임자 전용.

        body: {"is_derived": true|false}. recipe_use_reactor_set 와 동일한 헬퍼/패턴.
        파생 레시피는 앞 단계의 총량을 이월받아 다시 계량하지 않는다 — 반응기 이월(carry-over)
        허용 여부는 이 값으로 결정된다(use_reactor 와는 독립).
        """
        raw = body.get("is_derived")
        if raw is None or not isinstance(raw, bool):
            raise HTTPException(
                status_code=400,
                detail="is_derived 는 true 또는 false 이어야 합니다.",
            )
        is_derived = 1 if raw else 0

        with get_connection() as connection:
            recipe_row = connection.execute(
                "SELECT id, product_name FROM recipes WHERE id = ?", (recipe_id,)
            ).fetchone()
            if not recipe_row:
                raise HTTPException(status_code=404, detail="레시피를 찾을 수 없습니다.")

            connection.execute(
                "UPDATE recipes SET is_derived = ? WHERE id = ?", (is_derived, recipe_id)
            )
            write_audit_log(
                connection,
                action="recipe_is_derived_set",
                actor=current_user,
                target_type="recipe",
                target_id=recipe_id,
                target_label=str(recipe_row["product_name"]),
                details={"is_derived": bool(is_derived)},
            )
            connection.commit()

        return {"status": "ok", "recipe_id": recipe_id, "is_derived": bool(is_derived)}

    @router.put("/recipes/{recipe_id}/stage1")
    def set_recipe_stage1(
        recipe_id: int,
        body: dict[str, Any],
        current_user: dict[str, Any] = Depends(require_access_level("manager")),
    ) -> dict[str, Any]:
        """레시피 1차 연계(stage1_recipe_id) 지정/해제 — 책임자 전용.

        body: {"stage1_recipe_id": int|null}. null 은 링크 해제. recipe_is_derived_set 와 동일한
        헬퍼/패턴. stage1_recipe_id 가 지정되면 그 레시피가 존재해야 하고, 자기 자신이면 400.
        """
        raw = body.get("stage1_recipe_id")
        if raw is None or raw == "":
            stage1_recipe_id: int | None = None
        else:
            # 정수(id) 또는 숫자 문자열만 허용 — 그 외는 400.
            try:
                stage1_recipe_id = int(raw)
            except (TypeError, ValueError):
                raise HTTPException(
                    status_code=400,
                    detail="stage1_recipe_id 는 정수 또는 null 이어야 합니다.",
                )

        with get_connection() as connection:
            recipe_row = connection.execute(
                "SELECT id, product_name FROM recipes WHERE id = ?", (recipe_id,)
            ).fetchone()
            if not recipe_row:
                raise HTTPException(status_code=404, detail="레시피를 찾을 수 없습니다.")

            if stage1_recipe_id is not None:
                # 자기 자신을 1차로 지정 불가 — 순환/무의미.
                if stage1_recipe_id == recipe_id:
                    raise HTTPException(
                        status_code=400,
                        detail="레시피는 자기 자신을 1차로 지정할 수 없습니다.",
                    )
                # 대상 1차 레시피가 존재해야 함.
                target = connection.execute(
                    "SELECT product_name FROM recipes WHERE id = ?", (stage1_recipe_id,)
                ).fetchone()
                if not target:
                    raise HTTPException(
                        status_code=400,
                        detail="지정한 1차 레시피를 찾을 수 없습니다.",
                    )
                # GAP 4: A↔B 상호 지정(2노드 순환) 등 순환 링크 차단(유한 걸음 검사).
                if _stage1_would_cycle(connection, recipe_id, stage1_recipe_id):
                    raise HTTPException(
                        status_code=400,
                        detail="1차 연계가 순환됩니다 — 이미 상대 레시피가 이 레시피를 1차로 참조하고 있습니다.",
                    )
                target_label = str(target["product_name"])
            else:
                target_label = None

            connection.execute(
                "UPDATE recipes SET stage1_recipe_id = ? WHERE id = ?",
                (stage1_recipe_id, recipe_id),
            )
            write_audit_log(
                connection,
                action="recipe_stage1_set",
                actor=current_user,
                target_type="recipe",
                target_id=recipe_id,
                target_label=str(recipe_row["product_name"]),
                details={"stage1_recipe_id": stage1_recipe_id, "stage1_product_name": target_label},
            )
            connection.commit()

        return {"status": "ok", "recipe_id": recipe_id, "stage1_recipe_id": stage1_recipe_id}

    return router
