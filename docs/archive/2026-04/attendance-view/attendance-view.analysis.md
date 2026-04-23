# attendance-view Analysis Report

> Design vs Implementation Gap Analysis — retroactive PDCA Check phase

| Item | Value |
|------|-------|
| Project | IRMS (원료 계량 시스템) |
| Feature | attendance-view |
| Design Doc | [../02-design/features/attendance-view.design.md](../../02-design/features/attendance-view.design.md) |
| Report Doc | [../04-report/features/attendance-view.report.md](../../04-report/features/attendance-view.report.md) |
| Analyst | gap-detector (Claude) |
| Date | 2026-04-23 |
| Status | Approved — proceed to report update |

---

## Match Rate: 96%

| Category | Score | Status |
|----------|:-----:|:------:|
| Data Model | 100% | PASS |
| Excel Parser | 100% | PASS |
| Auth Module | 95% | PASS (PBKDF2 문서 수정 필요) |
| API Endpoints | 100% | PASS |
| Admin Bypass | 100% | PASS |
| Pages / Routing | 100% | PASS |
| CSRF Exempt List | 100% | PASS |
| Audit Log | 90% | PASS (reset 로그 라벨 nit) |
| Frontend Assets | 100% | PASS |
| Accepted Variances (not penalized) | — | Documented |

---

## 1. 분석 개요

IRMS `attendance-view` 기능은 `C:\ErpExcel\monthly_attendance_YYYY-MM.xlsx`를 사번 인증 기반으로 조회하는 월별 근태 뷰어이다. 설계 문서는 2026-04에 확정되었고, 구현 완료 후 본 보고서가 소급 작성되었다. 설계 vs 실제 구현을 항목별로 대조한 결과, 설계가 요구한 모든 기능이 구현되어 있고(사번 인증, 5분 유휴 타임아웃, 5회 잠금, 관리자 우회, 감사 로그, 엑셀 파서, 3개 페이지 + 5개 API), 알려진 Accepted Variance 3건(비밀번호 강제 변경 → 경고 배너, UI 확장, bcrypt → PBKDF2)만 존재한다.

중대한 미구현(Gap)은 없음. 나머지는 Low 수준 문서 수정 수준.

---

## 2. 검증 테이블

### 2.1 데이터 모델 (Design §2.1)

| 설계 항목 | 구현 위치 | 상태 |
|-----------|-----------|:----:|
| `attendance_users` 테이블 | `src/database.py:329-337` | Match |
| `emp_id TEXT PRIMARY KEY` | `src/database.py:330` | Match |
| `password_hash TEXT NOT NULL` | `src/database.py:331` | Match |
| `password_reset_required INTEGER DEFAULT 1` | `src/database.py:332` | Match |
| `failed_attempts INTEGER DEFAULT 0` | `src/database.py:333` | Match |
| `locked_until TEXT` | `src/database.py:334` | Match |
| `last_login_at TEXT` | `src/database.py:335` | Match |
| `created_at TEXT NOT NULL` | `src/database.py:336` | Match |
| `idx_attendance_users_locked_until` 인덱스 | `src/database.py:339-340` | Match |
| 이름/부서 DB 미저장 (엑셀에서 매 조회) | `src/services/attendance_excel.py:301-311` | Match |

### 2.2 엑셀 파서 (Design §3)

| 설계 항목 | 구현 위치 | 상태 |
|-----------|-----------|:----:|
| `read_only=True, data_only=True` | `src/services/attendance_excel.py:193` | Match |
| 헤더 2행, 데이터 3행부터 | `src/services/attendance_excel.py:201-202` (`row_idx < 2: continue`) | Match |
| `PermissionError` → 503 `FILE_LOCKED_RETRY` | `attendance_excel.py:194-195` + `attendance_routes.py:64-65` | Match |
| 컬럼 0=date,1=weekday,2=day_type,4=emp_id | `attendance_excel.py:38-41` | Match |
| 컬럼 6=gender,7=factory,8=name | `attendance_excel.py:42-44` | Match |
| 컬럼 10=job_type,11=department,12=shift_group,14=shift_time | `attendance_excel.py:45-48` | Match |
| 컬럼 17/18=check_in/out, 19=next_day | `attendance_excel.py:49-51` | Match |
| 컬럼 21-24 평일 조출/정상/연장/야근 | `attendance_excel.py:52-55` | Match |
| 컬럼 25-28 휴일 조출/정상/연장/야근 | `attendance_excel.py:56-59` | Match |
| 컬럼 29/30/31 지각/조퇴/외출, 33 비고 | `attendance_excel.py:60-63` | Match |
| `month_file_path`, `available_months`, `current_year_month` 헬퍼 | `attendance_excel.py:135/139/150` | Match |
| `available_months()` 내림차순 정렬 | `attendance_excel.py:147` (`sorted(..., reverse=True)`) | Match |
| `AttendanceProfile` 5개 필드 (emp_id, name, department, factory, shift_type) | `attendance_excel.py:104-113` | Partial — 구현이 8개 필드 (shift_group/job_type/gender 추가). Additive. |
| 캐시 없음 (매 요청 read) | `attendance_excel.py:247-316` (항상 `_load_workbook`) | Match |

