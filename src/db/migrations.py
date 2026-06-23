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
    "purchase_orders",
    "purchase_order_items",
    "material_lots",
    "po_receipts",
    "po_receipt_items",
    "viscosity_products",
    "viscosity_readings",
    "blend_records",
    "blend_details",
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
    # material-forecast: 소모량 예측·발주 추천 (0 = 전역 기본값 사용)
    ensure_column(connection, "materials", "lead_time_days", "REAL NOT NULL DEFAULT 0")
    ensure_column(connection, "materials", "reorder_cycle_days", "REAL NOT NULL DEFAULT 0")
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
    # forecast-dashboard-alert: 소비 집계는 material_id 조건 없이
    # reason='measurement' AND created_at>=cutoff 로 스캔하므로 (material_id,created_at)
    # 인덱스를 못 탄다. (reason, created_at)로 forecast 소비쿼리를 직접 지원.
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_stock_logs_reason_created "
        "ON material_stock_logs(reason, created_at)"
    )

    # order-sheet-erp: 발주서 (forecast 스냅샷) + ERP 전송 상태
    connection.execute(
        """
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
        )
        """
    )
    connection.execute(
        """
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
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_po_items_order ON purchase_order_items(order_id)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_po_status_created "
        "ON purchase_orders(status, created_at DESC)"
    )

    # lot-expiry-tracking: 입고 LOT별 유통기한 추적 (재고 차감 경로와 독립)
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS material_lots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            material_id INTEGER NOT NULL REFERENCES materials(id),
            lot_no TEXT,
            received_quantity REAL NOT NULL,
            remaining_quantity REAL NOT NULL,
            received_at TEXT NOT NULL,
            expiry_date TEXT,
            status TEXT NOT NULL DEFAULT 'active'
                CHECK (status IN ('active', 'depleted', 'discarded')),
            note TEXT,
            actor_id INTEGER,
            actor_name TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_material_lots_material "
        "ON material_lots(material_id, status)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_material_lots_expiry "
        "ON material_lots(expiry_date) WHERE status = 'active'"
    )

    # purchase-order-receiving: 발주 입고·검수 (발주 sent → LOT + 재고 동시 반영)
    # 입고 진행 축은 ERP 전송 status(draft/sent/...)와 직교한 receipt_status 로 분리.
    ensure_column(
        connection,
        "purchase_orders",
        "receipt_status",
        "TEXT NOT NULL DEFAULT 'pending'",
    )
    ensure_column(
        connection,
        "purchase_order_items",
        "received_qty",
        "REAL NOT NULL DEFAULT 0",
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS po_receipts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            receipt_no TEXT NOT NULL UNIQUE,
            order_id INTEGER NOT NULL REFERENCES purchase_orders(id) ON DELETE CASCADE,
            note TEXT,
            item_count INTEGER NOT NULL DEFAULT 0,
            total_qty REAL NOT NULL DEFAULT 0,
            received_by TEXT NOT NULL,
            received_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
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
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_po_receipts_order ON po_receipts(order_id)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_po_receipt_items_receipt "
        "ON po_receipt_items(receipt_id)"
    )

    # viscosity-analysis: 합성 점도 LOT별 측정 + 추세·이상 분석
    # 제품군마다 정상 점도 대역이 완전히 다르므로(PB~49, SBCT~204, SCRA~90)
    # 관리한계(spec)·sigma_k 는 제품(viscosity_products) 단위로 보관한다.
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS viscosity_products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            target REAL,
            lower_limit REAL,
            upper_limit REAL,
            sigma_k REAL NOT NULL DEFAULT 3,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS viscosity_readings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL REFERENCES viscosity_products(id) ON DELETE CASCADE,
            lot_no TEXT NOT NULL,
            viscosity REAL NOT NULL,
            measured_date TEXT,
            memo TEXT,
            recipe_material TEXT,
            material_lot TEXT,
            created_by TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_visc_readings_product_date "
        "ON viscosity_readings(product_id, measured_date)"
    )
    # 한 LOT = 한 점도. 중복 등록 차단 + 엑셀 재임포트 멱등성 보장.
    connection.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_visc_readings_product_lot "
        "ON viscosity_readings(product_id, lot_no)"
    )
    if not has_migration(connection, "seed_viscosity_products"):
        now = utc_now_text()
        for code, name in (("PB", "PB"), ("SBCT", "SBCT"), ("SCRA", "SCRA")):
            connection.execute(
                "INSERT OR IGNORE INTO viscosity_products (code, name, sigma_k, is_active, created_at) "
                "VALUES (?, ?, 3, 1, ?)",
                (code, name, now),
            )
        record_migration(connection, "seed_viscosity_products")

    # blend-overhaul: 배합 실적(잉크 계량 재구축) — DHR Generator 이식
    # 레시피(절대중량)→비율 환산→이론량/실제량/자재LOT/작업자/저울 기록. product_lot 자동생성.
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS blend_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_lot TEXT NOT NULL,
            recipe_id INTEGER REFERENCES recipes(id),
            product_name TEXT NOT NULL,
            ink_name TEXT,
            position TEXT,
            worker TEXT NOT NULL,
            work_date TEXT NOT NULL,
            work_time TEXT,
            total_amount REAL NOT NULL,
            scale TEXT,
            status TEXT NOT NULL DEFAULT 'completed'
                CHECK (status IN ('draft', 'completed', 'canceled')),
            note TEXT,
            created_by TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS blend_details (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            blend_record_id INTEGER NOT NULL
                REFERENCES blend_records(id) ON DELETE CASCADE,
            material_id INTEGER REFERENCES materials(id),
            material_code TEXT,
            material_name TEXT NOT NULL,
            material_lot TEXT,
            ratio REAL,
            theory_amount REAL,
            actual_amount REAL,
            sequence_order INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_blend_records_date "
        "ON blend_records(work_date DESC)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_blend_records_lot ON blend_records(product_lot)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_blend_records_recipe ON blend_records(recipe_id)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_blend_details_record "
        "ON blend_details(blend_record_id, sequence_order)"
    )
    # blend 결재 기록 (작성/검토/승인 — 이름+시각). 원본의 서명 이미지 위조 대체.
    ensure_column(connection, "blend_records", "reviewed_by", "TEXT")
    ensure_column(connection, "blend_records", "reviewed_at", "TEXT")
    ensure_column(connection, "blend_records", "approved_by", "TEXT")
    ensure_column(connection, "blend_records", "approved_at", "TEXT")
    # 전자서명(결재자가 직접 그린 PNG data URL). 원본의 서명 이미지 위조와 달리 실서명.
    ensure_column(connection, "blend_records", "worker_sign", "TEXT")
    ensure_column(connection, "blend_records", "reviewed_sign", "TEXT")
    ensure_column(connection, "blend_records", "approved_sign", "TEXT")

    # 점도 ↔ 배합 기록 연계 (선택). lot_no/material_lot 매칭과 별개로 직접 FK.
    ensure_column(connection, "viscosity_readings", "blend_record_id", "INTEGER")
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_visc_readings_blend "
        "ON viscosity_readings(blend_record_id) WHERE blend_record_id IS NOT NULL"
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
