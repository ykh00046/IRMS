# database-cleanup — Completion Report

| 항목 | 값 |
|---|---|
| Feature | `database-cleanup` |
| Phase | Report (PDCA 완료) |
| Level | Dynamic |
| 완료일 | 2026-05-27 |
| Match Rate | **99%** |
| pytest | **40/40 passed** (21.31s) |
| 선행 사이클 | split-work-js (2026-05-27) |

## 1. 요약

`src/database.py` **719 LOC** 단일 모듈을 책임별 **7개 sub-module + 1 package init** (총 791 LOC, 모두 ≤ 250)로 분리. 동시에 잉크/사출 OCR 기능(`28aa888`, 2026-05-19) 삭제 후 운영 DB에 잔존할 수 있는 **3개 고아 테이블**을 idempotent 마이그레이션으로 정리.

외부 동작 0줄 변경, 호출 사이트 21개 일괄 갱신, pytest 회귀 0건.

## 2. 산출물

### 신규 패키지 `src/db/`

| 모듈 | LOC | 역할 |
|---|---:|---|
| `__init__.py` | 46 | 18개 심볼 re-export |
| `time_utils.py` | 11 | `utc_now_text`, `utc_cutoff_text` |
| `connection.py` | 12 | `get_connection` |
| `queries.py` | 14 | `normalize_token`, `row_to_dict`, `in_clause` |
| `migrations.py` | 197 | ALTER/idempotent 마이그레이션 (+ 신규 `drop_orphan_plan_tables`) |
| `schema.py` | 193 | 테이블/인덱스 정의 + `init_db` 진입점 |
| `seeds.py` | 215 | 사용자/방/원재료/레시피 시드 |
| `audit.py` | 103 | `write_audit_log`, `list_audit_logs` |
| **합계** | **791** | (원본 719 + re-export 오버헤드 ~46 + 신규 DROP 마이그 ~12) |

### 신규 마이그레이션: `drop_orphan_plan_tables`

```python
if not has_migration(connection, "drop_orphan_plan_tables"):
    connection.execute("DROP TABLE IF EXISTS plan_schedules")
    connection.execute("DROP TABLE IF EXISTS plan_chemical_requests")
    connection.execute("DROP TABLE IF EXISTS production_plans")
    record_migration(connection, "drop_orphan_plan_tables")
```

- 외래키 자식 → 부모 순서 DROP
- `IF EXISTS`로 dev DB(테이블 없음)와 prod DB(테이블 있음) 양쪽에서 안전
- 2회 호출 시 `schema_migrations` 행 1건만 (시뮬레이션 검증 완료)

### 호출 사이트 갱신 (21개)

| 영역 | 파일 수 | 변경 패턴 |
|---|---:|---|
| `src/` | 19 | `from ..database` → `from ..db` / `from .database` → `from .db` |
| `tests/` | 1 | `from src.database` → `from src.db` |
| `scripts/` | 1 | `from src.database` → `from src.db` |
| `src/database.py` | -1 | 삭제 |

## 3. 검증 결과 (Gap 분석)

| ID | 항목 | 기준 | 실측 | 상태 |
|---|---|---|---|---|
| FR-01 | 심볼 보존 | 외부 import 가능 심볼 집합 동일 | 18개 보존 | ✅ |
| FR-02 | `init_db()` 진입점 | `apply_schema_migrations()` 호출 | `schema.py:170` | ✅ |
| FR-03 | 마이그레이션 멱등성 | 1회만 기록 | 2회 실행 시 count=1 | ✅ |
| FR-04 | dev/prod 양립 | `DROP TABLE IF EXISTS` | 시뮬레이션 통과 | ✅ |
| FR-05 | 평면 import | `from src.db import X` 동작 | OK | ✅ |
| NFR-01 | 모듈 LOC ≤ 250 | 각 모듈 250 이하 | max 215 (seeds) | ✅ |
| NFR-02 | 순환 import 없음 | import 성공 | OK | ✅ |
| NFR-03 | 신규 의존성 0 | 외부 라이브러리 추가 X | OK | ✅ |
| NFR-04 | 테스트 시간 회귀 | ±10% | 21.31s, 정상 범위 | ✅ |
| G1 | 모듈 분할 | 책임별 분리 | 7 sub-module + __init__ | ✅ |
| G2 | 고아 테이블 정리 | 3개 DROP | 시뮬 검증 | ✅ |
| G3 | pytest | 40/40 | 40 passed in 21.31s | ✅ |
| G4 | import 잔여 | 0건 | 0건 (`grep -rE`) | ✅ |

**13/13 통과, Match Rate 99%** (Plan에 표기한 "17 심볼"이 실제 18인 사소한 카운트 차이만 존재, 본질적 격차 없음).

## 4. 마이그레이션 시뮬레이션 로그

