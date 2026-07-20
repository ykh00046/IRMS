"""Shared helpers for recipe-related routers.

Extracted from former src/routers/recipe_routes.py during the
split-large-files PDCA cycle (2026-05). See
docs/01-plan/features/split-large-files.plan.md.

Public symbols (no leading underscore) so router modules and
routers can import without crossing the routers/ ↔ services/
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
            ri.actual_weight,
            ri.measured_at,
            ri.measured_by
        FROM recipe_items ri
        JOIN materials m ON m.id = ri.material_id
        WHERE ri.recipe_id IN ({ids})
        ORDER BY ri.recipe_id ASC, ri.id ASC  -- 등록(투입) 순서 보존 — 이름순이면
                                              -- 수정 등록 때마다 배합 순서가 뒤바뀐다
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


# 개정 체인의 "현재 버전(tip)" 판정 — 레시피 목록·배합 목록·배합 귀결이 공유하는 단일 규칙.
#
# 규칙: 취소(canceled)·초안(draft)은 체인을 **끊지 않고 건너뛴다**. 어떤 레시피에
# 활성(비취소) **후손이 하나라도 있으면** 그 레시피는 대체된 것(superseded)이라 숨긴다.
#
# 옛 규칙은 직계 자식만 봤다("취소되지 않은 자식을 가진 부모만 숨김"). 그래서 A→B→C
# 체인에서 중간 B만 취소하면 A(자식 B가 취소라 안 숨겨짐)와 C(자식 없음)가 **동시에**
# tip 으로 노출되고, 배합 귀결은 A에 머물러 옛 배합비로 이론량이 산출됐다(감사 F-4).
# 후손을 전이적으로 보면 A는 C 때문에 숨겨지고 tip 은 C 하나로 수렴한다.
# 2세대에서 개정본만 취소된 경우 원본이 복귀하는 기존 동작은 그대로 유지된다.
SUPERSEDED_RECIPE_IDS_SQL = """
    WITH RECURSIVE descendants(ancestor, node) AS (
        SELECT revision_of, id FROM recipes WHERE revision_of IS NOT NULL
        UNION
        SELECT d.ancestor, r.id FROM recipes r JOIN descendants d ON r.revision_of = d.node
    )
    SELECT DISTINCT d.ancestor FROM descendants d
    JOIN recipes n ON n.id = d.node
    WHERE n.status NOT IN ('canceled', 'draft')
"""


def resolve_chain_tip(connection, recipe_id: int) -> int:
    """주어진 레시피가 속한 체인의 현재 버전(tip) id 를 반환한다.

    자기 자신을 포함한 하위 트리에서 활성(비취소·비초안) 최신본을 고른다 —
    SUPERSEDED_RECIPE_IDS_SQL 과 같은 규칙이라 목록의 tip 과 항상 일치한다.
    활성 후보가 없으면(자신도 취소된 말단) 입력 id 를 그대로 돌려준다.
    """
    row = connection.execute(
        """
        WITH RECURSIVE subtree(node) AS (
            SELECT id FROM recipes WHERE id = ?
            UNION
            SELECT r.id FROM recipes r JOIN subtree s ON r.revision_of = s.node
        )
        SELECT id FROM recipes
        WHERE id IN (SELECT node FROM subtree)
          AND status NOT IN ('canceled', 'draft')
        ORDER BY id DESC LIMIT 1
        """,
        (int(recipe_id),),
    ).fetchone()
    return int(row["id"]) if row else int(recipe_id)


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
               r.created_by, r.created_at, r.completed_at, r.revision_of, r.remark,
               r.effective_from, COALESCE(r.use_reactor, 0) AS use_reactor,
               COALESCE(r.is_derived, 0) AS is_derived
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
