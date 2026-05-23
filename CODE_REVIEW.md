# IRMS 프로젝트 종합 코드 리뷰 및 개선 계획

작성일: 2026-05-23  
대상: FastAPI + Jinja2 + Vanilla JS + SQLite 기반 IRMS

## 요약

- 검토 범위: `src/`, `static/js/`, `templates/`, `tray_client/`, `tests/`, `.github/workflows/test.yml`, 실행 배치 파일
- 라우트 수: 백엔드 API 약 83개
- 테스트 파일: Python 8개, JS 4개
- 현재 리스크 수준: **중상**
- 우선 개선 축: **서비스 계층 분리, 인증/세션 정책 강화, 반복 쿼리 제거, 테스트/CI 재정비, 프론트 렌더링 표준화**

참고: CodeGraph MCP는 호출 시 `user cancelled MCP tool call`로 반환되어 사용할 수 없었다. 따라서 파일 구조, 핵심 진입점, 라우터/서비스/프론트엔드 파일을 직접 확인해 리뷰했다.

## 주요 발견사항

### Critical

1. **근태 계정 초기 비밀번호와 최소 길이 정책이 약함**
   - 위치: `src/attendance_auth.py:30`, `src/attendance_auth.py:31`, `src/attendance_auth.py:128`, `src/attendance_auth.py:205`
   - 현재 근태 계정은 첫 로그인 시 사번을 초기 비밀번호로 사용하고, 최소 길이가 4자다. 내부망 시스템이라도 근태 데이터는 개인정보성이 강하므로 계정 추측과 약한 비밀번호 리스크가 크다.
   - 권장 조치:
     - 첫 로그인용 임시 비밀번호를 사번과 분리하고 관리자 발급/1회성 토큰으로 변경한다.
     - 최소 길이를 8자 이상으로 올리고 사번, 사용자명, 반복 숫자, 연속 숫자 금지 검사를 추가한다.
     - 실패 이력과 잠금 이력을 감사 로그에 남긴다.

2. **공용 근태 알림 API가 인증 없이 내부망 IP만 신뢰함**
   - 위치: `src/routers/public_attendance_alert_routes.py:1`, `src/main.py:53`, `src/middleware/internal_only.py:31`
   - `/api/public/attendance-alerts/*`는 로그인 없이 월간/당일 이상 근태 항목을 반환한다. IP가 사설망이면 접근이 허용되며, 프록시나 VPN, 같은 LAN 내 비인가 단말에 대한 권한 경계가 약하다.
   - 권장 조치:
     - 트레이 클라이언트용 읽기 전용 API 키 또는 서명 토큰을 추가한다.
     - 응답에서 필요한 최소 필드만 반환하고 개인정보 필드는 축소한다.
     - 프록시 도입 시 `X-Forwarded-For` 신뢰 설정을 명시적으로 구성한다.

3. **스프레드시트 저장이 전체 삭제 후 재삽입 방식이라 데이터 손실/경합 리스크가 큼**
   - 위치: `src/routers/spreadsheet_routes.py:332`, `src/routers/spreadsheet_routes.py:335`, `src/routers/spreadsheet_routes.py:344`
   - `save_sheet`는 기존 행을 모두 삭제한 뒤 요청 본문 전체를 다시 삽입한다. 두 사용자가 동시에 편집하면 마지막 저장이 이전 작업을 통째로 덮어쓸 수 있고, `col_idx_str` 변환 실패도 500으로 이어질 수 있다.
   - 권장 조치:
     - `updated_at` 또는 `version` 기반 낙관적 잠금을 추가한다.
     - 변경 셀 단위 upsert API를 제공한다.
     - `cells` 키를 `conint/ge/le` 모델 또는 명시 검증으로 제한하고, 잘못된 키는 400으로 응답한다.

## Major