```
BEFORE: ['plan_chemical_requests', 'plan_schedules', 'production_plans']
AFTER : ['attendance_users', 'audit_logs', 'chat_messages', 'chat_rooms',
         'material_aliases', 'material_stock_logs', 'materials',
         'recipe_items', 'recipes', 'schema_migrations', 'sqlite_sequence',
         'ss_cells', 'ss_columns', 'ss_products', 'ss_rows', 'users']
migration row count after 1st init_db: 1
migration row count after 2nd init_db: 1
orphans dropped: True
```

## 5. 학습 사항 (Lessons learned)

### 5-1. seeds.py 의 schema 순환 회피
처음에는 `schema.py`가 `SEED_DEMO_DATA` 분기에서 `seeds`를 import해야 했고, `seeds`도 `time_utils`를 의존했다. 직선 import로 두면 `schema → seeds → time_utils`로 깔끔하나, `seeds`를 top-level에서 import하면 `init_db` 정의 시점에 즉시 로드되어 `seed_users` 등이 schema 정의 전에 노출된다는 의미상의 어색함이 있음. 해결: `seeds` import를 `init_db()` 함수 body 안 `if SEED_DEMO_DATA:` 블록으로 lazy 처리. 런타임 비용 무시 가능, 모듈 그래프 더 깔끔해짐.

### 5-2. WSL venv vs Windows 인터프리터
프로젝트 `.venv/`는 WSL Linux 빌드 (ELF 바이너리)라 Windows의 Git Bash에서 직접 실행 불가. 시스템 Python 3.13 (`AppData/Local/Programs/Python/Python313/python.exe`)으로 폴백해 fastapi/slowapi/pytest 모두 정상 작동. 향후 QA 자동화는 두 환경 모두 고려한 launcher 스크립트가 있으면 매끄러움.

### 5-3. pytest 임시 디렉터리 권한
이전 테스트 실행 잔재 `tmp_test_runtime/tmp*` 가 권한 문제로 수집 단계에서 실패. `pytest tests/`로 testpaths 명시해 우회. 본 사이클의 `cleanup` 범위에는 포함하지 않았지만, `pyproject.toml`에 `[tool.pytest.ini_options] testpaths = ["tests"]` 추가가 후속 청소 후보.

## 6. 적용 메모리 패턴

- [[project_split_refactor_pattern]] — Phase 1 (Python 라우터/서비스 분리) 패턴을 그대로 계승. nested closure 없는 단일 모듈이라 services 추출이 아닌 책임별 sub-module 분할로 변형 적용.
- [[feedback_browser_smoke_pattern]] — 적용 안 함 (DB 레이어 변경이라 브라우저 스모크 불필요, FastAPI 빌드 smoke로 갈음).

## 7. 부수 효과

- **잠재 운영 DB 청소**: 다음 `update_and_run.bat` 실행 시 운영 PC에서 자동으로 고아 테이블 DROP. 운영 측 별도 조치 불필요.
- **다음 PDCA 후보 변동**: 메모리 `project_stabilization.md`에 명시된 "다음 후보: attendance_excel.py(791) 또는 database.py(719)" 중 후자가 해소됨. 다음 부채 후보는 `attendance_excel.py` 단일.

## 8. 미해결 / 후속 작업

| 항목 | 우선순위 | 비고 |
|---|---|---|
| `pyproject.toml`에 `testpaths = ["tests"]` 추가 | 낮음 | QA 우회 불필요해짐 |
| `attendance_excel.py` (791 LOC) 분리 | 중 | 다음 R2 후보 1순위 |
| `_ALLOWED_TABLES`를 `schema.py`에서 자동 생성 | 낮음 | 수동 동기화 부담 적음 |

## 9. 커밋 권장 메시지

```
Split database.py (719 LOC) into src/db/ package + drop orphan tables

- Split src/database.py into 7 sub-modules under src/db/:
  time_utils, connection, queries, migrations, schema, seeds, audit
- Add drop_orphan_plan_tables migration to clean up production_plans /
  plan_schedules / plan_chemical_requests left over from the
  ink/injection OCR removal (28aa888, 2026-05-19). Idempotent via
  schema_migrations and DROP TABLE IF EXISTS — safe on both dev and
  prod DBs.
- Update 21 call sites (src/, tests/, scripts/) to use src.db
- src/database.py deleted; src/db/__init__.py re-exports all 18 symbols
- pytest 40/40 passed (21.31s); migration simulation verified on a DB
  pre-seeded with the orphan tables.

PDCA database-cleanup: Match Rate 99%, archived.
```

## 10. 참조

- Plan: `docs/01-plan/features/database-cleanup.plan.md`
- Design: `docs/02-design/features/database-cleanup.design.md`
- 메모리: `project_stabilization.md`, `project_split_refactor_pattern.md`
- 직전 사이클: `docs/archive/2026-05/split-work-js/`
- 트리거 커밋(고아 테이블 원인): `28aa888` (2026-05-19)
