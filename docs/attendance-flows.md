# 근태 도메인 흐름 문서 (Attendance Flows)

> 대상 코드: `src/routers/attendance_routes.py`, `src/attendance_auth.py`,
> `src/services/attendance_excel/*`, `src/routers/public_attendance_alert_routes.py`,
> `src/routers/pages.py`(근태 페이지), `templates/attendance*.html`,
> `static/js/attendance*.js`, `src/middleware/{internal_only,login_origin}.py`,
> `tray_client/src/{attendance_alerts,schedule}.py`
>
> 이 문서는 코드와 `tests/test_attendance*.py` 로 하나하나 대조해 작성했다.
> 각 규칙 옆의 `파일:함수` 는 그 규칙이 실제로 사는 위치다. 근무 판정 공식은
> 테스트 케이스로 역검증했다(§2 표의 "검증" 열).

---

## 0. 한눈에 보는 구조

```
ERP 야간 배치(18:00)
   └─ C:\ErpExcel\monthly_attendance_YYYY-MM.xlsx  (요청마다 새로 읽음, 캐시 없음)
        │
        ▼
  services/attendance_excel/          ← 순수 파싱·판정 엔진 (DB 무관)
    files.py    경로/시각 헬퍼, DEFAULT_COLUMNS(구 고정 인덱스)
    models.py   COL_* 상수, 헤더 매핑표, 데이터클래스
    parser.py   워크북 로드 → 헤더 자동 매핑 → 행→레코드
    anomaly.py  근무 baseline·부분휴가·이상 판정 (핵심)
    summary.py  직원 조회·월/연 집계·연차 버킷
        │
        ├──▶ routers/attendance_routes.py   /api/attendance/*  (본인·책임자 조회)
        │        └ attendance_auth.py        사번 세션·잠금·비번 (attendance_users 테이블)
        │
        └──▶ routers/public_attendance_alert_routes.py  /api/public/attendance-alerts/*
                 └ 내부망 전용(InternalNetworkOnlyMiddleware) → 트레이 알림 폴러
```

근태는 메인 IRMS 인증과 **별개의 자격 공간**이다. 신원은 ERP 사번,
저장은 `attendance_users` 테이블. 단, 메인 앱에 책임자(manager)로 로그인한
사람은 사번 게이트를 건너뛰고 "책임자 모드"로 아무 직원이나 볼 수 있다
(`attendance_auth.is_admin_mode`).

---

## 1. 엑셀 임포트 — 헤더 기반 자동 매핑

### 1.1 소스 파일 검색 (다중 소스/월)

`files.month_file_paths(year_month)` 는 `C:\ErpExcel` 안에서
`FILENAME_REGEX = ^monthly_attendance(?:_.+)?_(\d{4}-\d{2})\.xlsx$` 에 맞는
모든 파일을 모은다. 정규식의 `(?:_.+)?` 덕분에 `monthly_attendance_2026-04.xlsx`
뿐 아니라 `monthly_attendance_colorist_2026-04.xlsx`(조색팀 별도 소스)도 같은 달로
묶인다. 정렬은 정식 이름(접미사 없음)을 항상 앞에 둔다.
(검증: `test_attendance_excel_multi_source.py::test_month_file_paths_and_available_months_include_colorist_source`)

- 한 달 = 여러 파일이므로 직원 조회·이상 감지 모두 그 달의 **모든** 파일을 순회한다
  (`summary.employee_list`, `anomaly.detect_today_anomalies`,
  `detect_month_anomalies` 전부 `_month_file_paths_or_raise` 로 리스트를 받아 loop).
- `available_months()` 는 파일명에서 `YYYY-MM` 만 추려 내림차순 반환.
- 캐시 없음(파서 모듈 docstring): 파일이 작고(~85KB/500행), 야간 갱신 직후
  stale 데이터를 보이면 안 되므로 매 요청 재파싱.

### 1.2 헤더 기반 열 매핑 (2026-06 열 순서 변경 대응) — 핵심

배경(`files.py` 상단 주석, `test_attendance_excel_column_map.py` docstring):
2026-05 까지는 ERP 내보내기 열 순서가 고정이라 `COL_*` 상수(models.py)로 바로 읽었다.
2026-06 부터 ERP 가 신원/근무정보 블록(성명·근무타임·부서명 등)의 열을 재배치했다.
고정 인덱스로 읽으면 성명이 '근무공장' 값으로, 근무타임이 엉뚱한 값으로 잡혀
**근태 이상 감지가 통째로 무력화**된다.

해결: 실제 파일은 헤더 두 행(그룹 행0 + 세부 행1)에서 열 위치를 동적으로 찾는다.

- `parser._column_map_from_ws(ws)` → `parser._make_column_map(header_group, header_sub)`
- 세부 행 명칭 → 논리 필드: `models._HEADER_SIMPLE_FIELDS`
  (`근무일자→date`, `사번→emp_id`, `성명→name`, `근무타임→shift_time`, `근태코드→attendance_code` 등)