1. **라우터가 SQL, 트랜잭션, 도메인 규칙, 응답 조립을 동시에 담당**
   - 위치: `src/routers/recipe_operator_routes.py:337`, `src/routers/recipe_manager_routes.py:155`, `src/routers/weighing_routes.py:93`, `src/routers/spreadsheet_routes.py:171`
   - 일부 서비스(`stock_service`, `recipe_helpers`, `attendance_excel`)는 있지만, 대부분의 업무 규칙이 라우터 내부에 남아 있다.
   - 영향: 테스트 단위가 커지고, 권한/검증/트랜잭션 규칙이 중복되며, 기능 변경 시 회귀 범위가 넓어진다.
   - 권장 조치:
     - `RecipeService`, `WeighingService`, `SpreadsheetService`, `AttendanceService`로 쓰기 로직을 이동한다.
     - 라우터는 인증, 요청 모델, HTTP 오류 매핑, 서비스 호출만 담당하게 한다.

2. **담당자 진행 현황 엔드포인트에 N+1 쿼리 패턴이 있음**
   - 위치: `src/routers/recipe_manager_routes.py:155`, `src/routers/recipe_manager_routes.py:177`, `src/routers/recipe_manager_routes.py:184`, `src/routers/recipe_manager_routes.py:212`, `src/routers/recipe_manager_routes.py:259`
   - 작업자별 루프 안에서 레시피 목록, 총 단계 수, 카테고리 요약, 현재 레시피, 작업 제품을 반복 조회한다.
   - 권장 조치:
     - 하루 측정 데이터를 CTE로 한 번 구성하고 `GROUP BY measured_by`, `GROUP BY measured_by, category` 형태로 집계한다.
     - `recipe_items(measured_at, measured_by)` 또는 현재 쿼리에 맞는 복합 인덱스를 추가 검토한다.

3. **스프레드시트 목록/로드에 반복 쿼리와 불필요한 `SELECT *`가 많음**
   - 위치: `src/routers/spreadsheet_routes.py:98`, `src/routers/spreadsheet_routes.py:104`, `src/routers/spreadsheet_routes.py:171`, `src/routers/spreadsheet_routes.py:174`
   - 제품 목록에서 제품마다 컬럼 수와 행 수를 별도 조회하고, 시트 로드도 행마다 셀을 조회한다.
   - 권장 조치:
     - 제품 목록은 `LEFT JOIN` + `COUNT(DISTINCT ...)`로 한 번에 조회한다.
     - 시트 로드는 `ss_rows`와 `ss_cells`를 조인해서 한 번에 가져온 뒤 메모리에서 그룹핑한다.
     - 필요한 컬럼만 명시해 스키마 변경 영향을 줄인다.

4. **레시피 목록 API에 페이지네이션이 없음**
   - 위치: `src/routers/recipe_operator_routes.py:336`, `src/routers/recipe_operator_routes.py:337`, `src/routers/recipe_operator_routes.py:374`
   - `/api/recipes`는 조건에 맞는 레시피 전체와 모든 아이템을 반환한다. 데이터가 누적되면 응답 크기와 렌더링 비용이 빠르게 증가한다.
   - 권장 조치:
     - `limit`, `offset` 또는 커서 기반 페이지네이션을 추가한다.
     - 목록 응답과 상세 응답을 분리해 목록에서는 아이템 전체를 제외하거나 요약만 반환한다.

5. **계량 완료 처리의 상태 전이와 재고 차감이 큰 트랜잭션에 섞여 있음**
   - 위치: `src/routers/weighing_routes.py:93`, `src/routers/weighing_routes.py:146`, `src/routers/weighing_routes.py:156`, `src/routers/weighing_routes.py:168`, `src/routers/weighing_routes.py:184`
   - 계량 상태 업데이트, 레시피 상태 변경, 재고 차감, 잔여 수 계산, 감사 로그가 한 함수에 집중되어 있다.
   - 권장 조치:
     - `complete_step(recipe_id, recipe_item_id, actor)` 같은 서비스 함수로 이동하고 상태 전이 테스트를 집중 작성한다.
     - 동시 클릭 방지를 위해 `UPDATE ... WHERE measured_at IS NULL` 패턴은 유지하되, 재고 차감 결과와 감사 로그를 동일 서비스에서 일관되게 처리한다.

