import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Iterable

import logging

from .config import APP_ENV, DATA_DIR, DATABASE_PATH, SEED_DEMO_DATA

logger = logging.getLogger(__name__)
from .security import hash_password


def utc_now_text() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def get_connection() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATABASE_PATH, check_same_thread=False, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


_ALLOWED_TABLES = frozenset({
    "users",
    "materials",
    "material_aliases",
    "recipes",
    "recipe_items",
    "schema_migrations",
    "audit_logs",
    "chat_rooms",
    "chat_messages",
})


import re

_SAFE_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]*$")


def ensure_column(connection: sqlite3.Connection, table_name: str, column_name: str, column_def: str) -> None:
    if table_name not in _ALLOWED_TABLES:
        raise ValueError(f"Unknown table: {table_name}")
    if not _SAFE_IDENTIFIER.match(column_name):
        raise ValueError(f"Invalid column name: {column_name}")
    columns = {
        row["name"]
        for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    }
    if column_name in columns:
        return
    connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_def}")


def has_migration(connection: sqlite3.Connection, name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM schema_migrations WHERE name = ?", (name,)
    ).fetchone()
    return row is not None


def record_migration(connection: sqlite3.Connection, name: str) -> None:
    connection.execute(
        "INSERT OR IGNORE INTO schema_migrations (name, applied_at) VALUES (?, ?)",
        (name, utc_now_text()),
    )


def apply_schema_migrations(connection: sqlite3.Connection) -> None:
    ensure_column(connection, "users", "access_level", "TEXT")
    connection.execute(
        """
        UPDATE users
        SET access_level = CASE
            WHEN role = 'admin' OR username = 'admin' THEN 'admin'
            ELSE 'operator'
        END
        WHERE access_level IS NULL OR TRIM(access_level) = ''
        """
    )

    ensure_column(connection, "recipe_items", "measured_at", "TEXT")
    ensure_column(connection, "recipe_items", "measured_by", "TEXT")
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_recipe_items_measured_at ON recipe_items(measured_at)"
    )
    standardize_recipe_units_to_grams(connection)

    # C-1: 기획서 대비 누락 컬럼 추가
    ensure_column(connection, "recipes", "note", "TEXT")
    ensure_column(connection, "recipes", "cancel_reason", "TEXT")
    ensure_column(connection, "recipes", "started_by", "TEXT")
    ensure_column(connection, "recipes", "started_at", "TEXT")
    ensure_column(connection, "recipes", "raw_input_hash", "TEXT")
    ensure_column(connection, "recipes", "raw_input_text", "TEXT")
    ensure_column(connection, "recipes", "revision_of", "INTEGER")


def standardize_recipe_units_to_grams(connection: sqlite3.Connection) -> None:
    if has_migration(connection, "standardize_units_to_grams"):
        return

    kg_material_ids = [
        int(row["id"])
        for row in connection.execute(
            """
            SELECT id
            FROM materials
            WHERE is_active = 1 AND unit_type = 'weight' AND unit = 'kg'
            """
        ).fetchall()
    ]

    if kg_material_ids:
        placeholders = ", ".join("?" for _ in kg_material_ids)
        connection.execute(
            f"""
            UPDATE recipe_items
            SET value_weight = value_weight * 1000
            WHERE value_weight IS NOT NULL
              AND material_id IN ({placeholders})
            """,
            kg_material_ids,
        )

    # All recipes are managed in grams.
    connection.execute(
        """
        UPDATE materials
        SET unit_type = 'weight', unit = 'g'
        WHERE is_active = 1
        """
    )

    record_migration(connection, "standardize_units_to_grams")