### 2.3 인증 모듈 (Design §4)

| 설계 항목 | 구현 위치 | 상태 |
|-----------|-----------|:----:|
| 유휴 타임아웃 5분 | `src/attendance_auth.py:27` (`IDLE_TIMEOUT_SECONDS = 5 * 60`) | Match |
| 최대 실패 5회 | `src/attendance_auth.py:28` (`MAX_FAILED_ATTEMPTS = 5`) | Match |
| 5분 잠금 | `src/attendance_auth.py:29` (`LOCKOUT_SECONDS = 5 * 60`) | Match |
| 최소 비밀번호 길이 4 | `src/attendance_auth.py:30` (`MIN_PASSWORD_LENGTH = 4`) | Match |
| 해시는 **bcrypt** (design §4.1) | `src/attendance_auth.py:24` → `security.hash_password` = **PBKDF2-SHA256 200k iter** | **Variance (accepted)** |
| 세션 네임스페이스 `att_user` | `attendance_auth.py:26` | Match |
| 5회 도달 시 `locked_until=now+5min`, `failed_attempts=0` 리셋 | `attendance_auth.py:174-181` | Match |
| 로그인 성공 시 `failed_attempts=0, locked_until=NULL, last_login_at=now()` | `attendance_auth.py:102-110` | Match |
| 관리자 자동 감지 `is_admin_mode` (manager/admin) | `attendance_auth.py:263-269` | Match |
| 첫 로그인 시 엑셀에 존재하지 않으면 404 `EMP_NOT_IN_EXCEL` | `attendance_auth.py:154-158` | Match |
| 첫 로그인 자동 계정 생성 (해시=사번, reset=1) | `attendance_auth.py:159-161` | Match |
| 비밀번호 변경 성공 시 `password_reset_required=0` | `attendance_auth.py:113-124` + routes.py:115-117 | Match |
| 사번과 동일한 비번으로 변경 금지 (`PASSWORD_SAME_AS_EMPID`) | `attendance_auth.py:210-214` | Additive (design에 없음, 보안 강화) |

### 2.4 API 엔드포인트 (Design §5)

| 설계 엔드포인트 | 구현 위치 | 상태 |
|-----------------|-----------|:----:|
| `POST /api/attendance/login` (CSRF-exempt) | `attendance_routes.py:81-93` + `main.py:47` exempt | Match |
| `POST /api/attendance/logout` | `attendance_routes.py:95-98` | Match |
| `POST /api/attendance/change-password` (CSRF required) | `attendance_routes.py:100-119` (not in `main.py:41-48` exempt) | Match |
| `GET /api/attendance/me?month=` | `attendance_routes.py:121-136` | Match |
| `GET /api/attendance/admin/employees` | `attendance_routes.py:138-154` | Match |
| `GET /api/attendance/admin/view` | `attendance_routes.py:156-180` | Match |
| `POST /api/attendance/admin/reset-password` | `attendance_routes.py:182-199` | Match |
| `GET /api/attendance/session` (설계에 없음) | `attendance_routes.py:226-238` | Additive (세션 상태 폴링용) |
| `GET /api/attendance/admin/users` (설계에 없음) | `attendance_routes.py:201-224` | Additive (admin_users.html에서 사용) |
| 응답 필드: `profile/summary/rows/available_months` | `attendance_routes.py:69-75` | Match |
| Error codes: `INVALID_CREDENTIALS/LOCKED/EMP_NOT_IN_EXCEL/MONTH_FILE_NOT_FOUND/FILE_LOCKED_RETRY/FILE_FORMAT_INVALID/SESSION_EXPIRED/CURRENT_PASSWORD_WRONG/PASSWORD_TOO_SHORT` | `attendance_auth.py` + `attendance_routes.py` 전반 | Match |

