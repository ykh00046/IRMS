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
from ..services.recipe_helpers import (
    chain_member_ids,
    default_chain_tip,
    find_chain_root,
    resolve_chain_tip,
)


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
        move_records_to: int | None = Query(default=None),
    ) -> dict[str, Any]:
        if move_records_to is not None and delete_blend_records:
            raise HTTPException(
                status_code=400, detail="기록 삭제와 옮기기는 함께 쓸 수 없습니다."
            )
        if move_records_to is not None and move_records_to == recipe_id:
            raise HTTPException(status_code=400, detail="같은 판으로는 옮길 수 없습니다.")
        with get_connection() as connection:
            if move_records_to is not None:
                # 기록을 옮길 판 검증 — 같은 반제품(체인)의 살아 있는 판이어야 한다.
                exists = connection.execute(
                    "SELECT 1 FROM recipes WHERE id = ?", (recipe_id,)
                ).fetchone()
                if exists is None:
                    raise HTTPException(status_code=404, detail="RECIPE_NOT_FOUND")
                target_row = connection.execute(
                    "SELECT id, status FROM recipes WHERE id = ?", (move_records_to,)
                ).fetchone()
                if target_row is None:
                    raise HTTPException(status_code=404, detail="옮길 판을 찾을 수 없습니다.")
                if find_chain_root(connection, move_records_to) != find_chain_root(
                    connection, recipe_id
                ):
                    raise HTTPException(
                        status_code=400, detail="같은 반제품의 판으로만 옮길 수 있습니다."
                    )
                if target_row["status"] == "canceled":
                    raise HTTPException(
                        status_code=409, detail="취소된 판으로는 옮길 수 없습니다."
                    )
            result = record_delete_service.delete_recipe(
                connection,
                recipe_id,
                delete_linked_records=delete_blend_records,
                move_records_to=move_records_to,
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
                    "moved_records_to": result.moved_records_to,
                    "moved_record_count": result.moved_record_count,
                },
            )
            connection.commit()

        return {
            "status": "ok",
            "deleted_recipe_id": result.recipe_id,
            "linked_record_count": result.linked_record_count,
            "deleted_linked_records": result.deleted_linked_records,
            "moved_records_to": result.moved_records_to,
            "moved_record_count": result.moved_record_count,
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

    @router.put("/recipes/{recipe_id}/version-name")
    def set_recipe_version_name(
        recipe_id: int,
        body: dict[str, Any],
        current_user: dict[str, Any] = Depends(require_access_level("manager")),
    ) -> dict[str, Any]:
        """판 이름(version_name) 지정/해제 — 책임자 전용.

        body: {"version_name": str | None}. 앞뒤 공백을 걷고, 빈 값은 NULL(이름 없음).
        같은 체인의 판을 사람이 구분하게 하는 자유 문구(예: "저점도용")라 체인 전파는 없다.
        """
        raw = body.get("version_name")
        if raw is not None and not isinstance(raw, str):
            raise HTTPException(status_code=400, detail="버전 이름은 문자열이어야 합니다.")
        version_name = (raw or "").strip() or None
        if version_name is not None and len(version_name) > 40:
            raise HTTPException(status_code=400, detail="버전 이름은 40자 이내입니다.")

        with get_connection() as connection:
            recipe_row = connection.execute(
                "SELECT id, product_name FROM recipes WHERE id = ?", (recipe_id,)
            ).fetchone()
            if not recipe_row:
                raise HTTPException(status_code=404, detail="레시피를 찾을 수 없습니다.")

            connection.execute(
                "UPDATE recipes SET version_name = ? WHERE id = ?",
                (version_name, recipe_id),
            )
            write_audit_log(
                connection,
                action="recipe_version_name_set",
                actor=current_user,
                target_type="recipe",
                target_id=recipe_id,
                target_label=str(recipe_row["product_name"]),
                details={"version_name": version_name},
            )
            connection.commit()

        return {
            "status": "ok",
            "recipe_id": recipe_id,
            "version_name": version_name,
        }

    @router.put("/recipes/{recipe_id}/current")
    def set_recipe_current(
        recipe_id: int,
        current_user: dict[str, Any] = Depends(require_access_level("manager")),
    ) -> dict[str, Any]:
        """현재판 지정 — 책임자 전용. 복사본을 만들지 않고 체인의 현재판을 이 판으로 바꾼다.

        체인 전체의 is_pinned_current 를 끄고 이 판만 켠다. 이 판이 기본 규칙의 현재판
        (recipe_helpers.default_chain_tip)이면 지정 없이도 같은 결과라 플래그를 전부
        끈 채로 둔다(일관 규칙: 플래그는 "최신이 아닌 판을 현재판으로 쓸 때"에만 남는다).
        판정은
        recipe_helpers.resolve_chain_tip·SUPERSEDED_RECIPE_IDS_SQL 이 읽는다.
        """
        with get_connection() as connection:
            recipe_row = connection.execute(
                "SELECT id, product_name, status FROM recipes WHERE id = ?", (recipe_id,)
            ).fetchone()
            if not recipe_row:
                raise HTTPException(status_code=404, detail="레시피를 찾을 수 없습니다.")
            if recipe_row["status"] in ("canceled", "draft"):
                raise HTTPException(
                    status_code=409, detail="취소된 판은 현재판으로 지정할 수 없습니다."
                )

            root_id = find_chain_root(connection, recipe_id)
            previous_current_id = resolve_chain_tip(connection, root_id)
            chain_ids = chain_member_ids(connection, recipe_id)
            # 지정을 무시한 기본 현재판과 같으면 플래그 없이 둔다.
            pin = default_chain_tip(connection, root_id) != recipe_id

            placeholders = ",".join("?" for _ in chain_ids)
            connection.execute(
                f"UPDATE recipes SET is_pinned_current = 0 WHERE id IN ({placeholders})",
                chain_ids,
            )
            if pin:
                connection.execute(
                    "UPDATE recipes SET is_pinned_current = 1 WHERE id = ?", (recipe_id,)
                )
            write_audit_log(
                connection,
                action="recipe_current_set",
                actor=current_user,
                target_type="recipe",
                target_id=recipe_id,
                target_label=str(recipe_row["product_name"]),
                details={
                    "chain_root_id": root_id,
                    "previous_current_id": previous_current_id,
                },
            )
            connection.commit()

        return {
            "status": "ok",
            "recipe_id": recipe_id,
            "previous_current_id": previous_current_id,
            "is_pinned": pin,
        }

    @router.put("/recipes/{recipe_id}/loss-comp")
    def set_recipe_loss_comp(
        recipe_id: int,
        body: dict[str, Any],
        current_user: dict[str, Any] = Depends(require_access_level("manager")),
    ) -> dict[str, Any]:
        """레시피 아이템별 투입 로스 보정(loss_comp_g) 지정 — 책임자 전용.

        body: {"items": [{material_name, loss_comp_g}, ...]}.
        - material_name 은 이 레시피의 BOM 자재명이어야 한다(없으면 400).
        - loss_comp_g 는 0 이상 100 이하(음수 거부, 상한 100g). 0=보정 없음(해제).
        보정은 총량과 무관한 고정 g 이고, 배합 저장 시 theory_amount = 비율×총량+보정.
        """
        raw_items = body.get("items")
        if not isinstance(raw_items, list):
            raise HTTPException(status_code=400, detail="items 목록이 필요합니다.")

        # 정규화 + 검증(이름·값). 값은 0~100g.
        normalized: list[tuple[str, float]] = []
        for entry in raw_items:
            if not isinstance(entry, dict):
                raise HTTPException(status_code=400, detail="각 보정 항목은 객체여야 합니다.")
            name = str(entry.get("material_name") or "").strip()
            if not name:
                raise HTTPException(status_code=400, detail="자재명이 비었습니다.")
            raw_val = entry.get("loss_comp_g")
            try:
                val = float(raw_val)
            except (TypeError, ValueError):
                raise HTTPException(
                    status_code=400, detail=f"보정값은 숫자여야 합니다: {name}"
                )
            if not (0 <= val <= 100):
                raise HTTPException(
                    status_code=400, detail=f"보정값은 0 이상 100 이하여야 합니다: {name}"
                )
            normalized.append((name, val))

        with get_connection() as connection:
            recipe_row = connection.execute(
                "SELECT id, product_name FROM recipes WHERE id = ?", (recipe_id,)
            ).fetchone()
            if not recipe_row:
                raise HTTPException(status_code=404, detail="레시피를 찾을 수 없습니다.")

            # 이 레시피의 BOM 자재명 → recipe_items.id 매핑. BOM 에 없는 자재명 거부.
            bom_rows = connection.execute(
                """
                SELECT ri.id, m.name AS material_name
                FROM recipe_items ri JOIN materials m ON m.id = ri.material_id
                WHERE ri.recipe_id = ?
                """,
                (recipe_id,),
            ).fetchall()
            bom_by_name: dict[str, int] = {}
            for r in bom_rows:
                # 같은 이름의 자재가 여러 행이면 첫째 행(등록 순)에 적용 — 실제로는 드물다.
                bom_by_name.setdefault(str(r["material_name"]).strip(), int(r["id"]))

            missing: list[str] = []
            applied: list[dict[str, Any]] = []
            for name, val in normalized:
                item_id = bom_by_name.get(name)
                if item_id is None:
                    missing.append(name)
                    continue
                connection.execute(
                    "UPDATE recipe_items SET loss_comp_g = ? WHERE id = ?",
                    (val, item_id),
                )
                applied.append({"material_name": name, "loss_comp_g": val})
            if missing:
                raise HTTPException(
                    status_code=400,
                    detail="BOM 에 없는 자재명입니다: " + ", ".join(missing),
                )

            write_audit_log(
                connection,
                action="recipe_loss_comp_set",
                actor=current_user,
                target_type="recipe",
                target_id=recipe_id,
                target_label=str(recipe_row["product_name"]),
                details={"items": applied},
            )
            connection.commit()

        return {"status": "ok", "recipe_id": recipe_id, "items": applied}

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
