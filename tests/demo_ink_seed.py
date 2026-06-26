"""잉크 데모 시드(테스트 전용) — 구 Program-estimation/잉크 흔적을 운영 시드에서 분리.

운영/개발 앱에는 잉크 데모(자재·레시피)를 더 이상 심지 않는다. 잉크 샘플 데이터가 필요한
테스트만 여기서 seed_ink_materials / seed_ink_recipes 를 호출한다.
"""

import sqlite3


def seed_ink_materials(connection: sqlite3.Connection) -> None:
    material_count = connection.execute("SELECT COUNT(*) FROM materials").fetchone()[0]
    if material_count > 0:
        return

    seeds = [
        ("BYK-199", "weight", "g", "none", "첨가제", ["BYK199", "BYK 199"]),
        ("카본블랙", "weight", "g", "black", "안료", ["CARBON BLACK", "카본 블랙"]),
        ("RED 안료", "weight", "g", "red", "안료", ["RED", "RED PIGMENT"]),
        ("BLUE 안료", "weight", "g", "blue", "안료", ["BLUE", "BLUE PIGMENT"]),
        ("YELLOW 안료", "weight", "g", "yellow", "안료", ["YELLOW", "YELLOW PIGMENT"]),
        ("PB-APB", "weight", "g", "black", "첨가제", ["PBAPB"]),
    ]

    for name, unit_type, unit, color_group, category, aliases in seeds:
        cursor = connection.execute(
            """
            INSERT INTO materials (name, unit_type, unit, color_group, category, is_active)
            VALUES (?, ?, ?, ?, ?, 1)
            """,
            (name, unit_type, unit, color_group, category),
        )
        material_id = cursor.lastrowid
        for alias in aliases:
            connection.execute(
                "INSERT INTO material_aliases (material_id, alias_name) VALUES (?, ?)",
                (material_id, alias),
            )

    connection.commit()


def seed_ink_recipes(connection: sqlite3.Connection) -> None:
    recipe_count = connection.execute("SELECT COUNT(*) FROM recipes").fetchone()[0]
    if recipe_count > 0:
        return

    material_ids = {
        row["name"]: row["id"]
        for row in connection.execute("SELECT id, name FROM materials").fetchall()
    }

    seeds = [
        ("제품A", "1도", "잉크B", "pending", "작업자1", "2026-03-06T09:00:00", None,
         [("BYK-199", 1.5), ("카본블랙", 0.3)]),
        ("제품B", "2도", "잉크C", "in_progress", "작업자2", "2026-03-06T10:20:00", None,
         [("BYK-199", 2.0), ("RED 안료", 0.8)]),
        ("제품C", "1도", "잉크A", "completed", "작업자3", "2026-03-05T11:00:00", "2026-03-05T15:12:00",
         [("BYK-199", 4.2), ("BLUE 안료", 0.9), ("PB-APB", 2)]),
        ("제품D", "3도", "잉크D", "completed", "작업자1", "2026-03-04T09:40:00", "2026-03-04T14:24:00",
         [("YELLOW 안료", 1.1), ("카본블랙", 0.55)]),
        ("제품E", "2도", "잉크F", "completed", "작업자2", "2026-03-02T08:15:00", "2026-03-02T11:55:00",
         [("BYK-199", 3.6), ("RED 안료", 0.45), ("PB-APB", 1)]),
    ]

    for product_name, position, ink_name, status, created_by, created_at, completed_at, items in seeds:
        recipe_cursor = connection.execute(
            """
            INSERT INTO recipes (
                product_name, position, ink_name, status, created_by, created_at, completed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (product_name, position, ink_name, status, created_by, created_at, completed_at),
        )
        recipe_id = recipe_cursor.lastrowid
        for material_name, value in items:
            connection.execute(
                """
                INSERT INTO recipe_items (recipe_id, material_id, value_weight, value_text)
                VALUES (?, ?, ?, ?)
                """,
                (recipe_id, material_ids[material_name], float(value), None),
            )

    connection.commit()