### 2.5 페이지 라우트 (Design §6)

| 설계 항목 | 구현 위치 | 상태 |
|-----------|-----------|:----:|
| `GET /attendance` | `src/routers/pages.py:123-138` | Match |
| `GET /attendance/login` | `src/routers/pages.py:140-146` | Match |
| `GET /attendance/change-password` | `src/routers/pages.py:148-156` | Match |
| Entry 카드 `.access-card-att` → `/attendance` | `templates/entry.html:54-58` | Match |

### 2.6 프론트엔드 자산

| 자산 | 위치 | 상태 |
|------|------|:----:|
| `templates/attendance.html` | 존재 | Match |
| `templates/attendance_login.html` | 존재 | Match |
| `templates/attendance_change_password.html` | 존재 | Match |
| `static/css/attendance.css` | 존재 (`.att-page`, `.att-header` 등) | Match |
| `static/js/attendance.js` | 존재 | Match |
| `static/js/attendance_login.js` | 존재 | Match |
| `static/js/attendance_change_password.js` | 존재 | Match |
| admin_users.html 근태 계정 섹션 | `templates/admin_users.html:159,178` (`att-users-*`) | Match |
| admin_users.js 근태 탭 로직 | `static/js/admin_users.js:436-521` | Match |

### 2.7 보안 / 미들웨어

| 설계 항목 | 구현 위치 | 상태 |
|-----------|-----------|:----:|
| `/api/attendance/login`만 CSRF exempt | `src/main.py:41-48` (login만 포함, change-password 제외) | Match |
| InternalNetworkOnly 적용 범위 | `main.py:50-53` (`/api/public/notice`만 보호, attendance는 의도적으로 대상 외) | Match (설계 의도대로) |
| 별도 세션 쿠키 vs 단일 세션 + 네임스페이스 | 설계는 "별도 쿠키" 언급했으나 §4.2 후반부에서 "단일 세션 + `att_user` 네임스페이스"로 수정함 → 구현이 후자와 일치 | Match |

### 2.8 감사 로그 (Design §2.2)

| 설계 항목 | 구현 위치 | 상태 |
|-----------|-----------|:----:|
| `action='attendance_viewed_by_admin'` | `attendance_routes.py:169` | Match |
| `target_type='attendance'`, `target_id={emp_id}` | `attendance_routes.py:171-172` | Match |
| `target_label='{성명} (사번 {emp_id}) {YYYY-MM}'` | `attendance_routes.py:173-175` | Match |
| `action='attendance_password_reset'` | `attendance_routes.py:191` | Match |
| 비번 초기화 `target_label` 형식 | `attendance_routes.py:195` → `"사번 {emp_id}"` (성명 없음) | Partial — Low nit |

---

## 3. Accepted Variances (Match Rate 감점 없음)

사용자가 사전에 알린 세 가지 허용 변경. 기록 목적으로만 나열.

1. **비밀번호 변경 강제 → 경고 배너 (Option B)**
   - 설계 §3.1 "첫 로그인 비밀번호 변경 강제" → 구현에서는 `/attendance` 바로 진입 + 상단 노란색 경고 배너 (닫기 가능).
   - 관리자 검토 후 공식 완화. 설계 문서에 주석 추가 필요.
2. **UI 확장 — 8개 시간 컬럼 상시 노출 + 구분 pill + zebra + ×1.5 주석 제거**
   - 설계 mockup §6.4 대비 상세 테이블이 더 풍부. 사용자 피드백 반영한 UX 개선.
3. **비밀번호 해시 알고리즘 bcrypt → PBKDF2-SHA256 (200,000 iter)**
   - 설계 §4.1은 bcrypt 언급, 구현은 `src/security.py`의 기존 PBKDF2 래퍼를 재사용(`src/attendance_auth.py:24`). 기능적으로 동등하며 의존성 최소화 측면에서 합리적. 설계 문서만 수정 필요.

---

## 4. Gap 목록

