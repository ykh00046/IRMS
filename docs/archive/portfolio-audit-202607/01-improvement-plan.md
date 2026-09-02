# IRMS(BRM) 개선 플랜

작성일: 2026-07-12
대상: `C:\X\IRMS` — 배합·레시피 관리 시스템 (화면 브랜드 BRM, 내부 식별자 IRMS 유지)

---

## 1. 프로젝트 개요

### 목적
수기/엑셀로 관리하던 배합 실적(DHR)·레시피·점도 데이터를 디지털 전환한 사내 제조현장 시스템.
현장 PC(Host) 1대에서 FastAPI 서버를 돌리고, 폐쇄망 브라우저로 최대 8명이 접속하는 PoC → 실운영 전환 단계다.

주요 기능 축 (실제 라우터 기준, `src/routers/`):
- **배합 실적**: 레시피 절대중량(g) → 비율 환산 → 배치 총량별 이론 계량량 산출, 실측 입력, product_lot 자동 채번, DHR Excel/PDF 출력 (`blend_routes.py`, `services/blend_service.py`, `dhr_excel.py`, `dhr_pdf.py`)
- **레시피 관리**: 엑셀 붙여넣기 Smart Import(검증·미리보기), 개정(revision) 체인, 기준 자재(anchor) 계량 (`recipe_{manager,operator,import}_routes.py`, `import_parser.py`, `material_resolver.py`)
- **점도 분석**: 반제품 점도 등록·추세·이상 분석, 배합 기록 연계 (`viscosity_routes.py`, `viscosity_service.py`)
- **근태**: 사번+비밀번호 자체 세션, 엑셀 파싱 기반 이상 근태 요약 (`attendance_routes.py`, `attendance_auth.py`, `services/attendance_excel/`)
- **주변 시스템**: A&D/Mettler/CAS 저울 로컬 HTTP 에이전트(`scale_agent/agent.py`, 127.0.0.1:8787), Windows 트레이 알림 앱(`tray_client/`), Cloudflare Tunnel 외부 접근(`cloudflared/`), 상위 재고 대시보드용 공개 API(`public_material_usage_routes.py`)

### 기술 스택
- **Backend**: Python 3.11+, FastAPI, Jinja2, SQLite(WAL), starlette-csrf, slowapi, openpyxl, pymupdf
- **Frontend**: Vanilla JS(`window.IRMS` 네임스페이스) + 페이지별 CSS, 빌드 도구 없음(`?v=` 캐시버스팅)
- **운영**: `serve.py` 단일 창 러너(주기적 git pull 자동 업데이트 + SQLite 온라인 백업 + 포트 정리), Windows 배치 파일, GitHub Actions CI(pytest + `node --test`)

### 규모 (실측)
- Python 약 15,400줄(src/serve.py/scale_agent/tray_client/tools/scripts), 프론트엔드 약 13,400줄(JS/CSS/HTML)
- API 라우트 약 100개 (GET 69 / POST 20 / PUT 4 / DELETE 4 / PATCH 3)
- 테스트: pytest 313개 수집 확인(`python -m pytest tests --collect-only`), JS 테스트 4파일, CI에서 둘 다 실행
- 가장 큰 파일: `scale_agent/agent.py` 927줄, `src/services/blend_service.py` 878줄, `static/css/common.css` 1,420줄, `static/js/blend.js` 802줄

---

## 2. 현재 상태 진단

### 강점