6. **프론트엔드 렌더링이 `innerHTML` 중심으로 흩어져 있음**
   - 위치: `static/js/work.js:124`, `static/js/status.js:335`, `static/js/attendance.js:293`, `static/js/chat.js:122`, `static/js/stock.js:57`
   - 다수 파일에서 HTML 문자열을 조립한다. 대부분 `IRMS.escapeHtml`을 사용하지만, 파일별 자체 `escapeHtml`과 직접 문자열 조립이 섞여 있어 누락 시 XSS가 발생하기 쉽다.
   - 권장 조치:
     - 공통 `renderTableRows`, `el(tag, attrs, children)` 유틸 또는 작은 템플릿 헬퍼를 도입한다.
     - 사용자/DB 기반 값은 반드시 단일 escape 함수만 통과하도록 ESLint 규칙 또는 코드 리뷰 체크리스트를 둔다.

7. **운영 실행 스크립트와 배포 설정이 수동 배치 파일 중심**
   - 위치: `run_irms.bat`, `run_irms_intranet.bat`, `update_and_run.bat`, `.github/workflows/test.yml:31`
   - 서버 시작/업데이트/백업은 배치 파일에 의존하고, 서비스 관리자, 헬스체크, 롤백 절차가 명시되어 있지 않다.
   - 권장 조치:
     - Windows 서비스 등록 또는 NSSM/Task Scheduler 기반 운영 절차를 문서화한다.
     - 배포 전 smoke check와 DB 백업 검증을 CI 또는 릴리스 스크립트에 포함한다.

## Minor

1. **인코딩 깨짐으로 주석/라벨의 유지보수성이 낮음**
   - 위치: `src/database.py`, `src/auth.py`, `src/routers/spreadsheet_routes.py`, `static/js/common/format.js`, `README.md`
   - 여러 한글 문자열이 깨져 보인다. 실제 파일 인코딩 또는 콘솔 코드페이지 문제일 수 있으나, 소스 리뷰와 운영 디버깅에 방해가 된다.
   - 권장 조치: 저장소 전체를 UTF-8로 정규화하고 `.editorconfig`에 `charset = utf-8`을 추가한다.

2. **동적 SQL은 대부분 파라미터를 쓰지만 패턴이 일관되지 않음**
   - 위치: `src/services/recipe_helpers.py:49`, `src/routers/recipe_operator_routes.py:239`, `src/routers/recipe_stats_routes.py:50`
   - 현재 확인한 범위에서는 사용자 입력을 직접 문자열에 삽입하는 명백한 SQL Injection은 보이지 않는다. 다만 `IN (...)` 플레이스홀더 생성과 `where_sql` 조립이 여러 파일에 흩어져 있다.
   - 권장 조치: `build_where`, `build_in_clause` 같은 DB 헬퍼를 표준화하고 allowlist가 필요한 필터는 모델에서 제한한다.

3. **CSRF 쿠키 발급 코드가 중복됨**
   - 위치: `src/routers/auth_routes.py:26`, `src/routers/attendance_routes.py:87`
   - 동일한 CSRF 쿠키 발급 로직이 로그인 라우터와 근태 라우터에 중복되어 있다.
   - 권장 조치: `src/security.py` 또는 `src/csrf.py`로 추출해 cookie 옵션을 단일 관리한다.

4. **헬스체크가 중복 정의됨**
   - 위치: `src/main.py:65`, `src/routers/api.py:27`
   - `/health`와 `/api/health`가 각각 존재한다. 의도된 호환성일 수 있으나 모니터링 기준이 분산된다.
   - 권장 조치: 운영 헬스체크는 하나를 표준으로 문서화하고, DB 접근 여부를 포함한 readiness도 분리 검토한다.

