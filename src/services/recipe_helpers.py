"""Shared helpers for recipe-related routers.

Extracted from former src/routers/recipe_routes.py during the
split-large-files PDCA cycle (2026-05). See
docs/01-plan/features/split-large-files.plan.md.

Public symbols (no leading underscore) so router modules and
routers can import without crossing the routers/ ↔ services/
layer boundary in the wrong direction.
"""

import sqlite3
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
    # ri.loss_comp_g(투입 로스 보정) — 컬럼이 없는 구버전/테스트 DB 폴백(try/except 2단 쿼리).
    select_with_loss = """
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
            ri.measured_by,
            ri.loss_comp_g
        FROM recipe_items ri
        JOIN materials m ON m.id = ri.material_id
        WHERE ri.recipe_id IN ({ids})
        ORDER BY ri.recipe_id ASC, ri.id ASC
    """.format(ids=", ".join("?" for _ in recipe_ids))
    select_without_loss = """
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
    """.format(ids=", ".join("?" for _ in recipe_ids))
    try:
        item_rows = connection.execute(select_with_loss, recipe_ids).fetchall()
    except sqlite3.OperationalError:  # ri.loss_comp_g 컬럼이 없는 구버전/테스트 DB
        item_rows = connection.execute(select_without_loss, recipe_ids).fetchall()

    item_map: dict[int, list[dict[str, Any]]] = {}
    for item_row in item_rows:
        item = row_to_dict(item_row)
        item["target_value"] = format_display_value(item.get("value_weight"), item.get("value_text"))
        # 투입 로스 보정(2026-08-05) — 구버전 DB/단위테스트 스키마(loss_comp_g 컬럼 없음) 폴백.
        if "loss_comp_g" not in item or item.get("loss_comp_g") is None:
            item["loss_comp_g"] = 0.0
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
#
# 현재판 지정(2026-10-08): 체인에 is_pinned_current=1 인 활성 판이 있으면 그 판이 현재판이다.
# 체인 = 루트(revision_of 를 끝까지 올라간 판) + 루트의 전 후손. 여럿이 지정됐으면 가장
# 작은 id(resolve_chain_tip 과 같은 선택). 취소·초안인 지정 판은 무시한다(기본 규칙 폴백).
#
# 지정된 판에서 수정 등록하면 체인이 가지를 친다(v1→v2→v3 과 v1→v4). 그래서 "활성 후손이
# 없는 판"만 보는 옛 규칙으로는 v3·v4 가 동시에 보인다. 체인마다 현재판을 하나로 정한다:
#   현재판 = 지정 판(있으면) 아니면 활성 후손이 없는 활성 판(옛 규칙의 tip) 중 가장 큰 id.
#   숨김 = (옛 규칙의 숨김 − 현재판) ∪ (현재판이 아닌 활성 판).
# 가지가 없는 체인에서 옛 규칙의 tip 은 하나뿐이라 결과가 옛 규칙과 같다. id 가 아니라
# 계보로 고르므로 소급 연결(tools/link_recipe_chain.py)로 id 순서가 뒤섞인 체인도 같다.
# 취소된 말단은 옛 규칙처럼 숨기지 않는다(등록 이력 목록에서 복구할 수 있어야 한다).
# 결과 컬럼명은 ancestor 로 유지한다(레시피 Excel 내보내기가 r["ancestor"] 로 읽는다).
SUPERSEDED_RECIPE_IDS_SQL = """
    WITH RECURSIVE descendants(ancestor, node) AS (
        SELECT revision_of, id FROM recipes WHERE revision_of IS NOT NULL
        UNION
        SELECT d.ancestor, r.id FROM recipes r JOIN descendants d ON r.revision_of = d.node
    ),
    old_sup(id) AS (
        SELECT DISTINCT d.ancestor FROM descendants d
        JOIN recipes n ON n.id = d.node
        WHERE n.status NOT IN ('canceled', 'draft')
    ),
    up(start, node, parent, depth) AS (
        SELECT id, id, revision_of, 0 FROM recipes
        UNION ALL
        SELECT u.start, r.id, r.revision_of, u.depth + 1
        FROM recipes r JOIN up u ON r.id = u.parent
        WHERE u.depth < 100
    ),
    member(root, id) AS (
        SELECT node, start FROM up WHERE parent IS NULL
    ),
    chain_tip(root, tip) AS (
        SELECT m.root,
               COALESCE(
                   MIN(CASE WHEN COALESCE(r.is_pinned_current, 0) = 1
                             AND r.status NOT IN ('canceled', 'draft') THEN r.id END),
                   MAX(CASE WHEN r.status NOT IN ('canceled', 'draft')
                             AND r.id NOT IN (SELECT id FROM old_sup) THEN r.id END)
               )
        FROM member m JOIN recipes r ON r.id = m.id
        GROUP BY m.root
    )
    SELECT id AS ancestor FROM old_sup
    WHERE id NOT IN (SELECT tip FROM chain_tip WHERE tip IS NOT NULL)
    UNION
    SELECT m.id AS ancestor FROM member m
    JOIN recipes r ON r.id = m.id
    JOIN chain_tip t ON t.root = m.root
    WHERE r.status NOT IN ('canceled', 'draft')
      AND t.tip IS NOT NULL AND m.id <> t.tip
"""

