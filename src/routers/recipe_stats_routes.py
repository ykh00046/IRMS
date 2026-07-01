"""Manager-scope statistics endpoints for material consumption.

Aggregates completed recipes within a date range, optionally filtered by
material color_group / category, and offers a CSV export of the same data.

Split from src/routers/recipe_routes.py during the split-large-files
PDCA cycle (2026-05).

Endpoints:
    GET    /stats/consumption
    GET    /stats/export
"""

import csv
import io
from datetime import date
from typing import Any

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from ..db import get_connection, row_to_dict


def build_router() -> APIRouter:
    router = APIRouter()

    @router.get("/stats/consumption")
    def stats_consumption(
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

    @router.get("/stats/export")
    def stats_export(
        date_from: date = Query(...),
        date_to: date = Query(...),
        color_group: str | None = None,
        category: str | None = None,
    ) -> StreamingResponse:
        response = stats_consumption(date_from, date_to, color_group, category)
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

    return router
