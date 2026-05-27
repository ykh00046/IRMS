"""Shared helpers for recipe-related routers.

Extracted from former src/routers/recipe_routes.py during the
split-large-files PDCA cycle (2026-05). See
docs/01-plan/features/split-large-files.plan.md.

Public symbols (no leading underscore) so router modules and
weighing_routes can import without crossing the routers/ ↔ services/
layer boundary in the wrong direction.
"""

from typing import Any

from fastapi import HTTPException

from ..db import row_to_dict


def format_display_value(weight, text) -> str:
    """Combine weight and text into a display string."""
    if weight is not None and text:
        return f"{weight} ({text})"
    if weight is not None:
        return str(weight)
    if text:
        return text
    return ""


def fetch_recipe_items(connection, recipe_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    """Shared helper to fetch recipe items with material info."""
    if not recipe_ids:
        return {}
    item_rows = connection.execute(
        """
        SELECT
            ri.recipe_id,
            ri.material_id,
            m.name AS material_name,
            m.unit_type,
            m.unit,
            m.color_group,
            ri.value_weight,
            ri.value_text,
            ri.measured_at,
            ri.measured_by
        FROM recipe_items ri
        JOIN materials m ON m.id = ri.material_id
        WHERE ri.recipe_id IN ({ids})
        ORDER BY ri.recipe_id ASC, m.name ASC
        """.format(
            ids=", ".join("?" for _ in recipe_ids)
        ),
        recipe_ids,
    ).fetchall()

    item_map: dict[int, list[dict[str, Any]]] = {}
    for item_row in item_rows:
        item = row_to_dict(item_row)
        item["target_value"] = format_display_value(item.get("value_weight"), item.get("value_text"))
        item_map.setdefault(int(item_row["recipe_id"]), []).append(item)
    return item_map


def find_chain_root(connection, recipe_id: int) -> int:
    """Walk revision_of upward to find the root recipe of a revision chain."""
    row = connection.execute(
        """
        WITH RECURSIVE up(id, parent, depth) AS (
            SELECT id, revision_of, 0 FROM recipes WHERE id = ?
            UNION ALL
            SELECT r.id, r.revision_of, up.depth + 1
            FROM recipes r, up
            WHERE r.id = up.parent AND up.depth < 100
        )
        SELECT id FROM up WHERE parent IS NULL
        ORDER BY depth DESC LIMIT 1
        """,
        (recipe_id,),
    ).fetchone()
    return int(row["id"]) if row else recipe_id


def fetch_chain(connection, root_id: int) -> list[dict[str, Any]]:
    """Walk revision_of downward to fetch all revisions in a chain."""
    rows = connection.execute(
        """
        WITH RECURSIVE chain(id, depth) AS (
            SELECT ?, 0
            UNION ALL
            SELECT r.id, c.depth + 1 FROM recipes r, chain c
            WHERE r.revision_of = c.id AND c.depth < 100
        )
        SELECT r.id, r.product_name, r.position, r.ink_name, r.status,
               r.created_by, r.created_at, r.completed_at, r.revision_of, r.remark
        FROM recipes r
        WHERE r.id IN (SELECT id FROM chain)
        ORDER BY r.created_at ASC, r.id ASC
        """,
        (root_id,),
    ).fetchall()
    return [row_to_dict(r) for r in rows]


def ensure_material(connection, material_id: int) -> dict:
    """Return active material row or raise 404."""
    row = connection.execute(
        "SELECT id, name FROM materials WHERE id = ? AND is_active = 1",
        (material_id,),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="MATERIAL_NOT_FOUND")
    return row_to_dict(row)
