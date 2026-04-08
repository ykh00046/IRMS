import csv
import hashlib
import io
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from ..auth import get_current_user, require_access_level
from ..database import get_connection, list_audit_logs, row_to_dict, utc_now_text, write_audit_log
from ..services.import_parser import parse_import_text
from .models import ImportRequest, StatusUpdateRequest, actor_name, recipe_label


def _fetch_recipe_items(connection, recipe_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    """Shared helper to fetch recipe items with material info."""
    if not recipe_ids:
        return {}
    item_rows = connection.execute(
        """
        SELECT
            ri.recipe_id,
            ri.material_id,
            m.name AS material_name,
            m.unit_type,
            m.unit,
            m.color_group,
            COALESCE(ri.value_weight, ri.value_text) AS target_value,
            ri.measured_at,
            ri.measured_by
        FROM recipe_items ri
        JOIN materials m ON m.id = ri.material_id
        WHERE ri.recipe_id IN ({ids})
        ORDER BY ri.recipe_id ASC, m.name ASC
        """.format(
            ids=", ".join("?" for _ in recipe_ids)
        ),
        recipe_ids,
    ).fetchall()

    item_map: dict[int, list[dict[str, Any]]] = {}
    for item_row in item_rows:
        item_map.setdefault(int(item_row["recipe_id"]), []).append(row_to_dict(item_row))
    return item_map


def build_router() -> tuple[APIRouter, APIRouter]:
    operator_router = APIRouter(dependencies=[Depends(require_access_level("operator"))])
    manager_router = APIRouter(dependencies=[Depends(require_access_level("manager"))])

    @operator_router.get("/notifications/recipe-imports")
    async def recipe_import_notifications(
        after_id: int = Query(default=0, ge=0),
        limit: int = Query(default=10, ge=1, le=100),
        latest: bool = Query(default=False),
    ) -> dict[str, Any]:
        with get_connection() as connection:
            items = list_audit_logs(
                connection,
                limit=limit,
                action="recipes_imported",
                after_id=after_id,
                ascending=not latest,
            )

        latest_id = max((int(item["id"]) for item in items), default=after_id)
        return {"items": items, "total": len(items), "latest_id": latest_id}

    @operator_router.get("/materials")
    async def list_materials() -> dict[str, Any]:
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT id, name, unit_type, unit, color_group, category, is_active
                FROM materials
                WHERE is_active = 1
                ORDER BY name
                """
            ).fetchall()

            alias_rows = connection.execute(
                """
                SELECT material_id, alias_name
                FROM material_aliases
                ORDER BY alias_name
                """
            ).fetchall()

        alias_map: dict[int, list[str]] = {}
        for alias_row in alias_rows:
            alias_map.setdefault(alias_row["material_id"], []).append(alias_row["alias_name"])

        items = []
        for row in rows:
            payload = row_to_dict(row)
            payload["aliases"] = alias_map.get(row["id"], [])
            items.append(payload)

        return {"items": items, "total": len(items)}

    @operator_router.get("/recipes/products")
    async def list_products() -> dict[str, Any]:
        with get_connection() as connection:
            rows = connection.execute(
                "SELECT DISTINCT product_name FROM recipes ORDER BY product_name ASC"
            ).fetchall()
        items = [row["product_name"] for row in rows]
        return {"items": items, "total": len(items)}

    @operator_router.get("/recipes/by-product")
    async def recipes_by_product(
        product_name: str = Query(..., min_length=1, max_length=200),
        limit: int = Query(default=50, ge=1, le=200),
    ) -> dict[str, Any]:
        with get_connection() as connection:
            recipe_rows = connection.execute(
                """
                SELECT r.id, r.product_name, r.position, r.ink_name, r.status,
                       r.created_by, r.created_at, r.completed_at, r.revision_of
                FROM recipes r
                WHERE r.product_name = ?
                ORDER BY r.created_at DESC, r.id DESC
                LIMIT ?
                """,
                (product_name, limit),
            ).fetchall()

            recipe_ids = [row["id"] for row in recipe_rows]
            item_map = _fetch_recipe_items(connection, recipe_ids)

        items = []
        for recipe_row in recipe_rows:
            payload = row_to_dict(recipe_row)
            recipe_items = item_map.get(recipe_row["id"], [])
            payload["items"] = [
                {
                    "material_id": it["material_id"],
                    "material_name": it["material_name"],
                    "unit": it.get("unit"),
                    "value": it["target_value"],
                }
                for it in recipe_items
            ]
            items.append(payload)

        return {"product_name": product_name, "items": items, "total": len(items)}

    @operator_router.get("/recipes/{recipe_id}/detail")
    async def recipe_detail(recipe_id: int) -> dict[str, Any]:
        with get_connection() as connection:
            recipe_row = connection.execute(
                """
                SELECT r.id, r.product_name, r.position, r.ink_name, r.status,
                       r.created_by, r.created_at, r.completed_at, r.revision_of
                FROM recipes r
                WHERE r.id = ?
                """,
                (recipe_id,),
            ).fetchone()
            if not recipe_row:
                raise HTTPException(status_code=404, detail="Recipe not found")

            item_map = _fetch_recipe_items(connection, [recipe_id])

        recipe = row_to_dict(recipe_row)
        recipe_items = item_map.get(recipe_id, [])
        recipe["items"] = [
            {
                "material_id": it["material_id"],
                "material_name": it["material_name"],
                "unit": it.get("unit"),
                "value": it["target_value"],
                "measured_at": it.get("measured_at"),
                "measured_by": it.get("measured_by"),
            }
            for it in recipe_items
        ]

        # Build TSV for clipboard copy / spreadsheet load
        material_names = [it["material_name"] for it in recipe_items]
        header = ["제품명", "위치", "잉크명"] + material_names
        values = [
            recipe["product_name"] or "",
            recipe["position"] or "",
            recipe["ink_name"] or "",
        ] + [str(it["target_value"]) if it["target_value"] is not None else "" for it in recipe_items]
        recipe["tsv"] = "\t".join(header) + "\n" + "\t".join(values)

        return recipe

    @operator_router.get("/recipes")
    async def list_recipes(
        status: str | None = None,
        search: str | None = Query(None, max_length=100),
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> dict[str, Any]:
        where_parts: list[str] = []
        params: list[Any] = []

        if status:
            where_parts.append("r.status = ?")
            params.append(status)
        if search:
            where_parts.append("(r.product_name LIKE ? OR r.ink_name LIKE ?)")
            token = f"%{search.strip()}%"
            params.extend([token, token])
        if date_from:
            where_parts.append("date(r.created_at) >= ?")
            params.append(str(date_from))
        if date_to:
            where_parts.append("date(r.created_at) <= ?")
            params.append(str(date_to))

        where_sql = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""

        with get_connection() as connection:
            recipe_rows = connection.execute(
                f"""
                SELECT r.id, r.product_name, r.position, r.ink_name, r.status, r.created_by, r.created_at, r.completed_at
                FROM recipes r
                {where_sql}
                ORDER BY r.created_at DESC, r.id DESC
                """,
                params,
            ).fetchall()

            recipe_ids = [row["id"] for row in recipe_rows]
            item_map = _fetch_recipe_items(connection, recipe_ids)

        items = []
        for recipe_row in recipe_rows:
            payload = row_to_dict(recipe_row)
            recipe_items = item_map.get(recipe_row["id"], [])
            payload["items"] = [
                {
                    "material_id": it["material_id"],
                    "material_name": it["material_name"],
                    "unit_type": it.get("unit_type"),
                    "unit": it.get("unit"),
                    "color_group": it.get("color_group"),
                    "value": it["target_value"],
                }
                for it in recipe_items
            ]
            items.append(payload)

        return {"items": items, "total": len(items)}

    @operator_router.patch("/recipes/{recipe_id}/status")
    async def update_recipe_status(
        recipe_id: int,
        body: StatusUpdateRequest,
        request: Request,
    ) -> dict[str, Any]:
        current_user = get_current_user(request)
        transition_map = {
            "start": ("pending", "in_progress"),
            "complete": ("in_progress", "completed"),
            "cancel": ("pending", "canceled"),
        }
        actor = actor_name(current_user)

        with get_connection() as connection:
            row = connection.execute(
                "SELECT id, product_name, ink_name, status FROM recipes WHERE id = ?",
                (recipe_id,),
            ).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Recipe not found")

            current_status = row["status"]
            if body.action == "complete" and current_status == "pending":
                allowed = True
                next_status = "completed"
            elif body.action == "cancel" and current_status == "in_progress":
                allowed = True
                next_status = "canceled"
            else:
                from_status, next_status = transition_map[body.action]
                allowed = current_status == from_status

            if not allowed:
                raise HTTPException(status_code=409, detail="INVALID_STATUS_TRANSITION")

            # Prevent completing a recipe that has unmeasured weighing steps
            if next_status == "completed":
                unmeasured_count = int(
                    connection.execute(
                        "SELECT COUNT(*) AS count FROM recipe_items WHERE recipe_id = ? AND measured_at IS NULL",
                        (recipe_id,),
                    ).fetchone()["count"]
                )
                if unmeasured_count > 0:
                    raise HTTPException(
                        status_code=409,
                        detail=f"WEIGHING_INCOMPLETE:{unmeasured_count}",
                    )

            now = utc_now_text()
            completed_at = now if next_status == "completed" else None

            if body.action in ("complete", "start") and current_status == "pending":
                connection.execute(
                    "UPDATE recipes SET started_by = ?, started_at = ? WHERE id = ? AND started_at IS NULL",
                    (actor, now, recipe_id),
                )

            connection.execute(
                "UPDATE recipes SET status = ?, completed_at = ? WHERE id = ?",
                (next_status, completed_at, recipe_id),
            )

            if next_status == "canceled" and body.reason:
                connection.execute(
                    "UPDATE recipes SET cancel_reason = ? WHERE id = ?",
                    (body.reason, recipe_id),
                )

            write_audit_log(
                connection,
                action="recipe_status_updated",
                actor=current_user,
                target_type="recipe",
                target_id=recipe_id,
                target_label=recipe_label(row_to_dict(row)),
                details={
                    "action": body.action,
                    "from_status": current_status,
                    "to_status": next_status,
                    "reason": body.reason,
                },
            )
            connection.commit()

            updated = connection.execute(
                """
                SELECT id, product_name, position, ink_name, status, created_by, created_at, completed_at
                FROM recipes
                WHERE id = ?
                """,
                (recipe_id,),
            ).fetchone()

        return row_to_dict(updated)

    @manager_router.get("/recipes/progress")
    async def recipe_progress(
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
                    r.created_by, r.created_at, r.completed_at, r.started_by, r.started_at
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
            item_map = _fetch_recipe_items(connection, recipe_ids)

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

    @manager_router.post("/recipes/import/preview")
    async def import_preview(body: ImportRequest) -> dict[str, Any]:
        with get_connection() as connection:
            result = parse_import_text(connection, body.raw_text)
        return result

    @manager_router.post("/recipes/import")
    async def import_recipes(
        body: ImportRequest,
        request: Request,
    ) -> dict[str, Any]:
        current_user = get_current_user(request)
        creator_name = actor_name(current_user)
        with get_connection() as connection:
            parsed = parse_import_text(connection, body.raw_text)
            if parsed["errors"]:
                raise HTTPException(status_code=400, detail={"errors": parsed["errors"]})

            created_ids = []
            now = utc_now_text()
            raw_hash = hashlib.sha256(body.raw_text.encode()).hexdigest()

            for parsed_row in parsed["parsed_rows"]:
                cursor = connection.execute(
                    """
                    INSERT INTO recipes (
                        product_name, position, ink_name, status, created_by, created_at, completed_at,
                        raw_input_hash, raw_input_text, revision_of
                    ) VALUES (?, ?, ?, 'pending', ?, ?, NULL, ?, ?, ?)
                    """,
                    (
                        parsed_row["product_name"],
                        parsed_row["position"],
                        parsed_row["ink_name"],
                        creator_name,
                        now,
                        raw_hash,
                        body.raw_text,
                        body.revision_of,
                    ),
                )
                recipe_id = cursor.lastrowid
                created_ids.append(recipe_id)

                for item in parsed_row["items"]:
                    connection.execute(
                        """
                        INSERT INTO recipe_items (recipe_id, material_id, value_weight, value_text)
                        VALUES (?, ?, ?, ?)
                        """,
                        (recipe_id, item["material_id"], item["value_weight"], item["value_text"]),
                    )

            write_audit_log(
                connection,
                action="recipes_imported",
                actor=current_user,
                target_type="recipe_batch",
                target_label=f"{len(created_ids)} recipes",
                details={
                    "created_count": len(created_ids),
                    "created_ids": created_ids,
                    "warnings_count": len(parsed["warnings"]),
                    "raw_hash": raw_hash,
                    "revision_of": body.revision_of,
                },
            )
            connection.commit()

        return {
            "created_count": len(created_ids),
            "created_ids": created_ids,
            "warnings": parsed["warnings"],
        }

    @manager_router.get("/stats/consumption")
    async def stats_consumption(
        date_from: date = Query(...),
        date_to: date = Query(...),
        color_group: str | None = None,
        category: str | None = None,
    ) -> dict[str, Any]:
        where_parts = ["r.status = 'completed'", "date(r.completed_at) >= ?", "date(r.completed_at) <= ?"]
        params: list[Any] = [str(date_from), str(date_to)]

        if color_group:
            where_parts.append("m.color_group = ?")
            params.append(color_group)
        if category:
            where_parts.append("m.category = ?")
            params.append(category)

        where_sql = " AND ".join(where_parts)

        with get_connection() as connection:
            rows = connection.execute(
                f"""
                SELECT
                    m.id AS material_id,
                    m.name AS material_name,
                    m.unit_type, m.unit, m.color_group, m.category,
                    SUM(CASE WHEN m.unit_type = 'weight' THEN COALESCE(ri.value_weight, 0) ELSE 0 END) AS total_weight,
                    SUM(CASE WHEN m.unit_type = 'count' AND ri.value_text IS NOT NULL THEN 1 ELSE 0 END) AS total_count,
                    COUNT(DISTINCT r.id) AS recipe_count
                FROM recipes r
                JOIN recipe_items ri ON ri.recipe_id = r.id
                JOIN materials m ON m.id = ri.material_id
                WHERE {where_sql}
                GROUP BY m.id, m.name, m.unit_type, m.unit, m.color_group, m.category
                ORDER BY m.name
                """,
                params,
            ).fetchall()

            completed_row = connection.execute(
                f"""
                SELECT COUNT(DISTINCT r.id) AS completed_recipes
                FROM recipes r
                JOIN recipe_items ri ON ri.recipe_id = r.id
                JOIN materials m ON m.id = ri.material_id
                WHERE {where_sql}
                """,
                params,
            ).fetchone()

        items = [row_to_dict(row) for row in rows]
        total_weight = float(sum((row.get("total_weight") or 0) for row in items))
        total_count = float(sum((row.get("total_count") or 0) for row in items))

        return {
            "period": {"from": str(date_from), "to": str(date_to)},
            "summary": {
                "completed_recipes": int(completed_row["completed_recipes"]) if completed_row else 0,
                "active_materials": len(items),
                "total_weight": total_weight,
                "total_count": total_count,
            },
            "items": items,
        }

    @manager_router.get("/stats/export")
    async def stats_export(
        date_from: date = Query(...),
        date_to: date = Query(...),
        color_group: str | None = None,
        category: str | None = None,
    ) -> StreamingResponse:
        response = await stats_consumption(date_from, date_to, color_group, category)
        rows = response["items"]

        fieldnames = ["material_name", "color_group", "category", "unit_type", "unit", "total_weight", "total_count", "recipe_count"]
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

        output.seek(0)
        filename = f"irms-stats-{date_from}-{date_to}.csv"
        return StreamingResponse(
            output,
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    return operator_router, manager_router
