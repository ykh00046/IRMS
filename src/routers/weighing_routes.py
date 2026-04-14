from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ..auth import get_current_user, require_access_level
from ..database import get_connection, row_to_dict, utc_now_text, write_audit_log
from ..services import stock_service
from .recipe_routes import _format_display_value
from .models import (
    WeighingRecipeCompleteRequest,
    WeighingStepRequest,
    WeighingStepUndoRequest,
    actor_name,
    recipe_label,
)


def build_router() -> APIRouter:
    router = APIRouter(dependencies=[Depends(require_access_level("operator"))])

    @router.get("/weighing/queue")
    async def get_weighing_queue(
        color_group: str | None = Query(default=None),
    ) -> dict[str, Any]:
        allowed_groups = {"all", "black", "red", "blue", "yellow", "none"}
        group_filter = (color_group or "all").strip().lower()
        if group_filter not in allowed_groups:
            raise HTTPException(status_code=400, detail="INVALID_COLOR_GROUP")

        where_parts = [
            "r.status IN ('pending', 'in_progress')",
            "ri.measured_at IS NULL",
        ]
        params: list[Any] = []
        if group_filter != "all":
            where_parts.append("m.color_group = ?")
            params.append(group_filter)

        with get_connection() as connection:
            rows = connection.execute(
                f"""
                SELECT
                    r.id AS recipe_id, r.product_name, r.position,
                    r.ink_name, r.status AS recipe_status, r.created_at,
                    ri.material_id, m.name AS material_name,
                    m.unit_type, m.unit, m.color_group,
                    ri.value_weight, ri.value_text
                FROM recipes r
                JOIN recipe_items ri ON ri.recipe_id = r.id
                JOIN materials m ON m.id = ri.material_id
                WHERE {" AND ".join(where_parts)}
                ORDER BY
                    CASE m.color_group
                        WHEN 'black' THEN 1
                        WHEN 'red' THEN 2
                        WHEN 'blue' THEN 3
                        WHEN 'yellow' THEN 4
                        ELSE 5
                    END,
                    m.name ASC, r.created_at ASC, r.id ASC
                """,
                params,
            ).fetchall()

        from .recipe_routes import _format_display_value

        items: list[dict[str, Any]] = []
        by_color = {"black": 0, "red": 0, "blue": 0, "yellow": 0, "none": 0}
        recipe_ids: set[int] = set()

        for index, row in enumerate(rows, start=1):
            payload = row_to_dict(row)
            payload["target_value"] = _format_display_value(payload.get("value_weight"), payload.get("value_text"))
            payload["sequence"] = index
            items.append(payload)
            recipe_ids.add(row["recipe_id"])
            color = row["color_group"] or "none"
            if color not in by_color:
                by_color["none"] += 1
            else:
                by_color[color] += 1

        return {
            "items": items,
            "summary": {
                "total_steps": len(items),
                "recipe_count": len(recipe_ids),
                "by_color": by_color,
            },
            "color_group": group_filter,
        }

    @router.post("/weighing/step/complete")
    async def complete_weighing_step(
        body: WeighingStepRequest,
        request: Request,
    ) -> dict[str, Any]:
        current_user = get_current_user(request)
        measured_by = actor_name(current_user)
        with get_connection() as connection:
            recipe_row = connection.execute(
                "SELECT id, product_name, ink_name, status FROM recipes WHERE id = ?",
                (body.recipe_id,),
            ).fetchone()
            if not recipe_row:
                raise HTTPException(status_code=404, detail="Recipe not found")
            if recipe_row["status"] not in {"pending", "in_progress"}:
                raise HTTPException(status_code=409, detail="RECIPE_NOT_ACTIVE")

            item_row = connection.execute(
                """
                SELECT ri.id, m.name AS material_name,
                    ri.value_weight, ri.value_text
                FROM recipe_items ri
                JOIN materials m ON m.id = ri.material_id
                WHERE ri.recipe_id = ? AND ri.material_id = ?
                """,
                (body.recipe_id, body.material_id),
            ).fetchone()
            if not item_row:
                raise HTTPException(status_code=404, detail="Recipe item not found")

            measured_at = utc_now_text()

            if recipe_row["status"] == "pending":
                connection.execute(
                    """
                    UPDATE recipes
                    SET status = 'in_progress', completed_at = NULL,
                        started_by = ?, started_at = ?
                    WHERE id = ? AND started_at IS NULL
                    """,
                    (measured_by, measured_at, body.recipe_id),
                )

            update_cursor = connection.execute(
                """
                UPDATE recipe_items
                SET measured_at = ?, measured_by = ?
                WHERE recipe_id = ? AND material_id = ? AND measured_at IS NULL
                """,
                (measured_at, measured_by, body.recipe_id, body.material_id),
            )
            if update_cursor.rowcount == 0:
                raise HTTPException(status_code=409, detail="STEP_ALREADY_COMPLETED")

            stock_info = None
            item_weight = item_row["value_weight"]
            if item_weight is not None:
                stock_info = stock_service.deduct_for_measurement(
                    connection,
                    material_id=body.material_id,
                    weight=float(item_weight),
                    recipe_id=body.recipe_id,
                    recipe_item_id=int(item_row["id"]),
                    actor=current_user,
                )

            remaining_in_recipe = int(
                connection.execute(
                    "SELECT COUNT(*) AS count FROM recipe_items WHERE recipe_id = ? AND measured_at IS NULL",
                    (body.recipe_id,),
                ).fetchone()["count"]
            )

            remaining_total = int(
                connection.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM recipe_items ri
                    JOIN recipes r ON r.id = ri.recipe_id
                    WHERE r.status IN ('pending', 'in_progress') AND ri.measured_at IS NULL
                    """
                ).fetchone()["count"]
            )

            recipe_payload = row_to_dict(recipe_row)
            item_payload = row_to_dict(item_row)
            item_payload["target_value"] = _format_display_value(item_payload.get("value_weight"), item_payload.get("value_text"))
            write_audit_log(
                connection,
                action="weighing_step_completed",
                actor=current_user,
                target_type="recipe_item",
                target_id=f"{body.recipe_id}:{body.material_id}",
                target_label=f"{recipe_label(recipe_payload)} · {item_payload['material_name']}",
                details={
                    "recipe_id": body.recipe_id,
                    "material_id": body.material_id,
                    "material_name": item_payload["material_name"],
                    "target_value": item_payload["target_value"],
                    "measured_by": measured_by,
                    "measured_at": measured_at,
                    "remaining_in_recipe": remaining_in_recipe,
                    "remaining_total": remaining_total,
                },
            )
            connection.commit()

        return {
            "recipe_id": body.recipe_id,
            "material_id": body.material_id,
            "measured_at": measured_at,
            "remaining_in_recipe": remaining_in_recipe,
            "remaining_total": remaining_total,
            "ready_for_recipe_completion": remaining_in_recipe == 0,
        }

    @router.post("/weighing/step/undo")
    async def undo_weighing_step(
        body: WeighingStepUndoRequest,
        request: Request,
    ) -> dict[str, Any]:
        current_user = get_current_user(request)
        with get_connection() as connection:
            recipe_row = connection.execute(
                "SELECT id, product_name, ink_name, status FROM recipes WHERE id = ?",
                (body.recipe_id,),
            ).fetchone()
            if not recipe_row:
                raise HTTPException(status_code=404, detail="Recipe not found")
            if recipe_row["status"] in {"completed", "canceled"}:
                raise HTTPException(status_code=409, detail="RECIPE_ALREADY_CLOSED")

            item_row = connection.execute(
                """
                SELECT ri.id, m.name AS material_name, ri.measured_at
                FROM recipe_items ri
                JOIN materials m ON m.id = ri.material_id
                WHERE ri.recipe_id = ? AND ri.material_id = ?
                """,
                (body.recipe_id, body.material_id),
            ).fetchone()
            if not item_row:
                raise HTTPException(status_code=404, detail="Recipe item not found")
            if not item_row["measured_at"]:
                raise HTTPException(status_code=409, detail="STEP_NOT_COMPLETED")

            stock_service.reverse_measurement(
                connection, recipe_item_id=int(item_row["id"])
            )
            connection.execute(
                """
                UPDATE recipe_items
                SET measured_at = NULL, measured_by = NULL
                WHERE recipe_id = ? AND material_id = ?
                """,
                (body.recipe_id, body.material_id),
            )

            # If recipe was auto-set to in_progress and now has no other measured items,
            # revert to pending
            other_measured = int(
                connection.execute(
                    "SELECT COUNT(*) AS count FROM recipe_items WHERE recipe_id = ? AND measured_at IS NOT NULL",
                    (body.recipe_id,),
                ).fetchone()["count"]
            )
            if other_measured == 0 and recipe_row["status"] == "in_progress":
                connection.execute(
                    "UPDATE recipes SET status = 'pending', started_by = NULL, started_at = NULL WHERE id = ?",
                    (body.recipe_id,),
                )

            write_audit_log(
                connection,
                action="weighing_step_undone",
                actor=current_user,
                target_type="recipe_item",
                target_id=f"{body.recipe_id}:{body.material_id}",
                target_label=f"{recipe_label(row_to_dict(recipe_row))} · {item_row['material_name']}",
                details={
                    "recipe_id": body.recipe_id,
                    "material_id": body.material_id,
                    "material_name": item_row["material_name"],
                },
            )
            connection.commit()

        return {
            "recipe_id": body.recipe_id,
            "material_id": body.material_id,
            "undone": True,
        }

    @router.post("/weighing/recipe/complete")
    async def complete_weighing_recipe(
        body: WeighingRecipeCompleteRequest,
        request: Request,
    ) -> dict[str, Any]:
        current_user = get_current_user(request)
        with get_connection() as connection:
            row = connection.execute(
                """
                SELECT id, product_name, position, ink_name, status, created_by, created_at, completed_at
                FROM recipes WHERE id = ?
                """,
                (body.recipe_id,),
            ).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Recipe not found")
            if row["status"] in {"completed", "canceled"}:
                raise HTTPException(status_code=409, detail="RECIPE_ALREADY_CLOSED")

            remaining_count = int(
                connection.execute(
                    "SELECT COUNT(*) AS count FROM recipe_items WHERE recipe_id = ? AND measured_at IS NULL",
                    (body.recipe_id,),
                ).fetchone()["count"]
            )
            if remaining_count > 0:
                raise HTTPException(
                    status_code=409,
                    detail={"code": "REMAINING_WEIGHING_STEPS", "remaining_count": remaining_count},
                )

            completed_at = utc_now_text()
            connection.execute(
                "UPDATE recipes SET status = 'completed', completed_at = ? WHERE id = ?",
                (completed_at, body.recipe_id),
            )
            write_audit_log(
                connection,
                action="recipe_weighing_completed",
                actor=current_user,
                target_type="recipe",
                target_id=body.recipe_id,
                target_label=recipe_label(row_to_dict(row)),
                details={"completed_at": completed_at, "remaining_count": remaining_count},
            )
            connection.commit()

            updated = connection.execute(
                """
                SELECT id, product_name, position, ink_name, status, created_by, created_at, completed_at
                FROM recipes WHERE id = ?
                """,
                (body.recipe_id,),
            ).fetchone()

        return row_to_dict(updated)

    return router
