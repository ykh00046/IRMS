"""품목코드 관리 메뉴 — 책임자 전용 조회·지정 엔드포인트.

ERP 품목코드 도입(item-code P1~P6) 이후 운영자가 코드를 확인·지정할 화면이 없어
추가된 라우터. 자재 코드는 새 "품목코드" 탭에서, 레시피(반제품) 코드는 레시피
현황에서 인라인 지정한다.

참고 테이블:
    - item_code_master: ERP 마스터(code PK, name, spec, unit, kind, category_hint)
    - materials.code:   자재에 부여된 품목코드(부분 UNIQUE)
    - recipes.product_code: 반제품에 부여된 품목코드(개정 체인이 공유 → UNIQUE 아님)

엔드포인트:
    GET  /item-codes/master            마스터 검색(자재/반제품 코드 제안)
    GET  /item-codes/materials         자재 목록(코드 지정 화면용)
    PUT  /materials/{material_id}/code 자재 코드 지정/해제
    PUT  /recipes/{recipe_id}/product-code  반제품 코드 지정/해제(체인 전체)

recipe_manager_routes.py 의 권한·audit 패턴을 그대로 따른다.
`from __future__ import annotations` 사용 금지(프로젝트 제약).
"""

import re
import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth import require_access_level
from ..db import get_connection, write_audit_log

# 품목코드 완화 형식 — ERP 마스터에 없는 코드(BT000 등)도 운영자가 직접 넣을 수 있도록
# '2자리 영문 + 2~8자리 영숫자(총 4~10자)' 만 검사. 마스터 존재 여부는 강제하지 않는다.
_CODE_PATTERN = re.compile(r"^[A-Z]{2}[A-Z0-9]{2,8}$")