_CHAIN_FROM_ROOT_CTE = """
    WITH RECURSIVE chain(node) AS (
        SELECT ?
        UNION
        SELECT r.id FROM recipes r JOIN chain c ON r.revision_of = c.node
    )
"""


def chain_member_ids(connection, recipe_id: int) -> list[int]:
    """recipe_id 가 속한 개정 체인 전체 id(루트 + 루트의 모든 후손), id 오름차순."""
    root_id = find_chain_root(connection, int(recipe_id))
    rows = connection.execute(
        _CHAIN_FROM_ROOT_CTE
        + "SELECT id FROM recipes WHERE id IN (SELECT node FROM chain) ORDER BY id",
        (root_id,),
    ).fetchall()
    return [int(r["id"]) for r in rows]


def pinned_chain_member(connection, recipe_id: int) -> int | None:
    """체인에서 현재판으로 지정된 활성 판 id(여럿이면 가장 작은 id). 없으면 None.

    is_pinned_current 컬럼이 없는 구버전/단위테스트 스키마는 지정 없음으로 본다.
    """
    root_id = find_chain_root(connection, int(recipe_id))
    try:
        row = connection.execute(
            _CHAIN_FROM_ROOT_CTE
            + """
            SELECT id FROM recipes
            WHERE id IN (SELECT node FROM chain)
              AND COALESCE(is_pinned_current, 0) = 1
              AND status NOT IN ('canceled', 'draft')
            ORDER BY id LIMIT 1
            """,
            (root_id,),
        ).fetchone()
    except sqlite3.OperationalError:  # is_pinned_current 컬럼 없음
        return None
    return int(row["id"]) if row else None


def newest_active_in_subtree(connection, recipe_id: int) -> int | None:
    """recipe_id 자신을 포함한 하위 트리의 활성(비취소·비초안) 최신 id. 없으면 None.

    루트를 넘기면 체인 전체의 활성 최신본(지정을 무시한 기본 규칙의 현재판)이다.
    """
    row = connection.execute(
        _CHAIN_FROM_ROOT_CTE
        + """
        SELECT id FROM recipes
        WHERE id IN (SELECT node FROM chain)
          AND status NOT IN ('canceled', 'draft')
        ORDER BY id DESC LIMIT 1
        """,
        (int(recipe_id),),
    ).fetchone()
    return int(row["id"]) if row else None