| # | Severity | 항목 | 위치 | 조치 권장 |
|---|:--------:|------|------|-----------|
| 1 | Low | 설계 §4.1 bcrypt 명시 → 실제는 PBKDF2 | `docs/02-design/features/attendance-view.design.md` §4.1 | 설계 문서를 "PBKDF2-SHA256 (src/security.py 재사용)"으로 수정 |
| 2 | Low | `AttendanceProfile` 필드가 설계(5개)보다 3개 더 많음 (shift_group/job_type/gender) | `src/services/attendance_excel.py:104-113` | 설계 §3.2 dataclass에 3개 필드 추가하여 문서-코드 일치화 (또는 report에 "추가 노출" 명시) |
| 3 | Low | 비번 초기화 감사 로그 `target_label`에 성명 미포함 | `src/routers/attendance_routes.py:195` | 일관성 위해 `f"{name} (사번 {emp_id})"` 형식으로 통일 권장. 설계는 명시하지 않아 strictly-gap 아님 |
| 4 | Info | 설계에 없는 엔드포인트 2개 추가: `GET /api/attendance/session`, `GET /api/attendance/admin/users` | `src/routers/attendance_routes.py:201-238` | 설계 §5에 §5.7, §5.8 추가 문서화 |
| 5 | Info | 설계에 없는 보안 규칙 추가: 비번 == 사번 금지 (`PASSWORD_SAME_AS_EMPID`) | `src/attendance_auth.py:210-214` | 설계 §5.2 error code 표에 추가 |

High / Medium 심각도 Gap 없음.

---

## 5. 위험 항목 검증 결과

사용자가 콕 집어 확인 요청한 risky 항목:

| 점검 항목 | 결과 |
|-----------|------|
| `/api/attendance/change-password` CSRF 필수? | **PASS** — `src/main.py:41-48` exempt_urls에 login만 있고 change-password 없음. CSRFMiddleware 적용됨. |
| `attendance_users.locked_until` 인덱스 존재? | **PASS** — `src/database.py:339-340` `idx_attendance_users_locked_until` |
| `password_reset_required` 변경 성공 시 0으로 클리어? | **PASS** — DB 레벨 `attendance_auth.py:113-124`의 `_set_password(..., reset_required=0)`, 세션 레벨 `attendance_routes.py:115-117`에서 `sess["password_reset_required"] = False` |
| admin 감사 로그 `target_label` 형식? | **PASS (view)** / **Partial (reset)** — view는 `{name} (사번 {emp_id}) {YYYY-MM}` 일치, reset은 `사번 {emp_id}`로 이름 생략 (Gap #3) |
| `current_year_month` 헬퍼 존재 + `available_months()` 내림차순? | **PASS** — `attendance_excel.py:150-152` 헬퍼 존재, `attendance_excel.py:147` `sorted(..., reverse=True)` |
| IP whitelist 적용되는지? | **의도대로 미적용** — `main.py:50-53`은 `/api/public/notice`만 보호. 근태는 사내 전용이지만 InternalNetworkOnly 범위 외. 설계도 요구하지 않음. |

---

## 6. 종합 권장

**권장 조치: report 문서 갱신 후 완료 처리 (proceed to report).**

- 실질적인 구현 결함 없음 (High/Medium Gap 0건).
- 설계 문서만 3건 소폭 수정하면 100% 일치 가능: (a) bcrypt → PBKDF2 정정, (b) `AttendanceProfile` 3개 필드 추가, (c) `/session` + `/admin/users` 엔드포인트 추가 문서화.
- Gap #3 (reset 로그 성명)은 선택적 일관성 개선. 설계에 명시가 없으므로 원칙상 Match 처리 가능.
- 구현 재작업(`pdca iterate`) 불필요. `pdca report`로 진행 가능.

---

## 7. 검증 한계

정적 코드 분석 기반 — 런타임 동작은 이전 세션의 스모크 테스트 결과로 보강됨:
- 런타임 확인 불가 항목: 5분 유휴 만료, 5회 잠금 해제 타이밍, 파일 락 503 실제 응답, `C:\ErpExcel\monthly_attendance_YYYY-MM.xlsx` 물리 파일 컬럼 매핑 (report.md 스모크 테스트 결과로 이미 대부분 확인됨)
- 렌더링 DOM/CSS 실제 표시(줄무늬, 배너, 8컬럼 분리) — 템플릿/CSS 참조는 존재하나 브라우저 렌더링은 수동 확인 필요

---

## 8. 변경 이력

| 버전 | 날짜 | 작성자 | 비고 |
|:----:|------|--------|------|
| 0.1 | 2026-04-23 | gap-detector (Claude) | 소급 초기 분석. Match Rate 96%, High/Medium Gap 없음. |