## 영역별 상세 리뷰

### 1. 코드 품질

- 큰 파일이 남아 있다: `src/services/attendance_excel.py` 약 27KB, `static/js/work.js` 약 26KB, `static/js/spreadsheet_editor.js` 약 23KB, `src/routers/spreadsheet_routes.py` 약 19KB.
- `src/routers/spreadsheet_routes.py`는 요청 모델, 기본 컬럼 템플릿, DB 접근, 수식 평가 연동, CRUD를 모두 포함한다.
- `src/routers/weighing_routes.py`는 상태 전이와 재고 차감 규칙이 핵심 업무 로직인데 라우터 함수 안에 직접 구현되어 있다.
- 권장 순서:
  1. 계량/재고/레시피 상태 전이를 서비스로 이동
  2. 스프레드시트 로드/저장 서비스를 분리
  3. 프론트엔드 대형 파일을 화면 상태, API, 렌더링, 이벤트로 분리

### 2. 아키텍처

- 긍정적 요소:
  - FastAPI 라우터가 도메인별로 나뉘어 있다.
  - `require_access_level` 의존성으로 접근 수준을 일관되게 걸고 있다.
  - `stock_service`, `attendance_excel`, `recipe_helpers` 같은 일부 서비스 추출이 시작되어 있다.
- 개선 필요:
  - 라우터가 데이터 접근 계층을 직접 호출하는 패턴이 광범위하다.
  - DB 커넥션 생성이 요청마다 직접 일어나고, 트랜잭션 경계가 함수마다 암묵적이다.
  - 별도 마이그레이션 도구 없이 `init_db()`와 `ensure_column()`이 스키마 변경을 모두 처리한다.
- 권장 구조:
  - `src/repositories/`: SQL 전담
  - `src/services/`: 업무 규칙/트랜잭션 전담
  - `src/routers/`: HTTP 모델/권한/응답 매핑
  - `src/schemas/`: Pydantic 요청/응답 모델

### 3. 보안

- 긍정적 요소:
  - 비밀번호는 PBKDF2-SHA256 200,000회로 해시된다(`src/security.py:7`).
  - 세션 쿠키는 운영 모드에서 `https_only=True`, `same_site=strict`로 설정된다(`src/main.py:28`).
  - CSRF 미들웨어가 있고 unsafe 요청은 프론트 공통 요청 유틸에서 `x-csrftoken`을 붙인다(`static/js/common/core.js:65`).
  - 로그인에는 rate limit가 있다(`src/routers/auth_routes.py:92`, `src/routers/auth_routes.py:98`, `src/routers/auth_routes.py:104`).
- 개선 필요:
  - 근태 로그인에는 별도 rate limit 데코레이터가 없고 자체 lockout만 있다.
  - `/api/attendance/logout`은 CSRF exempt다(`src/main.py:49`). idempotent라 영향은 작지만 정책적으로 예외 목록은 최소화해야 한다.
  - 관리자 비밀번호 재설정은 강도 검사와 만료/강제 변경 정책을 강화해야 한다(`src/routers/admin_routes.py:206`).
  - 내부망 전용 공개 API는 토큰 기반 접근 통제와 응답 최소화가 필요하다.

### 4. 성능

- N+1 후보:
  - `recipe_manager_routes.operator_progress`: 작업자별 여러 쿼리 반복
  - `spreadsheet_routes.list_products`: 제품별 컬럼/행 수 반복 조회
  - `spreadsheet_routes._load_sheet_data`: 행별 셀 조회
- 인덱스:
  - 기본적인 레시피/아이템/채팅/감사 로그 인덱스는 존재한다(`src/database.py:319` 이후).
  - 대시보드와 진행 현황은 `measured_at` 중심 범위 검색이 많으므로 `recipe_items(measured_at, recipe_id)`와 `recipe_items(measured_at, material_id)`를 실제 쿼리 플랜으로 검증해야 한다.
