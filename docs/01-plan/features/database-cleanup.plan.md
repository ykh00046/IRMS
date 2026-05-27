# database-cleanup — Plan

| 항목 | 값 |
|---|---|
| Feature | `database-cleanup` |
| Phase | Plan |
| Level | Dynamic |
| 작성일 | 2026-05-27 |
| 작성자 | Claude (Opus 4.7) |
| 선행 사이클 | split-work-js (Phase 4, archived 99%, 2026-05-27) |

## 1. 배경

`src/database.py`는 단일 파일 **719 LOC**로, IRMS Python 소스에서 두 번째로 큰 모듈이다 (1위는 `attendance_excel.py` 791). 13개 라우터/서비스 + 1개 스크립트 + 1개 테스트 = **15개 호출 사이트**에서 8개 심볼을 import한다.

또한 `28aa888` (2026-05-19) 잉크/사출 OCR 기능 삭제 후 **3개 고아 테이블**이 운영 DB(`data/irms.db`, gitignored)에 잔존할 수 있다:

- `production_plans`
- `plan_schedules`
- `plan_chemical_requests`

dev DB는 이미 깨끗하다(검증 완료, 2026-05-27 16:xx). 운영 DB(현장 PC `192.168.11.147`)는 미확인 — idempotent DROP 마이그레이션이 안전하다.

## 2. 목표 (Goals)

| ID | 목표 | 측정 |
|---|---|---|
| G1 | `src/database.py` 719 LOC → 책임별 7개 모듈 (각 < 250 LOC) | `wc -l src/db/*.py` |
| G2 | 고아 테이블 3개를 idempotent 마이그레이션으로 DROP | `schema_migrations`에 `drop_orphan_plan_tables` 기록 |
| G3 | 외부 동작 0줄 변경 (pytest 40/40 통과) | `pytest -q` |
| G4 | 분리 후 모든 import 사이트 갱신 완료 (shim 없음) | `grep -r "from .database\|from src.database"` = 0건 |

## 3. 비목표 (Non-goals)

- DB 엔진 교체 (SQLite → Postgres) — 별건 Phase로 분리
- 시드 데이터 변경 (사용자/방/원재료/레시피) — 분리만, 내용 동일
- 스키마 컬럼/인덱스 추가/삭제 (고아 테이블 제외)
- ORM 도입 (SQLAlchemy 등)
- Audit log UI/조회 화면 (별건 기능 추가)

## 4. 범위

### 변경 파일
- 생성: `src/db/__init__.py`, `src/db/connection.py`, `src/db/time_utils.py`, `src/db/migrations.py`, `src/db/schema.py`, `src/db/seeds.py`, `src/db/queries.py`, `src/db/audit.py`
- 삭제: `src/database.py`
- 갱신: 13개 라우터/서비스 + `scripts/import_excel_recipes.py` + `tests/test_notice_chat_routes.py` = 15개 파일

### 영향 없는 영역
- 템플릿 (`templates/`)
- 정적 자원 (`static/`)
- 운영 스크립트 (`update_and_run.bat`)
- 환경 변수 (`src/config.py`)

## 5. 요구사항

### 기능 요구사항 (FR)

| ID | 요구사항 |
|---|---|
| FR-01 | 분리 전후 외부에서 import 가능한 심볼 집합이 동일하다 |
| FR-02 | `init_db()` 호출 시 `apply_schema_migrations()`가 마지막에 실행되고, 고아 테이블 DROP은 그 안에서 수행된다 |
| FR-03 | `drop_orphan_plan_tables` 마이그레이션은 `schema_migrations`에 1회만 기록되며 재실행해도 멱등이다 |
| FR-04 | `DROP TABLE IF EXISTS`를 사용하여 dev DB(테이블 없음)와 prod DB(테이블 있음) 모두에서 정상 동작한다 |
| FR-05 | `from src.db import X` 또는 `from src.db.<module> import X` 양쪽 모두 작동한다 |

### 비기능 요구사항 (NFR)

| ID | 요구사항 |
|---|---|
| NFR-01 | 각 신규 모듈은 250 LOC 이하 |
| NFR-02 | 순환 import 없음 |
| NFR-03 | 신규 외부 의존성 0개 |
| NFR-04 | 테스트 실행 시간 회귀 없음 (기존 대비 ±10% 이내) |

## 6. 위험/완화

| 위험 | 완화책 |
|---|---|
| 누락된 import 사이트 → 런타임 `ImportError` | grep으로 `database` 전부 검색 + pytest로 import smoke 검증 |
| 운영 DB의 고아 테이블에 외래키가 다른 테이블을 가리킬 경우 DROP 실패 | 고아 테이블은 자체 PK/FK만 가짐(production_plans → 자기 자신 참조 없음, plan_schedules.plan_id → production_plans). DROP 순서: `plan_schedules` → `plan_chemical_requests` → `production_plans` |
| 분리 후 사이클 의존성 (예: schema가 seeds를, seeds가 schema를) | 각 모듈은 단방향: connection → time_utils → migrations → schema → seeds → queries → audit |
| Backwards-compat shim 없음 → 외부 코드(현장 운영 환경)가 깨질 가능성 | IRMS는 단일 PC에서 운영, 외부 소비자 없음. 모든 호출 사이트가 이 repo 내부 |

## 7. 검증 기준 (Acceptance)

| 항목 | 기준 |
|---|---|
| 빌드 | `python -c "from src.main import app"` 무오류 |
| 테스트 | pytest 전체 통과 (현재 40 케이스) |
| LOC | `src/db/*.py` 각 ≤ 250 LOC |
| 마이그레이션 | dev DB에서 init_db 2회 호출 시 `drop_orphan_plan_tables` 기록 1건만 존재 |
| Gap Rate | gap-detector ≥ 90% |
| Import | `grep -r "from \\.\\.database\\|from \\.database\\|from src.database" src/ tests/ scripts/` = 0건 |

## 8. 일정 (예상)

| Phase | 예상 시간 |
|---|---|
| Plan | 10분 (완료) |
| Design | 15분 |
| Do (분리+갱신) | 40분 |
| QA (pytest + 시뮬) | 15분 |
| Iterate | 10분 |
| Report + Memory | 10분 |
| **총** | **~100분** |

## 9. 참조

- 직전 사이클: `docs/archive/2026-05/split-work-js/`
- 분리 공통 패턴: 메모리 `project_split_refactor_pattern.md` (Phase 1 — Python 라우터 + services 추출 패턴)
- 잉크/사출 삭제 커밋: `28aa888` (2026-05-19)
- 원본: `src/database.py` (719 LOC, HEAD `e1aa1c3`)
