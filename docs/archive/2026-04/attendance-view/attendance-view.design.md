# Attendance View Design

> 사번 인증 기반 월별 근태 조회 페이지 상세 설계서

## 1. Overview

| Item | Detail |
|------|--------|
| Feature | attendance-view |
| Plan | `docs/01-plan/features/attendance-view.plan.md` |
| Source | `C:\ErpExcel\monthly_attendance_YYYY-MM.xlsx` (Sheet1, 헤더 2행, 데이터 3행~) |
| Auth | 별도 세션(`irms_att_session`) + PBKDF2-SHA256 + 5분 비활성 타임아웃 + 5회 잠금 |

## 2. Data Model

### 2.1 신규 테이블: `attendance_users`

```sql
CREATE TABLE IF NOT EXISTS attendance_users (
    emp_id TEXT PRIMARY KEY,                -- 사번 (엑셀 컬럼 인덱스 4)
    password_hash TEXT NOT NULL,            -- PBKDF2-SHA256 해시
    password_reset_required INTEGER NOT NULL DEFAULT 1,
    failed_attempts INTEGER NOT NULL DEFAULT 0,
    locked_until TEXT,                      -- ISO-8601 UTC, NULL이면 안 잠김
    last_login_at TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_attendance_users_locked_until
  ON attendance_users(locked_until);
```

이름(성명), 부서명 등은 엑셀에서 매 조회 시 읽으므로 DB 저장 불필요.

### 2.2 `audit_logs` 재사용

관리자가 전체 조회 시:
```
action = 'attendance_viewed_by_admin'
target_type = 'attendance'
target_id = '{emp_id}'
target_label = '{성명} (사번 {emp_id}) {YYYY-MM}'
```

## 3. Excel Parser

### 3.1 컬럼 매핑 (0-index)

| 인덱스 | 컬럼 | 타입 | 비고 |
|--------|------|------|------|
| 0 | 근무일자 | `datetime`→str `YYYY-MM-DD` | |
| 1 | 요일 | str (수/목/금...) | |
| 2 | 구분 | str (평일/토요일/일요일/휴일) | |
| 4 | 사번 | str (6자리) | 조회 키 |
| 6 | 남여 | str | |
| 7 | 근무공장 | str (A관(1공장)/C관(2공장)) | |
| 8 | 성명 | str | 표시용 |
| 10 | 근무직구분 | str (생산직) | |
| 11 | 부서명 | str (원료생산팀) | |
| 12 | 근무조구분 | str | |
| 14 | 근무타임 | str (주간/야간) | |
| 17 | 출근 | str `HH:MM` | 빈칸이면 무출근 |
| 18 | 퇴근 | str `HH:MM` | 빈칸이면 무퇴근 |
| 19 | 익일 | int (0/1) | 퇴근이 다음날 |
| 21 | 평일 조출 | float (시간) | |
| 22 | 평일 정상 | float | |
| 23 | 평일 연장 | float | 급여 1.5배 가산 |
| 24 | 평일 야근 | float | 급여 1.5배 가산 |
| 25 | 휴일 조출 | float | |
| 26 | 휴일 정상 | float | |
| 27 | 휴일 연장 | float | 급여 1.5배 가산 |
| 28 | 휴일 야근 | float | 급여 1.5배 가산 |
| 29 | 지각시간 | float | |
| 30 | 조퇴시간 | float | |
| 31 | 외출시간 | float | |
| 33 | 비고 | str | |

### 3.2 파싱 결과 구조

```python
@dataclass
class AttendanceRow:
    date: str             # "2026-04-01"
    weekday: str          # "수"
    day_type: str         # "평일"
    check_in: str | None  # "07:20" or None
    check_out: str | None
    next_day: bool
    weekday_early: float  # 평일 조출
    weekday_normal: float
    weekday_overtime: float
    weekday_night: float
    holiday_early: float
    holiday_normal: float
    holiday_overtime: float
    holiday_night: float
    late_hours: float
    early_leave_hours: float
    outing_hours: float
    note: str | None

@dataclass
class AttendanceProfile:
    emp_id: str
    name: str
    department: str
    factory: str
    shift_time: str
    shift_group: str
    job_type: str
    gender: str

@dataclass
class AttendanceSummary:
    work_days: int
    late_count: int
    late_total: float
    early_leave_count: int
    early_leave_total: float
    outing_count: int
    outing_total: float
    weekday_early: float
    weekday_normal: float
    weekday_overtime: float
    weekday_night: float
    holiday_early: float
    holiday_normal: float
    holiday_overtime: float
    holiday_night: float
```

### 3.3 서비스 인터페이스

```python
# src/services/attendance_excel.py

def month_file_path(year_month: str) -> Path:  # "2026-04"
def available_months() -> list[str]:           # 파일 존재하는 YYYY-MM 목록
def load_month(year_month: str) -> list[AttendanceRow including emp_id, name ...]
def employee_list(year_month: str) -> list[dict[emp_id, name, dept, factory]]
def filter_by_emp(year_month, emp_id) -> (profile, rows, summary)
```

