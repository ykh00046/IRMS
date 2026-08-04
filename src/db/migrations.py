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
    # 아래 4개가 빠져 있어, 이 테이블에 ensure_column 을 쓰는 순간 ValueError 로
    # init_db 전체가 죽어 서버가 기동하지 않는다(비개발자 운영 PC에서 자동 git pull
    # 직후 발생하면 복구 수단이 없다). 실제 스키마에 있는 테이블은 모두 등재해 둔다.
    "app_settings",
    "blend_rescale_approvals",
    "blend_save_requests",
    "item_code_master",
    "recipe_steps",
    "manual_material_lots",
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

    # 기준 배합량(g, 선택): 이 레시피를 기본으로 배합할 때의 총량. 연구소 이관 값이
    # 100/4000 등으로 안 떨어져도(예: 합 3924.38) 배합 화면 '기준 적용' 버튼이 이 값을
    # 사용. 미설정 시 자재 합계로 폴백.
    ensure_column(connection, "recipes", "base_total", "REAL")
    # 공정 설명 줄(레시피 자재 사이 안내문 — 예: "개시제 교반 - 300rpm").
    # position = 그 설명 앞에 오는 자재 수(0=맨 앞). 공식 배합일지 출력에는 미포함,
    # 배합 화면·기록 상세 표시 전용.
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS recipe_steps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipe_id INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
            position INTEGER NOT NULL,
            note TEXT NOT NULL
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_recipe_steps_recipe ON recipe_steps(recipe_id)"
    )

    # 기준 배합량 확장(최대 3개, 쉼표 구분 TEXT). 기존 단일 base_total 은 이관 후 잔존(legacy).
    ensure_column(connection, "recipes", "base_totals", "TEXT")
    connection.execute(
        "UPDATE recipes SET base_totals = CAST(base_total AS TEXT) "
        "WHERE base_total IS NOT NULL AND base_totals IS NULL"
    )

    # 기준 자재(anchor material): 반제품(PB 등) 배합 시 먼저 계량할 자재를 가리킨다.
    # 이 자재의 실측 중량을 기준으로 다른 자재들의 이론량을 산출(total_amount 기준 대신).
    # 미지정 시 기존처럼 총량(total_amount) 기준. recipe_items.material_id 중 하나.
    ensure_column(connection, "recipes", "anchor_material_id", "INTEGER")
    # 레시피별 계량 허용 편차(g). NULL = 기본값 0.05g 사용(하위호환 — 기존 레시피 동작 불변).
    ensure_column(connection, "recipes", "tolerance_g", "REAL")
    # 레시피 분류(약품/합성/잉크). NULL = 미분류(하위호환 — 기존 레시피 동작 불변).
    # 값 검증은 API에서(여기선 컬럼만). 배합·이어서계량 화면의 2단계 선택(분류→레시피)용.
    ensure_column(connection, "recipes", "category", "TEXT")

    # ERP 품목코드 도입(item-code P1). 마스터는 item_code_master, 부여된 코드는
    # materials.code / recipes.product_code. NULL=미부여(하위호환 — 기존 동작 불변).
    ensure_column(connection, "materials", "code", "TEXT")
    connection.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_materials_code "
        "ON materials(code) WHERE code IS NOT NULL"
    )
    # 반제품 코드 — 개정 체인이 같은 코드를 공유하므로 UNIQUE 아님.
    ensure_column(connection, "recipes", "product_code", "TEXT")
    # ERP 품목 마스터(엑셀 임포트 스크립트 tools/import_item_codes.py 가 채움)
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS item_code_master (
            code TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            spec TEXT,
            unit TEXT,
            kind TEXT NOT NULL CHECK (kind IN ('material', 'product')),
            category_hint TEXT,
            source TEXT,
            imported_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_item_code_master_kind_name "
        "ON item_code_master(kind, name)"
    )

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
        "CREATE INDEX IF NOT EXISTS idx_blend_records_recipe ON blend_records(recipe_id)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_blend_details_record "
        "ON blend_details(blend_record_id, sequence_order)"
    )
    # 자재 LOT 역추적용 — blend_details.material_lot 검색(추적성 조회·기록 검색 확장)
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_blend_details_material_lot "
        "ON blend_details(material_lot) WHERE material_lot IS NOT NULL"
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
    # spec-out(측정 제외, 2026-07-31): 단일 이상 측정 하나가 σ(이상을 잡아야 할 표준편차)를
    # 스스로 오염시키는 문제를 끊는다. 삭제하지 않고 '통계 제외' 로만 표시 — 기록에는 남고
    # 화면엔 배지+사유로 보이되, 평균/σ/관리한계/추세/기간 집계에서는 빠진다(책임자만 토글).
    ensure_column(connection, "viscosity_readings", "excluded", "INTEGER NOT NULL DEFAULT 0")
    ensure_column(connection, "viscosity_readings", "exclude_reason", "TEXT")
    ensure_column(connection, "viscosity_readings", "excluded_by", "TEXT")
    ensure_column(connection, "viscosity_readings", "excluded_at", "TEXT")
    # 반응기는 배합 실적을 진행한 위치 → blend_records 에 기록. 점도는 실적에서 물려받아 표시.
    ensure_column(connection, "blend_records", "reactor", "INTEGER")
    # 수동 입력 여부: 저울 연동 중 계량값을 직접 입력했는가(추적성).
    # 기록(배치) 단위 + 상세(자재 행) 단위 — 어느 자재가 수동이었는지까지 남긴다.
    ensure_column(connection, "blend_records", "manual_entry", "INTEGER NOT NULL DEFAULT 0")
    # 증량 승인제(rescale-approval 2026-07-22): 증량 이력 JSON([{before_total, after_total,
    # approved_by|absence, at}]) · 횟수(최대 2회) · 책임자 미확인(부재 진행) 플래그.
    ensure_column(connection, "blend_records", "rescale_events_json", "TEXT")
    ensure_column(connection, "blend_records", "rescale_count", "INTEGER NOT NULL DEFAULT 0")
    ensure_column(connection, "blend_records", "rescale_unacked", "INTEGER NOT NULL DEFAULT 0")
    # 일괄 재생성(bulk-regen) 표식: 같은 레시피로 여러 (작업일, 총량) 실적을 한 번에
    # 생성한 문서·계획용 기록. 실제 현장 계량이 아니라 재생성본임을 목록/상세/공식 DHR 에
    # 표시하기 위한 플래그(0=일반 실적, 1=일괄 재생성).
    ensure_column(connection, "blend_records", "is_bulk_regenerated", "INTEGER NOT NULL DEFAULT 0")
    # 수기 입력 '책임자 부재 진행'(2026-07-25): 저울 전용 모드에서 비밀번호 승인 없이 사유만
    # 남기고 손입력한 기록. 사유 텍스트 + 책임자 미확인 플래그 — 증량 부재와 동일하게 사후
    # 확인(ack) 루프·트레이 반복 알림 대상이 된다(사용자 결정: 증량과 대칭으로 격상).
    ensure_column(connection, "blend_records", "manual_absence_reason", "TEXT")
    ensure_column(connection, "blend_records", "manual_unacked", "INTEGER NOT NULL DEFAULT 0")
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_blend_records_rescale_unacked "
        "ON blend_records(rescale_unacked) WHERE rescale_unacked = 1"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_blend_records_manual_unacked "
        "ON blend_records(manual_unacked) WHERE manual_unacked = 1"
    )
    # 성장 대비 인덱스(2026-07-25 감사, 40,000기록/320,000상세 합성 DB 로 실측).
    # 자재 사용량 집계는 기간 1년 조회에서 758~988ms 가 걸렸고(기록당 상세를 반복 조회 +
    # GROUP BY 임시 B-tree), 아래 두 인덱스로 커버링 스캔이 되어 ~500ms 로 떨어진다.
    # 대상: material_usage / material_usage_periods / material_batches / public material-usage.
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_blend_records_status_date "
        "ON blend_records(status, work_date)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_blend_details_record_material "
        "ON blend_details(blend_record_id, material_name, actual_amount, theory_amount)"
    )
    # 제품별 빈도(product_usage)의 GROUP BY product_name + MAX(work_date) 커버.
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_blend_records_product_status "
        "ON blend_records(product_name, status, work_date)"
    )
    # 감사 로그 조회는 ORDER BY created_at DESC, id DESC 인데 기존 인덱스가 created_at
    # 단독이라 두 번째 키에서 임시 B-tree 가 붙었다.
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_logs_created_id "
        "ON audit_logs(created_at DESC, id DESC)"
    )
    # 즉석 승인 토큰(세션 미생성 1회 인증): 발급 후 저장 시 1회 소비.
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS blend_rescale_approvals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            approver TEXT NOT NULL,
            created_at TEXT NOT NULL,
            used INTEGER NOT NULL DEFAULT 0,
            purpose TEXT
        )
        """
    )
    # 승인의 '목적' — 'rescale'(초과 계량 증량) | 'manual'(저울 전용 모드 수기 입력 허용).
    # 없던 시절에는 두 승인이 같은 토큰이라, 수기입력 승인을 받아 그 approval_id 를
    # 30분 안에 아무 배합의 증량 승인으로 통과시킬 수 있었다(책임자가 무엇을 승인했는지
    # DB 로 구분 불가). 소비 시 목적이 'rescale' 인 토큰만 증량으로 인정한다.
    ensure_column(connection, "blend_rescale_approvals", "purpose", "TEXT")
    if not has_migration(connection, "blend_rescale_approvals_purpose"):
        # 하위호환: 이미 발급된 옛 행은 목적을 알 수 없다. 옛 코드에서 실제로 소비되던
        # 유일한 목적이 증량이므로 'rescale' 로 백필한다 — 배포 직후 저장을 앞둔
        # 현장의 유효 승인이 갑자기 무효가 되어 작업이 멈추는 일을 막는다(1회만 수행).
        connection.execute(
            "UPDATE blend_rescale_approvals SET purpose = 'rescale' "
            "WHERE purpose IS NULL OR TRIM(purpose) = ''"
        )
        record_migration(connection, "blend_rescale_approvals_purpose")

    # 저장 멱등성(중복 방지): 클라이언트가 만든 1회용 request_id 를 기록한다.
    # 저장이 커밋될 때 이 행도 같은 트랜잭션으로 함께 남으므로, 실패한 저장은 id 를
    # 소모하지 않는다. 타임아웃 재시도(같은 id)는 첫 결과를 그대로 되돌려준다.
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS blend_save_requests (
            request_id TEXT PRIMARY KEY,
            endpoint TEXT NOT NULL,
            record_ids TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_blend_save_requests_created "
        "ON blend_save_requests(created_at)"
    )
    ensure_column(connection, "blend_details", "manual_entry", "INTEGER NOT NULL DEFAULT 0")
    # 반응기 이월(carry-over) 행 표식 — 반응기 1차 배합의 총량을 2차 기준 자재 실제량으로
    # 그대로 가져온 행. 서버가 강제 채운 값임을 추적에 남긴다(manual_entry 과 함께 보관).
    ensure_column(connection, "blend_details", "carried_over", "INTEGER NOT NULL DEFAULT 0")

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
    # people-merge: 이용자 명단(workers)을 사람 목록의 단일 소스로. 그 중 지정된 사람만
    # 개인 비밀번호를 가진 책임자(수정·삭제·관리 권한)로 로그인한다.
    ensure_column(connection, "workers", "is_manager", "INTEGER NOT NULL DEFAULT 0")
    ensure_column(connection, "workers", "password_hash", "TEXT")
    ensure_column(connection, "workers", "session_token", "TEXT")
    # 작업자 분류(파트): 약품/합성/잉크/용수. NULL=미지정(하위호환 — 기존 동작 불변).
    # 레시피 분류와 동일 체계 — 작업자 로그인 화면에서 파트별로 걸러 고르는 용도.
    ensure_column(connection, "workers", "category", "TEXT")
    # 근태 로그인 실패 카운터의 시간 윈도우 기준 시각 — 오래된 실패는 감쇠시킨다(감사 F-11).
    ensure_column(connection, "attendance_users", "last_failed_at", "TEXT")
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
                "UPDATE users SET role = 'admin', access_level = 'manager', is_active = 1 "
                "WHERE username = 'admin'"
            )
        else:
            connection.execute(
                "INSERT INTO users (username, password_hash, display_name, role, "
                "access_level, is_active, created_at) "
                "VALUES ('admin', ?, '책임자', 'admin', 'manager', 1, ?)",
                (hash_password("admin"), utc_now_text()),
            )
        record_migration(connection, "ensure_single_admin")

    # admin access level: 김지훈, 김진우, 함지안 → admin (구 3단계 시절 승격)
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

    # 권한 2단계 통합: 구 '관리자(admin)'를 '책임자(manager)'로 흡수. 남아있는 모든
    # access_level='admin' 계정을 manager 로 승격하고, 기본 admin 계정의 표시 이름이
    # 옛 기본값 '관리자'면 '책임자'로 정리(사용자가 바꾼 이름은 보존).
    if not has_migration(connection, "collapse_admin_into_manager"):
        connection.execute(
            "UPDATE users SET access_level = 'manager' WHERE access_level = 'admin'"
        )
        connection.execute(
            "UPDATE users SET display_name = '책임자' "
            "WHERE username = 'admin' AND display_name = '관리자'"
        )
        record_migration(connection, "collapse_admin_into_manager")

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

    # scale-only-mode: 앱 전역 on/off 설정(키-값). 저울 전용 입력 모드 토글값 보관.
    # 시드 없음 — 행 부재 = 기본값(false). GET 서비스 헬퍼가 부재/구버전을 기본값 폴밋.
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            updated_by TEXT
        )
        """
    )

    # 감사 F-1: product_lot 전역 유일 봉인 — 채번 경쟁(read-then-write)으로 생겼을 수
    # 있는 기존 중복을 정리(재채번 + audit_logs 기록)한 뒤 UNIQUE 인덱스를 만든다.
    # 실패(예: 정리 후에도 위반)하면 init_db 트랜잭션 전체가 롤백되고 서버 기동이
    # 실패한다 — 조용히 넘어가지 않는다(fail-loud).
    if not has_migration(connection, "blend_records_product_lot_unique"):
        dedup_product_lots(connection)
        connection.execute("DROP INDEX IF EXISTS idx_blend_records_lot")
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_blend_records_lot_unique "
            "ON blend_records(product_lot)"
        )
        record_migration(connection, "blend_records_product_lot_unique")

    # reactor-ownership: 반응기 사용 여부 소유를 viscosity_products 에서 recipes 로 이전.
    # recipes.use_reactor 가 새 단일 소스(배합 반응기 강제·점도 화면 모두 이 값을 따름).
    ensure_column(connection, "recipes", "use_reactor", "INTEGER NOT NULL DEFAULT 0")
    # 1회 마이게이션 — 점도 제품(use_reactor=1)에 매칭되는 반제품명(code 또는 name 일치)의
    # 기존 레시피를 use_reactor=1 로 시드. 이후에는 레시피 등록/PUT 으로만 변경된다.
    if not has_migration(connection, "recipes_use_reactor_from_viscosity"):
        connection.execute(
            """
            UPDATE recipes
            SET use_reactor = 1
            WHERE product_name IN (
                SELECT name FROM viscosity_products WHERE use_reactor = 1
                UNION
                SELECT code FROM viscosity_products WHERE use_reactor = 1
            )
            """
        )
        record_migration(connection, "recipes_use_reactor_from_viscosity")

    # 파생(derived): 이 레시피가 앞 단계의 총량을 그대로 이월받아 다시 계량하지 않는지.
    # use_reactor(반응기 번호 요구) 와는 독립 — 반응기 전용·파생 전용·둘 다·둘 다 아님 가능.
    # 파생 여부가 반응기 이월(carry-over) 허용 여부를 결정한다. 데이터 시드 없음 — 사용자가
    # 레시피마다 지정(기본 0). use_reactor 시드와 달리 마이그레이션 기록만 남긴다(idempotent).
    ensure_column(connection, "recipes", "is_derived", "INTEGER NOT NULL DEFAULT 0")
    if not has_migration(connection, "recipes_is_derived"):
        record_migration(connection, "recipes_is_derived")

    # 1차→2차 레시피 연계 — 2차 레시피가 명시적으로 자신의 1차 레시피를 가리킨다(recipes.stage1_recipe_id).
    # NULL = 단일 단계 레시피(1차 링크 없음). use_reactor/is_derived 와 동일한 ensure_column 패턴.
    # 데이터 시드 없음 — 사용자가 2차 레시피마다 지정(기본 NULL).
    ensure_column(connection, "recipes", "stage1_recipe_id", "INTEGER")
    if not has_migration(connection, "recipes_stage1_recipe_id"):
        record_migration(connection, "recipes_stage1_recipe_id")

    # 반응기 현황판(reactor_slots)은 도입 후 제거됨(현장 요청) — 이미 만들어진 DB 는 정리한다.
    # 파생 이월·반응기 번호 저장 등 나머지 기능은 그대로. 고아 테이블 DROP 패턴.
    if not has_migration(connection, "drop_reactor_slots"):
        connection.execute("DROP TABLE IF EXISTS reactor_slots")
        record_migration(connection, "drop_reactor_slots")

    # 수동 자재 LOT — 책임자가 즉석 인증으로 추가한 LOT(엑셀 재고와 무관하게 valid).
    # 배합 화면에서 ERP 엑셀에 없거나 소진된 LOT 를 부득이 써야 할 때, 책임자 자격
    # 검증을 거쳐 이 표에 남기면 현장이 막히지 않는다. (material_code, lot) 유일.
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS manual_material_lots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            material_code TEXT NOT NULL,
            lot TEXT NOT NULL,
            note TEXT,
            created_by TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(material_code, lot)
        )
        """
    )

    # 앞 단계 배합 기록에 없는 반제품 LOT 로 진행한 건(2026-08-04 차단 해제).
    # 예전에는 저장을 막고 사유 입력을 요구했다 — 1차 배합을 만들고 곧바로 2차에 투입하는
    # 정당한 경우에도 매번 걸려, 작업자가 사유란에 아무 글자나 치고 넘어가면서 통제가
    # 형해화됐다. 지금은 막지 않고 가벼운 확인 창만 띄운다. 대신 **진행한 사실 자체를
    # 여기에 구조화해 남긴다** — 사유가 비어 있어도 행은 반드시 생긴다. 그래야 나중에
    # "그 LOT 이 결국 생겼는지" 자동 대사(이 표 × blend_records.product_lot)가 가능하다.
    #   acknowledged: 작업자가 확인 창의 '계속' 을 눌렀는가(0 = 창을 못 본 경로로 저장됨).
    # 추가 전용(additive) 테이블 — 기존 데이터·컬럼에 영향 없음.
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS blend_lot_acks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            record_id INTEGER NOT NULL,
            material_name TEXT NOT NULL,
            material_lot TEXT NOT NULL,
            reason TEXT,
            acknowledged INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_blend_lot_acks_record "
        "ON blend_lot_acks(record_id)"
    )
    # 대사(reconciliation) 조회축 — (자재명, LOT) 로 blend_records 와 맞춰본다.
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_blend_lot_acks_pair "
        "ON blend_lot_acks(material_name, material_lot)"
    )


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


def dedup_product_lots(connection: sqlite3.Connection) -> list[dict]:
    """중복 product_lot 정리 (감사 F-1 마이그레이션 보조).

    각 중복 그룹에서 최소 id(최초 저장분)의 LOT 은 보존하고, 나머지 행을 재채번한다.
    - 정규형({product_name}{YYMMDD}{seq}) LOT: 같은 base 의 다음 빈 순번(2자리 zero-pad)
    - 비정규(레거시) LOT: 원 라벨 뒤에 "-2", "-3" … 접미(라벨 식별성 보존)
    - 연계 점도: blend_record_id 로 물린 viscosity_readings.lot_no 를 함께 갱신
    - 모든 변경을 audit_logs(action='product_lot_dedup')에 old→new 로 기록
    반환: [{"id", "old", "new"}, ...]
    """
    from .audit import write_audit_log  # 같은 패키지 — 순환 없음(audit 은 queries/time_utils 만 의존)

    dup_rows = connection.execute(
        """
        SELECT id, product_lot, product_name, work_date FROM blend_records
        WHERE product_lot IN (
            SELECT product_lot FROM blend_records
            GROUP BY product_lot HAVING COUNT(*) > 1
        )
        ORDER BY product_lot, id
        """
    ).fetchall()
    if not dup_rows:
        return []
    taken = {
        str(r["product_lot"])
        for r in connection.execute("SELECT product_lot FROM blend_records").fetchall()
    }
    keep_seen: set[str] = set()
    changes: list[dict] = []
    for row in dup_rows:
        old = str(row["product_lot"])
        if old not in keep_seen:
            keep_seen.add(old)  # 그룹 대표(최소 id) — 원 LOT 유지
            continue
        # base 는 generate_product_lot 과 동일 규칙으로 재계산 (제품명 + YYMMDD)
        digits = "".join(ch for ch in str(row["work_date"]) if ch.isdigit())
        yymmdd = digits[2:8] if len(digits) >= 8 else digits[-6:]
        base = f"{str(row['product_name']).strip()}{yymmdd}"
        suffix = old[len(base):] if old.startswith(base) else None
        if suffix is not None and suffix.isdigit():
            seq = int(suffix)
            while True:
                seq += 1
                new = f"{base}{seq:02d}"
                if new not in taken:
                    break
        else:
            n = 1
            while True:
                n += 1
                new = f"{old}-{n}"
                if new not in taken:
                    break
        taken.add(new)
        connection.execute(
            "UPDATE blend_records SET product_lot = ?, updated_at = ? WHERE id = ?",
            (new, utc_now_text(), int(row["id"])),
        )
        connection.execute(
            "UPDATE viscosity_readings SET lot_no = ? WHERE blend_record_id = ? AND lot_no = ?",
            (new, int(row["id"]), old),
        )
        write_audit_log(
            connection,
            action="product_lot_dedup",
            target_type="blend_record",
            target_id=int(row["id"]),
            target_label=new,
            details={"old": old, "new": new},
        )
        changes.append({"id": int(row["id"]), "old": old, "new": new})
    return changes