1. **계층 분리가 실제로 이루어져 있다.** `routers/`(HTTP) → `services/`(업무 규칙) → `db/`(connection/schema/migrations/queries/audit) 구조가 문서(CLAUDE.md)와 일치한다. 2026-05의 `split-large-files`, `split-common-js` 등 분할 리팩터링 이력이 `docs/archive/2026-05/`에 남아 있고 결과가 코드에 반영돼 있다.
2. **내부망 PoC치고 보안 기본기가 탄탄하다.** `src/main.py`의 미들웨어 스택: SessionMiddleware(운영 시 `https_only`+`strict` SameSite) → CSRFMiddleware(예외 경로 최소화, sendBeacon 예외에 주석으로 근거 명시) → `InternalNetworkOnlyMiddleware`(사설 IP 검사 + `hmac.compare_digest` 토큰) → SecurityHeadersMiddleware. `src/config.py`는 운영 환경에서 `IRMS_SESSION_SECRET`/`IRMS_TRAY_API_TOKEN` 미설정 시 부팅 실패, `src/db/schema.py:123`은 운영에서 데모 시드 활성화 시 `RuntimeError`를 던진다. 세션 토큰 회전(단일 세션, `auth.py login_user`)도 구현돼 있다.
3. **마이그레이션 규율이 있다.** `src/db/migrations.py`의 `schema_migrations` 테이블 + `has_migration`/`record_migration` + `ensure_column`(테이블 allowlist `_ALLOWED_TABLES`, 식별자 정규식 검증). `docs/migrations.md`에 "배포된 마이그레이션은 수정 금지, 끝에 추가" 규칙이 문서화돼 있다.
4. **테스트·CI가 살아 있다.** 2026-05 CODE_REVIEW.md 시점에는 pytest 수집조차 실패했으나, 현재는 313개 테스트가 수집되고 `.github/workflows/test.yml`이 pytest와 JS 테스트를 모두 실행한다. `tools/smoke_irms.py`(py_compile + create_app + /health)와 `tools/bootstrap_irms.py`도 갖춰져 있다.
5. **운영 자동화가 현장 사정에 맞게 설계됐다.** `serve.py`: origin/main 감시 → DB 온라인 백업 → git pull → pip install(lock 우선) → 재시작, 실패 시 기존 서버 무중단 유지. 일 1회 백업 + 보존 30일/최소 5개 + `IRMS_BACKUP_MIRROR` 2차 사본. `requirements-lock.txt`로 운영 의존성 고정.
6. **과거 리뷰 지적사항 상당수가 이미 해소됐다.** CODE_REVIEW.md(2026-05-23)의 P0 중 테스트 실행성 복구, 트레이 API 토큰(`IRMS_TRAY_API_TOKEN`), 의존성 잠금이 반영됐고, 레시피 목록에도 `limit` 파라미터가 존재한다(`recipe_operator_routes.py:50,112,410`).

### 약점·기술 부채

1. **[위생] 저장소 루트가 런타임/테스트 산출물로 뒤덮여 있다.**
   실측: 루트에 `tmp_*` 디렉터리 **43개**, 스크린샷 PNG 30여 개(`irms-management-*.png`, `tmp_ui_runtime_*.png` 등), `tmpivkqp8nt/`, `.venv-wsl-backup-20260527-210916/`(**119MB**), 한글 엑셀 4개(`레시피.xlsx`, `배합기록.xlsx` 등), `1779428060.png`, `2.jpg`. `.gitignore`에는 잡혀 있어 커밋되진 않지만, 디스크와 탐색 노이즈가 심각하고 `serve.py`의 git 작업 디렉터리(=운영 서버 루트)와 뒤섞여 있다. `data/`에도 `__tmp_status_*`, `audit_ui_test_db` 등 테스트 잔재가 라이브 `irms.db` 옆에 존재한다.
2. **[문서 드리프트] 문서가 코드보다 한 세대 뒤처져 있다.**
   - `docs/migrations.md`가 `src/database.py`를 기준으로 설명하지만 실제 파일은 `src/db/migrations.py`로 이동했다.
   - `CODE_REVIEW.md`는 이미 제거된 모듈(`spreadsheet_routes.py`, `weighing_routes.py`, `stock_service`, `chat.js`)을 지적하고 있어 현행 리스크 목록으로 쓸 수 없다.
   - `docs/IRMS-overview.md`의 ERD(`users.id FK created_by`)와 실제 스키마(`recipes.created_by TEXT`, `src/db/schema.py:49`)가 다르다. README는 "Ink Recipe Management"인데 CLAUDE.md는 '잉크/ink' 단어 금지·브랜드 BRM을 명시한다.
   - CLAUDE.md의 서비스 목록에 있는 `variance` 서비스 파일은 없고 변수명(`blend_routes.py:162`)만 남아 있다.
3. **[스키마 드리프트 방어 코드] 서비스 레이어에 `except sqlite3.OperationalError` 폴백이 산재한다.**
   `blend_service.py:83`(anchor_material_id 컬럼 부재), `:105`(recipe_steps 테이블 부재) 등 "구버전/테스트 DB 대응" try/except가 정상 경로에 섞여 있다. 이는 테스트 픽스처가 전체 마이그레이션을 돌지 않는다는 신호이며, 실제 스키마 문제를 조용히 삼킬 수 있다.
