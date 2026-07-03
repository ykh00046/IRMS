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

            # 수정 등록(revision)이면 원본 체인의 DHR 전용 여부를 승계한다.
            # 승계하지 않으면 새 버전이 일반 레시피가 되어 배합 화면에 노출되는 회귀 발생.
            inherited_is_dhr = 0
            if body.revision_of is not None:
                parent_row = connection.execute(
                    "SELECT COALESCE(is_dhr, 0) AS is_dhr FROM recipes WHERE id = ?",
                    (body.revision_of,),
                ).fetchone()
                if parent_row:
                    inherited_is_dhr = int(parent_row["is_dhr"])

            for parsed_row in parsed["parsed_rows"]:
                # 등록 즉시 사용 가능(completed). (구) 계량 워크플로의 pending→진행→완료
                # 단계는 /blend 전환으로 폐기 — 승인 단계가 없어 pending 은 영구 정체됨.
                cursor = connection.execute(
                    """
                    INSERT INTO recipes (
                        product_name, position, ink_name, status, created_by, created_at, completed_at,
                        raw_input_hash, raw_input_text, revision_of, remark, effective_from, is_dhr
                    ) VALUES (?, ?, ?, 'completed', ?, ?, ?, ?, ?, ?, ?, ?, ?)
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

    return router