def default_chain_tip(connection, root_id: int) -> int | None:
    """지정을 무시한 기본 현재판: 체인에서 활성 후손이 없는 활성 판 중 가장 큰 id.

    가지 없는 체인에서는 계보상 가장 깊은 활성 판 하나다(SUPERSEDED_RECIPE_IDS_SQL 의
    chain_tip 기본값과 같은 정의). 활성 판이 없으면 None.
    """
    rows = connection.execute(
        _CHAIN_FROM_ROOT_CTE
        + "SELECT id, revision_of, status FROM recipes WHERE id IN (SELECT node FROM chain)",
        (int(root_id),),
    ).fetchall()
    parent = {int(r["id"]): r["revision_of"] for r in rows}
    active = {int(r["id"]) for r in rows if r["status"] not in ("canceled", "draft")}
    has_active_descendant: set[int] = set()
    for node in active:
        seen: set[int] = set()
        cur = parent.get(node)
        while cur is not None and int(cur) in parent and int(cur) not in seen:
            cur = int(cur)
            seen.add(cur)
            has_active_descendant.add(cur)
            cur = parent.get(cur)
    candidates = active - has_active_descendant
    return max(candidates) if candidates else None


def resolve_chain_tip(connection, recipe_id: int) -> int:
    """주어진 레시피가 속한 체인의 현재 버전(tip) id 를 반환한다.

    1) 체인(루트+전 후손)에 현재판으로 지정된(is_pinned_current=1) 활성 판이 있으면 그 판.
       버전 관리의 '현재판 지정'으로 옛 판(예: 저점도용 v2)을 복사 없이 되살린 경우다.
    2) 자기 하위 트리에 활성 판이 하나도 없으면(취소된 말단) 입력 id 를 그대로 돌려준다.
    3) 그 밖에는 체인의 기본 현재판(default_chain_tip). 가지가 없는 체인에서는 종전의
       "자기 하위 트리의 활성 최신본"과 같고, 지정 판에서 수정 등록해 가지가 생겨도
       하나로 수렴한다.
    SUPERSEDED_RECIPE_IDS_SQL 과 같은 규칙이라 목록의 tip 과 항상 일치한다.
    """
    pinned = pinned_chain_member(connection, recipe_id)
    if pinned is not None:
        return pinned
    if newest_active_in_subtree(connection, recipe_id) is None:
        return int(recipe_id)
    tip = default_chain_tip(connection, find_chain_root(connection, int(recipe_id)))
    return tip if tip is not None else int(recipe_id)


def current_recipe_by_names(connection, select_cols: str, names: list[Any]):
    """반제품명(names 중 하나)이 일치하는 completed 레시피 중 현재판(tip) 한 행.

    점도·반응기처럼 "제품명 → 그 제품의 레시피"를 찾는 곳이 쓰는 규칙. 종전엔 id 최신
    completed 를 골랐는데, 현재판 지정이 생기면 최신 판과 현재판이 다를 수 있다.
    tip 이 없거나 현재판 컬럼이 없는 구버전 스키마면 종전 규칙(최신 completed)으로 폴백.
    select_cols 는 호출부의 고정 문자열이어야 한다(사용자 입력 금지).
    마지막 폴백 쿼리의 OperationalError(테이블 없음 등)는 호출부가 처리한다.
    """
    placeholders = " OR ".join("product_name = ?" for _ in names)
    base = (
        f"SELECT {select_cols} FROM recipes "
        f"WHERE ({placeholders}) AND status = 'completed'"
    )
    try:
        row = connection.execute(
            base + f" AND id NOT IN ({SUPERSEDED_RECIPE_IDS_SQL}) ORDER BY id DESC LIMIT 1",
            list(names),
        ).fetchone()
        if row:
            return row
    except sqlite3.OperationalError:  # is_pinned_current 컬럼 없는 구버전/단위테스트 스키마
        pass
    return connection.execute(base + " ORDER BY id DESC LIMIT 1", list(names)).fetchone()


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
               COALESCE(r.is_derived, 0) AS is_derived,
               r.stage1_recipe_id,
               s.product_name AS stage1_product_name
        FROM recipes r
        LEFT JOIN recipes s ON s.id = r.stage1_recipe_id
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
