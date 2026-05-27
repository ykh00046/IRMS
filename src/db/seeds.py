import sqlite3

from ..security import hash_password
from .time_utils import utc_now_text


def seed_users(connection: sqlite3.Connection) -> None:
    seeds = [
        # 책임자
        ("120206", "120206", "함지안", "user", "manager"),
        ("160228", "160228", "김지훈", "user", "manager"),
        ("130801", "130801", "김진우", "user", "manager"),
        ("160501", "160501", "김규철", "user", "manager"),
        ("220314", "220314", "이광준", "user", "manager"),
        ("220316", "220316", "강도윤", "user", "manager"),
        ("190308", "190308", "문동식", "user", "manager"),
        ("240212", "240212", "김성근", "user", "manager"),
        ("250411", "250411", "민윤정", "user", "manager"),
        ("250612", "250612", "이시현", "user", "manager"),
        ("250731", "250731", "김태균", "user", "manager"),
        # 담당자
        ("221023", "221023", "김도현", "user", "operator"),
        ("240909", "240909", "김민준", "user", "operator"),
        ("240910", "240910", "박효빈", "user", "operator"),
        ("250941", "250941", "한가람", "user", "operator"),
        ("251006", "251006", "김용범", "user", "operator"),
        ("251051", "251051", "배정한", "user", "operator"),
        ("251066", "251066", "설영훈", "user", "operator"),
        ("251110", "251110", "최선미", "user", "operator"),
        ("251155", "251155", "소보섭", "user", "operator"),
        ("260152", "260152", "권효성", "user", "operator"),
        ("260226", "260226", "김상욱", "user", "operator"),
    ]

    for username, password, display_name, role, access_level in seeds:
        existing = connection.execute(
            "SELECT id FROM users WHERE username = ?",
            (username,),
        ).fetchone()
        if existing:
            continue

        connection.execute(
            """
            INSERT INTO users (username, password_hash, display_name, role, access_level, is_active, created_at)
            VALUES (?, ?, ?, ?, ?, 1, ?)
            """,
            (username, hash_password(password), display_name, role, access_level, utc_now_text()),
        )

    connection.commit()


def seed_chat_rooms(connection: sqlite3.Connection) -> None:
    seeds = [
        ("notice", "전체 공지방", "notice", 10),
        ("mass_response", "양산대응 현황", "workflow", 20),
        ("liquid_ink_response", "액상잉크 대응 현황", "workflow", 30),
        ("sample_mass_production", "샘플시양산", "workflow", 40),
    ]

    for room_key, name, scope, sort_order in seeds:
        existing = connection.execute(
            "SELECT key FROM chat_rooms WHERE key = ?",
            (room_key,),
        ).fetchone()
        if existing:
            connection.execute(
                """
                UPDATE chat_rooms
                SET name = ?, scope = ?, sort_order = ?, is_active = 1
                WHERE key = ?
                """,
                (name, scope, sort_order, room_key),
            )
            continue

        connection.execute(
            """
            INSERT INTO chat_rooms (key, name, scope, sort_order, is_active)
            VALUES (?, ?, ?, ?, 1)
            """,
            (room_key, name, scope, sort_order),
        )

    connection.commit()


def seed_materials(connection: sqlite3.Connection) -> None:
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


def seed_recipes(connection: sqlite3.Connection) -> None:
    recipe_count = connection.execute("SELECT COUNT(*) FROM recipes").fetchone()[0]
    if recipe_count > 0:
        return

    material_ids = {
        row["name"]: row["id"]
        for row in connection.execute("SELECT id, name FROM materials").fetchall()
    }

    seeds = [
        (
            "제품A",
            "1도",
            "잉크B",
            "pending",
            "작업자1",
            "2026-03-06T09:00:00",
            None,
            [("BYK-199", 1.5), ("카본블랙", 0.3)],
        ),
        (
            "제품B",
            "2도",
            "잉크C",
            "in_progress",
            "작업자2",
            "2026-03-06T10:20:00",
            None,
            [("BYK-199", 2.0), ("RED 안료", 0.8)],
        ),
        (
            "제품C",
            "1도",
            "잉크A",
            "completed",
            "작업자3",
            "2026-03-05T11:00:00",
            "2026-03-05T15:12:00",
            [("BYK-199", 4.2), ("BLUE 안료", 0.9), ("PB-APB", 2)],
        ),
        (
            "제품D",
            "3도",
            "잉크D",
            "completed",
            "작업자1",
            "2026-03-04T09:40:00",
            "2026-03-04T14:24:00",
            [("YELLOW 안료", 1.1), ("카본블랙", 0.55)],
        ),
        (
            "제품E",
            "2도",
            "잉크F",
            "completed",
            "작업자2",
            "2026-03-02T08:15:00",
            "2026-03-02T11:55:00",
            [("BYK-199", 3.6), ("RED 안료", 0.45), ("PB-APB", 1)],
        ),
    ]

    for seed in seeds:
        (
            product_name,
            position,
            ink_name,
            status,
            created_by,
            created_at,
            completed_at,
            items,
        ) = seed
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
            material_id = material_ids[material_name]
            connection.execute(
                """
                INSERT INTO recipe_items (recipe_id, material_id, value_weight, value_text)
                VALUES (?, ?, ?, ?)
                """,
                (recipe_id, material_id, float(value), None),
            )

    connection.commit()