- 병합된 그룹 헤더 처리: `평일근무시간 / 휴일근무시간` 아래 `조출/정상/연장/야근`
  세부 명칭이 두 번 나오므로, 그룹 행 텍스트를 오른쪽으로 채워(`filled_group`)
  `평일`이면 `weekday_*`, `휴일`이면 `holiday_*` 로 접두를 붙인다
  (`_HEADER_WORKHOUR_SUFFIX`).
- `비고(note)` 는 세부 행이 비고 그룹 헤더에만 존재하는 특례로 잡는다.
- (검증: `test_june_layout_maps_to_correct_indices`, `test_june_row_reads_real_name_and_shift`)

### 1.3 폴백 규칙

`_make_column_map` 은 **필수 필드**(`models._HEADER_REQUIRED_FIELDS` =
`date, emp_id, name, attendance_code, check_in, check_out, shift_time, day_type`)를
모두 찾지 못하면 `files.DEFAULT_COLUMNS`(구 고정 인덱스)를 그대로 돌려준다.
헤더가 없는 입력(단위 테스트가 위치 기반으로 만든 행)이나 구버전 레이아웃 안전판이다.
(검증: `test_missing_header_falls_back_to_default_columns`)

- 필수 필드가 다 잡히면 반환값은 `{**DEFAULT_COLUMNS, **detected}` —
  검출되지 않은 비필수 필드는 구 기본 인덱스로 폴백한다. **이제 이 조용한 폴백을
  서버 로그로 한 번 경고한다**(GAP-1 해결, 2026-07-22). `parser._build_column_map`
  이 `(colmap, warnings)` 를 돌려주고 `_make_column_map` 이 warnings 를 `logging`
  으로 남긴다. 필수 미충족으로 통째 폴백하는 정상 시나리오(헤더 없는 단위테스트·
  구버전)는 경고 없음.

### 1.4 셀 파싱 규칙 (`parser.py`)

| 필드 | 함수 | 규칙 |
|---|---|---|
| 문자열 | `_cell_str` | `str`→strip; `datetime/date`→`%Y-%m-%d`; `time`→`%H:%M`; 정수형 `float`(171013.0)→정수 문자열(BUG-2 해결); 그 외 `str(v).strip()` |
| 시각 | `_cell_time` | `time/datetime`→`%H:%M`; 문자열은 그대로; 빈값→`None` |
| 숫자 | `_cell_float` | 실패/빈값→`0.0` |
| 구분(day_type) | `_cell_day_type` | 지정 열 값이 `DAY_TYPE_VALUES` 밖이면 **바로 오른쪽 열도 확인**(열 밀림 방어) |
| 데이터 시작 | `_iter_data_rows` | 0·1행은 헤더로 스킵, `date`·`emp_id` 둘 다 비면 스킵 |

- `_row_to_record` 는 `emp_id` 가 비면 빈 dict 반환(무시). 파싱과 동시에
  `row.issues = anomaly._row_issue_labels(row, shift_time)` 로 이상 라벨을 붙인다.
- `DAY_TYPE_VALUES = ("평일","평일2","주휴","무휴","유휴")`.
- **사번 정규화(`models.normalize_emp_id`, BUG-2 해결)**: 파싱 측 `_cell_str` 가
  숫자형 셀을 정수 문자열로 낮추고, 조회 측(`summary.employee_exists_in_any_month`,
  `employee_profile_from_any_month`, `_load_month_rows_for_employee`)이 비교 양쪽을
  `normalize_emp_id`(strip + 정수형 실수 → 정수 문자열)로 맞춰, 엑셀 사번이 숫자형
  (`171013.0`)이든 문자열이든 로그인 사번 `"171013"` 과 일치한다. 문자형 사번(앞자리
  0 포함)은 손상 없이 보존. **주의**: DB 계정 조회(`attendance_auth._fetch`)는 정확
  일치라 이 정규화 범위 밖 — 저장된 계정 값 자체가 어긋나면 별도 처리 필요.

---

## 2. 근무 판정 규칙 전체 표 (baseline)

핵심 함수는 **하나**(`_compute_row_anomaly_baseline`)이고, 나머지는 그 어댑터다:

- `anomaly._compute_row_anomaly_baseline(row, shift_time)`
  — **감지 경로가 쓰는 실제 함수.** 주간 + 2교대(주/야) 부분휴가 모두 처리.
- `anomaly._compute_anomaly_baseline(shift_time, day_type, note, code)`
  — 얇은 어댑터. 최소 `AttendanceRow` 를 구성해 위 함수에 **위임**한다(GAP-2 해결,
  2026-07-23). 두 진입점은 2교대 부분휴가 포함 항상 동일한 결과를 낸다. 기존
  `day_type` 테스트 caller 호환을 위해 시그니처·export 유지.

### 2.1 기본 baseline (`anomaly.SHIFT_BASELINES`, 자정 기준 분)

