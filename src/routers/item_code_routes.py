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
from ..db import get_connection, utc_now_text, write_audit_log

# 품목코드 형식 — 자재·반제품 모두 영문 1~2자 접두 + 영숫자(사용자 확인 2026-07-21).
#  · 자재(materials.code): 본래 영문 2자(AC0101) 였으나, 반제품(PB/B0020 등)이 자재로
#    함께 쓰이며 B-계열 단일 접두 코드도 자재에 부여될 수 있어 영문 1~2자로 완화.
#  · 반제품(recipes.product_code): B 단독(B0082) 또는 BC/BW 등 영문 1~2자.
# 마스터 존재 여부는 강제하지 않는다(운영자 직접 입력 허용).
_PRODUCT_CODE_PATTERN = re.compile(r"^[A-Z]{1,2}[A-Z0-9]{2,8}$")  # 자재·반제품 공통 — 영문 1~2자


def _validate_code(raw: Any) -> str | None:
    """요청 본문의 code(자재 품목코드) 값을 정규화·검증.

    반환:
        None  → 코드 해제(NULL 저장). raw 가 None 이거나 빈 문자열인 경우.
        str   → 대문자로 정규화된 코드.

    검증 형식에 맞지 않으면 HTTPException(400) 를 발생시킨다.
    자재 코드는 반제품(B-계열) 코드와 동일한 영문 1~2자 패턴을 허용한다 — 반제품이
    자재로 전용되어 B-단일 접두 코드를 가지는 경우(예: PB/B0020) UI 재지정이 막히지 않도록.
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if text == "":
        return None
    code = text.upper()
    if not _PRODUCT_CODE_PATTERN.match(code):
        raise HTTPException(
            status_code=400,
            detail="품목코드 형식이 올바르지 않습니다. (영문 1~2자 + 영문/숫자 2~8자)",
        )
    return code


def _validate_product_code(raw: Any) -> str | None:
    """요청 본문의 product_code(반제품 품목코드) 값을 정규화·검증.

    _validate_code 와 동일 패턴(영문 1~2자 접두 + 영숫자)을 쓴다. 별개 함수로 둔 것은
    의미론적 구분(자재 vs 반제품)과 향후 패턴 분리 가능성 때문. 현재는 같은 정규식.
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if text == "":
        return None
    code = text.upper()
    if not _PRODUCT_CODE_PATTERN.match(code):
        raise HTTPException(
            status_code=400,
            detail="품목코드 형식이 올바르지 않습니다. (영문 1~2자 + 영문/숫자 2~8자)",
        )
    return code


def _revision_chain_ids(connection: sqlite3.Connection, recipe_id: int) -> list[int]:
    """recipe_id 가 속한 개정 체인의 전체 id 목록(자신 포함) 반환.

    revision_of 를 루트까지 올라간 뒤(visited-set 순환 가드), 루트에서 파생된 모든
    자손을 재귀 CTE 로 수집. PUT product-code(A4) 와 revision 등록(BUG 1)이 같은 체인
    정의를 공유하도록 모듈 단위 헬퍼로 뽑았다.
    """
    root_id = recipe_id
    visited: set[int] = set()
    cursor = connection.execute(
        "SELECT id, revision_of FROM recipes WHERE id = ?", (recipe_id,)
    ).fetchone()
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
    return [int(r["id"]) for r in chain_rows]


