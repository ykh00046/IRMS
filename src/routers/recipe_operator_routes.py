"""Operator-scope read endpoints for recipes, recipe history, and materials.

Provides 9 endpoints accessible to operator-level users for browsing recipes,
viewing version history, comparing revisions, and listing active materials.
All endpoints are read-only (GET) except PATCH /recipes/{id}/status which
operators use to advance recipe workflow state.

Split from src/routers/recipe_routes.py during the split-large-files
PDCA cycle (2026-05). See docs/01-plan/features/split-large-files.plan.md.

Endpoints:
    GET    /notifications/recipe-imports
    GET    /materials
    GET    /recipes/products
    GET    /recipes/by-product
    GET    /recipes/{recipe_id}/detail
    GET    /recipes/{recipe_id}/history
    GET    /recipes/history/compare
    GET    /recipes
    PATCH  /recipes/{recipe_id}/status
"""

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ..auth import get_current_user, require_access_level
from ..db import (
    get_connection,
    list_audit_logs,
    row_to_dict,
    utc_now_text,
    write_audit_log,
)
from ..services import stock_service
from ..services.recipe_helpers import (
    fetch_chain,
    fetch_recipe_items,
    find_chain_root,
)
from .models import StatusUpdateRequest, actor_name, recipe_label


def build_router() -> APIRouter:
    router = APIRouter(dependencies=[Depends(require_access_level("operator"))])

    @router.get("/notifications/recipe-imports")
    def recipe_import_notifications(
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

    @router.get("/materials")
    def list_materials() -> dict[str, Any]:
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

    @router.get("/recipes/products")
    def list_products(dhr: bool = Query(default=False)) -> dict[str, Any]:
        # dhr=False(기본): 일반 레시피만. dhr=True: DHR 전용 레시피만(분리 조회).
        with get_connection() as connection:
            rows = connection.execute(
                "SELECT DISTINCT product_name FROM recipes "
                "WHERE COALESCE(is_dhr, 0) = ? ORDER BY product_name ASC",
                (1 if dhr else 0,),
            ).fetchall()
        items = [row["product_name"] for row in rows]
        return {"items": items, "total": len(items)}

    @router.get("/recipes/by-product")
    def recipes_by_product(
        product_name: str = Query(..., min_length=1, max_length=200),
        limit: int = Query(default=50, ge=1, le=200),
        current_only: bool = Query(default=True),
        dhr: bool = Query(default=False),
    ) -> dict[str, Any]:
        # current_only(기본): 옛 버전(다른 리비전의 부모) 숨기고 각 체인의 현재 버전(tip)만.
        revision_filter = (
            "AND r.id NOT IN (SELECT revision_of FROM recipes WHERE revision_of IS NOT NULL)"
            if current_only
            else ""
        )
        # dhr=False(기본): 일반 레시피만. dhr=True: DHR 전용만 — 둘이 섞이지 않게 분리.
        dhr_filter = "AND COALESCE(r.is_dhr, 0) = 1" if dhr else "AND COALESCE(r.is_dhr, 0) = 0"
        with get_connection() as connection:
            recipe_rows = connection.execute(
                f"""
                SELECT r.id, r.product_name, r.position, r.ink_name, r.status,
                       r.created_by, r.created_at, r.completed_at, r.revision_of, r.remark,
                       r.effective_from, COALESCE(r.is_dhr, 0) AS is_dhr
                FROM recipes r
                WHERE r.product_name = ? {revision_filter} {dhr_filter}
                ORDER BY r.created_at DESC, r.id DESC
                LIMIT ?
                """,
                (product_name, limit),
            ).fetchall()

            recipe_ids = [row["id"] for row in recipe_rows]
            item_map = fetch_recipe_items(connection, recipe_ids)

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

    @router.patch("/recipes/{recipe_id}/dhr")
    def set_recipe_dhr(
        recipe_id: int,
        body: dict[str, Any],
        request: Request,
    ) -> dict[str, Any]:
        """레시피를 DHR 전용으로 지정/해제 — 일반 조회·배합 선택에서 제외(DHR 전용으로만 사용)."""
        current_user = get_current_user(request)
        is_dhr = 1 if body.get("is_dhr") else 0
        with get_connection() as connection:
            row = connection.execute(
                "SELECT id, product_name FROM recipes WHERE id = ?", (recipe_id,)
            ).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="레시피를 찾을 수 없습니다.")
            connection.execute("UPDATE recipes SET is_dhr = ? WHERE id = ?", (is_dhr, recipe_id))
            write_audit_log(
                connection, action="recipe_dhr_set", actor=current_user,
                target_type="recipe", target_id=recipe_id,
                target_label=str(row["product_name"]), details={"is_dhr": bool(is_dhr)},
            )
            connection.commit()
        return {"id": recipe_id, "is_dhr": bool(is_dhr)}

    @router.get("/recipes/{recipe_id}/detail")
    def recipe_detail(recipe_id: int) -> dict[str, Any]:
        with get_connection() as connection:
            recipe_row = connection.execute(
                """
                SELECT r.id, r.product_name, r.position, r.ink_name, r.status,
                       r.created_by, r.created_at, r.completed_at, r.revision_of, r.remark,
                       r.effective_from
                FROM recipes r
                WHERE r.id = ?
                """,
                (recipe_id,),
            ).fetchone()
            if not recipe_row:
                raise HTTPException(status_code=404, detail="Recipe not found")

            item_map = fetch_recipe_items(connection, [recipe_id])

        recipe = row_to_dict(recipe_row)
        recipe_items = item_map.get(recipe_id, [])
        recipe["items"] = [
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

    @router.get("/recipes/{recipe_id}/history")
    def recipe_history(recipe_id: int) -> dict[str, Any]:
        with get_connection() as connection:
            exists = connection.execute("SELECT 1 FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
            if not exists:
                raise HTTPException(status_code=404, detail="Recipe not found")
            root_id = find_chain_root(connection, recipe_id)
            chain = fetch_chain(connection, root_id)
            item_map = fetch_recipe_items(connection, [r["id"] for r in chain])

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
                "effective_from": rec.get("effective_from"),
                "remark": rec.get("remark"),
                "revision_of": rec.get("revision_of"),
                "item_count": len(item_map.get(rec["id"], [])),
                "is_current": rec["id"] == current_id,
                "is_root": rec.get("revision_of") is None,
            })
        return {"root_id": root_id, "current_id": current_id, "items": items}

    @router.get("/recipes/history/compare")
    def recipe_history_compare(ids: str = Query(..., min_length=1, max_length=500)) -> dict[str, Any]:
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

            roots = {find_chain_root(connection, int(r["id"])) for r in recipe_rows}
            if len(roots) > 1:
                raise HTTPException(status_code=400, detail="DIFFERENT_CHAINS")

            item_map = fetch_recipe_items(connection, id_list)

        by_id = {int(r["id"]): row_to_dict(r) for r in recipe_rows}

        root_id = next(iter(roots))
        with get_connection() as connection:
            chain = fetch_chain(connection, root_id)
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

    @router.get("/recipes")
    def list_recipes(
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
            item_map = fetch_recipe_items(connection, recipe_ids)

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

    @router.patch("/recipes/{recipe_id}/status")
    def update_recipe_status(
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

    return router
