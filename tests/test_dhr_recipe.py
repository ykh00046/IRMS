"""DHR 전용 레시피 분리 — 일반 배합 선택에서 제외, dhr=True로 DHR 전용만 조회.

격리를 위해 자체 in-memory DB 사용(설정 reload/get_connection 미사용 — 풀 스위트 오염 방지).
list_blend_recipes 는 recipes + recipe_items(LEFT JOIN)만 참조한다.
"""

import sqlite3

from src.services import blend_service


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE recipes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_name TEXT, position TEXT, ink_name TEXT,
            status TEXT DEFAULT 'completed', created_at TEXT DEFAULT '2026-06-26',
            is_dhr INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE recipe_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT, recipe_id INTEGER,
            material_id INTEGER, value_weight REAL
        );
        """
    )
    return conn


def _add(conn: sqlite3.Connection, name: str, is_dhr: int) -> int:
    conn.execute(
        "INSERT INTO recipes (product_name, ink_name, status, is_dhr) "
        "VALUES (?, 'x', 'completed', ?)",
        (name, is_dhr),
    )
    rid = conn.execute("SELECT id FROM recipes WHERE product_name = ?", (name,)).fetchone()[0]
    conn.execute(
        "INSERT INTO recipe_items (recipe_id, material_id, value_weight) VALUES (?, 1, 100)",
        (rid,),
    )
    return rid


def test_dhr_recipe_excluded_from_blend_selection():
    conn = _db()
    _add(conn, "일반P", 0)
    dhr_id = _add(conn, "DHR전용P", 1)
    conn.commit()

    # 일반 배합 선택: DHR 제외
    names = [r["product_name"] for r in blend_service.list_blend_recipes(conn)]
    assert "일반P" in names
    assert "DHR전용P" not in names

    # DHR 전용 조회(일괄 배합일지 소스)
    dhr_names = [r["product_name"] for r in blend_service.list_blend_recipes(conn, dhr=True)]
    assert dhr_names == ["DHR전용P"]

    # DHR 해제 → 다시 일반 배합 선택에 포함
    conn.execute("UPDATE recipes SET is_dhr = 0 WHERE id = ?", (dhr_id,))
    conn.commit()
    assert "DHR전용P" in [r["product_name"] for r in blend_service.list_blend_recipes(conn)]
