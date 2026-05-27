# database-cleanup — Design

| 항목 | 값 |
|---|---|
| Feature | `database-cleanup` |
| Phase | Design |
| 작성일 | 2026-05-27 |
| 선행 | `docs/01-plan/features/database-cleanup.plan.md` |

## 1. 모듈 구조

```
src/db/
├── __init__.py        # 공개 API 재-export (호출 사이트는 src.db.X 또는 src.db.<sub>.X 모두 허용)
├── time_utils.py      # utc_now_text, utc_cutoff_text
├── connection.py      # get_connection
├── queries.py         # normalize_token, row_to_dict, in_clause
├── schema.py          # init_db (스키마 정의 + apply_schema_migrations 호출 + 시드 진입점)
├── migrations.py      # _ALLOWED_TABLES, ensure_column, has_migration, record_migration,
│                      # apply_schema_migrations, standardize_recipe_units_to_grams,
│                      # drop_orphan_plan_tables (신규)
├── seeds.py           # seed_users, seed_chat_rooms, seed_materials, seed_recipes
└── audit.py           # write_audit_log, list_audit_logs
```

### 단방향 의존성 그래프

```
time_utils ──┐
             ├──> connection ──┐
queries ─────┘                 ├──> migrations ──┐
                               │                 ├──> schema ──> seeds
                               │                 │       ↑
audit ─────────────────────────┘                 │       │
  └─ uses: time_utils, queries.row_to_dict       │       │
                                                 │       │
                                  __init__.py re-exports all
```

순환 없음 확인:
- `time_utils`: 의존 없음 (stdlib only)
- `connection`: `..config` 만
- `queries`: 의존 없음 (stdlib only)
- `migrations`: `time_utils`, `connection`은 호출자가 connection 인자로 전달 → 직접 import 안 함
- `schema`: `connection`, `migrations`, `seeds`, `..config`
- `seeds`: `time_utils`, `..security` (hash_password)
- `audit`: `time_utils`, `queries`(row_to_dict)
- `__init__`: 모든 하위 모듈에서 re-export

## 2. `src/db/__init__.py` 공개 API (FR-01 보장)

분리 전 `database.py`에서 외부 import된 심볼 8개 + utility 5개:

```python
# src/db/__init__.py
from .time_utils import utc_now_text, utc_cutoff_text
from .connection import get_connection
from .queries import normalize_token, row_to_dict, in_clause
from .schema import init_db
from .migrations import (
    apply_schema_migrations,
    ensure_column,
    has_migration,
    record_migration,
    standardize_recipe_units_to_grams,
)
from .seeds import seed_users, seed_chat_rooms, seed_materials, seed_recipes
from .audit import write_audit_log, list_audit_logs

__all__ = [
    "utc_now_text",
    "utc_cutoff_text",
    "get_connection",
    "normalize_token",
    "row_to_dict",
    "in_clause",
    "init_db",
    "apply_schema_migrations",
    "ensure_column",
    "has_migration",
    "record_migration",
    "standardize_recipe_units_to_grams",
    "seed_users",
    "seed_chat_rooms",
    "seed_materials",
    "seed_recipes",
    "write_audit_log",
    "list_audit_logs",
]
```

호출 사이트가 `from ..db import get_connection, write_audit_log` 같은 평면 import만 사용해도 동작 보장.

## 3. 신규 마이그레이션: `drop_orphan_plan_tables`

### 설계

`src/db/migrations.py` 의 `apply_schema_migrations()` 끝에 추가:

```python
# 잉크/사출 OCR 기능 (28aa888, 2026-05-19) 삭제 후 잔존 테이블 정리
if not has_migration(connection, "drop_orphan_plan_tables"):
    # 외래키 의존성 순서: 자식(plan_schedules, plan_chemical_requests) → 부모(production_plans)
    connection.execute("DROP TABLE IF EXISTS plan_schedules")
    connection.execute("DROP TABLE IF EXISTS plan_chemical_requests")
    connection.execute("DROP TABLE IF EXISTS production_plans")
    record_migration(connection, "drop_orphan_plan_tables")
```

### 멱등성 보장

- `has_migration()` 체크로 1회만 실행
- `DROP TABLE IF EXISTS`로 dev DB에서도 안전
- 외래키 순서 — `plan_schedules.plan_id REFERENCES production_plans(id)` 이므로 자식부터 DROP

### 결과 (예상)

| 환경 | 분리 전 | 1회 실행 후 |
|---|---|---|
| dev (data/irms.db 깨끗) | 16개 테이블 | 16개 테이블 + `schema_migrations` 행 1건 추가 |
| prod (고아 3개 잔존 가정) | 19개 테이블 | 16개 테이블 + `schema_migrations` 행 1건 추가 |

## 4. 모듈별 코드 매핑 (분리 전 LOC → 분리 후)

