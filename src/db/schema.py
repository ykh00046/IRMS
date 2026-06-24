import logging

from ..config import APP_ENV, SEED_DEMO_DATA
from .connection import get_connection
from .migrations import apply_schema_migrations

logger = logging.getLogger(__name__)


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
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                session_token TEXT
            );

            CREATE TABLE IF NOT EXISTS materials (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                unit_type TEXT NOT NULL CHECK (unit_type IN ('weight', 'count')),
                unit TEXT NOT NULL,
                color_group TEXT NOT NULL CHECK (color_group IN ('black', 'red', 'blue', 'yellow', 'none')),
                category TEXT,
                is_active INTEGER NOT NULL DEFAULT 1,
                stock_quantity REAL NOT NULL DEFAULT 0,
                stock_threshold REAL NOT NULL DEFAULT 0,
                lead_time_days REAL NOT NULL DEFAULT 0,
                reorder_cycle_days REAL NOT NULL DEFAULT 0
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
                completed_at TEXT,
                note TEXT,
                cancel_reason TEXT,
                started_by TEXT,
                started_at TEXT,
                raw_input_hash TEXT,
                raw_input_text TEXT,
                revision_of INTEGER,
                remark TEXT
            );

            CREATE TABLE IF NOT EXISTS recipe_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recipe_id INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
                material_id INTEGER NOT NULL REFERENCES materials(id),
                value_weight REAL,
                value_text TEXT,
                actual_weight REAL,
                measured_at TEXT,
                measured_by TEXT
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

            CREATE INDEX IF NOT EXISTS idx_recipes_status ON recipes(status);
            CREATE INDEX IF NOT EXISTS idx_recipes_created_at ON recipes(created_at);
            CREATE INDEX IF NOT EXISTS idx_recipes_raw_hash ON recipes(raw_input_hash) WHERE raw_input_hash IS NOT NULL;
            CREATE INDEX IF NOT EXISTS idx_recipes_completed_at ON recipes(completed_at) WHERE status = 'completed' AND completed_at IS NOT NULL;
            CREATE INDEX IF NOT EXISTS idx_recipes_revision_of ON recipes(revision_of) WHERE revision_of IS NOT NULL;
            CREATE INDEX IF NOT EXISTS idx_recipe_items_recipe ON recipe_items(recipe_id);
            CREATE INDEX IF NOT EXISTS idx_recipe_items_material ON recipe_items(material_id);
            CREATE INDEX IF NOT EXISTS idx_recipe_items_measured_by ON recipe_items(measured_by, measured_at) WHERE measured_by IS NOT NULL;
            CREATE INDEX IF NOT EXISTS idx_alias_name ON material_aliases(alias_name);
            CREATE INDEX IF NOT EXISTS idx_audit_logs_created_at ON audit_logs(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_audit_logs_action ON audit_logs(action);
            CREATE INDEX IF NOT EXISTS idx_audit_logs_actor_user_id ON audit_logs(actor_user_id);

            CREATE TABLE IF NOT EXISTS attendance_users (
                emp_id TEXT PRIMARY KEY,
                password_hash TEXT NOT NULL,
                password_reset_required INTEGER NOT NULL DEFAULT 1,
                failed_attempts INTEGER NOT NULL DEFAULT 0,
                locked_until TEXT,
                last_login_at TEXT,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_attendance_users_locked_until
                ON attendance_users(locked_until);

            -- Spreadsheet editor tables
            CREATE TABLE IF NOT EXISTS ss_products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                description TEXT,
                recipe_type TEXT NOT NULL DEFAULT 'solution',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS ss_columns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id INTEGER NOT NULL REFERENCES ss_products(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                col_index INTEGER NOT NULL,
                col_type TEXT NOT NULL CHECK (col_type IN ('text', 'numeric')),
                formula_type TEXT,
                formula_params TEXT,
                is_readonly INTEGER NOT NULL DEFAULT 0,
                UNIQUE(product_id, col_index)
            );

            CREATE TABLE IF NOT EXISTS ss_rows (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id INTEGER NOT NULL REFERENCES ss_products(id) ON DELETE CASCADE,
                row_index INTEGER NOT NULL,
                UNIQUE(product_id, row_index)
            );

            CREATE TABLE IF NOT EXISTS ss_cells (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                row_id INTEGER NOT NULL REFERENCES ss_rows(id) ON DELETE CASCADE,
                column_id INTEGER NOT NULL REFERENCES ss_columns(id) ON DELETE CASCADE,
                value TEXT,
                UNIQUE(row_id, column_id)
            );

            CREATE INDEX IF NOT EXISTS idx_ss_columns_product ON ss_columns(product_id, col_index);
            CREATE INDEX IF NOT EXISTS idx_ss_rows_product ON ss_rows(product_id, row_index);
            CREATE INDEX IF NOT EXISTS idx_ss_cells_row ON ss_cells(row_id);

            -- order-sheet-erp: 발주서 (forecast 스냅샷)
            CREATE TABLE IF NOT EXISTS purchase_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_no TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'draft'
                    CHECK (status IN ('draft', 'sent', 'failed', 'cancelled')),
                window_days INTEGER NOT NULL,
                note TEXT,
                item_count INTEGER NOT NULL DEFAULT 0,
                total_qty REAL NOT NULL DEFAULT 0,
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT,
                sent_at TEXT,
                sent_by TEXT,
                erp_mode TEXT,
                erp_status_code INTEGER,
                erp_response TEXT
            );

            CREATE TABLE IF NOT EXISTS purchase_order_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id INTEGER NOT NULL REFERENCES purchase_orders(id) ON DELETE CASCADE,
                material_id INTEGER NOT NULL,
                material_name TEXT NOT NULL,
                category TEXT,
                unit TEXT NOT NULL DEFAULT 'g',
                stock_quantity REAL,
                avg_daily REAL,
                days_remaining REAL,
                predicted_stockout_date TEXT,
                urgency_status TEXT,
                recommended_qty REAL NOT NULL,
                order_qty REAL NOT NULL,
                note TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_po_items_order ON purchase_order_items(order_id);
            CREATE INDEX IF NOT EXISTS idx_po_status_created ON purchase_orders(status, created_at DESC);

            -- purchase-order-receiving: 발주 입고·검수 (LOT + 재고 동시 반영 이력)
            -- receipt_status/received_qty 컬럼은 migrations.ensure_column 에서 추가.
            CREATE TABLE IF NOT EXISTS po_receipts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                receipt_no TEXT NOT NULL UNIQUE,
                order_id INTEGER NOT NULL REFERENCES purchase_orders(id) ON DELETE CASCADE,
                note TEXT,
                item_count INTEGER NOT NULL DEFAULT 0,
                total_qty REAL NOT NULL DEFAULT 0,
                received_by TEXT NOT NULL,
                received_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS po_receipt_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                receipt_id INTEGER NOT NULL REFERENCES po_receipts(id) ON DELETE CASCADE,
                order_item_id INTEGER NOT NULL REFERENCES purchase_order_items(id),
                material_id INTEGER NOT NULL,
                material_name TEXT NOT NULL,
                received_qty REAL NOT NULL,
                lot_no TEXT,
                expiry_date TEXT,
                lot_id INTEGER,
                stock_log_id INTEGER,
                note TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_po_receipts_order ON po_receipts(order_id);
            CREATE INDEX IF NOT EXISTS idx_po_receipt_items_receipt ON po_receipt_items(receipt_id);
            """
        )

        apply_schema_migrations(connection)
        if SEED_DEMO_DATA:
            if APP_ENV == "production":
                raise RuntimeError("IRMS_SEED_DEMO_DATA must not be enabled in production.")
            logger.warning("SEED_DEMO_DATA is enabled — inserting demo data with default passwords.")
            from .seeds import seed_users, seed_materials, seed_recipes, seed_workers
            seed_users(connection)
            seed_workers(connection)
            seed_materials(connection)
            seed_recipes(connection)