| shift_time | 출근 | 정규 퇴근 | 비고 |
|---|---|---|---|
| `주간` | 09:00 (540) | 18:00 (1080) | 평일만. 점심 1h 포함 대칭 모델 |
| `2교대(주간)` | 07:00 (420) | 15:45 (945) | = 07:00 + 정규8h + 휴식45분 |
| `2교대(야간)` | 19:00 (1140) | 27:45 (1665) | 익일 03:45. ERP가 24+ 시각으로 기록 |
| 그 외/빈값 | — | — | `None` → **감지 제외**(교대 비번일·미지 근무형태) |

- 상수: `SHIFT2_REGULAR_WORK_MINUTES=480`, `SHIFT2_BREAK_MINUTES=45`.
  2교대 정규 퇴근은 "잔업 없이 정규 8h 를 마친 시각"으로 둔다. 잔업 끝(주간 19:00)을
  기준 삼으면 면제 신호가 ERP에 없어 **정상 정시 퇴근이 전부 오탐**이 되기 때문
  (`anomaly.py` SHIFT_BASELINES 위 주석).
- `평일2`(주간 점심 30분 단축일, `DAY_SHIFT_SHORT_LUNCH_DAY_TYPE`): 퇴근 baseline
  −30분 → 17:30. (검증: `test_attendance_excel_day_type.py::test_weekday2_day_shift_uses_1730_checkout_baseline`)

### 2.2 부분휴가(반차/반반차) baseline 이동 — 근무형태별 비대칭

반차·반반차는 전일휴가가 **아니다**. 감지에서 제외하지 않고 baseline 만 이동시켜
계속 감지한다(`_is_full_day_leave` 가 반차/반반차를 먼저 걸러 `False` 반환).

문자열 판정: `_partial_leave_shift` 가 `day_type+note+attendance_code` 를 합쳐 검사하고,
**"반반차"가 "반차"의 부분문자열이므로 반드시 반반차를 먼저 검사**한다
(`PARTIAL_LEAVE_SHIFT_HOURS = (("반반차",2),("반차",5))`).

| 근무형태 | 휴가 | 오전(늦게 출근) | 오후(일찍 퇴근) | 검증(테스트) |
|---|---|---|---|---|
| 주간 (점심 포함 대칭) | 반차 | 출근 09:00+5h=**14:00** | 퇴근 18:00−5h=**13:00** | shift2 `test_day_shift_partial_leave_unchanged` |
| 주간 | 반반차 | 출근 09:00+2h=**11:00** | 퇴근 18:00−2h=**16:00** | 동상 |
| 2교대(주간) 07:00 | 반차 | 출근 07:00+4h=**11:00** | 퇴근 07:00+(8−4)h=**11:00** | shift2 CASES |
| 2교대(주간) | 반반차 | 출근 07:00+2h=**09:00** | 퇴근 07:00+(8−2)h=**13:00** | shift2 CASES |
| 2교대(야간) 19:00 | 반차 | 출근 19:00+4h=**23:00** | 퇴근 19:00+4h=**23:00** | shift2 CASES |
| 2교대(야간) | 반반차 | 출근 19:00+2h=**21:00** | 퇴근 19:00+6h=**25:00**(익일 01:00) | shift2 CASES |

핵심 비대칭(2교대, `_compute_row_anomaly_baseline` 216–228):
- **주간(09-18)**: 점심 1h 를 포함한 대칭 모델이라 반차=5h(근무4h+점심1h)/반반차=2h 를
  출근에 더하거나 퇴근에서 빼는 방식(`base_in += m` / `base_out -= m`).
- **2교대**: 휴가량은 반차 4h / 반반차 2h (`SHIFT2_PARTIAL_LEAVE_MINUTES`, 점심 보정 없음).
  휴식 45분은 정규근무 후반부에 있어 6h 이하만 일하는 오후 반차/반반차엔 안 붙는다.
  그래서 오후 퇴근은 `base_out = base_in + (정규8h − 휴가량)` 으로 **휴식 미포함** 재계산.

### 2.3 오전/오후 미표기 시 추론

`day_type+note+code` 에 "오전"/"오후" 표기가 없으면(`half == "unknown"`)
출퇴근 기록으로 추론한다.

- 주간: `_infer_partial_leave_half`, 2교대: `_infer_shift2_partial_half`
- 규칙(양쪽 동일):
  - `check_in ≥ base_in + 휴가량` → **오전**(늦게 출근)
  - `check_out < base_out(정규 퇴근)` → **오후**(정규 퇴근 이전 퇴근)
  - 둘 다 아니면 `unknown` → baseline 그대로(보수적).
- 오후 추론이 "정규 퇴근 이전"을 신호로 쓰는 이유: 모델상 기대 퇴근(예 2교대 반반차 13:00)
  보다 늦게(잔업 일부) 나가도 정규 퇴근(15:45) 전이면 오후로 봐서 **조퇴 오탐을 막는다**.
  (현장 사례: 반반차 표기 없음, 06:55 출근/14:42 퇴근 →
  `test_afternoon_leave_inferred_when_overtime_partially_worked`,
  `test_unknown_half_quarter_leave_infers_afternoon_from_checkout`)

### 2.4 전일휴가 제외 (`_is_full_day_leave`)

