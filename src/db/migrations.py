import re
import sqlite3

from .time_utils import utc_now_text


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
    "ss_products",
    "ss_columns",
    "ss_rows",
    "ss_cells",
    "attendance_users",
})


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
            WHEN role = 'admin' OR username = 'admin' THEN 'manager'
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

    # excel-recipe-migration: 엑셀 원본의 비고 컬럼 이관용
    ensure_column(connection, "recipes", "remark", "TEXT")

    # single-session enforcement: 로그인 시 발급, 새 로그인 시 회전
    ensure_column(connection, "users", "session_token", "TEXT")

    # spreadsheet recipe type: 액상(solution) / 파우더(powder)
    ensure_column(connection, "ss_products", "recipe_type", "TEXT NOT NULL DEFAULT 'solution'")

    # material-stock-tracking: 원재료 재고 추적
    ensure_column(connection, "materials", "stock_quantity", "REAL NOT NULL DEFAULT 0")
    ensure_column(connection, "materials", "stock_threshold", "REAL NOT NULL DEFAULT 0")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS material_stock_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            material_id INTEGER NOT NULL,
            delta REAL NOT NULL,
            balance_after REAL NOT NULL,
            reason TEXT NOT NULL,
            actor_id INTEGER,
            actor_name TEXT,
            recipe_id INTEGER,
            recipe_item_id INTEGER,
            note TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (material_id) REFERENCES materials(id)
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_stock_logs_material ON material_stock_logs(material_id, created_at DESC)"
    )
    connection.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_stock_logs_item_measurement "
        "ON material_stock_logs(recipe_item_id) WHERE reason = 'measurement'"
    )

    # formula-excel-style: 기존 수식 컬럼을 numeric으로 전환
    if not has_migration(connection, "formula_columns_to_numeric"):
        connection.execute(
            """
            UPDATE ss_columns
               SET col_type = 'numeric',
                   formula_type = NULL,
                   formula_params = NULL,
                   is_readonly = 0
             WHERE col_type = 'formula'
            """
        )
        record_migration(connection, "formula_columns_to_numeric")

    # admin access level: 김지훈, 김진우, 함지안 → admin
    if not has_migration(connection, "admin_access_level"):
        connection.execute(
            """
            UPDATE users
            SET access_level = 'admin'
            WHERE display_name IN ('김지훈', '김진우', '함지안')
              AND access_level = 'manager'
            """
        )
        record_migration(connection, "admin_access_level")

    # 잉크/사출 OCR 기능 (28aa888, 2026-05-19) 삭제 후 잔존 테이블 정리.
    # dev DB는 이미 깨끗하므로 no-op, 운영 DB(현장 PC)에서만 실제 DROP 수행.
    if not has_migration(connection, "drop_orphan_plan_tables"):
        # 외래키 의존성 순서: 자식부터 DROP
        # plan_schedules.plan_id REFERENCES production_plans(id)
        # plan_chemical_requests.plan_id REFERENCES production_plans(id)
        connection.execute("DROP TABLE IF EXISTS plan_schedules")
        connection.execute("DROP TABLE IF EXISTS plan_chemical_requests")
        connection.execute("DROP TABLE IF EXISTS production_plans")
        record_migration(connection, "drop_orphan_plan_tables")


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