4. **[인증 복잡도] 한 쿠키 세션에 4종 인증이 동거한다.**
   책임자(workers 명단, `mgr_worker_id`) / 레거시 users(`user_id`) / 배합 작업자(blend_session) / 근태(attendance_auth)가 같은 `irms_session` 쿠키를 공유한다. `auth.py:99~102` 주석이 증언하듯 `session.clear()` 한 번으로 현장 작업자 세션이 끊기는 사고가 이미 있었고, `_AUTH_SESSION_KEYS` 수동 관리로 봉합한 상태다. 키 하나만 잘못 건드려도 재발한다.
5. **[대형 파일] 분할 리팩터링 이후에도 한계선을 넘는 파일이 남아 있다.**
   `blend_service.py` 878줄, `viscosity_service.py` 679줄, `blend_routes.py` 649줄, `recipe_operator_routes.py` 567줄, `scale_agent/agent.py` 927줄(단일 파일에 시리얼 프로토콜 3종 + HTTP 서버 + 설정 + 자동탐지), `static/js/blend.js` 802줄, `common.css` 1,420줄.
6. **[품질 도구 부재] lint/type check 설정이 전혀 없다.**
   `pyproject.toml`, ruff/mypy/eslint 설정 파일이 존재하지 않는다(`pytest.ini`만 존재). CI는 테스트만 돌린다. `innerHTML` 조립 중심 프론트엔드에서 escape 누락을 정적으로 잡을 수단이 없다.
7. **[가용성 한계] 단일 Host PC + 콘솔 프로세스 운영.**
   `serve.py`는 우수하지만 Windows 서비스가 아니라 콘솔 창이다(로그아웃/재부팅 시 수동 재기동). 백업은 있으나 복구 리허설·무결성 검증(`PRAGMA integrity_check`)이 자동화돼 있지 않다. DB 요청마다 `sqlite3.connect`를 새로 열고(`db/connection.py:9`) 모든 라우트가 sync def(스레드풀)라 8명 규모에선 문제없으나 상한이 문서화돼 있지 않다.
8. **[감사로그 이원화] 설계 문서(IRMS-overview.md 8장)의 audit 표준과 실제 `audit_logs` 스키마(action/actor_* 중심, `schema.py:78`)가 다르고, 이벤트 커버리지(로그인 실패, 마스터 변경 등)가 코드 기준으로 재정의돼 있지 않다.

---

## 3. 개선 과제 (우선순위)

### P0 — 즉시 (운영 리스크·비가역 손실 방지)

| # | 과제 | 이유 | 기대 효과 |
|---|------|------|-----------|
| P0-1 | **레포 위생 대청소 + 재발 방지 규칙**: 루트 `tmp_*` 43개·PNG·`tmpivkqp8nt`·`.venv-wsl-backup-*`(119MB) 삭제, 스크린샷은 `docs/assets/` 또는 삭제, 루트 엑셀 4개는 `excel/legacy/`로 이동. 테스트/E2E 산출물은 스크래치 디렉터리(예: `.tmp-tests/` 하위)로만 쓰도록 conftest·스크립트 경로 통일 | 운영 서버 루트 = git 작업 디렉터리인데 산출물이 섞여 있어 사고(잘못된 삭제/백업 누락) 확률이 높음 | 디스크 120MB+ 회수, 탐색·백업·배포 신뢰성 확보 |
| P0-2 | **백업 무결성 자동 검증**: `serve.py` 백업 직후 사본에 `PRAGMA integrity_check` + 핵심 테이블 COUNT 실행, 실패 시 로그 경고. 분기 1회 복구 리허설 절차를 `docs/`에 명문화 | 백업은 만들지만 열어본 적 없는 백업은 백업이 아님. DHR은 품질 증빙 데이터라 손실 비가역 | 복구 실패 리스크 제거, 감사 대응력 |
| P0-3 | **문서 현행화**: `docs/migrations.md`의 `src/database.py` → `src/db/migrations.py` 경정, CODE_REVIEW.md 상단에 "2026-05 시점, 상당수 해소됨" 배너 추가 또는 `docs/archive/`로 이동, README 명칭 정리(BRM, 'ink' 제거), CLAUDE.md 서비스 목록 실제 파일과 동기화 | 새 작업자(사람/AI)가 낡은 문서를 근거로 잘못된 수정을 하게 됨 | 온보딩 정확도, AI 협업 품질 |
| P0-4 | **테스트 픽스처를 전체 마이그레이션 경유로 통일**하고 서비스의 `except sqlite3.OperationalError` 폴백(`blend_service.py:83,105` 등) 제거 | 폴백이 실제 스키마 장애를 은폐. "구버전 DB 대응"은 마이그레이션의 일이지 서비스의 일이 아님 | 조용한 데이터 누락 버그 예방, 코드 단순화 |