`FULL_DAY_LEAVE_KEYWORDS = ("연차","월차","휴가","휴직","유급","공가","훈련","예비군","교육","결근")`.
`day_type+note+code` 에 이 키워드가 있으면 **감지에서 완전히 제외**(단, 반차/반반차 키워드가
있으면 먼저 `False`).

- `휴직`: 육아휴직·병가휴직 등 장기 전일 부재 — 출퇴근 기록 없음이 정상이라 "출근 누락"으로
  잡으면 안 됨. (검증: `test_attendance_excel_column_map.py::test_parental_leave_produces_no_anomaly`,
  `test_generic_leave_of_absence_excluded`; 반례 유지 `test_real_absence_still_flagged`)
- `결근`: 근태코드로 명시되면 담당자가 이미 처리한 전일 부재 → 미타각으로 잡지 않음
  (`test_attendance_excel_anomaly_resolution.py::test_detect_month_anomalies_excludes_coded_absence`).

### 2.5 연차 일수 버킷 (`summary._annual_leave_bucket`)

`_is_annual_leave_row` (`ANNUAL_LEAVE_KEYWORDS = 연차/월차/휴가/반차/유급/공가`)이 참인 행을
연 집계에서 카운트. 일수는:

- "반반차" 포함 → `quarter` 0.25 (먼저 검사)
- `HALF_DAY_LEAVE_KEYWORDS = ("반차",)` 포함 → `half` 0.5 (GAP-5 해결: 단독 "오전"/"오후"는
  반일 신호 아님 — 전일 연차 비고에 섞인 AM/PM 문구 오분류 차단)
- 그 외 → `full` 1.0

(검증: `test_attendance_annual_leave.py` — 연차1.0 + 오후반차0.5 + 반반차0.25 = 1.75)

---

## 3. 이상(알림) 판정

원칙(메모리 `feedback_alert_semantics`): **ERP 컬럼(근태코드/공제시간)에 값이 있으면
담당자가 이미 처리한 것. 비어 있는데 실제로 기준을 벗어났을 때만 알림.**

핵심: `anomaly._unprocessed_row_issues(row, shift_time, reference=?)` (314–352).

### 3.1 게이트(순서대로)
1. `_row_is_future` — 행 날짜가 기준일보다 미래면 `[]`.
2. `day_type ∉ ("평일","평일2")`(`ALERT_WORKDAY_TYPES`) → `[]` (휴일·주휴 제외).
3. 근태코드 이슈(`_attendance_code_issues`) + 공제코드 불일치(`_deduction_code_mismatch_issues`)
   먼저 수집.
4. `_is_full_day_leave` 면 여기까지의 코드 이슈만 반환(시간 기반 미타각 판정 건너뜀).
5. baseline `None`(미지 근무형태)이면 코드 이슈만 반환.

### 3.2 이상 항목 표

| 이상 라벨 | 조건 | 근거 함수 |
|---|---|---|
| `출근 누락` | `late==0` ∧ `check_in` 없음 ∧ 출근 baseline+유예 지남 | `_unprocessed_row_issues` 338 |
| `지각 미처리` | `late==0` ∧ `check_in분 > 출근 baseline` | 340–343 |
| `퇴근 누락` | `early_leave==0` ∧ `check_out` 없음 ∧ 퇴근 baseline+유예 지남 | 345 |
| `조퇴 미처리` | `early_leave==0` ∧ `check_out분 < 퇴근 baseline` | 347–350 |
| `근태코드 누락(지각/조퇴/외출)` | 해당 공제시간 > 0 인데 코드에 지각/조퇴/외출 없음 | `_deduction_code_mismatch_issues` 305–310 |
| `공제시간 불일치` | 지각/조퇴/외출 공제시간 > 0 인데 코드가 휴가류(연차/반차 등) | 301–302 |
| `출근 누락`/`퇴근 누락` (코드) | 근태코드에 "미타각/누락" + "출"/"퇴" 포함 | `_attendance_code_issues` 274–288 |

- 유예: `ALERT_GRACE_MINUTES = 15`. `_baseline_has_passed` 는 근무일 자정에
  `baseline+15분` 을 더한 **실제 기준 datetime** 과 `reference` 를 직접 비교한다
  (`reference >= 근무일0시 + baseline + 유예`). baseline 이 1440 이상인 2교대(야간)
  퇴근(27:45 = 익일 03:45)도 익일 날짜로 정확히 걸쳐 계산된다(BUG-1 해결). 날짜
  지름길(`row_date < reference.date()` → 무조건 통과)을 쓰던 이전 방식은 새벽 열람 시
  야간 퇴근을 조기 통과시켜 오탐했다.
- 지각 처리 완료 예: 코드 "지각" + `late_hours=0.5` → 이상 아님
  (`test_row_to_record_with_late_code_ignores_matched_late_deduction`).
- 지각 시간은 있는데 코드가 없으면 `근태코드 누락(지각)`
  (`test_month_anomaly_reports_late_deduction_without_late_code`).
- (검증: 미처리 3종 동시 — `test_attendance_excel_row_details.py::test_row_to_record_only_marks_unprocessed_issues`)

