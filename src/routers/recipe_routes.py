import csv
import hashlib
import io
from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..auth import get_current_user, require_access_level
from ..database import get_connection, list_audit_logs, row_to_dict, utc_now_text, write_audit_log
from ..services import stock_service
from ..services.import_parser import parse_import_text
from .models import ImportRequest, StatusUpdateRequest, actor_name, recipe_label


def _format_display_value(weight, text) -> str:
    """Combine weight and text into a display string."""
    if weight is not None and text:
        return f"{weight} ({text})"
    if weight is not None:
        return str(weight)
    if text:
        return text
    return ""


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
            ri.value_weight,
            ri.value_text,
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
        item = row_to_dict(item_row)
        item["target_value"] = _format_display_value(item.get("value_weight"), item.get("value_text"))
        item_map.setdefault(int(item_row["recipe_id"]), []).append(item)
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
                SELECT id, name, unit_type, unit, color_group, category, is_active,
                       stock_quantity, stock_threshold
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
            qty = float(payload.get("stock_quantity") or 0)
            thr = float(payload.get("stock_threshold") or 0)
            payload["stock_status"] = stock_service.stock_status(qty, thr)
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
                       r.created_by, r.created_at, r.completed_at, r.revision_of, r.remark
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
                       r.created_by, r.created_at, r.completed_at, r.revision_of, r.remark
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

    def _find_chain_root(connection, recipe_id: int) -> int:
        row = connection.execute(
            """
            WITH RECURSIVE up(id, parent, depth) AS (
                SELECT id, revision_of, 0 FROM recipes WHERE id = ?
                UNION ALL
                SELECT r.id, r.revision_of, up.depth + 1
                FROM recipes r, up
                WHERE r.id = up.parent AND up.depth < 100
            )
            SELECT id FROM up WHERE parent IS NULL
            ORDER BY depth DESC LIMIT 1
            """,
            (recipe_id,),
        ).fetchone()
        return int(row["id"]) if row else recipe_id

    def _fetch_chain(connection, root_id: int) -> list[dict[str, Any]]:
        rows = connection.execute(
            """
            WITH RECURSIVE chain(id, depth) AS (
                SELECT ?, 0
                UNION ALL
                SELECT r.id, c.depth + 1 FROM recipes r, chain c
                WHERE r.revision_of = c.id AND c.depth < 100
            )
            SELECT r.id, r.product_name, r.position, r.ink_name, r.status,
                   r.created_by, r.created_at, r.completed_at, r.revision_of, r.remark
            FROM recipes r
            WHERE r.id IN (SELECT id FROM chain)
            ORDER BY r.created_at ASC, r.id ASC
            """,
            (root_id,),
        ).fetchall()
        return [row_to_dict(r) for r in rows]

    @operator_router.get("/recipes/{recipe_id}/history")
    async def recipe_history(recipe_id: int) -> dict[str, Any]:
        with get_connection() as connection:
            exists = connection.execute("SELECT 1 FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
            if not exists:
                raise HTTPException(status_code=404, detail="Recipe not found")
            root_id = _find_chain_root(connection, recipe_id)
            chain = _fetch_chain(connection, root_id)
            item_map = _fetch_recipe_items(connection, [r["id"] for r in chain])

        if not chain:
            return {"root_id": root_id, "current_id": recipe_id, "items": []}

        current_id = max(chain, key=lambda r: (r["created_at"] or "", r["id"]))["id"]
        items = []
        for idx, rec in enumerate(chain, start=1):
            items.append({
                "id": rec["id"],
                "version_label": f"v{idx}",
                "product_name": rec["product_name"],
                "position": rec["position"],
                "ink_name": rec["ink_name"],
                "status": rec["status"],
                "created_by": rec["created_by"],
                "created_at": rec["created_at"],
                "remark": rec.get("remark"),
                "revision_of": rec.get("revision_of"),
                "item_count": len(item_map.get(rec["id"], [])),
                "is_current": rec["id"] == current_id,
                "is_root": rec.get("revision_of") is None,
            })
        return {"root_id": root_id, "current_id": current_id, "items": items}

    @operator_router.get("/recipes/history/compare")
    async def recipe_history_compare(ids: str = Query(..., min_length=1, max_length=500)) -> dict[str, Any]:
        try:
            id_list = [int(x.strip()) for x in ids.split(",") if x.strip()]
        except ValueError:
            raise HTTPException(status_code=400, detail="INVALID_IDS")
        if len(id_list) < 2:
            raise HTTPException(status_code=400, detail="NEED_AT_LEAST_TWO")
        if len(id_list) > 50:
            raise HTTPException(status_code=400, detail="TOO_MANY_IDS")

        with get_connection() as connection:
            placeholders = ",".join("?" for _ in id_list)
            recipe_rows = connection.execute(
                f"""
                SELECT id, product_name, position, ink_name, status, created_by,
                       created_at, revision_of, remark
                FROM recipes WHERE id IN ({placeholders})
                """,
                id_list,
            ).fetchall()
            if len(recipe_rows) != len(id_list):
                raise HTTPException(status_code=404, detail="SOME_RECIPES_NOT_FOUND")

            roots = {_find_chain_root(connection, int(r["id"])) for r in recipe_rows}
            if len(roots) > 1:
                raise HTTPException(status_code=400, detail="DIFFERENT_CHAINS")

            item_map = _fetch_recipe_items(connection, id_list)

        by_id = {int(r["id"]): row_to_dict(r) for r in recipe_rows}

        root_id = next(iter(roots))
        with get_connection() as connection:
            chain = _fetch_chain(connection, root_id)
        label_map: dict[int, str] = {}
        for idx, rec in enumerate(chain, start=1):
            label_map[int(rec["id"])] = f"v{idx}"

        ordered = sorted(id_list, key=lambda x: (by_id[x]["created_at"] or "", x))

        versions = []
        for rid in ordered:
            rec = by_id[rid]
            versions.append({
                "id": rid,
                "version_label": label_map.get(rid, "?"),
                "product_name": rec["product_name"],
                "position": rec["position"],
                "ink_name": rec["ink_name"],
                "created_by": rec["created_by"],
                "created_at": rec["created_at"],
            })

        material_order: list[int] = []
        material_names: dict[int, str] = {}
        per_recipe: dict[int, dict[int, dict]] = {}
        for rid in ordered:
            per_recipe[rid] = {}
            for it in item_map.get(rid, []):
                mid = int(it["material_id"])
                per_recipe[rid][mid] = it
                if mid not in material_names:
                    material_names[mid] = it["material_name"]
                    material_order.append(mid)

        material_order.sort(key=lambda mid: material_names[mid])

        materials_payload = []
        for mid in material_order:
            values = []
            distinct_values: set[str] = set()
            present_count = 0
            for rid in ordered:
                it = per_recipe[rid].get(mid)
                if it:
                    present_count += 1
                    weight = it.get("value_weight")
                    text = it.get("value_text")
                    key = f"{weight}|{text}"
                    distinct_values.add(key)
                    values.append({
                        "version_id": rid,
                        "value_weight": weight,
                        "value_text": text,
                        "display": it.get("target_value"),
                    })
                else:
                    values.append({
                        "version_id": rid,
                        "value_weight": None,
                        "value_text": None,
                        "display": None,
                    })

            if present_count < len(ordered):
                status = "partial"
            elif len(distinct_values) == 1:
                status = "same"
            else:
                status = "modified"

            materials_payload.append({
                "material_id": mid,
                "material_name": material_names[mid],
                "values": values,
                "change_status": status,
            })

        return {"versions": versions, "materials": materials_payload}

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
                SELECT r.id, r.product_name, r.position, r.ink_name, r.status, r.created_by, r.created_at, r.completed_at, r.remark
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

    @manager_router.delete("/recipes/{recipe_id}")
    async def delete_recipe(recipe_id: int, request: Request) -> dict[str, str]:
        current_user = get_current_user(request)
        with get_connection() as connection:
            row = connection.execute(
                "SELECT id, product_name, ink_name, status, created_by FROM recipes WHERE id = ?",
                (recipe_id,),
            ).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="RECIPE_NOT_FOUND")

            if row["status"] not in ("pending", "canceled"):
                raise HTTPException(
                    status_code=400,
                    detail="CANNOT_DELETE_ACTIVE_RECIPE",
                )

            connection.execute("DELETE FROM recipe_items WHERE recipe_id = ?", (recipe_id,))
            connection.execute("DELETE FROM recipes WHERE id = ?", (recipe_id,))
            write_audit_log(
                connection,
                action="recipe_deleted",
                actor=current_user,
                target_type="recipe",
                target_id=recipe_id,
                target_label=f"{row['product_name']} · {row['ink_name']}",
                details={"status": row["status"], "created_by": row["created_by"]},
            )
            connection.commit()

        return {"status": "ok"}

    # ---- material stock tracking ----

    class _StockAmountBody(BaseModel):
        amount: float
        note: str | None = None

    class _StockAdjustBody(BaseModel):
        new_quantity: float
        note: str

    class _StockDiscardBody(BaseModel):
        amount: float
        note: str

    class _StockThresholdBody(BaseModel):
        threshold: float

    def _ensure_material(connection, material_id: int) -> dict:
        row = connection.execute(
            "SELECT id, name FROM materials WHERE id = ? AND is_active = 1",
            (material_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="MATERIAL_NOT_FOUND")
        return row_to_dict(row)

    @operator_router.get("/materials/stock")
    async def get_material_stock() -> dict[str, Any]:
        with get_connection() as connection:
            items = stock_service.list_stock(connection)
        return {"items": items, "total": len(items)}

    @operator_router.get("/materials/{material_id}/stock-log")
    async def get_material_stock_log(material_id: int, limit: int = Query(50, ge=1, le=500)) -> dict[str, Any]:
        with get_connection() as connection:
            _ensure_material(connection, material_id)
            logs = stock_service.list_logs(connection, material_id, limit=limit)
        return {"items": logs, "total": len(logs)}

    @manager_router.post("/materials/{material_id}/stock/restock")
    async def material_stock_restock(material_id: int, body: _StockAmountBody, request: Request) -> dict[str, Any]:
        current_user = get_current_user(request)
        with get_connection() as connection:
            material = _ensure_material(connection, material_id)
            try:
                result = stock_service.restock(
                    connection,
                    material_id=material_id,
                    amount=body.amount,
                    actor=current_user,
                    note=body.note,
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
            write_audit_log(
                connection,
                action="material_stock_restock",
                actor=current_user,
                target_type="material",
                target_id=str(material_id),
                target_label=material["name"],
                details={"amount": body.amount, "note": body.note, **result},
            )
            connection.commit()
        return result

    @manager_router.post("/materials/{material_id}/stock/adjust")
    async def material_stock_adjust(material_id: int, body: _StockAdjustBody, request: Request) -> dict[str, Any]:
        current_user = get_current_user(request)
        with get_connection() as connection:
            material = _ensure_material(connection, material_id)
            try:
                result = stock_service.adjust(
                    connection,
                    material_id=material_id,
                    new_quantity=body.new_quantity,
                    actor=current_user,
                    note=body.note,
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
            write_audit_log(
                connection,
                action="material_stock_adjust",
                actor=current_user,
                target_type="material",
                target_id=str(material_id),
                target_label=material["name"],
                details={"new_quantity": body.new_quantity, "note": body.note, **result},
            )
            connection.commit()
        return result

    @manager_router.post("/materials/{material_id}/stock/discard")
    async def material_stock_discard(material_id: int, body: _StockDiscardBody, request: Request) -> dict[str, Any]:
        current_user = get_current_user(request)
        with get_connection() as connection:
            material = _ensure_material(connection, material_id)
            try:
                result = stock_service.discard(
                    connection,
                    material_id=material_id,
                    amount=body.amount,
                    actor=current_user,
                    note=body.note,
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
            write_audit_log(
                connection,
                action="material_stock_discard",
                actor=current_user,
                target_type="material",
                target_id=str(material_id),
                target_label=material["name"],
                details={"amount": body.amount, "note": body.note, **result},
            )
            connection.commit()
        return result

    @manager_router.patch("/materials/{material_id}/stock-threshold")
    async def material_stock_threshold(material_id: int, body: _StockThresholdBody, request: Request) -> dict[str, Any]:
        current_user = get_current_user(request)
        with get_connection() as connection:
            material = _ensure_material(connection, material_id)
            try:
                stock_service.set_threshold(connection, material_id, body.threshold)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
            write_audit_log(
                connection,
                action="material_stock_threshold_set",
                actor=current_user,
                target_type="material",
                target_id=str(material_id),
                target_label=material["name"],
                details={"threshold": body.threshold},
            )
            connection.commit()
        return {"material_id": material_id, "threshold": body.threshold}

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

    @manager_router.get("/recipes/operator-progress")
    async def operator_progress() -> dict[str, Any]:
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

            if not body.force and body.revision_of is None:
                existing = connection.execute(
                    "SELECT id, product_name, created_at FROM recipes WHERE raw_input_hash = ? AND revision_of IS NULL LIMIT 5",
                    (raw_hash,),
                ).fetchall()
                if existing:
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "code": "DUPLICATE_IMPORT",
                            "message": "동일한 내용이 이미 등록되어 있습니다. 다시 등록하려면 force 옵션을 사용하세요.",
                            "existing": [
                                {"id": r["id"], "product_name": r["product_name"], "created_at": r["created_at"]}
                                for r in existing
                            ],
                        },
                    )

            for parsed_row in parsed["parsed_rows"]:
                cursor = connection.execute(
                    """
                    INSERT INTO recipes (
                        product_name, position, ink_name, status, created_by, created_at, completed_at,
                        raw_input_hash, raw_input_text, revision_of, remark
                    ) VALUES (?, ?, ?, 'pending', ?, ?, NULL, ?, ?, ?, ?)
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
                        parsed_row.get("remark"),
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