**락 회피:** `openpyxl.load_workbook(path, read_only=True, data_only=True)`
**에러 처리:** `PermissionError` → 503 "FILE_LOCKED_RETRY"
**캐시:** 없음 (매 요청 읽기, 84KB라 수 ms)

## 4. Authentication

### 4.1 비밀번호 해시

기존 `src/security.py`의 PBKDF2-SHA256 래퍼 재사용:
```python
from ..security import hash_password, verify_password
```

### 4.2 세션

별도 쿠키 이름: `irms_att_session` (기존 `irms_session`과 충돌 방지).

SessionMiddleware 인스턴스가 단일이므로, **단일 세션 안에 두 개의 네임스페이스**로 관리:
- `request.session["user"]` → 기존 IRMS 사용자
- `request.session["att_user"]` → 근태 사용자 `{emp_id, authenticated_at, last_activity}`

### 4.3 비활성 타임아웃 (5분)

로그인/조회 요청마다 `att_user.last_activity = now()` 갱신.
요청 시 `now() - last_activity > 300초` → 세션 파기 + 401 반환.

### 4.4 실패 잠금 (5회 / 5분)

- 로그인 실패 → `attendance_users.failed_attempts += 1`
- 5회 도달 → `locked_until = now() + 5min`, `failed_attempts` 리셋
- 로그인 시 `locked_until > now()` → 423 LOCKED
- 로그인 성공 → `failed_attempts = 0`, `locked_until = NULL`, `last_login_at = now()`

### 4.5 관리자 특권

**자동 감지:** `request.session["user"]` 존재하고 `access_level in ("admin","manager")`
→ 근태 세션 없이도 `admin_mode=True`로 접근 허용.

## 5. API Design

모든 엔드포인트는 `/api/attendance/` prefix.

### 5.1 로그인

```
POST /api/attendance/login
Body: { emp_id: string, password: string }
Response 200: {
  emp_id, name, password_reset_required: bool
}
Response 401: { detail: "INVALID_CREDENTIALS" }
Response 423: { detail: "LOCKED", locked_until: "..." }
Response 404: { detail: "EMP_NOT_IN_EXCEL" }  # 엑셀에도 없으면 가입 불가
```

**첫 로그인 흐름:**
1. emp_id 엑셀에 존재 + attendance_users에 없음 → `hash_password(emp_id)`로 자동 INSERT, `password_reset_required=1`
2. 입력 비번 검증
3. 세션에 `att_user` 저장
4. `password_reset_required=true` 반환 → 프론트가 안내 배너 표시, 조회는 계속 허용

### 5.2 비밀번호 변경

```
POST /api/attendance/change-password
Body: { current_password, new_password }
Response 200: { status: "ok" }
Response 400: { detail: "CURRENT_PASSWORD_WRONG" | "PASSWORD_TOO_SHORT" }
```

성공 시 `password_reset_required=0`.

### 5.3 로그아웃

```
POST /api/attendance/logout → 200 (세션 제거)
```

### 5.4 본인 조회

```
GET /api/attendance/me?month=2026-04
Response 200: {
  profile: { emp_id, name, department, factory, shift_type },
  month: "2026-04",
  summary: { work_days, late_count, late_total, ..., weekday_early, ... },
  rows: [ { date, weekday, day_type, check_in, check_out, ... } ],
  available_months: ["2026-04", "2026-03", ...]
}
Response 401 / 404 / 503
```

- `month` 미지정 시 현재 달
- 근태 사용자: 자기 emp_id 고정 (request.session에서 가져옴)
- 관리자: 이 엔드포인트 쓰지 않음 (`/admin` 버전)

### 5.5 관리자 — 사용자 목록 + 조회

```
GET /api/attendance/admin/employees?month=2026-04
Response 200: { items: [ { emp_id, name, department, factory } ] }

GET /api/attendance/admin/view?emp_id=090702&month=2026-04
Response 200: (same as /me)
  → audit_logs INSERT
```

### 5.6 관리자 — 비밀번호 초기화

```
POST /api/attendance/admin/reset-password
Body: { emp_id }
Response 200: { status: "ok" }
  → password_hash = hash_password(emp_id)
  → password_reset_required = 1
  → failed_attempts = 0, locked_until = NULL
  → audit_logs INSERT ('attendance_password_reset')
```

### 5.7 관리자 — 근태 계정 목록

```
GET /api/attendance/admin/users
Response 200: {
  items: [
    { emp_id, password_reset_required, failed_attempts, locked_until, last_login_at, created_at }
  ],
  total
}
```

### 5.8 세션 상태

```
GET /api/attendance/session
Response 200: {
  authenticated: bool,
  admin_mode: bool,
  emp_id: string | null,
  password_reset_required: bool
}
```

## 6. UI Design

### 6.1 Entry 카드 추가 (templates/entry.html)