def init_db() -> None:
    with get_connection() as connection:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                display_name TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('admin', 'user')),
                access_level TEXT,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS materials (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                unit_type TEXT NOT NULL CHECK (unit_type IN ('weight', 'count')),
                unit TEXT NOT NULL,
                color_group TEXT NOT NULL CHECK (color_group IN ('black', 'red', 'blue', 'yellow', 'none')),
                category TEXT,
                is_active INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS material_aliases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                material_id INTEGER NOT NULL REFERENCES materials(id) ON DELETE CASCADE,
                alias_name TEXT NOT NULL UNIQUE
            );

            CREATE TABLE IF NOT EXISTS recipes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_name TEXT NOT NULL,
                position TEXT,
                ink_name TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('pending', 'in_progress', 'completed', 'canceled', 'draft')),
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL,
                completed_at TEXT
            );

            CREATE TABLE IF NOT EXISTS recipe_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recipe_id INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
                material_id INTEGER NOT NULL REFERENCES materials(id),
                value_weight REAL,
                value_text TEXT
            );

            CREATE TABLE IF NOT EXISTS schema_migrations (
                name TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                action TEXT NOT NULL,
                actor_user_id INTEGER,
                actor_username TEXT,
                actor_display_name TEXT,
                actor_access_level TEXT,
                target_type TEXT,
                target_id TEXT,
                target_label TEXT,
                details_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS chat_rooms (
                key TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                scope TEXT NOT NULL CHECK (scope IN ('notice', 'workflow')),
                sort_order INTEGER NOT NULL DEFAULT 0,
                is_active INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                room_key TEXT NOT NULL REFERENCES chat_rooms(key) ON DELETE CASCADE,
                message_text TEXT NOT NULL,
                stage TEXT CHECK (stage IN ('registered', 'in_progress', 'completed')),
                created_by_user_id INTEGER REFERENCES users(id),
                created_by_username TEXT NOT NULL,
                created_by_display_name TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_recipes_status ON recipes(status);
            CREATE INDEX IF NOT EXISTS idx_recipes_created_at ON recipes(created_at);
            CREATE INDEX IF NOT EXISTS idx_recipe_items_recipe ON recipe_items(recipe_id);
            CREATE INDEX IF NOT EXISTS idx_recipe_items_material ON recipe_items(material_id);
            CREATE INDEX IF NOT EXISTS idx_alias_name ON material_aliases(alias_name);
            CREATE INDEX IF NOT EXISTS idx_audit_logs_created_at ON audit_logs(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_audit_logs_action ON audit_logs(action);
            CREATE INDEX IF NOT EXISTS idx_audit_logs_actor_user_id ON audit_logs(actor_user_id);
            CREATE INDEX IF NOT EXISTS idx_chat_rooms_sort_order ON chat_rooms(sort_order, is_active);
            CREATE INDEX IF NOT EXISTS idx_chat_messages_room_id ON chat_messages(room_key, id DESC);
            CREATE INDEX IF NOT EXISTS idx_chat_messages_created_at ON chat_messages(created_at DESC);
            """
        )

        apply_schema_migrations(connection)
        if SEED_DEMO_DATA:
            if APP_ENV == "production":
                raise RuntimeError("IRMS_SEED_DEMO_DATA must not be enabled in production.")
            logger.warning("SEED_DEMO_DATA is enabled — inserting demo data with default passwords.")
            seed_users(connection)
            seed_chat_rooms(connection)
            seed_materials(connection)
            seed_recipes(connection)


def seed_users(connection: sqlite3.Connection) -> None:
    seeds = [
        # 관리자
        ("admin", "admin123", "관리자", "admin", "admin"),
        # 매니저
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
        # 작업자
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


def normalize_token(value: str) -> str:
    return "".join(part for part in value.strip().upper() if part.isalnum())


def row_to_dict(row: sqlite3.Row) -> dict:
    return {key: row[key] for key in row.keys()}


def in_clause(values: Iterable) -> str:
    return ", ".join("?" for _ in values)


def write_audit_log(
    connection: sqlite3.Connection,
    *,
    action: str,
    actor: dict[str, Any] | None = None,
    target_type: str | None = None,
    target_id: Any | None = None,
    target_label: str | None = None,
    details: dict[str, Any] | None = None,
    created_at: str | None = None,
) -> None:
    connection.execute(
        """
        INSERT INTO audit_logs (
            action,
            actor_user_id,
            actor_username,
            actor_display_name,
            actor_access_level,
            target_type,
            target_id,
            target_label,
            details_json,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            action,
            actor.get("id") if actor else None,
            actor.get("username") if actor else None,
            actor.get("display_name") if actor else None,
            actor.get("access_level") if actor else None,
            target_type,
            None if target_id is None else str(target_id),
            target_label,
            json.dumps(details or {}, ensure_ascii=False),
            created_at or utc_now_text(),
        ),
    )


def list_audit_logs(
    connection: sqlite3.Connection,
    *,
    limit: int = 100,
    offset: int = 0,
    action: str | None = None,
    after_id: int | None = None,
    ascending: bool = False,
) -> list[dict[str, Any]]:
    safe_limit = max(1, min(int(limit), 500))
    where_parts: list[str] = []
    params: list[Any] = []

    if action:
        where_parts.append("action = ?")
        params.append(action)

    if after_id is not None:
        where_parts.append("id > ?")
        params.append(int(after_id))

    where_sql = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
    order_sql = "ORDER BY created_at ASC, id ASC" if ascending else "ORDER BY created_at DESC, id DESC"
    rows = connection.execute(
        f"""
        SELECT
            id,
            action,
            actor_user_id,
            actor_username,
            actor_display_name,
            actor_access_level,
            target_type,
            target_id,
            target_label,
            details_json,
            created_at
        FROM audit_logs
        {where_sql}
        {order_sql}
        LIMIT ? OFFSET ?
        """,
        [*params, safe_limit, max(0, int(offset))],
    ).fetchall()

    items = [row_to_dict(row) for row in rows]
    for item in items:
        try:
            item["details"] = json.loads(item.pop("details_json") or "{}")
        except json.JSONDecodeError:
            item["details"] = {}
            item.pop("details_json", None)
    return items