### 3.3 알림 카테고리·표시 (`_row_alert_category`, `_anomaly_detail`)

이상 라벨 조합을 코드/제목으로 축약: 출·퇴 모두 누락→`1 출/퇴근 미타각`,
출근만→`2`, 퇴근만→`3`, 코드/공제 불일치→`0 근태 이상`, 지각/조퇴 계열→`4`,
반차/반반차 조퇴예상→`5/6`. (문자열은 유니코드 이스케이프로 하드코딩 —
`_row_alert_category`)

### 3.4 월 집계 병합 (`_merge_anomaly_record`, `detect_month_anomalies`)

같은 사번의 여러 날 이상을 한 항목으로 묶고(dates/details/issues 중복 제거), 날짜순 정렬.
(검증: `test_detect_month_anomalies_merges_dates_and_dedupes_issues`)

### 3.5 트레이 알림 슬롯 (09/13/16)

- `tray_client/src/schedule.py`: `SCHEDULED_ALERT_HOURS = (9, 13, 16)`,
  하루 슬롯당 1번. `SLOT_STALE_GRACE_MINUTES = 30` — 앱 재시작 시 이미 30분 지난 슬롯은
  건너뛴다(켤 때마다 도로 뜨는 것 방지, `stale_slot_key_on_startup`).
- `attendance_alerts.AttendanceAlertPoller` 가 `/api/public/attendance-alerts/**month**` 를 폴링.
  `anomaly_signature` 로 같은 슬롯 내 동일 결과 중복 팝업 억제. 404(파일 없음)→슬롯 스킵,
  503(파일 잠김)→`SLOT_RETRY_SECONDS=60` 후 재시도.
- 즉 트레이는 **월 전체 미처리 이상**을 하루 3회 요약 팝업으로 띄운다(당일만이 아님).

---

## 4. 계정 수명주기 (`attendance_auth.py`)

저장소: `attendance_users(emp_id, password_hash, password_reset_required,
failed_attempts, locked_until, last_failed_at, last_login_at, created_at)`.

### 4.1 프로비저닝 — 자동 생성 없음

- **최초/재발급은 책임자만.** `POST /api/attendance/admin/reset-password`
  (`attendance_routes.admin_reset_password` → `attendance_auth.reset_password_to_temporary`).
- 대상 사번이 엑셀 어느 달에도 없으면 `EMP_NOT_IN_EXCEL(404)`
  (`employee_exists_in_any_month` 확인). 있으면 랜덤 임시 비번(`secrets.token_urlsafe(18)`)
  발급, `password_reset_required=1` 로 계정 생성 또는 재설정.
- 로그인 시 계정이 없으면 **절대 자동 생성 안 함** — `INVALID_CREDENTIALS(401)` 로 통일 응답,
  실제 사유(`employee_not_in_excel` vs `account_not_provisioned`)는 감사 로그에만.
  (검증: `test_attendance_auth_hardening.py` 전 4건 — 자동생성 금지·사번=비번 예측 금지·
  존재 여부 노출 금지·랜덤 임시비번)
- 발급/조회 UI: `templates/attendance_login.html` 는 "책임자에게 발급받으세요" 안내 +
  `/management/login?next=/attendance` 링크. 발급 실행 화면은 책임자용 사용자 관리
  (`/admin/users`).

### 4.2 최초 로그인 → 변경 강제(소프트)

- 로그인 응답에 `password_reset_required` 를 실어 세션에 저장(`login_session`).
- `attendance.html` 상단 경고 배너 + `attendance_change_password.html` 유도.
- **강제는 소프트다**: 조회는 임시 비번으로도 계속 가능하고, "나중에 변경" 버튼 존재
  (`attendance_change_password.js`). 하드 게이트(403) 아님 — §7 GAP-3 참조.

### 4.3 비밀번호 정책 (`validate_password_strength`, `MIN_PASSWORD_LENGTH=8`)

- 8자 이상, 사번과 동일 금지, 반복 숫자 금지(`_is_repeated_digits`),
  연속 숫자 금지(`_is_sequential_digits`, 인접 차이 ±1, 9↔0 wrap 없음).
- 변경 엔드포인트 `POST /change-password` 는 현재 비번 검증 후 새 비번 검증,
  `password_reset_required=0` 으로 갱신. Pydantic `ChangePasswordRequest.new_password`
  도 `min_length=8`.

### 4.4 잠금(brute force) — 15분 창 + 5분 잠금

- `MAX_FAILED_ATTEMPTS=5`, `LOCKOUT_SECONDS=5*60`, `FAILED_WINDOW_SECONDS=15*60`.
- `_attempts_within_window`: 마지막 실패가 15분 창을 넘겼거나 기록 없으면 카운터를 **1부터
  다시** 센다(감사 F-11: 며칠에 걸친 오타가 잠금으로 누적되던 버그 수정).
