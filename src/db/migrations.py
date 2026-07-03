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
    "attendance_users",
    "viscosity_products",
    "viscosity_readings",
    "blend_records",
    "blend_details",
    "workers",
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
    ensure_column(connection, "recipe_items", "actual_weight", "REAL")
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_recipe_items_measured_at ON recipe_items(measured_at)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_recipe_items_actual_weight "
        "ON recipe_items(actual_weight) WHERE actual_weight IS NOT NULL"
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
    # 레시피(버전)별 "사용 시작일" — 미지정 시 등록일로 갈음(등록 시 채움).
    ensure_column(connection, "recipes", "effective_from", "TEXT")
    # DHR 전용 레시피: 일반 레시피 조회·배합 선택에서 제외, DHR(배합일지) 전용으로만 사용.
    ensure_column(connection, "recipes", "is_dhr", "INTEGER NOT NULL DEFAULT 0")

    # excel-recipe-migration: 엑셀 원본의 비고 컬럼 이관용
    ensure_column(connection, "recipes", "remark", "TEXT")

    # 레시피 상태 단순화: (구) 계량 워크플로의 pending/in_progress 단계는 /blend 전환으로
    # 폐기됨(승인 단계 없음 → 영구 정체). 등록 즉시 사용(completed) 정책으로 통일하고
    # 기존에 정체돼 있던 레시피도 completed 로 전환한다(취소 건은 보존).
    if not has_migration(connection, "recipes_status_active_default"):
        connection.execute(
            "UPDATE recipes SET status = 'completed', "
            "completed_at = COALESCE(completed_at, created_at) "
            "WHERE status IN ('pending', 'in_progress')"
        )
        record_migration(connection, "recipes_status_active_default")

    # single-session enforcement: 로그인 시 발급, 새 로그인 시 회전
    ensure_column(connection, "users", "session_token", "TEXT")

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
    # 백필(멱등): 기존에 따로 넣은 점도값을 LOT(lot_no=product_lot)로 배합 기록에 연계.
    connection.execute(
        """
        UPDATE viscosity_readings
        SET blend_record_id = (
            SELECT br.id FROM blend_records br
            WHERE br.product_lot = viscosity_readings.lot_no
            ORDER BY br.id LIMIT 1
        )
        WHERE blend_record_id IS NULL
          AND lot_no IN (SELECT product_lot FROM blend_records)
        """
    )

    # 점도 측정 조건(반제품마다 1회 세팅, 매 측정마다 재입력하지 않음): rpm + 온도(°C)
    ensure_column(connection, "viscosity_products", "rpm", "REAL")
    ensure_column(connection, "viscosity_products", "temperature", "REAL")

    # 매일 점도 측정 알림 대상 여부. 웹 점도 설정에서 반제품별로 켠다(트레이 대신 웹이 소유).
    # 트레이는 오늘 측정이 밀린 '알림 대상' 반제품을 서버에 물어보기만 한다.
    ensure_column(connection, "viscosity_products", "remind_daily", "INTEGER NOT NULL DEFAULT 0")

    # 반응기(1~4)에서 진행하는 반제품 여부 + 측정 시 반응기 번호. 특정 반제품만 반응기
    # 지정 필수(use_reactor=1). 지정 시 점도를 반응기별로 추세·이상 분석할 수 있다.
    ensure_column(connection, "viscosity_products", "use_reactor", "INTEGER NOT NULL DEFAULT 0")
    ensure_column(connection, "viscosity_readings", "reactor", "INTEGER")
    # 반응기는 배합 실적을 진행한 위치 → blend_records 에 기록. 점도는 실적에서 물려받아 표시.
    ensure_column(connection, "blend_records", "reactor", "INTEGER")

    # auth-simplify: 작업자 명단(비밀번호 없는 이름 등록부). 근태 제외 작업자는 이름만 입력.
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS workers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        )
        """
    )
    # 기존 사용자 이름을 작업자 명단에 1회 프리필 (로그인 계정과 별개로 선택 편의)
    if not has_migration(connection, "prefill_workers_from_users"):
        now = utc_now_text()
        for row in connection.execute(
            "SELECT DISTINCT display_name FROM users WHERE display_name IS NOT NULL AND TRIM(display_name) != ''"
        ).fetchall():
            connection.execute(
                "INSERT OR IGNORE INTO workers (name, is_active, created_at) VALUES (?, 1, ?)",
                (row["display_name"].strip(), now),
            )
        record_migration(connection, "prefill_workers_from_users")

    # auth-simplify: 단일 관리자 계정(admin/admin) 보장. 비번은 관리 화면에서 변경 가능.
    # 기존 계정 비활성화는 자동이 아니라 admin 이 화면에서 1회 수행(되돌릴 수 있게).
    if not has_migration(connection, "ensure_single_admin"):
        from ..security import hash_password
        existing = connection.execute(
            "SELECT id FROM users WHERE username = 'admin'"
        ).fetchone()
        if existing:
            connection.execute(
                "UPDATE users SET role = 'admin', access_level = 'admin', is_active = 1 "
                "WHERE username = 'admin'"
            )
        else:
            connection.execute(
                "INSERT INTO users (username, password_hash, display_name, role, "
                "access_level, is_active, created_at) "
                "VALUES ('admin', ?, '관리자', 'admin', 'admin', 1, ?)",
                (hash_password("admin"), utc_now_text()),
            )
        record_migration(connection, "ensure_single_admin")

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

    # 채팅 기능 제거 후 잔존 테이블 정리. chat_messages.created_by_user_id 가
    # users(id) 를 FK 참조해 사용자 삭제가 FOREIGN KEY 위반(500)으로 막히던 원인.
    if not has_migration(connection, "drop_orphan_chat_tables"):
        connection.execute("DROP TABLE IF EXISTS chat_messages")
        connection.execute("DROP TABLE IF EXISTS chat_rooms")
        record_migration(connection, "drop_orphan_chat_tables")


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