- 파일 I/O:
  - 근태 Excel은 요청마다 `openpyxl.load_workbook(..., read_only=True, data_only=True)`로 읽는다(`src/services/attendance_excel.py:506`). 파일이 작다는 주석은 있지만 월/연간 요약 요청이 늘면 캐시가 필요하다.
- 권장 조치:
  - `EXPLAIN QUERY PLAN` 기반으로 대시보드/진행현황 상위 10개 쿼리를 측정한다.
  - 근태 Excel은 파일 mtime 기반 TTL 캐시를 적용한다.
  - 목록 API에 페이지네이션과 필드 축소를 추가한다.

### 5. 테스트

- 현재 테스트 구성:
  - Python 테스트 8개
  - JS 테스트 4개
  - CI는 `.github/workflows/test.yml:31`에서 `pytest -q`만 실행
- 직접 실행 결과:
  - `pytest -q`: `ModuleNotFoundError: No module named 'src'`로 수집 실패
  - `PYTHONPATH=.; pytest -q`: `openpyxl`, `pystray` 미설치로 수집 실패
- 테스트 공백:
  - 계량 동시 완료/취소/되돌리기
  - 재고 음수/복구/중복 차감 방지
  - 관리자 권한 경계
  - CSRF 실패/성공 플로우
  - 스프레드시트 동시 저장과 잘못된 셀 키
  - `/api/recipes` 대량 데이터 성능
  - JS 테스트 CI 실행
- 권장 조치:
  - `requirements-dev.txt` 또는 `pyproject.toml`로 테스트 의존성을 고정한다.
  - CI에 `PYTHONPATH=.` 또는 editable install을 명시한다.
  - 트레이 클라이언트 의존성은 mock/import guard를 두거나 별도 extras로 분리한다.
  - JS 테스트 실행 명령과 Node 버전을 CI에 추가한다.

### 6. 프론트엔드

- 긍정적 요소:
  - 공통 네임스페이스 `IRMS`와 `IRMS._core.request`가 있다.
  - CSRF, 에러 텍스트 변환, 로그인 리다이렉트가 공통화되어 있다.
  - 많은 렌더링 지점에서 `IRMS.escapeHtml`을 사용한다.
- 개선 필요:
  - 대형 화면 스크립트가 상태, API, 렌더링, 이벤트를 한 파일에 포함한다.
  - `innerHTML` 문자열 조립이 넓게 퍼져 있어 escape 누락을 정적으로 잡기 어렵다.
  - 일부 파일은 자체 `escapeHtml`을 정의하고 일부는 공통 함수를 사용한다.
  - polling interval 해제/중복 시작 규칙이 파일마다 다르다.
- 권장 조치:
  - 공통 API 클라이언트를 모든 파일에 적용하고 직접 `fetch`를 줄인다.
  - 렌더링 헬퍼와 escape 정책을 단일화한다.
  - JS 테스트를 CI에 포함하고 `innerHTML` 사용 지점의 escape 테스트를 추가한다.

### 7. DevOps/운영

- 현재 상태:
  - CI는 Python 3.11에서 pytest만 실행한다.
  - Dockerfile, compose, 서비스 유닛, 배포 매니페스트는 없다.
  - Windows 배치 파일로 실행/업데이트/백업을 수행한다.
- 리스크:
  - 운영 서버에서 `.env` 누락 시 development 기본값으로 실행될 수 있다(`update_and_run.bat` 경고 후 계속 진행).
  - 의존성 버전이 하한만 지정되어 재현성이 낮다(`requirements.txt`).
  - DB 마이그레이션과 앱 시작이 결합되어 있어 시작 중 스키마 변경 실패가 운영 장애로 이어질 수 있다.