- 창 안에서 5회 도달 → `locked_until = now+5분`, `failed_attempts=0` 리셋, `LOCKED(423)`.
- 잠금 중 로그인 → `LOCKED(423)` + `locked_until`.
- (검증: `test_attendance_lockout_window.py` — 창 밖 4건은 재시작/창 안 5건째는 잠금/
  정확히 창 경계는 아직 유효)
- 라우트 레벨 `@limiter.limit("5/minute")`(slowapi, IP 기준)이 계정 잠금과 별개로 겹침.

### 4.5 세션 유휴 타임아웃

- 서버: `IDLE_TIMEOUT_SECONDS=5*60`. `current_attendance_emp_id` 가 매 접근마다
  `last_activity` 로 5분 초과 시 세션 파기. `touch_session` 이 활동마다 갱신.
- 클라이언트(`attendance_session.js`): 화면 보이는 동안 3분/숨기면 30초 카운트다운 배지,
  T=0 에 `sendBeacon('/api/attendance/logout')` 후 로그인으로. 탭/창 닫힘도 sendBeacon.
  서버 5분은 스크립트가 못 도는 경우(브라우저 크래시 등)의 안전망.
  (주의: 클라 3분 < 서버 5분 — 클라가 더 공격적. §7 참고)

---

## 5. 조회 흐름

### 5.1 본인 조회 (사번 로그인)

`GET /api/attendance/me?month=` → `require_view_context` →
`_load_attendance_response(month, emp_id)`.
반환: profile / summary(월) / annual_summary(연) / rows / available_months.
월 파라미터 검증 `_resolve_month`(형식 `YYYY-MM` 아니면 현재월).

- 월 파일 없음→404 `MONTH_FILE_NOT_FOUND`, 잠김→503, 형식오류→500.
- 프론트 `attendance.js` 가 월 이동(available_months 인덱스)·이상 요약·일자별 표 렌더.

### 5.2 책임자 모드 (사번 없이)

- 진입: 메인 앱에 manager 로 로그인하면 `is_admin_mode(request)==True`
  (`get_current_user` + `has_access_level(user,"manager")`).
- `attendance_page`(`pages.py` 204)는 `emp_id` 없어도 admin 이면 화면 허용.
- 순수 책임자(자기 사번 세션 없음)는 `/me` 호출 시 `ATTENDANCE_EMP_REQUIRED_IN_ADMIN_MODE(400)` —
  대신 직원 선택 UI 사용:
  - `GET /admin/employees?month=` (드롭다운, `require_irms_manager`)
  - `GET /admin/view?emp_id=&month=` (임의 직원 조회, **감사 로그 기록**
    `attendance_viewed_by_admin`).
- 프론트: `att-admin-picker`(select + 사번 직접입력). 책임자 모드에선 본인 비번 변경 대상
  없음 안내.

### 5.3 관리자 감사 로그

- 근태 관련 감사 액션: `attendance_viewed_by_admin`(조회), `attendance_password_reset`(재발급),
  `attendance_login_failed`(로그인 실패, 사유 포함) — 모두 `db.write_audit_log`.
- 열람: `GET /api/admin/audit-logs`(`admin_routes.admin_list_audit_logs`, 책임자 전용) +
  `admin_users.html` 화면. 근태 사용자 목록은 `GET /api/attendance/admin/users`
  (잠금/실패횟수/마지막 로그인 표시).

---

## 6. 공개 알림 API 경계 (내부망/토큰)

라우터: `public_attendance_alert_routes.build_router()` → prefix `/public/attendance-alerts`
(전체 경로 `/api/public/attendance-alerts/...`). **로그인 없음**(읽기 전용 + 같은 망이면
근태 화면으로 이미 보이는 데이터).

- `GET /today` — `detect_today_anomalies(현재월, 오늘)`.
- `GET /month` — `alert_year_month()`(현재월 파일 없으면 최신 가용월) →
  `detect_month_anomalies`. 트레이가 쓰는 실제 엔드포인트.

경계 방어(2겹, `main.py` 미들웨어 순서):
1. `InternalNetworkOnlyMiddleware`(`internal_only.py`): 보호 prefix
   (`/api/public/attendance-alerts` 등) 요청의 클라 IP 가 사설 대역
   (127/8, 10/8, 172.16/12, 192.168/16, ::1, fc00::/7)이 아니면 403 `INTERNAL_NETWORK_ONLY`.
   `X-Forwarded-For` **의도적으로 무시**(리버스 프록시 없음 전제).
   - 선택적 토큰: `X-IRMS-Tray-Token` == 설정 토큰(`hmac.compare_digest`)이면 통과.
     `require_api_token=True` 면 IP 무관 **토큰 필수**(사설 IP 통과 무효화).
     트레이는 `config.tray_api_token` 를 헤더로 부착(`attendance_alerts._poll_once`).
2. `LoginOriginMiddleware`(`login_origin.py`): 근태/배합/관리 로그인 3경로는 CSRF 토큰
   면제라(토큰 받기 전 호출), 대신 교차 출처 POST(Origin≠Host)를 403
   `CROSS_ORIGIN_LOGIN_BLOCKED` 로 막는다(감사 F-10 로그인 CSRF 방어).
   Origin 없으면(비브라우저) 통과. `IRMS_TRUSTED_ORIGINS` 로 추가 허용.