### P1 — 2~6주 (구조 부채 상환)

| # | 과제 | 이유 | 기대 효과 |
|---|------|------|-----------|
| P1-1 | **세션 네임스페이스 통합 모듈**: `src/sessions.py`(가칭)로 4종 인증의 세션 키를 네임스페이스 상수 + `clear_scope(scope)` API로 일원화. `auth.py`/`blend_session.py`/`attendance_auth.py`가 직접 `request.session` 키를 만지지 않게 함 | `_AUTH_SESSION_KEYS` 수동 목록은 이미 사고 전력이 있는 지점 | 세션 간섭 버그 재발 차단 |
| P1-2 | **ruff + mypy(점진) + CI 게이트**: `pyproject.toml` 도입, ruff(E/F/I/B) 즉시 적용, mypy는 `src/db/`·`src/services/`부터. JS는 `innerHTML` 사용 지점 escape 검사 규칙(간단한 grep 기반 스크립트라도) | 리뷰 부담을 도구로 이전. 현재 정적 분석 0 | 회귀·XSS 누락 조기 검출 |
| P1-3 | **대형 모듈 2차 분할**: `blend_service.py` → 환산(`blend_math`)·조회(`blend_read`)·기록(`blend_write`), `scale_agent/agent.py` → `protocols.py`/`server.py`/`config.py`, `blend.js` → 이미 분리된 `blend_lib.js` 패턴 확장 | 800~900줄 파일은 변경 회귀 반경이 큼. 기존 분할 리팩터링의 미완 구간 | 변경 안전성, 테스트 용이성 |
| P1-4 | **Windows 서비스화(또는 Task Scheduler 자동 시작)**: `serve.py`를 NSSM/작업 스케줄러로 부팅 시 자동 기동, 콘솔 로그는 파일 로테이션으로 | 정전·재부팅 후 수동 기동 의존은 가용성 구멍 | 무인 복구, 운영자 부담 감소 |
| P1-5 | **감사로그 커버리지 정의서**: 실제 `audit_logs` 스키마 기준으로 필수 이벤트 표(로그인 실패/잠금, 마스터 변경, 기록 삭제, 서명)를 문서화하고 누락 이벤트 보강. `record_delete_service.py` 등 기존 구현과 대조 | 설계 문서(8장)와 구현이 달라 증빙 요구 시 공백 확인 불가 | 추적성 100% 입증 가능 |

### P2 — 1~3개월+ (성장 대비)

| # | 과제 | 이유 | 기대 효과 |
|---|------|------|-----------|
| P2-1 | **관측성**: 구조화 로깅(요청 ID, 사용자, 소요시간), 느린 쿼리(>100ms) 로그, `/health`에 DB readiness 분리 | 현장 "느려요" 신고 시 재현 수단이 없음 | 장애 분석 시간 단축 |
| P2-2 | **데이터 증가 대비 쿼리 점검**: `EXPLAIN QUERY PLAN`으로 대시보드·insight 상위 쿼리 검증, `recipe_operator_routes.py:410`의 `limit=1000` 기본값 재검토 | 기록형 테이블(blend_records, viscosity_readings)은 단조 증가 | 1~2년 뒤 성능 절벽 예방 |
| P2-3 | **프론트 렌더링 표준화**: `el()`/`renderRows()` 공통 헬퍼로 `innerHTML` 문자열 조립 축소, 파일별 자체 escape 제거 | CODE_REVIEW 지적 중 미해소 항목 | XSS 표면 축소, 코드 중복 감소 |
| P2-4 | **DB 상한 문서화 + 이행 트리거 정의**: 동시 사용자·DB 크기·쓰기 빈도 임계값을 정하고 초과 시 Postgres 이행 착수 기준 명문화(IRMS-overview 7.5의 현행화) | "언젠가 Postgres"는 계획이 아님 | 이행 시점 판단 객관화 |

---

## 4. 신규 기능 제안 (도메인 기반, 3~5개)

