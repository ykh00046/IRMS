"""Manager-scope recipe import endpoints (Excel/TSV bulk import).

Provides 2 endpoints for previewing parsed import payloads and committing
them. Duplicate detection by SHA-256 hash of raw_text; the `force` flag
bypasses it and `revision_of` marks the new batch as a revision chain.

Split from src/routers/recipe_routes.py during the split-large-files
PDCA cycle (2026-05).

Endpoints:
    POST   /recipes/import/preview
    POST   /recipes/import
"""

import hashlib
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from ..auth import get_current_user, require_access_level
from ..db import get_connection, utc_now_text, write_audit_log
from ..services.import_parser import parse_import_text
from .models import ImportRequest, actor_name


def build_router() -> APIRouter:
    router = APIRouter(dependencies=[Depends(require_access_level("manager"))])

    @router.post("/recipes/import/preview")
    def import_preview(body: ImportRequest) -> dict[str, Any]:
        with get_connection() as connection:
            result = parse_import_text(connection, body.raw_text)
        return result

    @router.post("/recipes/import")
    def import_recipes(
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
            effective_from = (body.effective_from or "").strip() or now[:10]
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

            # 수정 등록(revision)이면 원본 체인의 DHR 전용 여부·기준 배합량을 승계한다.
            # 승계하지 않으면 새 버전이 일반 레시피가 되어 배합 화면에 노출되는 회귀 발생.
            inherited_is_dhr = 0
            totals = body.base_totals or ([body.base_total] if body.base_total else None)
            # 기준 자재 승계용 — 요청에 anchor_material 이 없으면 부모의 anchor_material_id 를
            # 새 버전의 자재 중 일치하는 것이 있을 때만 물려받는다(없으면 NULL).
            inherited_anchor_material_id: int | None = None
            if body.revision_of is not None:
                parent_row = connection.execute(
                    "SELECT COALESCE(is_dhr, 0) AS is_dhr, base_total, base_totals, anchor_material_id "
                    "FROM recipes WHERE id = ?",
                    (body.revision_of,),
                ).fetchone()
                if parent_row:
                    inherited_is_dhr = int(parent_row["is_dhr"])
                    if totals is None:
                        if parent_row["base_totals"]:
                            totals = [
                                float(t) for t in str(parent_row["base_totals"]).split(",") if t.strip()
                            ]
                        elif parent_row["base_total"]:
                            totals = [float(parent_row["base_total"])]
                    if parent_row["anchor_material_id"] is not None:
                        inherited_anchor_material_id = int(parent_row["anchor_material_id"])
            # 정수는 '.0' 없이 저장(프리필 표시·비교 편의)
            base_totals_text = (
                ",".join(str(int(t)) if float(t) == int(t) else str(t) for t in totals[:3])
                if totals else None
            )

            for parsed_row in parsed["parsed_rows"]:
                # 기준 자재 결정:
                # - 요청에 anchor_material 이 있으면 → 임포트 항목 중 이름이 정확히 일치하는
                #   자재의 id. 일치하는 항목이 없으면 400(잘못된 지정).
                # - 요청에 없고 수정 등록이면 → 부모의 anchor_material_id 를 새 버전 자재 중
                #   여전히 존재할 때만 승계(없으면 NULL). 이것이 base_totals 승계 구조와 동일.
                item_ids = [it["material_id"] for it in parsed_row["items"]]
                item_id_set = set(item_ids)
                anchor_material_id: int | None = None
                if body.anchor_material is not None:
                    anchor_name = body.anchor_material.strip()
                    if anchor_name:
                        # 임포트 항목의 자재 id 로 materials.name 을 조회해 정확히 일치하는 이름을 찾는다.
                        matched_id = None
                        if item_id_set:
                            placeholders = ",".join("?" for _ in item_ids)
                            name_row = connection.execute(
                                f"SELECT id FROM materials WHERE id IN ({placeholders}) "
                                f"AND name = ? LIMIT 1",
                                (*item_ids, anchor_name),
                            ).fetchone()
                            if name_row:
                                matched_id = int(name_row["id"])
                        if matched_id is None:
                            raise HTTPException(
                                status_code=400,
                                detail=(
                                    f"기준 자재 '{anchor_name}'가 임포트 항목에 없습니다. "
                                    "자재 이름을 확인해 주세요."
                                ),
                            )
                        anchor_material_id = matched_id
                elif inherited_anchor_material_id is not None:
                    # 부모의 기준 자재가 새 버전 자재에 여전히 있으면 승계, 아니면 버림(NULL)
                    anchor_material_id = (
                        inherited_anchor_material_id
                        if inherited_anchor_material_id in item_id_set
                        else None
                    )

                # 등록 즉시 사용 가능(completed). (구) 계량 워크플로의 pending→진행→완료
                # 단계는 /blend 전환으로 폐기 — 승인 단계가 없어 pending 은 영구 정체됨.
                cursor = connection.execute(
                    """
                    INSERT INTO recipes (
                        product_name, position, ink_name, status, created_by, created_at, completed_at,
                        raw_input_hash, raw_input_text, revision_of, remark, effective_from, is_dhr,
                        base_totals, anchor_material_id
                    ) VALUES (?, ?, ?, 'completed', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        parsed_row["product_name"],
                        parsed_row["position"],
                        # ink 는 폐기 중인 개념 — 열이 없으면(문서상 기본 형식) 반제품명으로 대체.
                        # (ink_name 은 NOT NULL 이라 None 이면 등록이 500 으로 실패했음)
                        parsed_row["ink_name"] or parsed_row["product_name"],
                        creator_name,
                        now,
                        now,
                        raw_hash,
                        body.raw_text,
                        body.revision_of,
                        parsed_row.get("remark"),
                        effective_from,
                        inherited_is_dhr,
                        base_totals_text,
                        anchor_material_id,
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

                # 공정 설명 줄('설명' 열) — 자재 사이 안내문. 공식 배합일지 출력 미포함.
                for step in parsed_row.get("steps", []):
                    connection.execute(
                        "INSERT INTO recipe_steps (recipe_id, position, note) VALUES (?, ?, ?)",
                        (recipe_id, int(step["position"]), step["note"]),
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

    return router