CSRF 면제 목록(`main.py` 43–60): `/api/attendance/login`(토큰 전 로그인),
`/api/attendance/logout`(sendBeacon 은 커스텀 헤더 못 붙임, 멱등이라 위험 낮음).

---

## 7. 갭 헌트 (BUG / GAP / POLISH)

> 판정 규칙·헤더 매핑·계정/엑셀 사번 일치·알림 오탐/누락·잠금 우회 관점.
> 각 항목은 코드·테스트로 확인했고, 심각도는 현장 운영 맥락 기준(트레이는 09/13/16만
> 폴링, 공용 PC)으로 매겼다.

### BUG

- **BUG-1 ✅ 해결(2026-07-22) 야간조 자정 넘김 — 퇴근 baseline 조기 통과.**
  `anomaly._baseline_has_passed` 가 날짜 지름길(`row_date < reference.date()` → 무조건
  통과) 대신 **근무일 자정 + baseline + 유예의 실제 datetime** 과 `reference` 를 직접
  비교하도록 수정. 2교대(야간) 퇴근 baseline 27:45(익일 03:45)가 익일 날짜로 정확히
  걸쳐, 근무일 D 의 야간행을 D+1 새벽 02:00 에 평가해도 `퇴근 누락` 오탐이 나지
  않는다(04:00 도래 시에만 감지). 회귀 테스트 `test_attendance_night_shift.py`.
  (구 증상: baseline 이 1440 이상인데 날짜만 보고 조기 통과 → 웹 새벽 열람 오탐.)

- **BUG-2 ✅ 해결(2026-07-22) 숫자형 사번 셀 → 소수/문자열 불일치.**
  `parser._cell_str` 가 정수형 `float`(171013.0)을 정수 문자열로 낮추고, 조회 측
  (`summary.employee_exists_in_any_month` / `employee_profile_from_any_month` /
  `_load_month_rows_for_employee`)이 비교 양쪽을 공용 `models.normalize_emp_id`
  (strip + 정수형 실수 → 정수 문자열)로 통과시켜, 엑셀 사번이 숫자형이든 문자열이든
  로그인 사번 `"171013"` 과 일치한다. 문자형 사번(앞자리 0)은 보존. 회귀 테스트
  `test_attendance_excel_emp_id.py`. **단, DB 계정 조회(`attendance_auth._fetch`)는
  정확 일치라 조회측 정규화 범위 밖** — 아래 §7 주 참조.

### GAP

- **GAP-1 ✅ 해결(2026-07-22) 헤더 부분 이동 시 조용한 오매핑.**
  `parser._build_column_map` 이 필수 8필드는 잡혔지만 알려진 선택 열(예 `외출시간`,
  `조출`)이 헤더에서 검출되지 않아 구 기본 인덱스로 폴백되면 `(colmap, warnings)` 의
  warnings 에 폴백 필드 목록을 담아 반환하고, `_make_column_map` 이 이를 서버 로그로
  경고한다. 임포트는 실패시키지 않고 헤더로 잡히는 데이터는 그대로 파싱한다. 필수
  미충족의 통째 폴백(정상 시나리오)은 경고 없음. 회귀 테스트
  `test_attendance_excel_column_map.py::ColumnMapWarningTests`.

- **GAP-2 ✅ 해결(2026-07-23) baseline 함수 이중화 — 2교대 부분휴가 불일치 함정.**
  `_compute_anomaly_baseline`(`anomaly.py:120`)을 실제 함수
  `_compute_row_anomaly_baseline`(205)에 **위임하는 얇은 어댑터**로 통합했다. 최소
  `AttendanceRow` 를 구성해 후자를 호출하므로, 2교대 반차/반반차에 전자를 잘못 호출해도
  baseline 이 동일하게 이동해 조퇴/지각 오탐이 나지 않는다(오용 불가). 둘 다 export 유지
  (기존 `day_type` 테스트 caller 보존). 회귀 테스트
  `test_attendance_shift2_partial_leave.py::test_both_baseline_entry_points_agree_on_shift2_partial_leave`.

- **GAP-3 ✅ 해결(2026-07-23) 죽은 403 처리 제거.**
  정책은 그대로 소프트(배너·"나중에 변경", 조회 비차단)로 두고, 도달 불가였던
  `attendance.js` 의 403 `PASSWORD_RESET_REQUIRED` 처리 분기만 제거했다(서버의 어떤
  조회 엔드포인트도 그 코드를 반환하지 않음 — grep 확인). 서버 동작을 설명하는 주석을
  남겼다. 하드 게이트가 필요해지면 `require_view_context` 에서 발급직후 상태를 403 으로
  막고 이 분기를 되살리면 된다.