def _validate_code(raw: Any) -> str | None:
    """요청 본문의 code 값을 정규화·검증.

    반환:
        None  → 코드 해제(NULL 저장). raw 가 None 이거나 빈 문자열인 경우.
        str   → 대문자로 정규화된 코드.

    검증 형식에 맞지 않으면 HTTPException(400) 를 발생시킨다.
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if text == "":
        return None
    code = text.upper()
    if not _CODE_PATTERN.match(code):
        raise HTTPException(
            status_code=400,
            detail="품목코드 형식이 올바르지 않습니다. (영문 2자 + 영문/숫자 2~8자)",
        )
    return code


def _escape_like(text: str) -> str:
    r"""LIKE 패턴용 이스케이프 — %, _, \ 를 리터럴로 취급."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def create_item_code_router() -> APIRouter:
    router = APIRouter()

    # ------------------------------------------------------------------
    # A1. GET /item-codes/master — ERP 품목 마스터 검색(제안 목록용)
    # ------------------------------------------------------------------
    @router.get("/item-codes/master")
    def search_item_code_master(
        q: str | None = Query(default=None),
        kind: str | None = Query(default=None),
        current_user: dict[str, Any] = Depends(require_access_level("manager")),
    ) -> dict[str, Any]:
        query = (q or "").strip()
        if query == "":
            raise HTTPException(status_code=400, detail="검색어(q)를 입력하세요.")

        if kind is not None and kind not in ("material", "product"):
            raise HTTPException(
                status_code=400, detail="kind 는 'material' 또는 'product' 이어야 합니다."
            )

        like = f"%{_escape_like(query)}%"
        params: list[Any] = [like, like]
        kind_clause = ""
        if kind is not None:
            kind_clause = " AND kind = ?"
            params.append(kind)

        # item_code_master 테이블이 없는 DB(마이그 전)에서도 500 이 나면 안 된다.
        try:
            with get_connection() as connection:
                rows = connection.execute(
                    f"""
                    SELECT code, name, spec, unit, kind, category_hint
                    FROM item_code_master
                    WHERE (code LIKE ? ESCAPE '\\' OR name LIKE ? ESCAPE '\\')
                      {kind_clause}
                    ORDER BY name
                    LIMIT 30
                    """,
                    params,
                ).fetchall()
        except sqlite3.OperationalError:
            return {"items": []}

        return {
            "items": [
                {
                    "code": r["code"],
                    "name": r["name"],
                    "spec": r["spec"],
                    "unit": r["unit"],
                    "kind": r["kind"],
                    "category_hint": r["category_hint"],
                }
                for r in rows
            ]
        }

    # ------------------------------------------------------------------
    # A2. GET /item-codes/materials — 자재 목록(코드 지정 화면용)
    # ------------------------------------------------------------------
    @router.get("/item-codes/materials")
    def list_materials_for_codes(
        uncoded: str | None = Query(default=None),
        q: str | None = Query(default=None),
        current_user: dict[str, Any] = Depends(require_access_level("manager")),
    ) -> dict[str, Any]:
        where_parts = ["is_active = 1"]
        params: list[Any] = []
        if uncoded == "1":
            where_parts.append("code IS NULL")
        name_query = (q or "").strip()
        if name_query:
            where_parts.append("name LIKE ? ESCAPE '\\'")
            params.append(f"%{_escape_like(name_query)}%")

        where_sql = " AND ".join(where_parts)
        with get_connection() as connection:
            rows = connection.execute(
                f"""
                SELECT id, name, code, category, is_active
                FROM materials
                WHERE {where_sql}
                ORDER BY name
                """,
                params,
            ).fetchall()

        return {
            "items": [
                {
                    "id": r["id"],
                    "name": r["name"],
                    "code": r["code"],
                    "category": r["category"],
                    "is_active": r["is_active"],
                }
                for r in rows
            ]
        }

    # ------------------------------------------------------------------
    # A3. PUT /materials/{material_id}/code — 자재 코드 지정/해제
    # ------------------------------------------------------------------
    @router.put("/materials/{material_id}/code")
    def set_material_code(
        material_id: int,
        body: dict[str, Any],
        current_user: dict[str, Any] = Depends(require_access_level("manager")),
    ) -> dict[str, Any]:
        code = _validate_code(body.get("code"))

        with get_connection() as connection:
            material_row = connection.execute(
                "SELECT id, name, code FROM materials WHERE id = ?", (material_id,)
            ).fetchone()
            if not material_row:
                raise HTTPException(status_code=404, detail="자재를 찾을 수 없습니다.")

            # 동일 code 를 가진 다른 자재가 있으면 충돌(자재명 포함)
            if code is not None:
                other = connection.execute(
                    "SELECT name FROM materials WHERE code = ? AND id != ? LIMIT 1",
                    (code, material_id),
                ).fetchone()
                if other:
                    raise HTTPException(
                        status_code=409,
                        detail=f"이미 다른 자재({other['name']})가 사용 중인 코드입니다.",
                    )

            connection.execute(
                "UPDATE materials SET code = ? WHERE id = ?", (code, material_id)
            )

            # master_name 은 참고용 — 마스터 조회 실패는 무시하고 null.
            master_name: str | None = None
            if code is not None:
                try:
                    master_row = connection.execute(
                        "SELECT name FROM item_code_master WHERE code = ?", (code,)
                    ).fetchone()
                    if master_row:
                        master_name = master_row["name"]
                except sqlite3.OperationalError:
                    master_name = None

            write_audit_log(
                connection,
                action="material_code_set",
                actor=current_user,
                target_type="material",
                target_id=material_id,
                target_label=material_row["name"],
                details={"code": code},
            )
            connection.commit()

        return {
            "status": "ok",
            "material_id": material_id,
            "code": code,
            "master_name": master_name,
        }

    # ------------------------------------------------------------------
    # A4. PUT /recipes/{recipe_id}/product-code — 반제품 코드 지정/해제(체인 전체)
    # ------------------------------------------------------------------
    @router.put("/recipes/{recipe_id}/product-code")
    def set_recipe_product_code(
        recipe_id: int,
        body: dict[str, Any],
        current_user: dict[str, Any] = Depends(require_access_level("manager")),
    ) -> dict[str, Any]:
        product_code = _validate_code(body.get("product_code"))

        with get_connection() as connection:
            recipe_row = connection.execute(
                "SELECT id, product_name, product_code, revision_of FROM recipes WHERE id = ?",
                (recipe_id,),
            ).fetchone()
            if not recipe_row:
                raise HTTPException(status_code=404, detail="레시피를 찾을 수 없습니다.")

            # 체인 루트 탐색: revision_of 를 끝까지 올라감.
            root_id = recipe_row["id"]
            visited: set[int] = set()
            cursor = recipe_row
            while cursor is not None and cursor["revision_of"] is not None:
                parent_id = int(cursor["revision_of"])
                if parent_id in visited or parent_id == int(cursor["id"]):
                    break  # 순환 가드
                visited.add(parent_id)
                cursor = connection.execute(
                    "SELECT id, revision_of FROM recipes WHERE id = ?", (parent_id,)
                ).fetchone()
                if cursor is None:
                    break
                root_id = cursor["id"]

            # 루트에서 파생된 전체 체인(재귀 CTE).
            chain_rows = connection.execute(
                """
                WITH RECURSIVE chain(id) AS (
                    SELECT ?
                    UNION ALL
                    SELECT r.id FROM recipes r JOIN chain c ON r.revision_of = c.id
                )
                SELECT id FROM chain
                """,
                (root_id,),
            ).fetchall()
            chain_ids = [int(r["id"]) for r in chain_rows]
            placeholders = ",".join("?" for _ in chain_ids)

            # 다른 체인의 레시피가 같은 product_code 를 쓰고 있으면 충돌(반제품명 포함).
            if product_code is not None and chain_ids:
                conflict = connection.execute(
                    f"""
                    SELECT product_name FROM recipes
                    WHERE product_code = ? AND id NOT IN ({placeholders}) LIMIT 1
                    """,
                    [product_code, *chain_ids],
                ).fetchone()
                if conflict:
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            f"이미 다른 반제품({conflict['product_name']})이"
                            " 사용 중인 코드입니다."
                        ),
                    )

            updated = connection.execute(
                f"UPDATE recipes SET product_code = ? WHERE id IN ({placeholders})",
                [product_code, *chain_ids],
            ).rowcount

            write_audit_log(
                connection,
                action="recipe_product_code_set",
                actor=current_user,
                target_type="recipe",
                target_id=recipe_id,
                target_label=recipe_row["product_name"],
                details={"product_code": product_code, "updated": updated},
            )
            connection.commit()

        return {
            "status": "ok",
            "recipe_id": recipe_id,
            "product_code": product_code,
            "updated": updated,
        }

    return router
