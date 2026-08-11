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
    GET/POST    /materials/{material_id}/aliases            자재 동의어 목록·등록
    DELETE      /materials/{material_id}/aliases/{alias_id} 자재 동의어 해제

recipe_manager_routes.py 의 권한·audit 패턴을 그대로 따른다.
`from __future__ import annotations` 사용 금지(프로젝트 제약).
"""

import re
import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth import require_access_level
from ..db import get_connection, normalize_token, utc_now_text, write_audit_log

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


def _refresh_manual_master_name(
    connection: sqlite3.Connection, code: Any, name: str
) -> None:
    """force 이동으로 코드가 새 자재로 옮겨간 뒤, source='manual' 마스터 행의 이름을 새 보유
    자재명으로 갱신한다.

    _ensure_master_entry 는 INSERT OR IGNORE 라 코드 부여 당시 원 보유 자재명으로 마스터 행이
    한 번 만들어지면, 코드가 다른 자재로 이동해도 마스터 name 이 옛 자재명에 고착돼 제안 검색이
    엉뚱한 이름을 보였다(POLISH). manual 행만 새 이름으로 맞춘다.

    - ERP 임포트분(source != 'manual')은 권위 데이터라 절대 건드리지 않는다.
    - 마이그 전 DB(테이블 없음)는 조용히 무시(_ensure_master_entry 와 동일 방어).
    """
    if not code:
        return
    try:
        connection.execute(
            "UPDATE item_code_master SET name = ? WHERE code = ? AND source = 'manual'",
            (name, code),
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
            # loss_comp_g 컬럼이 없는 구버전/테스트 DB 폴백(0) — try/except 2단 쿼리.
            try:
                rows = connection.execute(
                    f"""
                    SELECT id, name, code, category, is_active, loss_comp_g
                    FROM materials
                    WHERE {where_sql}
                    ORDER BY name
                    """,
                    params,
                ).fetchall()
            except sqlite3.OperationalError:
                rows = connection.execute(
                    f"""
                    SELECT id, name, code, category, is_active
                    FROM materials
                    WHERE {where_sql}
                    ORDER BY name
                    """,
                    params,
                ).fetchall()

            # 자재별 동의어 수 — 화면 배지용(A6). material_aliases 가 없는 구버전/테스트
            # DB 는 빈 맵 폴백이라 목록 자체는 계속 뜬다.
            try:
                alias_counts = {
                    int(r["material_id"]): int(r["n"])
                    for r in connection.execute(
                        "SELECT material_id, COUNT(*) AS n FROM material_aliases "
                        "GROUP BY material_id"
                    ).fetchall()
                }
            except sqlite3.OperationalError:
                alias_counts = {}

        return {
            "items": [
                {
                    "id": r["id"],
                    "name": r["name"],
                    "code": r["code"],
                    "category": r["category"],
                    "is_active": r["is_active"],
                    # 투입 로스 보정(자재 마스터 기본값, 3라운드) — 컬럼 없으면 0.
                    "loss_comp_g": float(r["loss_comp_g"]) if "loss_comp_g" in r.keys() and r["loss_comp_g"] is not None else 0.0,
                    "alias_count": alias_counts.get(int(r["id"]), 0),
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
                # POLISH: force 이동이었다면 manual 마스터 행 이름을 새 보유 자재명으로 갱신
                # (ensure 는 INSERT OR IGNORE 라 기존 행 이름을 안 바꿔 옛 자재명이 고착됐다).
                if moved_from_name is not None:
                    _refresh_manual_master_name(connection, code, material_row["name"])

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
    # A3b. PUT /materials/{material_id}/loss-comp — 자재(품목) 투입 로스 보정 지정/해제
    # ------------------------------------------------------------------
    # 붓는 로스가 있는 파우더 품목에 고정 g 보정을 한 번 지정하면, 그 자재가 들어가는
    # 모든 레시피에 자동 적용된다(레시피 아이템별 지정이 있으면 그것이 우선 override).
    # set_material_code(A3) 와 같은 패턴 — 책임자 전용, 0~100g.
    @router.put("/materials/{material_id}/loss-comp")
    def set_material_loss_comp(
        material_id: int,
        body: dict[str, Any],
        current_user: dict[str, Any] = Depends(require_access_level("manager")),
    ) -> dict[str, Any]:
        raw = body.get("loss_comp_g")
        loss_comp_g: float | None
        if raw is None:
            # null 은 해제(0) — 컬럼이 NOT NULL DEFAULT 0 이라 null 저장 불가. 0 으로 정규화.
            loss_comp_g = 0.0
        else:
            try:
                loss_comp_g = float(raw)
            except (TypeError, ValueError):
                raise HTTPException(
                    status_code=400, detail="loss_comp_g 는 숫자 또는 null 이어야 합니다."
                )
            if not (0 <= loss_comp_g <= 100):
                raise HTTPException(
                    status_code=400,
                    detail="투입 로스 보정은 0 이상 100 이하여야 합니다.",
                )

        with get_connection() as connection:
            material_row = connection.execute(
                "SELECT id, name, loss_comp_g FROM materials WHERE id = ?", (material_id,)
            ).fetchone()
            if not material_row:
                raise HTTPException(status_code=404, detail="자재를 찾을 수 없습니다.")

            # loss_comp_g 컬럼이 없는 구버전/테스트 DB — 500 대신 안내(다른 ensure_column 컬럼과 동일).
            try:
                connection.execute(
                    "UPDATE materials SET loss_comp_g = ? WHERE id = ?",
                    (loss_comp_g, material_id),
                )
            except sqlite3.OperationalError:
                raise HTTPException(
                    status_code=500,
                    detail="이 서버의 자재 테이블에 loss_comp_g 컬럼이 없습니다 — DB 마이그레이션이 필요합니다.",
                )

            write_audit_log(
                connection,
                action="material_loss_comp_set",
                actor=current_user,
                target_type="material",
                target_id=material_id,
                target_label=material_row["name"],
                details={"loss_comp_g": loss_comp_g},
            )
            connection.commit()

        return {
            "status": "ok",
            "material_id": material_id,
            "loss_comp_g": loss_comp_g,
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
            # 단 **반제품명이 같으면 충돌이 아니다** — 같은 제품이므로 같은 코드가 정상이다.
            # 예전에는 이름이 같아도 체인이 다르면 막았는데, 같은 이름으로 별개 체인이
            # 생겨버린 데이터에서는 자기 제품의 코드를 재지정하는 것조차 영구히 막혔다
            # (현장 신고: "이미 사용 중인 코드라고 뜨는데 수정이 안 된다").
            if product_code is not None and chain_ids:
                conflict = connection.execute(
                    f"""
                    SELECT id, product_name FROM recipes
                    WHERE product_code = ? AND id NOT IN ({placeholders})
                      AND product_name <> ?
                    LIMIT 1
                    """,
                    [product_code, *chain_ids, recipe_row["product_name"]],
                ).fetchone()
                if conflict:
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            f"이미 다른 반제품({conflict['product_name']}, id={conflict['id']})이"
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
                # POLISH: force 이동이었다면 manual 마스터 행 이름을 새 자재명으로 갱신
                # (A3 set_material_code 와 동일 — 옛 보유 자재명 고착 방지).
                if moved_from_name is not None:
                    _refresh_manual_master_name(connection, code, name)

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
    # ------------------------------------------------------------------
    # A4b. PUT /materials/{material_id}/name — 자재명 변경(옛 이름은 동의어로 보존)
    # ------------------------------------------------------------------
    # blend_details.material_name 은 기록 시점의 문자열로 박제된다. 그래서 자재명을
    # 그냥 바꾸면 과거 기록은 옛 이름으로 남고, 품목코드 해석이 그 이름을 못 찾아
    # 자재 사용량이 코드 없이 나간다 — 2026-08-11 에 고친 미매핑 사고와 같은 구조다.
    # 그래서 이름 변경은 항상 옛 이름을 동의어로 남긴다(keep_alias=false 로만 생략).
    # recipe_items 는 material_id FK 라 이름 변경의 영향을 받지 않는다.
    @router.put("/materials/{material_id}/name")
    def rename_material(
        material_id: int,
        body: dict[str, Any],
        current_user: dict[str, Any] = Depends(require_access_level("manager")),
    ) -> dict[str, Any]:
        new_name = str(body.get("name") or "").strip()
        if not new_name:
            raise HTTPException(status_code=400, detail="자재명을 입력하세요.")
        if len(new_name) > _ALIAS_MAX_LEN:
            raise HTTPException(
                status_code=400, detail=f"자재명은 {_ALIAS_MAX_LEN}자 이내여야 합니다."
            )
        if not normalize_token(new_name):
            raise HTTPException(
                status_code=400, detail="영문·숫자·한글이 포함된 이름이어야 합니다."
            )
        # 기본은 보존. 옛 이름이 오타여서 남길 가치가 없을 때만 화면에서 끈다.
        keep_alias = body.get("keep_alias", True) is not False

        with get_connection() as connection:
            material_row = connection.execute(
                "SELECT id, name, code FROM materials WHERE id = ?", (material_id,)
            ).fetchone()
            if not material_row:
                raise HTTPException(status_code=404, detail="자재를 찾을 수 없습니다.")
            old_name = material_row["name"] or ""
            if old_name == new_name:
                return {"status": "ok", "name": new_name, "alias_kept": None}

            # 자재명 중복 — 대소문자 무시(POST /materials 와 같은 규칙).
            dup = connection.execute(
                "SELECT id FROM materials WHERE lower(name) = lower(?) AND id != ? LIMIT 1",
                (new_name, material_id),
            ).fetchone()
            if dup:
                raise HTTPException(status_code=409, detail="이미 등록된 자재명입니다.")

            # 새 이름이 다른 자재의 동의어면 거부 — 그대로 두면 같은 이름이 두 자재를
            # 가리켜 해석이 갈린다.
            new_key = normalize_token(new_name)
            for row in connection.execute(
                "SELECT a.alias_name, a.material_id, m.name AS owner "
                "FROM material_aliases a JOIN materials m ON m.id = a.material_id"
            ).fetchall():
                if normalize_token(row["alias_name"] or "") != new_key:
                    continue
                if int(row["material_id"]) != material_id:
                    raise HTTPException(
                        status_code=409,
                        detail=f"이미 자재 '{row['owner']}' 의 동의어입니다.",
                    )

            connection.execute(
                "UPDATE materials SET name = ? WHERE id = ?", (new_name, material_id)
            )

            # 옛 이름을 동의어로 남긴다 — 과거 기록이 계속 이 자재의 코드로 집계되도록.
            # 이미 같은 정규화 키의 동의어가 있으면(재변경 등) 새로 넣지 않는다.
            alias_kept = None
            if keep_alias:
                old_key = normalize_token(old_name)
                existing = {
                    normalize_token(r["alias_name"] or "")
                    for r in connection.execute(
                        "SELECT alias_name FROM material_aliases"
                    ).fetchall()
                }
                # 새 이름과 같은 키(대소문자만 바꾼 개명)면 동의어가 무의미하다.
                if old_key and old_key != new_key and old_key not in existing:
                    try:
                        connection.execute(
                            "INSERT INTO material_aliases (material_id, alias_name) "
                            "VALUES (?, ?)",
                            (material_id, old_name),
                        )
                        alias_kept = old_name
                    except sqlite3.IntegrityError:  # alias_name UNIQUE 경합
                        alias_kept = None

            # 코드를 쥐고 있으면 manual 마스터 행의 이름도 새 자재명으로 맞춘다
            # (set_material_code 의 force 이동과 같은 취지 — 옛 이름 고착 방지).
            if material_row["code"]:
                _refresh_manual_master_name(connection, material_row["code"], new_name)

            write_audit_log(
                connection,
                action="material_renamed",
                actor=current_user,
                target_type="material",
                target_id=material_id,
                target_label=new_name,
                details={
                    "old_name": old_name,
                    "new_name": new_name,
                    "alias_kept": alias_kept,
                    "code": material_row["code"],
                },
            )
            connection.commit()

        return {"status": "ok", "name": new_name, "alias_kept": alias_kept}

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

    # ------------------------------------------------------------------
    # A6. 자재 동의어(별칭) — 같은 물질이 기록에 다른 이름으로 남은 경우를 잇는다.
    # ------------------------------------------------------------------
    # 배경: 품목코드는 자재 1행이 배타 소유한다(A3 의 409/force 규칙). 그래서 같은
    # 물질을 두 이름으로 등록해 두면 양쪽에 같은 코드를 줄 수 없다. 실제로 배합 기록의
    # material_name 이 마스터명이 아닌 이름으로 남는 일이 있고(예: PMA 를 풀네임
    # 'Propylene glycol monomethyl etheracetate' 로 기록), 그런 행은 자재 사용량 API 가
    # 품목코드 없이 내보내 상위 재고 대시보드가 조용히 버린다.
    # 해결: 자재를 합치거나 코드를 옮기지 않고, 그 이름을 자재의 '동의어'로 등록해
    # 품목코드 해석(blend_service._alias_code_map)이 같은 코드로 잇게 한다. 기록의
    # 텍스트는 그대로 두므로 과거 기록도 읽는 시점에 소급 반영된다.

    _ALIAS_MAX_LEN = 120

    def _validate_alias(raw: Any) -> str:
        """요청 본문의 alias_name 을 정규화·검증. 실패 시 400.

        저장은 사용자가 입력한 원문 그대로(대소문자·공백 보존) 한다 — 화면에 보이는
        이름이 기록의 이름과 같아야 운영자가 대조할 수 있다. 매칭은 저장값이 아니라
        normalize_token 으로 하므로 원문 보존이 해석을 방해하지 않는다.
        """
        text = str(raw or "").strip()
        if not text:
            raise HTTPException(status_code=400, detail="동의어를 입력하세요.")
        if len(text) > _ALIAS_MAX_LEN:
            raise HTTPException(
                status_code=400, detail=f"동의어는 {_ALIAS_MAX_LEN}자 이내여야 합니다."
            )
        # 기호만 남는 입력(예: '---')은 normalize_token 이 빈 문자열이 되어 어떤 기록과도
        # 매칭될 수 없다 — 등록해 봐야 무의미하므로 입력 단계에서 막는다.
        if not normalize_token(text):
            raise HTTPException(
                status_code=400, detail="영문·숫자·한글이 포함된 이름이어야 합니다."
            )
        return text

    @router.get("/materials/{material_id}/aliases")
    def list_material_aliases(
        material_id: int,
        current_user: dict[str, Any] = Depends(require_access_level("manager")),
    ) -> dict[str, Any]:
        with get_connection() as connection:
            material_row = connection.execute(
                "SELECT id, name, code FROM materials WHERE id = ?", (material_id,)
            ).fetchone()
            if not material_row:
                raise HTTPException(status_code=404, detail="자재를 찾을 수 없습니다.")
            rows = connection.execute(
                "SELECT id, alias_name FROM material_aliases "
                "WHERE material_id = ? ORDER BY alias_name",
                (material_id,),
            ).fetchall()
        return {
            "material": {
                "id": material_row["id"],
                "name": material_row["name"],
                "code": material_row["code"],
            },
            "items": [{"id": r["id"], "alias_name": r["alias_name"]} for r in rows],
        }

    @router.post("/materials/{material_id}/aliases")
    def add_material_alias(
        material_id: int,
        body: dict[str, Any],
        current_user: dict[str, Any] = Depends(require_access_level("manager")),
    ) -> dict[str, Any]:
        alias_name = _validate_alias(body.get("alias_name"))
        key = normalize_token(alias_name)

        with get_connection() as connection:
            material_row = connection.execute(
                "SELECT id, name, code FROM materials WHERE id = ?", (material_id,)
            ).fetchone()
            if not material_row:
                raise HTTPException(status_code=404, detail="자재를 찾을 수 없습니다.")

            # 자기 이름과 같은 동의어는 무의미(이미 자재명으로 해석된다).
            if normalize_token(material_row["name"] or "") == key:
                raise HTTPException(
                    status_code=400, detail="자재명과 같은 이름은 동의어가 될 수 없습니다."
                )

            # 다른 자재의 '이름'과 겹치면 거부. 그 이름은 이미 그 자재로 해석되므로
            # (해석 2순위 materials.code) 동의어로 가로채면 실적이 엉뚱한 코드로 간다.
            # 비교는 normalize_token 기준 — 해석기와 같은 정규화라야 실제 충돌을 잡는다.
            for other in connection.execute(
                "SELECT id, name FROM materials WHERE id != ?", (material_id,)
            ).fetchall():
                if normalize_token(other["name"] or "") == key:
                    raise HTTPException(
                        status_code=409,
                        detail=f"이미 자재 '{other['name']}' 의 이름입니다. 동의어로 쓸 수 없습니다.",
                    )

            # 이미 등록된 동의어인가 — 같은 자재면 중복, 다른 자재면 충돌.
            for row in connection.execute(
                "SELECT a.id, a.alias_name, a.material_id, m.name AS owner "
                "FROM material_aliases a JOIN materials m ON m.id = a.material_id"
            ).fetchall():
                if normalize_token(row["alias_name"] or "") != key:
                    continue
                if int(row["material_id"]) == material_id:
                    raise HTTPException(
                        status_code=409, detail="이미 등록된 동의어입니다."
                    )
                raise HTTPException(
                    status_code=409,
                    detail=f"이미 자재 '{row['owner']}' 의 동의어입니다.",
                )

            try:
                cursor = connection.execute(
                    "INSERT INTO material_aliases (material_id, alias_name) VALUES (?, ?)",
                    (material_id, alias_name),
                )
            except sqlite3.IntegrityError:  # alias_name UNIQUE — 위 검사와 경합한 동시 등록
                raise HTTPException(status_code=409, detail="이미 등록된 동의어입니다.")
            new_id = cursor.lastrowid

            write_audit_log(
                connection,
                action="material_alias_added",
                actor=current_user,
                target_type="material",
                target_id=material_id,
                target_label=material_row["name"],
                details={"alias_name": alias_name, "code": material_row["code"]},
            )
            connection.commit()

        return {"status": "ok", "id": new_id, "alias_name": alias_name}

    @router.delete("/materials/{material_id}/aliases/{alias_id}")
    def delete_material_alias(
        material_id: int,
        alias_id: int,
        current_user: dict[str, Any] = Depends(require_access_level("manager")),
    ) -> dict[str, Any]:
        with get_connection() as connection:
            material_row = connection.execute(
                "SELECT id, name, code FROM materials WHERE id = ?", (material_id,)
            ).fetchone()
            if not material_row:
                raise HTTPException(status_code=404, detail="자재를 찾을 수 없습니다.")
            # material_id 를 조건에 함께 둔다 — 다른 자재의 동의어를 id 만으로 지우지 못하게.
            alias_row = connection.execute(
                "SELECT id, alias_name FROM material_aliases WHERE id = ? AND material_id = ?",
                (alias_id, material_id),
            ).fetchone()
            if not alias_row:
                raise HTTPException(status_code=404, detail="동의어를 찾을 수 없습니다.")

            connection.execute("DELETE FROM material_aliases WHERE id = ?", (alias_id,))
            write_audit_log(
                connection,
                action="material_alias_removed",
                actor=current_user,
                target_type="material",
                target_id=material_id,
                target_label=material_row["name"],
                details={"alias_name": alias_row["alias_name"], "code": material_row["code"]},
            )
            connection.commit()

        return {"status": "ok", "deleted": alias_row["alias_name"]}

    return router