- **GAP-4 ✅ 해결(2026-07-23) 로그인 JS 의 도달 불가 에러 매핑 제거.**
  `attendance_login.js` 의 `EMP_NOT_IN_EXCEL` 매핑을 제거했다. 로그인
  (`authenticate`)은 계정 없음/미프로비저닝/엑셀에 없는 사번을 전부
  `INVALID_CREDENTIALS` 로 통일 응답한다(§4.1 보안 계약, grep 으로 `EMP_NOT_IN_EXCEL`
  이 `attendance_auth.reset_password_to_temporary` 재발급 경로에서만 나옴을 확인).
  서버 계약을 설명하는 주석을 남겼다.

- **GAP-5 ✅ 해결(2026-07-23) 연차 버킷 오전/오후 오분류.**
  `summary.HALF_DAY_LEAVE_KEYWORDS` 를 `("반차",)` 로 좁혀, 단독 "오전"/"오후"
  문구만으로는 반일(0.5)로 잡지 않는다. 감지 엔진(`anomaly._partial_leave_shift`,
  반차/반반차만 부분휴가로 인식)과 정합. "오전반차"/"오후반차"/"반차 오전" 등은 모두
  "반차"를 포함하므로 그대로 잡히고, 반반차는 먼저 0.25로 걸러진다. 회귀 테스트
  `test_attendance_annual_leave.py::test_full_day_leave_with_am_pm_in_note_is_not_half`
  (기존 케이스 전부 green 유지).

### POLISH

- **POLISH-1 ✅ 해결(2026-07-22) 비밀번호 변경 화면 "4자 이상" 표기.**
  `attendance_change_password.html` 라벨을 "8자 이상"으로, 두 입력의 `minlength` 를
  `8` 로 수정해 서버 정책(`MIN_PASSWORD_LENGTH=8`, Pydantic `min_length=8`)과 일치.

- **POLISH-2 ✅ 해결(2026-07-22) 로그인 placeholder "초기값은 사번과 동일".**
  `attendance_login.html` placeholder 를 "임시 비밀번호는 책임자에게 발급받으세요"로
  교체 — 랜덤 임시비번 발급 흐름 및 같은 화면 하단 안내와 정합.

- **POLISH-3 ✅ 해결(2026-07-23) 2교대 테스트 docstring 휴식값 정정.**
  `tests/test_attendance_shift2_partial_leave.py` docstring 을 "휴식 45분 / 주간 15:45
  / 야간 27:45" 로 수정해 실제 CASES·코드(`SHIFT2_BREAK_MINUTES=45`)·`anomaly.py` 본주석과
  일치시켰다.

- **POLISH-4 ✅ 해결(2026-07-23) 코드 문자열 하드코딩(유니코드 이스케이프) 가독화.**
  `anomaly._row_alert_category` / `_hide_detail_issue_text` 의 `\uXXXX` 이스케이프를
  읽을 수 있는 한글 리터럴로 치환(파일은 UTF-8 유지). 문자열 값은 동일해 동작 무변경 —
  `_unprocessed_row_issues` 가 만드는 라벨과 정확히 일치(테스트로 검증).

---

## 8. 검증 불가/미확인 항목

- 실제 `C:\ErpExcel\*.xlsx` 파일은 저장소에 없어 **운영 헤더 실물**을 직접 못 봤다.
  헤더 매핑은 `test_attendance_excel_column_map.py` 가 재현한 2026-06 헤더로만 검증됨.
  BUG-2(숫자형 사번)·GAP-1(부분 이동)의 실발생 여부는 실물 파일 확인 필요.
- **BUG-2 현장 이슈(사번 221023 로그인 불일치) 범위 주의**: 이번 조회측 정규화는
  **엑셀 매칭**(존재 확인·프로필·행 로드)만 치유한다. 즉 엑셀 셀이 `221023.0` 로 들어와
  재발급(`employee_exists_in_any_month`) 404 나 프로필/행 누락이 나던 경로는 해결된다.
  로그인 자체는 `attendance_auth._fetch` 가 `attendance_users.emp_id` 와 **정확 일치**로
  조회하므로, 계정이 사람이 타이핑한 `"221023"` 로 저장돼 있으면 원래 정상이다. 반대로
  **저장된 계정 값 자체가 `"221023.0"` 등으로 오염**된 경우(과거 버그성 프로비저닝)는
  조회측 정규화만으로는 치유되지 않고 DB 값 마이그레이션 또는 `_fetch` 정규화가 별도로
  필요하다. 다만 프로비저닝은 관리자 입력값(스트립된 `"221023"`)을 쓰므로 이 오염
  시나리오는 실무상 드물다.
- 트레이 알림의 실제 팝업 렌더(`attendance_popup.py`)는 이 리뷰 범위 밖(알림 채널까지만).
- `IRMS_TRUSTED_ORIGINS`·`REQUIRE_TRAY_API_TOKEN` 운영 설정값은 저장소에 없어(런타임 env),
  공개 API 가 실제로 토큰 필수 모드인지 IP 모드인지는 운영 PC 에서만 확정 가능.
- 테스트는 hermes venv 로 돌린다는 메모리(`project_test_env_flaky`)가 있으나, 본 작업은
  읽기 전용이라 실제 pytest 는 수행하지 않았다(지시 범위).
