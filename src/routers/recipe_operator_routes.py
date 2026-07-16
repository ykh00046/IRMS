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

from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth import require_access_level
from ..db import (
    get_connection,
    list_audit_logs,
    row_to_dict,
    utc_now_text,
    write_audit_log,
)
from ..services.recipe_helpers import (
    SUPERSEDED_RECIPE_IDS_SQL,
    fetch_chain,
    fetch_recipe_items,
    find_chain_root,
)
from .models import StatusUpdateRequest, actor_name, recipe_label


def build_router() -> APIRouter:
    router = APIRouter()

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
        # current_only(기본): 옛 버전 숨기고 각 체인의 현재 버전(tip)만.
        # 판정 규칙은 recipe_helpers 단일 소스 — 현황·배합 목록·배합 귀결이 모두 같은 규칙.
        revision_filter = (
            f"AND r.id NOT IN ({SUPERSEDED_RECIPE_IDS_SQL})" if current_only else ""
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
        current_user: dict[str, Any] = Depends(require_access_level("manager")),
    ) -> dict[str, Any]:
        """레시피를 DHR 전용으로 지정/해제 — 버전 체인 전체에 적용.

        한 제품의 여러 버전 중 하나만 바꾸면 옛 버전이 일반으로 남아 배합 화면에
        계속 노출되므로, revision 체인 전체를 함께 지정/해제한다.
        일반 조회·배합 선택에서 제외되고 DHR(배합일지) 전용으로만 사용된다.
        """
        is_dhr = 1 if body.get("is_dhr") else 0
        with get_connection() as connection:
            row = connection.execute(
                "SELECT id, product_name FROM recipes WHERE id = ?", (recipe_id,)
            ).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="레시피를 찾을 수 없습니다.")
            root_id = find_chain_root(connection, recipe_id)
            chain_ids = [int(r["id"]) for r in fetch_chain(connection, root_id)] or [recipe_id]
            placeholders = ",".join("?" for _ in chain_ids)
            connection.execute(
                f"UPDATE recipes SET is_dhr = ? WHERE id IN ({placeholders})",
                (is_dhr, *chain_ids),
            )
            write_audit_log(
                connection, action="recipe_dhr_set", actor=current_user,
                target_type="recipe", target_id=recipe_id,
                target_label=str(row["product_name"]),
                details={"is_dhr": bool(is_dhr), "chain_count": len(chain_ids)},
            )
            connection.commit()
        return {"id": recipe_id, "is_dhr": bool(is_dhr), "chain_count": len(chain_ids)}

    @router.get("/recipes/{recipe_id}/detail")
    def recipe_detail(recipe_id: int) -> dict[str, Any]:
        with get_connection() as connection:
            recipe_row = connection.execute(
                """
                SELECT r.id, r.product_name, r.position, r.ink_name, r.status,
                       r.created_by, r.created_at, r.completed_at, r.revision_of, r.remark,
                       r.effective_from, COALESCE(r.is_dhr, 0) AS is_dhr,
                       r.base_total, r.base_totals,
                       r.anchor_material_id,
                       am.name AS anchor_material_name,
                       r.tolerance_g, r.category, r.product_code
                FROM recipes r
                LEFT JOIN materials am ON am.id = r.anchor_material_id
                WHERE r.id = ?
                """,
                (recipe_id,),
            ).fetchone()
            if not recipe_row:
                raise HTTPException(status_code=404, detail="Recipe not found")

            item_map = fetch_recipe_items(connection, [recipe_id])
            step_rows = connection.execute(
                "SELECT position, note FROM recipe_steps WHERE recipe_id = ? "
                "ORDER BY position, id",
                (recipe_id,),
            ).fetchall()

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
        steps = [{"position": int(s["position"]), "note": s["note"]} for s in step_rows]
        recipe["steps"] = steps

        # TSV: 공정 설명('설명' 열)을 자재 사이 원위치에 끼워 수정 등록 왕복 보존
        header = ["반제품명"]
        values = [recipe["product_name"] or ""]
        step_queue = list(steps)
        for idx, it in enumerate(recipe_items):
            while step_queue and step_queue[0]["position"] <= idx:
                header.append("설명")
                values.append(step_queue.pop(0)["note"])
            header.append(it["material_name"])
            values.append(str(it["target_value"]) if it["target_value"] is not None else "")
        while step_queue:  # 마지막 자재 뒤 설명
            header.append("설명")
            values.append(step_queue.pop(0)["note"])
        header.append("비고")
        values.append(recipe.get("remark") or "")
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

        # 현재 사용 = 취소되지 않은 최신 버전(전부 취소면 최신) — 현황·배합 노출과 동일 규칙.
        active = [r for r in chain if r["status"] != "canceled"]
        current_id = max(active or chain, key=lambda r: (r["created_at"] or "", r["id"]))["id"]
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
        limit: int = Query(default=1000, ge=1, le=5000),
    ) -> dict[str, Any]:
        # 현황에는 각 개정 체인의 최신 버전만 — 수정 등록 때마다 같은 제품이 줄줄이
        # 늘어나 보이지 않게. 옛 버전은 '버전 이력'에서 조회.
        # 판정 규칙은 recipe_helpers 단일 소스 (배합 목록·배합 귀결과 동일).
        where_parts: list[str] = [f"r.id NOT IN ({SUPERSEDED_RECIPE_IDS_SQL})"]
        params: list[Any] = []

        if status:
            where_parts.append("r.status = ?")
            params.append(status)
        if search:
            where_parts.append(
                """
                (
                    r.product_name LIKE ?
                    OR r.ink_name LIKE ?
                    OR EXISTS (
                        SELECT 1
                        FROM recipe_items ri
                        JOIN materials m ON m.id = ri.material_id
                        WHERE ri.recipe_id = r.id AND m.name LIKE ?
                    )
                )
                """
            )
            token = f"%{search.strip()}%"
            params.extend([token, token, token])
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
                SELECT r.id, r.product_name, r.position, r.ink_name, r.status, r.category, r.created_by,
                       r.created_at, r.completed_at, r.remark, COALESCE(r.is_dhr, 0) AS is_dhr,
                       r.product_code
                FROM recipes r
                {where_sql}
                ORDER BY r.created_at DESC, r.id DESC
                LIMIT ?
                """,
                [*params, limit],
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
        current_user: dict[str, Any] = Depends(require_access_level("manager")),
    ) -> dict[str, Any]:
        actor = actor_name(current_user)

        with get_connection() as connection:
            row = connection.execute(
                "SELECT id, product_name, ink_name, status FROM recipes WHERE id = ?",
                (recipe_id,),
            ).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Recipe not found")

            current_status = row["status"]
            # 등록 즉시 completed 정책(등록 취소만 노출). 취소는 어떤 활성 상태에서든 허용하고,
            # 레거시 워크플로(start/complete)는 옛 pending/in_progress 데이터를 위해 유지.
            if body.action == "cancel":
                allowed = current_status in ("pending", "in_progress", "completed")
                next_status = "canceled"
            elif body.action == "complete":
                allowed = current_status in ("pending", "in_progress")
                next_status = "completed"
            else:  # start
                allowed = current_status == "pending"
                next_status = "in_progress"

            if not allowed:
                raise HTTPException(status_code=409, detail="INVALID_STATUS_TRANSITION")

            # (구) 계량 미완료(recipe_items.measured_at) 검사는 /blend 전환으로 폐기 —
            # 배합 워크플로에선 measured_at 이 항상 NULL 이라 완료 전환을 영구히 막았다.

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