- 권장 조치:
  - `IRMS_ENV=production`에서 `IRMS_SESSION_SECRET` 없으면 실패하는 현재 정책은 유지하되, 운영 배치에서는 `.env` 누락 시 중단한다.
  - `pip-tools` 또는 잠금 파일로 운영 의존성을 고정한다.
  - 백업 복구 리허설 절차와 smoke check를 배포 스크립트에 필수 단계로 둔다.

## 우선순위 개선 계획

### P0: 즉시 처리

1. 근태 인증 강화
   - `src/attendance_auth.py`
   - 초기 비밀번호를 사번에서 분리, 최소 길이 8자 이상, 약한 패턴 금지, 실패 감사 로그 추가.

2. 테스트 실행성 복구
   - `requirements-dev.txt` 또는 `pyproject.toml` 추가
   - CI와 로컬에서 동일하게 `pytest`가 수집되도록 `PYTHONPATH`/패키지 설치 방식 정리.

3. 스프레드시트 저장 보호
   - `src/routers/spreadsheet_routes.py`
   - 잘못된 셀 키 400 처리, row/column 제한, 낙관적 잠금 추가.

4. 공개 근태 알림 API 보호
   - `src/routers/public_attendance_alert_routes.py`, `src/middleware/internal_only.py`
   - 트레이 클라이언트 토큰 또는 서명 헤더 추가.

### P1: 2-4주

1. `WeighingService`와 `StockService` 경계 정리
   - `src/routers/weighing_routes.py`, `src/services/stock_service.py`
   - 계량 완료/취소/초기화/레시피 완료 상태 전이를 서비스 테스트로 고정.

2. N+1 쿼리 제거
   - `src/routers/recipe_manager_routes.py`, `src/routers/spreadsheet_routes.py`
   - 진행 현황과 스프레드시트 목록/로드 쿼리를 집계 쿼리로 재작성.

3. 레시피 목록 페이지네이션
   - `src/routers/recipe_operator_routes.py`
   - 목록/상세 응답 분리, `limit`/`offset` 추가.

4. 프론트 렌더링 표준화
   - `static/js/common/`, `static/js/work.js`, `static/js/status.js`, `static/js/attendance.js`
   - 공통 escape/render 헬퍼와 JS 테스트 추가.

### P2: 1-3개월

1. DB 마이그레이션 체계 도입
   - `src/database.py`
   - `init_db()`에서 스키마 생성/마이그레이션/시드 데이터를 분리.

2. 배포 체계 정리
   - `.github/workflows/test.yml`, `run_irms_intranet.bat`, `update_and_run.bat`
   - 운영용 smoke, 백업 검증, 서비스 재시작, 롤백 절차 표준화.

3. 관측성 추가
   - 요청 ID, 구조화 로그, 느린 쿼리 로그, 주요 업무 이벤트 메트릭 추가.

4. 의존성/정적 분석
   - Python lint/type check, JS lint, 보안 스캔, 의존성 잠금 도입.

## 권장 작업 순서

1. 테스트 실행성부터 복구한다. 현재 테스트가 수집 단계에서 실패하면 이후 리팩터링의 안전망이 없다.
2. 근태 인증과 공개 API 보호를 먼저 강화한다. 개인정보와 내부망 신뢰 경계 문제라 우선순위가 높다.
3. 계량/재고/스프레드시트 저장 로직에 서비스 계층과 테스트를 추가한다.
4. N+1 쿼리와 대량 목록 응답을 개선해 운영 데이터 증가에 대비한다.
5. 프론트 렌더링 패턴과 CI를 정리해 화면 회귀를 줄인다.

## 검증 기록

- `pytest -q`: 실패. `src` 모듈을 찾지 못해 수집 단계에서 중단.
- `PYTHONPATH=.; pytest -q`: 실패. 현재 실행 환경에 `openpyxl`, `pystray`가 없어 수집 단계에서 중단.
- CodeGraph MCP: 실패. 호출 결과가 `user cancelled MCP tool call`로 반환됨.
