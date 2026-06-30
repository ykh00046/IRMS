"""Manager-scope recipe endpoints: deletion + progress dashboards.

Provides endpoints accessible to manager-level users for deleting recipes
and viewing aggregate progress/operator dashboards.

Split from src/routers/recipe_routes.py during the split-large-files
PDCA cycle (2026-05).

Endpoints:
    DELETE /recipes/{recipe_id}
    GET    /recipes/progress
    GET    /recipes/operator-progress
"""

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ..auth import get_current_user, require_access_level
from ..db import get_connection, row_to_dict, write_audit_log
from ..services import record_delete_service
from ..services.recipe_helpers import fetch_recipe_items


def build_router() -> APIRouter:
    router = APIRouter(dependencies=[Depends(require_access_level("manager"))])

    @router.delete("/recipes/{recipe_id}")
    def delete_recipe(
        recipe_id: int,
        request: Request,
        delete_blend_records: bool = Query(default=False),
    ) -> dict[str, Any]:
        current_user = get_current_user(request)
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

    @router.get("/recipes/progress")
    def recipe_progress(
        status_filter: str = Query(default="active"),
    ) -> dict[str, Any]:
        allowed_filters = {"active", "all", "pending", "in_progress", "completed", "canceled"}
        normalized_filter = status_filter.strip().lower()
        if normalized_filter not in allowed_filters:
            raise HTTPException(status_code=400, detail="INVALID_STATUS_FILTER")

        where_parts: list[str] = []
        params: list[Any] = []
        if normalized_filter == "active":
            where_parts.append("r.status IN ('pending', 'in_progress')")
        elif normalized_filter != "all":
            where_parts.append("r.status = ?")
            params.append(normalized_filter)

        where_sql = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""

        with get_connection() as connection:
            recipe_rows = connection.execute(
                f"""
                SELECT
                    r.id, r.product_name, r.position, r.ink_name, r.status,
                    r.created_by, r.created_at, r.completed_at, r.started_by, r.started_at, r.remark
                FROM recipes r
                {where_sql}
                ORDER BY
                    CASE r.status
                        WHEN 'in_progress' THEN 0
                        WHEN 'pending' THEN 1
                        WHEN 'completed' THEN 2
                        ELSE 3
                    END,
                    COALESCE(r.started_at, r.created_at) DESC, r.id DESC
                """,
                params,
            ).fetchall()

            recipe_ids = [int(row["id"]) for row in recipe_rows]
            item_map = fetch_recipe_items(connection, recipe_ids)

        items: list[dict[str, Any]] = []
        summary = {
            "total_recipes": len(recipe_rows),
            "active_recipes": 0,
            "in_progress_recipes": 0,
            "pending_recipes": 0,
            "completed_recipes": 0,
            "remaining_steps": 0,
            "open_positions": 0,
        }
        active_positions: set[str] = set()

        for recipe_row in recipe_rows:
            payload = row_to_dict(recipe_row)
            recipe_items = item_map.get(int(recipe_row["id"]), [])
            total_steps = len(recipe_items)
            completed_items = [item for item in recipe_items if item.get("measured_at")]
            remaining_items = [item for item in recipe_items if not item.get("measured_at")]
            next_item = remaining_items[0] if remaining_items else None
            last_completed = completed_items[-1] if completed_items else None
            completed_steps = len(completed_items)
            remaining_steps = len(remaining_items)
            progress_pct = round((completed_steps / total_steps) * 100, 1) if total_steps else 0.0

            payload.update(
                {
                    "total_steps": total_steps,
                    "completed_steps": completed_steps,
                    "remaining_steps": remaining_steps,
                    "progress_pct": progress_pct,
                    "next_item": next_item,
                    "remaining_materials": [item["material_name"] for item in remaining_items],
                    "last_completed_item": last_completed,
                }
            )
            items.append(payload)

            if payload["status"] in {"pending", "in_progress"}:
                summary["active_recipes"] += 1
                summary["remaining_steps"] += remaining_steps
                if payload.get("position"):
                    active_positions.add(str(payload["position"]))
            if payload["status"] == "in_progress":
                summary["in_progress_recipes"] += 1
            elif payload["status"] == "pending":
                summary["pending_recipes"] += 1
            elif payload["status"] == "completed":
                summary["completed_recipes"] += 1

        summary["open_positions"] = len(active_positions)
        return {"status_filter": normalized_filter, "summary": summary, "items": items}

    @router.get("/recipes/operator-progress")
    def operator_progress() -> dict[str, Any]:
        today = datetime.now(timezone.utc).date()
        day_start = f"{today}T00:00:00Z"
        day_end = f"{today + timedelta(days=1)}T00:00:00Z"

        with get_connection() as connection:
            # 1) operators who measured today
            op_rows = connection.execute(
                """
                SELECT ri.measured_by AS name,
                       COUNT(*)       AS completed_steps,
                       MAX(ri.measured_at) AS last_measured_at
                FROM recipe_items ri
                WHERE ri.measured_by IS NOT NULL
                  AND ri.measured_at >= ? AND ri.measured_at < ?
                GROUP BY ri.measured_by
                ORDER BY last_measured_at DESC
                """,
                (day_start, day_end),
            ).fetchall()

            operators = []
            for op in op_rows:
                op_name = op["name"]
                completed_steps = op["completed_steps"]

                # 2) recipe IDs this operator touched today
                recipe_id_rows = connection.execute(
                    """
                    SELECT DISTINCT recipe_id
                    FROM recipe_items
                    WHERE measured_by = ? AND measured_at >= ? AND measured_at < ?
                    """,
                    (op_name, day_start, day_end),
                ).fetchall()
                recipe_ids = [r["recipe_id"] for r in recipe_id_rows]

                if not recipe_ids:
                    continue

                placeholders = ", ".join("?" for _ in recipe_ids)

                # 3) total steps across those recipes (for this operator + unassigned)
                total_row = connection.execute(
                    f"""
                    SELECT COUNT(*) AS total_steps
                    FROM recipe_items
                    WHERE recipe_id IN ({placeholders})
                      AND (measured_by = ? OR measured_by IS NULL)
                    """,
                    [*recipe_ids, op_name],
                ).fetchone()
                total_steps = total_row["total_steps"] if total_row else completed_steps

                progress_pct = round((completed_steps / total_steps) * 100, 1) if total_steps else 0.0

                # 4) category summary
                cat_rows = connection.execute(
                    f"""
                    SELECT COALESCE(m.category, '미분류') AS category,
                           COUNT(CASE WHEN ri.measured_at IS NOT NULL
                                       AND ri.measured_at >= ? AND ri.measured_at < ?
                                 THEN 1 END) AS completed,
                           COUNT(*) AS total
                    FROM recipe_items ri
                    JOIN materials m ON m.id = ri.material_id
                    WHERE ri.recipe_id IN ({placeholders})
                    GROUP BY COALESCE(m.category, '미분류')
                    ORDER BY total DESC
                    """,
                    [day_start, day_end, *recipe_ids],
                ).fetchall()
                category_summary = [
                    {"category": r["category"], "completed": r["completed"], "total": r["total"]}
                    for r in cat_rows
                ]

                # 5) current recipe (most recently touched, still in_progress)
                current_row = connection.execute(
                    """
                    SELECT r.id, r.product_name, r.ink_name, r.position
                    FROM recipes r
                    WHERE r.id = (
                        SELECT ri.recipe_id
                        FROM recipe_items ri
                        WHERE ri.measured_by = ?
                          AND ri.measured_at >= ? AND ri.measured_at < ?
                        ORDER BY ri.measured_at DESC LIMIT 1
                    ) AND r.status = 'in_progress'
                    """,
                    (op_name, day_start, day_end),
                ).fetchone()
                current_recipe = (
                    {
                        "recipe_id": current_row["id"],
                        "product_name": current_row["product_name"],
                        "ink_name": current_row["ink_name"],
                        "position": current_row["position"],
                    }
                    if current_row
                    else None
                )

                # 6) worked recipes (product_name + count)
                worked_rows = connection.execute(
                    f"""
                    SELECT r.product_name, COUNT(DISTINCT r.id) AS cnt
                    FROM recipes r
                    WHERE r.id IN ({placeholders})
                    GROUP BY r.product_name
                    ORDER BY cnt DESC
                    """,
                    recipe_ids,
                ).fetchall()
                worked_recipes = [
                    {"product_name": r["product_name"], "count": r["cnt"]}
                    for r in worked_rows
                ]

                operators.append({
                    "name": op_name,
                    "completed_steps": completed_steps,
                    "total_steps": total_steps,
                    "progress_pct": progress_pct,
                    "last_measured_at": op["last_measured_at"],
                    "current_recipe": current_recipe,
                    "category_summary": category_summary,
                    "worked_recipes": worked_recipes,
                })

        return {"date": str(today), "operators": operators, "total_operators": len(operators)}

    return router
