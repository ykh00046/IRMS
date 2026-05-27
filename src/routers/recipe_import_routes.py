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
    async def import_preview(body: ImportRequest) -> dict[str, Any]:
        with get_connection() as connection:
            result = parse_import_text(connection, body.raw_text)
        return result

    @router.post("/recipes/import")
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

    return router