| 신규 모듈 | 원본 LOC 범위 | 신규 LOC (예상) |
|---|---|---|
| `time_utils.py` | 1-3, 14-21 | ~20 |
| `connection.py` | 1-2, 24-30 | ~15 |
| `queries.py` | 613-622 | ~15 |
| `migrations.py` | 33-67, 70-81, 84-175, 178-214, + 신규 drop | ~145 |
| `schema.py` | 217-399 | ~190 |
| `seeds.py` | 402-610 | ~215 |
| `audit.py` | 625-719 | ~100 |
| `__init__.py` | (신규) | ~30 |
| **합계** | 719 | **~730** (`__init__` re-export 오버헤드 ~11 LOC) |

모두 NFR-01 (≤ 250 LOC) 충족.

## 5. 호출 사이트 갱신 매핑

기존 `from ..database import X` / `from .database import X` / `from src.database import X` → `from ..db import X` / `from .db import X` / `from src.db import X`로 변경 (심볼 그대로).

| 파일 | 현재 import |
|---|---|
| `src/main.py` | `from .database import init_db, utc_now_text` |
| `src/auth.py` | `from .database import get_connection` |
| `src/attendance_auth.py` | `from .database import get_connection, utc_now_text, write_audit_log` |
| `src/routers/api.py` | `from ..database import utc_now_text` |
| `src/routers/models.py` | `from ..database import row_to_dict` |
| `src/routers/auth_routes.py` | `from ..database import get_connection, write_audit_log` |
| `src/routers/admin_routes.py` | `from ..database import get_connection, list_audit_logs, row_to_dict, utc_now_text, write_audit_log` |
| `src/routers/attendance_routes.py` | `from ..database import get_connection, write_audit_log` |
| `src/routers/chat_routes.py` | `from ..database import get_connection, utc_cutoff_text, utc_now_text, write_audit_log` |
| `src/routers/dashboard_routes.py` | `from ..database import get_connection, row_to_dict` |
| `src/routers/recipe_import_routes.py` | `from ..database import get_connection, utc_now_text, write_audit_log` |
| `src/routers/recipe_manager_routes.py` | `from ..database import get_connection, row_to_dict, write_audit_log` |
| `src/routers/recipe_operator_routes.py` | `from ..database import (` (다중 행) |
| `src/routers/recipe_stats_routes.py` | `from ..database import get_connection, row_to_dict` |
| `src/routers/spreadsheet_routes.py` | `from ..database import get_connection, utc_now_text` |
| `src/routers/stock_routes.py` | `from ..database import get_connection, write_audit_log` |
| `src/routers/weighing_routes.py` | `from ..database import get_connection, row_to_dict, utc_now_text, write_audit_log` |
| `src/services/import_parser.py` | `from ..database import normalize_token` |
| `src/services/recipe_helpers.py` | `from ..database import row_to_dict` |
| `src/services/stock_service.py` | `from ..database import utc_now_text` |
| `tests/test_notice_chat_routes.py` | `from src.database import utc_now_text` |
| `scripts/import_excel_recipes.py` | `from src.database import get_connection, init_db, utc_now_text, write_audit_log` |

→ 단순 `database` → `db` 치환. sed/replace_all 또는 Edit 도구로 1건씩 수행.

## 6. 검증 절차 (gap-detector 대응)

| 단계 | 명령 / 방법 | 기대치 |
|---|---|---|
| 정적: import 잔여 | `grep -rE "from \\.+database\\b\|from src\\.database\\b" src/ tests/ scripts/` | 0건 |
| 정적: LOC | `wc -l src/db/*.py` | 각 ≤ 250 |
| 정적: 심볼 보존 | `python -c "from src import db; print(sorted(db.__all__))"` | 17개 (Plan §6 참조) |
| 동적: 빌드 | `python -c "from src.main import app; print(app.title)"` | `IRMS` |
| 동적: 테스트 | `pytest -q` | all pass (≥ 40) |
| 동적: 마이그레이션 시뮬 | 임시 DB에 고아 테이블 생성 → `init_db()` → 테이블 없음 + 마이그레이션 기록 | OK |

## 7. 함정 / 학습 사항 적용

메모리 `project_split_refactor_pattern.md` 의 Phase 1 (Python) 패턴 함정 회피:

- ❌ **nested closure 헬퍼**: 없음 (database.py는 module-level 함수만)
- ❌ **cross-router import 안티패턴**: services 추출 불필요 (현재 모든 함수가 module-level public)
- ✅ **public symbol naming**: 언더스코어 prefix 제거 — `_ALLOWED_TABLES`, `_SAFE_IDENTIFIER` 는 `migrations.py` 내부에서만 쓰이므로 유지
- ✅ **순환 import 차단**: 의존성 그래프 §1 참고

## 8. Out of scope (이번 분리에서 다루지 않음)

- `seed_*` 함수들의 동적 데이터(사용자 목록, 시드 레시피) 외부 JSON/CSV 이관
- Audit log 조회 화면 (Plan §3 비목표)
- `_ALLOWED_TABLES`를 schema.py에서 자동 생성 (현재는 수동 동기화)

## 9. 롤백 계획

`git revert <commit>` 1회로 복원 가능. 마이그레이션은 단방향이지만 (DROP된 고아 테이블은 비어있어야 함) 데이터 손실 없음 (28aa888 시점 이후 사용 안 됨).