def _escape_like(text: str) -> str:
    r"""LIKE 패턴용 이스케이프 — %, _, \ 를 리터럴로 취급."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _ensure_master_entry(
    connection: sqlite3.Connection, code: str, name: str, kind: str
) -> None:
    """코드가 item_code_master 에 없을 때만 manual 행을 채운다.

    품목코드 관리 화면에서 운영자가 새 코드를 부여·등록하면, ERP Excel 재임포트
    없이도 마스터 제안(검색)에 노출되도록 같은 코드의 마스터 행을 보충한다.

    - INSERT OR IGNORE: 이미 코드가 있으면(ERP Excel 임포트분 포함) 아무것도 하지
      않는다. ERP 데이터가 권위(authoritative)를 가지므로 운영자 입력으로 기존
      name/source/category_hint 를 덮어쓰지 않는다.
    - 새 행은 source='manual', spec/unit/category_hint=NULL, imported_at=now 로
      기록되어 임포트분과 구분된다.
    - 마이그 전 DB(item_code_master 테이블 없음)에서는 500 없이 조용히 무시한다
      — search_item_code_master 와 동일한 방어 패턴.
    """
    try:
        connection.execute(
            """
            INSERT OR IGNORE INTO item_code_master
                (code, name, spec, unit, kind, category_hint, source, imported_at)
            VALUES (?, ?, NULL, NULL, ?, NULL, 'manual', ?)
            """,
            (code, name, kind, utc_now_text()),
        )
    except sqlite3.OperationalError:
        pass


def _cleanup_orphan_master(connection: sqlite3.Connection, code: Any) -> None:
    """코드가 어느 자재/반제품에도 더 이상 안 쓰이면 manual 마스터 행을 정리한다.

    _ensure_master_entry 가 코드 부여 시 source='manual' 행을 보충하는데, 그 코드가
    자재 삭제(A5)·해제/이동(A3)으로 어디에도 안 남으면 A1 제안 검색·임포트 미리보기
    인덱스에 '유령 코드'로 계속 뜬다. 참조가 사라진 manual 행만 지워 이를 막는다.

    - ERP 임포트분(source != 'manual')은 권위(authoritative) 데이터라 절대 건드리지 않는다.
    - materials.code · recipes.product_code 어느 한쪽이라도 아직 코드를 쥐고 있으면 보존.
    - 마이그 전 DB(테이블/컬럼 없음)는 조용히 무시(_ensure_master_entry 와 동일 방어).
    """
    if not code:
        return
    try:
        holder = connection.execute(
            "SELECT 1 FROM materials WHERE code = ? LIMIT 1", (code,)
        ).fetchone()
        if holder:
            return
        holder = connection.execute(
            "SELECT 1 FROM recipes WHERE product_code = ? LIMIT 1", (code,)
        ).fetchone()
        if holder:
            return
        connection.execute(
            "DELETE FROM item_code_master WHERE code = ? AND source = 'manual'",
            (code,),
        )
    except sqlite3.OperationalError:
        pass


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
        # force=true → 코드 충돌 시 기존 보유 자재에서 코드를 빼고(이동) 이 자재에 부여.
        # 비활성 자재가 코드를 쥐고 있어도 목록에 안 보여 빠져나가지 못하는 사태를 해소.
        force = bool(body.get("force"))

        with get_connection() as connection:
            material_row = connection.execute(
                "SELECT id, name, code FROM materials WHERE id = ?", (material_id,)
            ).fetchone()
            if not material_row:
                raise HTTPException(status_code=404, detail="자재를 찾을 수 없습니다.")

            # 동일 code 를 가진 다른 자재가 있으면 충돌. is_active 필터 없음(비활성도 이동 대상).
            moved_from_name: str | None = None
            if code is not None:
                other = connection.execute(
                    "SELECT id, name FROM materials WHERE code = ? AND id != ? LIMIT 1",
                    (code, material_id),
                ).fetchone()
                if other:
                    if not force:
                        raise HTTPException(
                            status_code=409,
                            detail=f"이미 다른 자재({other['name']})가 사용 중인 코드입니다.",
                        )
                    # force=true — 같은 트랜잭션에서 기존 보유 자재의 코드를 NULL 로.
                    # audit(details) 에 이동 사실을 남긴다(아래 material_code_cleared · set).
                    connection.execute(
                        "UPDATE materials SET code = NULL WHERE code = ? AND id != ?",
                        (code, material_id),
                    )
                    write_audit_log(
                        connection,
                        action="material_code_cleared",
                        actor=current_user,
                        target_type="material",
                        target_id=other["id"],
                        target_label=other["name"],
                        details={
                            "code": code,
                            "moved_to_material_id": material_id,
                            "moved_to_name": material_row["name"],
                        },
                    )
                    moved_from_name = other["name"]

            old_code = material_row["code"]
            connection.execute(
                "UPDATE materials SET code = ? WHERE id = ?", (code, material_id)
            )

            # 새 코드면 item_code_master 에도 manual 행을 채운다(재임포트 면역).
            if code is not None:
                _ensure_master_entry(
                    connection, code, material_row["name"], "material"
                )

            # 이 자재가 쥐고 있던 옛 코드가 풀렸다면(해제 또는 다른 코드로 교체) 참조가
            # 사라진 manual 마스터 행을 정리한다. force 이동으로 비운 다른 자재의 코드는
            # 곧바로 이 자재에 재부여되므로 여전히 쓰여 정리 대상이 아니다.
            if old_code and old_code != code:
                _cleanup_orphan_master(connection, old_code)

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
                # moved_from_name 은 이동이 일어난 경우에만(그 외 None).
                details={"code": code, "moved_from_name": moved_from_name},
            )
            connection.commit()

        return {
            "status": "ok",
            "material_id": material_id,
            "code": code,
            "master_name": master_name,
            "moved_from": moved_from_name,
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
        product_code = _validate_product_code(body.get("product_code"))

        with get_connection() as connection:
            recipe_row = connection.execute(
                "SELECT id, product_name, product_code, revision_of FROM recipes WHERE id = ?",
                (recipe_id,),
            ).fetchone()
            if not recipe_row:
                raise HTTPException(status_code=404, detail="레시피를 찾을 수 없습니다.")

            # 이 레시피가 속한 개정 체인 전체(_revision_chain_ids 와 동일 정의).
            chain_ids = _revision_chain_ids(connection, recipe_id)
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

            # 새 코드면 item_code_master 에도 manual 행을 채운다(재임포트 면역).
            if product_code is not None:
                _ensure_master_entry(
                    connection, product_code, recipe_row["product_name"], "product"
                )

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

    # ------------------------------------------------------------------
    # A6. POST /materials — 신규 자재 등록(코드 지정 화면)
    # ------------------------------------------------------------------
    # 품목코드 관리 화면에서 운영자가 직접 새 자재를 만들 수 있게 한다.
    # INSERT 기본값은 import_parser._auto_register_material 과 동일(unit_type='weight',
    # unit='g', color_group='none', category='미분류', is_active=1) — 화면에서 만든 자재가
    # 임포트로 만들어진 자재와 동일하게 취급되도록.
    # 자재명은 대소문자 무시 중복 금지, code 는 _validate_code 경유(없어도 등록 가능)하되
    # 다른 자재가 이미 쓰고 있으면 409(자재명 포함) — A3(set_material_code) 규칙과 동일.
    # force=true 메 기존 보유 자재에서 코드를 빼고(이동) 새 자재에 부여(A3 과 동일 규칙).
    @router.post("/materials")
    def create_material(
        body: dict[str, Any],
        current_user: dict[str, Any] = Depends(require_access_level("manager")),
    ) -> dict[str, Any]:
        name = str(body.get("name") or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="자재명을 입력하세요.")

        code = _validate_code(body.get("code"))
        # force=true → 코드 충돌 시 기존 보유 자재에서 코드를 빼고(이동) 새 자재에 부여.
        force = bool(body.get("force"))

        with get_connection() as connection:
            # 자재명 중복 — 대소문자 무시.
            dup = connection.execute(
                "SELECT id FROM materials WHERE lower(name) = lower(?) LIMIT 1",
                (name,),
            ).fetchone()
            if dup:
                raise HTTPException(
                    status_code=409, detail="이미 등록된 자재명입니다."
                )

            # code 중복 — 다른 자재가 이미 쓰고 있으면 409(자재명 포함). A3 과 동일 규칙.
            # is_active 필터 없음(비활성도 이동 대상). force=true 면 코드를 이동.
            # 기존 보유 자재의 code 를 NULL 로 비우는 UPDATE 는 INSERT 앞에 있어야 한다
            # (materials.code 부분 UNIQUE — 새 행이 같은 코드를 넣기 전에 비워야 충돌 없음).
            # 다만 material_code_cleared audit 은 INSERT 뒤로 미뤄, 새 자재 id 를
            # moved_to_material_id 에 담는다(BUG: 종전엔 INSERT 전이라 None 이었다).
            moved_from_name: str | None = None
            cleared_other_id: int | None = None
            cleared_other_name: str | None = None
            if code is not None:
                other = connection.execute(
                    "SELECT id, name FROM materials WHERE code = ? LIMIT 1", (code,)
                ).fetchone()
                if other:
                    if not force:
                        raise HTTPException(
                            status_code=409,
                            detail=f"이미 다른 자재({other['name']})가 사용 중인 코드입니다.",
                        )
                    connection.execute(
                        "UPDATE materials SET code = NULL WHERE code = ?",
                        (code,),
                    )
                    cleared_other_id = other["id"]
                    cleared_other_name = other["name"]
                    moved_from_name = other["name"]

            cursor = connection.execute(
                """
                INSERT INTO materials (name, unit_type, unit, color_group, category, is_active, code)
                VALUES (?, 'weight', 'g', 'none', '미분류', 1, ?)
                """,
                (name, code),
            )
            new_id = cursor.lastrowid

            # 새 코드면 item_code_master 에도 manual 행을 채운다(재임포트 면역).
            if code is not None:
                _ensure_master_entry(connection, code, name, "material")

            # 이동 audit — 이제 new_id 가 있으므로 moved_to_material_id 를 채운다.
            if cleared_other_id is not None:
                write_audit_log(
                    connection,
                    action="material_code_cleared",
                    actor=current_user,
                    target_type="material",
                    target_id=cleared_other_id,
                    target_label=cleared_other_name,
                    details={
                        "code": code,
                        "moved_to_name": name,
                        "moved_to_material_id": new_id,
                    },
                )

            write_audit_log(
                connection,
                action="material_created",
                actor=current_user,
                target_type="material",
                target_id=new_id,
                target_label=name,
                details={"code": code, "moved_from_name": moved_from_name},
            )
            connection.commit()

        return {
            "status": "ok",
            "id": new_id,
            "name": name,
            "code": code,
            "moved_from": moved_from_name,
        }

    # ------------------------------------------------------------------
    # A5. DELETE /materials/{material_id} — 자재 삭제(레시피 미참조 시)
    # ------------------------------------------------------------------
    # tools/apply_manual_item_codes.py 의 DELETE_PLAIN 과 동일 규칙.
    # recipe_items 가 한 건이라도 참조 중이면 레시피가 깨지므로(Not Null FK)
    # 409 로 거부 — 비활성화로 대체하지 않고 명시적으로 운영자에게 맡긴다.
    # 참조 0 이면 blend_details.material_id 를 NULL 로(기록의 이름·수치 보존),
    # material_aliases 는 FK ON DELETE CASCADE 로 자동 제거, materials 행 삭제.
    @router.delete("/materials/{material_id}")
    def delete_material(
        material_id: int,
        current_user: dict[str, Any] = Depends(require_access_level("manager")),
    ) -> dict[str, Any]:
        with get_connection() as connection:
            material_row = connection.execute(
                "SELECT id, name, code FROM materials WHERE id = ?", (material_id,)
            ).fetchone()
            if not material_row:
                raise HTTPException(status_code=404, detail="자재를 찾을 수 없습니다.")

            # recipe_items 참조 수 — 이 자재를 쓰는 반제품(레시피)명 최대 5개.
            ref_rows = connection.execute(
                """
                SELECT DISTINCT r.id, r.product_name
                FROM recipe_items ri
                JOIN recipes r ON r.id = ri.recipe_id
                WHERE ri.material_id = ?
                ORDER BY r.product_name
                LIMIT 5
                """,
                (material_id,),
            ).fetchall()
            if ref_rows:
                names = [r["product_name"] for r in ref_rows if r["product_name"]]
                names_text = ", ".join(names) if names else "(이름 없음)"
                detail = (
                    "레시피가 이 자재를 사용 중입니다: "
                    f"{names_text} … — 해당 레시피를 수정 등록으로 정리한 뒤 삭제하세요."
                )
                raise HTTPException(status_code=409, detail=detail)

            # blend_details 링크 NULL — 기록의 텍스트(material_name 등)는 보존.
            link_count = (
                connection.execute(
                    "SELECT COUNT(*) FROM blend_details WHERE material_id = ?",
                    (material_id,),
                ).fetchone()[0]
                or 0
            )
            connection.execute(
                "UPDATE blend_details SET material_id = NULL WHERE material_id = ?",
                (material_id,),
            )

            # material_aliases 는 FK ON DELETE CASCADE 로 자동 제거.
            # 삭제 시점의 code 를 audit details 에 남긴다(코드 지정 화면 추적용).
            deleted_code = material_row["code"]
            connection.execute(
                "DELETE FROM materials WHERE id = ?", (material_id,)
            )

            # 삭제로 코드 참조가 사라졌으면 manual 마스터 유령 행 정리(ERP 행은 보존).
            _cleanup_orphan_master(connection, deleted_code)

            write_audit_log(
                connection,
                action="material_deleted",
                actor=current_user,
                target_type="material",
                target_id=material_id,
                target_label=material_row["name"],
                details={"code": deleted_code, "blend_detail_links": link_count},
            )
            connection.commit()

        return {"status": "ok", "deleted": material_row["name"]}

    return router