1. **자재 LOT 역추적 조회** — `blend_details.material_lot`(migrations.py:230)이 이미 저장되고 있다. "이 자재 LOT이 들어간 배합/제품 LOT 전부"를 조회하는 화면+API를 추가하면 자재 불량 발생 시 영향 배치를 즉시 특정할 수 있다. 스키마 변경 없이 조회 기능만으로 구현 가능 — 투자 대비 효과가 가장 크다.
2. **점도 SPC 관리도(X̄-R) + 이탈 사전 경고** — `viscosity_service.py`의 추세·이상 분석과 `tray_client/viscosity_alerts.py` 리마인더를 확장해, 제품별 관리 한계선(UCL/LCL) 이탈·연속 상승 패턴을 트레이 알림으로 푸시. 기존 데이터·알림 인프라 재사용.
3. **월간 DHR 일괄 마감 리포트** — `dhr_excel.py`/`dhr_pdf.py`/`signature_processor.py`가 개별 DHR을 이미 생성하므로, 월 단위로 서명 완료/미서명 현황 요약 + 전체 DHR ZIP 일괄 출력을 추가. 월말 품질 증빙 취합 수작업 제거.
4. **레시피 개정 승인 워크플로** — `revision_of` 체인과 `static/js/management/version-compare.js`가 존재한다. 개정 생성 시 '승인 대기' 상태를 두고 책임자 승인 후에만 배합 화면에 노출되도록 하면(현재는 `_resolve_latest_revision`이 즉시 최신판 사용, `blend_service.py:36`) 무단 변경 리스크가 사라진다.
5. **읽기 전용 현황판(공장 대형 모니터용)** — 로그인 없는 내부망 전용(기존 `InternalNetworkOnlyMiddleware` 재사용) 대시보드 뷰: 금일 배합 진행/완료, 점도 등록 현황. 폴링 기반이면 SSE 없이도 충분.

---

## 5. 코드 품질 개선 방향 (레포 위생 포함)

1. **위생 규칙을 도구화**: 산출물 경로를 `conftest.py`·E2E 스크립트에서 스크래치 디렉터리로 강제하고, 루트에 새 `tmp_*`가 생기면 CI에서 실패시키는 검사 스크립트(파일 목록 diff) 추가. `.gitignore`는 이미 충분 — 문제는 로컬 청소 습관.
2. **`pyproject.toml`로 설정 집중**: pytest.ini 흡수, ruff/mypy 설정, 패키지 메타. 도구 설정 파일 난립 방지.
3. **서비스 함수 시그니처 표준화**: `(connection, *args) -> dict|list` 패턴은 이미 잘 지켜지고 있으니(예: `blend_service.get_recipe_for_blend`), 트랜잭션 경계(commit 책임)를 "라우터가 열고 서비스는 커밋하지 않음" 식으로 규칙 문서화.
4. **한글 주석 품질 유지**: 현재 주석은 "왜"를 잘 설명한다(`auth.py:99`, `serve.py:56` 등). 이 수준을 CONVENTION으로 명시해 유지.
5. **프론트 escape 단일화**: `IRMS.escapeHtml` 하나만 사용, 파일 자체 정의 금지 — grep 기반 CI 검사로 강제.

---

## 6. 로드맵

### 단기 (1개월)
- P0-1 레포 대청소 + 산출물 경로 강제 (1주)
- P0-2 백업 무결성 검증 + 복구 리허설 문서 (3일)
- P0-3 문서 현행화 (2일)
- P0-4 테스트 픽스처 마이그레이션 통일 + OperationalError 폴백 제거 (1주)
- P1-2 착수: ruff 도입 + CI 게이트 (2일)

### 중기 (3개월)
- P1-1 세션 네임스페이스 통합 (1주)
- P1-3 대형 모듈 2차 분할 — blend_service, scale_agent 우선 (2~3주)
- P1-4 Windows 서비스화 + 로그 파일 로테이션 (1주)
- P1-5 감사로그 커버리지 정의·보강 (1주)
- 신규 기능 1번(LOT 역추적) 출시 — 조회 전용이라 리스크 낮음 (1~2주)

### 장기 (6개월+)
- P2-1 관측성(구조화 로그, 느린 쿼리) 도입
- P2-2 쿼리 플랜 점검 + 데이터 보존 정책(오래된 기록 아카이브)
- P2-3 프론트 렌더링 표준화
- 신규 기능 2~4번(점도 SPC, 월간 DHR 마감, 개정 승인) 순차 도입
- P2-4 기준 충족 시 Postgres 이행 검토 (충족 전에는 착수하지 않음 — SQLite+WAL은 현 규모에 적정)