```html
<div class="access-grid">
  <article class="access-card access-card-work">... 계량 시작 ...</article>
  <article class="access-card access-card-mgmt">... 관리 화면 ...</article>
  <article class="access-card access-card-att">
    <span class="access-chip">Attendance</span>
    <h2>근태 확인</h2>
    <p>사번으로 로그인해 이번 달 출퇴근과 연장 시간을 확인합니다.</p>
    <a href="/attendance" class="btn accent access-link">근태 보기</a>
  </article>
</div>
```

CSS: `access.css`에 `.access-card-att` 스타일 추가 (기존과 다른 액센트).

### 6.2 로그인 페이지 (templates/attendance_login.html)

```
┌───────────────────────┐
│  근태 조회             │
│  사번 [        ]       │
│  비밀번호 [      ]     │
│  [로그인] [취소]       │
│  ※ 초기 비밀번호는 사번 │
└───────────────────────┘
```

### 6.3 비밀번호 변경 페이지 (attendance_change_password.html)

```
┌─────────────────────────────┐
│ 비밀번호 변경                 │
│ 현재 비밀번호 [       ]      │
│ 새 비밀번호  [       ]       │
│ 새 비밀번호 확인 [       ]   │
│ [변경하고 로그인]             │
└─────────────────────────────┘
```

### 6.4 조회 페이지 (templates/attendance.html)

```
┌────────────────────────────────────────────────────┐
│ [IRMS 로고]  홍길동 님  사번 171013  원료생산팀     │
│                    [비번변경]  [로그아웃]           │
├────────────────────────────────────────────────────┤
│              ◀  2026-04  ▶                          │
├──────────────────────┬─────────────────────────────┤
│ 근무일    20일        │  지각    2회 · 0.5h         │
│                       │  조퇴    1회 · 0.25h        │
│                       │  외출    0회                 │
├──────────────────────┴─────────────────────────────┤
│   평일 근무      │   휴일 근무                        │
│   정상 160.0h    │   정상  0.0h                      │
│   연장  25.0h ★  │   연장  0.0h ★ (★ 급여 1.5배)     │
│   야근   0.0h ★  │   야근  0.0h ★                    │
│   조출   3.0h    │   조출  0.0h                      │
├────────────────────────────────────────────────────┤
│ 일자별 상세                                         │
│ 04-01(수) 평일  07:20-18:02  정상 8.0 조출 1.5 ...  │
│ 04-02(목) 평일  08:30-18:00  정상 8.0              │
│ ...                                                 │
└────────────────────────────────────────────────────┘
```

**관리자 모드 추가 UI:**
페이지 최상단에 드롭다운 `[직원 선택 ▾]` → 사번(성명) 목록 → 선택 시 해당 직원 조회.

### 6.5 관리자 메뉴: 비밀번호 초기화

기존 admin_users 페이지에 새 탭/섹션 "근태 계정":
```
근태 사용자 목록
┌──────┬────────┬───────────┬──────────┐
│ 사번 │ 성명(엑셀)│ 마지막 로그인│ [초기화] │
├──────┼────────┼───────────┼──────────┤
│090702│ 전명옥 │ 2026-04-22│ [초기화] │
│...                                     │
```

## 7. Error Handling

| 상황 | HTTP | 응답 |
|------|------|------|
| 사번 잘못 / 비번 잘못 | 401 | `INVALID_CREDENTIALS` |
| 사번이 엑셀에 없음 | 404 | `EMP_NOT_IN_EXCEL` |
| 5회 잠금 | 423 | `LOCKED` |
| 엑셀 파일 없음 | 404 | `MONTH_FILE_NOT_FOUND` |
| 엑셀 락 | 503 | `FILE_LOCKED_RETRY` |
| 엑셀 헤더 이상 | 500 | `FILE_FORMAT_INVALID` |
| 세션 만료 (5분) | 401 | `SESSION_EXPIRED` |
| 비번 변경 중 현재 비번 오류 | 400 | `CURRENT_PASSWORD_WRONG` |
| 새 비번이 사번과 동일 | 400 | `PASSWORD_SAME_AS_EMPID` |

## 8. Test Plan (수동)

1. 초기 상태 → 사번 171013 + 비번 171013 로그인 → 조회 페이지 + 안내 배너
2. 비번 변경(예: `newpass1`) → 배너 해제
3. 로그아웃 → 171013 + `newpass1` 로 재로그인 → 바로 조회 페이지
4. 5분 대기 후 API 호출 → 401 `SESSION_EXPIRED`
5. 5회 잘못된 비번 → 423 LOCKED, 5분 후 다시 시도 가능
6. 관리자로 IRMS 로그인 → `/attendance` 접속 → 드롭다운에서 직원 선택 → 조회 가능
7. 관리자 비번 초기화 → 해당 사용자 다음 로그인 시 안내 배너 표시, 조회는 계속 가능
8. 2026-04 ◀ 클릭 → 2026-03 (파일 없으면 비활성)
9. 엑셀 파일을 Excel로 열어놓은 상태에서 조회 → 락 에러 메시지 또는 정상(read_only 모드는 대부분 OK)

## 9. Next Step

구현 시작 — DB → 파서 → 인증 → API → UI 순.
