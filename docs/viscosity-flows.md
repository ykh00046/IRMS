# 점도(Viscosity) 도메인 흐름 문서

> 합성 점도 등록·추세·이상 분석 기능의 동작을 코드 근거와 함께 정리한 문서.
> 인용은 `파일:함수` 또는 `파일:라인` 형식이며, 모두 실제 코드에서 확인했다.
> 작성 시점 기준 파일: `src/routers/viscosity_routes.py`,
> `src/services/viscosity_service.py`, `src/routers/public_viscosity_reminder_routes.py`,
> `src/routers/blend_routes.py`, `src/routers/models.py`,
> `templates/viscosity.html`, `static/js/viscosity.js`, `static/js/viscosity_lib.js`,
> `tray_client/src/viscosity_alerts.py`, `scripts/import_viscosity.py`,
> `tests/test_viscosity.py`.

관련 파일 목록(절대경로):
- `C:\X\IRMS\src\routers\viscosity_routes.py`
- `C:\X\IRMS\src\services\viscosity_service.py`
- `C:\X\IRMS\src\routers\public_viscosity_reminder_routes.py`
- `C:\X\IRMS\src\routers\blend_routes.py`
- `C:\X\IRMS\src\routers\models.py`
- `C:\X\IRMS\templates\viscosity.html`
- `C:\X\IRMS\static\js\viscosity.js`
- `C:\X\IRMS\static\js\viscosity_lib.js`
- `C:\X\IRMS\tray_client\src\viscosity_alerts.py`
- `C:\X\IRMS\scripts\import_viscosity.py`
- `C:\X\IRMS\tests\test_viscosity.py`

---

## 1. 등록 흐름

### 1.1 등록 경로는 배합 기록 연계로 일원화

화면(`templates/viscosity.html`)에는 자유 입력 폼이 없다. 사용자는 아래 배합 기록 표에서
**미등록 LOT 행을 고르고 점도값만 입력**한다(`viscosity.html:68-125`, `visc-form`).
저장은 배합 실적 연계 라우트로 간다.

- 프런트: `submitReading` → `POST /blend/records/{recordId}/viscosity`
  (`static/js/viscosity.js:540-574`). 본문은 `{ viscosity, memo }`뿐이다
  (`BlendViscosityBody`, `src/routers/models.py:246-250`).
- 백엔드: `blend_add_viscosity` (`src/routers/blend_routes.py:408-461`).
  - 배합 기록의 `product_name`으로 점도 제품을 자동 확보한다
    (`viscosity_service.ensure_product_by_code`, 아래 7절).
  - `lot_no`는 배합 기록의 `product_lot`, `measured_date`는 배합 기록의 `work_date`,
    `recipe_material`은 `product_name`, `material_lot`은 첫 자재의 LOT,
    `reactor`는 배합 실적의 `reactor`를 그대로 물려받는다
    (`blend_routes.py:429-444`). 반응기·측정일은 **점도 화면에서 입력하지 않는다**.
  - 감사 로그 `blend_viscosity_link` 기록 후 커밋.

또 하나의 등록 경로가 코드상 존재한다: `POST /viscosity/readings`
(`viscosity_routes.py:84-149`, `ViscosityReadingBody`). `product_id`·`lot_no`·`reactor`를
직접 받는 **수동/임포트용** 경로다. 라우트 주석은 "이 직접 등록 경로(수동/임포트)는 reactor를
선택적으로만 받는다"고 명시한다(`viscosity_routes.py:92-93`). 현재 화면 UI는 이 경로를 쓰지 않고
배합 연계 경로만 쓴다(`blend_routes.py:415-416` 주석: "UI는 점도 관리 화면 한 곳 — 이 라우트가
그 화면의 저장 경로").

### 1.2 measured_date = 작업일 규칙, 폴백은 1회만

측정일 결정 순서(`viscosity_routes.py:95-101`):
1. `body.measured_date`(명시값)
2. `viscosity_service.parse_lot_date(body.lot_no)` — LOT에서 날짜 추론
3. `local_today_text()` — 현장 로컬 '오늘'

배합 연계 경로에서는 `measured_date = record["work_date"]`로 항상 작업일이 채워진다
(`blend_routes.py:436`).

**폴백 1회 규칙(감사 F-9)**: `viscosity_add_reading`은 위에서 확정한 `resolved_date`를
`add_reading(measured_date=resolved_date)`로 그대로 넘긴다(`viscosity_routes.py:106-122`).
원본(`body.measured_date`=None)을 넘기면 서비스의 `add_reading`이 **같은 폴백을 다시** 돌려
(`viscosity_service.py:673`, `measured_date or parse_lot_date(lot_no) or date.today()`),
자정 경계에서 "A년 표본으로 판정 → B년으로 저장"이 될 수 있다. 이 폴백 이원화 방지는
`tests/test_viscosity.py:453-512`(`test_direct_registration_stores_the_date_used_for_judgement`)로
회귀 방지된다. 서비스 폴백은 UTC `date.today()`, 라우트 폴백은 로컬 `local_today_text()`인 점도
주의(`viscosity_routes.py:98-99` 주석, `viscosity_service.py:672` 주석) — 라우트가 먼저
로컬 기준으로 확정하므로 실사용 경로에서는 서비스 폴백이 발동하지 않는다.

`parse_lot_date`(`viscosity_service.py:30-67`)는 8자리 `YYMMDDSS`(PB, 하루 2로트)·6자리
`YYMMDD`(SBCT)·`datetime`/`date`·`YYYY-MM-DD[ ...]` 문자열을 인식하고, 그 외는 `None`.

### 1.3 중복 방지·삭제

- **중복**: `(product_id, lot_no)` UNIQUE 인덱스로 같은 LOT 재등록은 `IntegrityError` →
  409 응답(`viscosity_routes.py:123-127`, `blend_routes.py:445-449`).
- **삭제**: `DELETE /viscosity/readings/{id}`(`viscosity_routes.py`). 존재하지 않으면
  404, 성공 시 감사 `viscosity_reading_delete`. 화면에서는 관리자에게만 '삭제' 버튼이
  보인다(`viscosity.js:461-471`, `isManager`). **삭제는 mgr_router 라우트로, 정책 ⓑ 로
  책임자 권한이 서버에서 강제된다**(api.py include 의존성; 8절 GAP-1 해결).

---

## 2. 이상 판정

핵심 로직: `_control_limits`(`viscosity_service.py:237-264`)와
`_classify`(`viscosity_service.py:267-299`). 두 축을 결합한다.

### 2.1 중심선과 관리한계

- **중심선(center)**: `target`이 있으면 `target`, 없으면 표본 평균(`mean`)
  (`viscosity_service.py:242`). 테스트 `test_target_used_as_center_when_set`
  (`test_viscosity.py:155-161`).
- **표본표준편차(std)**: `n >= 2`일 때 `statistics.stdev`, 아니면 `0.0`
  (`viscosity_service.py:241`).
- **관리한계**: `center`가 있고 `std > 0`일 때만 산출(`viscosity_service.py:246-250`).
  - UCL/LCL = center ± `sigma_k`·σ (제품별 `sigma_k`, 기본 3)
  - UWL/LWL = center ± `WARN_SIGMA`·σ (`WARN_SIGMA = 2.0` 고정, `viscosity_service.py:27`)

### 2.2 판정 규칙과 경계값

`_classify`(`viscosity_service.py:272-299`) 순서:
1. spec 위반(관리 상/하한): `value > upper_limit` → `spec_high`,
   `value < lower_limit` → `spec_low` (둘 다 **엄격 부등호**, 경계값=한계값은 위반 아님).
2. sigma 위반: `value > ucl` → `sigma_high`, `value < lcl` → `sigma_low` (엄격 부등호).
3. 위 중 하나라도 있으면 `status = "anomaly"`.
4. 없으면 경고 구간: `value > uwl`(2σ 초과) → `warn_high`, `value < lwl` → `warn_low`,
   `status = "warn"`.
5. 그 외 `status = "normal"`.

경계값 정리:
- 값이 정확히 상/하한(spec) 위: 위반 아님(normal 또는 warn).
- 값이 정확히 kσ(=UCL): `value > ucl`이 거짓이므로 anomaly 아님. 단 2σ 초과이면 **warn**.
- 값이 정확히 2σ(=UWL): `value > uwl`이 거짓이므로 warn 아님 → normal.

테스트: `test_sigma_anomaly_detected`(`test_viscosity.py:132-142`),
`test_spec_limit_anomaly`(145-152), `test_warn_zone`(164-172),
`test_normal_when_no_spec_and_low_variance`(175-181).

### 2.3 반제품 설정 항목

제품 단위 설정은 `viscosity_products` 테이블에 저장(`viscosity_routes.py:202-253`,
`ViscosityProductUpdateBody` `models.py:101-121`):
- `target`(목표=중심선), `lower_limit`/`upper_limit`(관리 상·하한),
- `sigma_k`(통계 관리계수 k, 1~6, 기본 3),
- `rpm`·`temperature`(측정 조건 — 매 측정 재입력하지 않고 제품당 1회 설정,
  `viscosity.html:214`),
- `remind_daily`(매일 알림 대상 토글, 6절),
- `is_active`(사용/중지).

**반응기 사용 여부(`use_reactor`)는 더 이상 점도 제품이 소유하지 않는다.** 소유가 레시피로
이전되어, PATCH 라우트는 본문의 `use_reactor`를 받아도 **무시**한다(`viscosity_routes.py:211-213`,
`248` 주석). 실제 값은 매칭되는 최신 `completed` 레시피의 `recipes.use_reactor`에서 읽고,
없으면 구 열을 폴백으로 쓴다(`_recipe_use_reactor` `viscosity_service.py:128-149`,
`_serialize_product` `152-170`). 화면에서도 "반응기 진행 여부는 레시피 관리에서 설정"이라고
안내한다(`viscosity.html:221`).

### 2.4 스펙 미지정 시 자기흡수(self-absorption) 한계 — 명시

`target`도 `lower/upper_limit`도 없는 제품은 중심선=표본 평균, 관리한계=평균±kσ로
**표본 자기 자신만으로** 판정한다(`viscosity_service.py:242`, `246-250`). 이때 구조적 한계가 있다:

- 이상값이 표본에 포함되면 그 값이 평균과 σ를 함께 끌어올려(자기흡수), 관리한계가 넓어지고
  자기 자신이 한계 안으로 들어와 이상으로 안 잡힐 수 있다.
- 표본이 한쪽으로 서서히 드리프트해도 중심선이 평균을 따라 움직여 편차가 흡수된다.
- 따라서 절대 기준이 필요한 반제품은 관리자가 `target`/`lower/upper_limit`(spec)을
  설정해야 한다. spec은 표본과 무관하게 절대 판정하므로 자기흡수를 받지 않는다
  (`test_spec_limit_anomaly`). 서비스 docstring도 이를 명시한다
  (`viscosity_service.py:3-10`).

`has_spec`(= lower/upper 중 하나라도 설정)이 overview 카드에 표시된다
(`_serialize_product` `viscosity_service.py:169`, `overview` `541-573`).

---

## 3. 추세 / 기간 분석

### 3.1 run / shift 경보 (Western Electric 부분집합)

`_trend_alerts`(`viscosity_service.py:302-348`), 파라미터
`RUN_LENGTH = 5`, `SHIFT_LENGTH = 7`(`viscosity_service.py:25-26`).
- **run**: 시계열 끝에서부터 연속 단조 상승/하락이 5회 이상이면 `run_up`/`run_down`
  (테스트 `test_run_up_trend` `test_viscosity.py:185-192`).
- **shift**: 중심선 한쪽으로 연속 7회 이상 치우치면 `shift_high`/`shift_low`
  (`center is not None`일 때만).

라벨: `TREND_LABEL`(`viscosity_lib.js:34-39`), 배너 렌더 `renderTrendBanner`
(`viscosity.js:217-228`) — 추세가 하나라도 있으면 상단 배너 표시.

### 3.2 기간 버킷 (일~연)

`_period_key`(`viscosity_service.py:351-381`):
- `day` → `2026-03-15`, `week` → `2026-W11`(ISO 주차), `month` → `2026-03`,
  `quarter` → `2026-Q1`, `year` → `2026`.
- 모든 키는 사전식 정렬 = 시간순 정렬. 알 수 없는 granularity는 분기로 폴백
  (`test_period_key_day_and_week` `test_viscosity.py:230-239`).

`summarize_periods`(`viscosity_service.py:384-413`)가 버킷별 건수·평균·σ·min/max·이상수·경고수와
전기 대비 평균변화(`mean_delta`)를 계산.

**화면 표시 상한(최근 60개 구간)**: 서버는 전체 버킷을 반환하지만, 프런트
`renderPeriods`(`static/js/viscosity.js`)는 `PERIOD_DISPLAY_CAP = 60`으로 **최근 60개
구간만** 표·차트에 그린다. `periods`는 오름차순(오래된→최신)이라 끝에서 60개를 잘라
(`slice(-60)`) **차트는 그대로 시간순(왼→오)** 유지, **표는 뒤집어 최신 구간을 위로**
보여준다. '일' 단위 + 연도=전체에서 버킷이 수백~수천으로 불어나 표·차트가 무거워지는 것을
막는 조치. 60개를 넘겨 잘린 경우 표 맨 위에 안내 행 "최근 60개 구간만 표시 — 전체 구간은
Excel 내보내기를 이용하세요."(`td.visc-period-truncation`)를 표시하고, 전체 구간은 Excel
내보내기(6.2절)의 '기간 요약' 시트로 확인한다. 이상 행 강조(`row-anomaly`)는 유지.

### 3.3 기간 경보: anomaly_spike / mean_shift와 2026-07-22 완화

`_period_alerts`(`viscosity_service.py:416-451`):
- **anomaly_spike**: 직전 기간 대비 이상 건수가 2건 이상이고 더 늘어난 기간
  (`p["anomaly_count"] >= 2 and > prev`). granularity 제한 **없음** — 일/주에서도 계산.
- **mean_shift_up/down**: 전기 대비 평균변화 절대값이 전체 σ 이상인 기간.
  **월/분기/연도에서만** 계산한다(`coarse = granularity in ("month","quarter","year")`,
  `viscosity_service.py:427`, `439-449`).

완화 배경(코드 주석 `viscosity_service.py:421-425`): 일/주 단위는 구간당 측정이 1~2건이라
평균이 사실상 개별 측정값이고 정상 등락(±1σ)이 전부 경보로 잡히는 과민 문제가 있었다
(2026-07-22 현장 보고: 46.8~49.8 정상 범위에서 경보 18건). 테스트
`test_period_mean_shift_alert`(`test_viscosity.py:305-321`)는 분기 단위로 검증.

화면 경보 배너는 최신 알림 1건만 보이고 나머지는 "외 N건"으로 접는다
(`renderPeriodAlerts` `viscosity.js:230-248`) — 상세는 아래 기간별 표의 '전기 대비' 열 참조.
라벨: `PERIOD_ALERT_LABEL`(`viscosity_lib.js:40-44`).

---

## 4. 화면 상태 규칙

- **빈 선택으로 시작**: 반제품 select 첫 옵션은 placeholder "— 반제품 선택 —"
  (`renderProductSelect` `viscosity.js:111-137`). 고르기 전에는 카드·표가 빈 안내 상태
  (`showEmptyState` `86-109`). 사용자 요청 2026-07-22("-선택-", `viscosity.js:117-118`).
- **선택 = 표시 일치**: 초기 로드와 select 변경이 같은 경로(`selectProduct` → `loadProduct`)를
  타서 불일치를 원천 차단(`loadOverview` `53-70`, 주석 `72-73`).
- **상태 열**: 배합 기록 표에 값 옆 배지 대신 **별도 '상태' 열**을 상시 표시
  (`appendStatusCell` `viscosity.js:409-430`, 표 헤더 `viscosity.html:99`). 이유: 수십 행에서
  값 옆 배지가 안 보인다는 현장 요청. 배합 연계 측정에는 판정이 없어, 재조회한 분석에서
  같은 LOT의 status를 찾아 붙인다(`findReadingByLot`/`statusForLot` `viscosity.js:514-528`).
- **경고 배너 접기·위치**: 추세 배너(`visc-trend-banner`)와 기간 경보 배너(`visc-period-alert`)는
  카드 아래·등록 패널 위에 위치(`viscosity.html:60-66`), 알림 없으면 `hidden`. 기간 경보는
  최신 1건 + "외 N건" 접기(3.3절).
- **등록 폼 활성 조건**: 선택 배합 기록에 연계 측정이 **없을 때만** 제출 활성
  (`renderSelectedBlend`/`setSubmitEnabled` `viscosity.js:475-538`). 이미 등록된 행은 잠긴다.
- **신규 등록 즉시 판정 피드백**: 등록 후 재조회해 이상/경고면 폼 결과 배너 + `notify`로 알림
  (`warnNewReading` `viscosity.js:582-606`), 정상이면 성공 알림.

---

## 5. 반응기 필터

- 서버: `analyze_product(..., reactor=)`가 `_fetch_readings`의 `reactor` 절로 해당 반응기
  표본만 집계(`viscosity_service.py:189-192`, `474-537`). `available_reactors`로 실제
  기록 있는 반응기 목록 제공(`223-234`). 라우트는 reactor가 1~4가 아니면 None으로 무시
  (`viscosity_routes.py:78-79`).
- 프런트: 제품이 **반응기 진행(`use_reactor`=true)일 때만** 툴바 반응기 필터를 노출
  (`renderReactorControls` `viscosity.js:171-194`, 옵션 전체/반응기 1~4). 반제품을 바꾸면
  반응기 필터를 초기화(`selectProduct({resetReactor:true})` `viscosity.js:132-136`).
- 테스트 `test_reactor_stored_and_filtered`(`test_viscosity.py:80-106`).
- 등록 시 반응기는 배합 실적에서 물려받으므로 점도 화면에서 직접 입력하지 않는다
  (`blend_routes.py:443`, `viscosity.js:557`).

---

## 6. 매일 알림(트레이 리마인더) · Excel 내보내기

### 6.1 매일 측정 리마인더

- 서버 쿼리 `daily_reading_reminders`(`viscosity_service.py:581-649`): `is_active=1` AND
  `remind_daily=1`인 반제품 중, **오늘(target_date) measured_date로 등록된 측정이 없는**
  (`NOT EXISTS ... today.measured_date = ?`) 대상만 반환. `codes`는 선택적 추가 필터일 뿐,
  비면 알림 대상 전체(서버 주도).
- 공개 API: `GET /api/public/viscosity-reminders/due?target_date=`
  (`public_viscosity_reminder_routes.py:27-43`). `target_date` 기본값=오늘.
- 트레이: `ViscosityAlertPoller`(`tray_client/src/viscosity_alerts.py`)가 근태와 동일하게
  정해진 시각 슬롯(09/13/16시)당 1회만 팝업(`24-28`). 앱 재시작 시 이미 지난 슬롯은
  처리된 것으로 표시(`stale_slot_key_on_startup`, `65-67`). 같은 슬롯·같은 대상(서명 일치)이면
  중복 팝업 억제(`_poll_and_notify` `102-117`). 대상 목록은 로컬에 두지 않고 서버(`remind_daily`)가
  소유(`119-121` 주석).

### 6.2 Excel 내보내기 (xlsx)

- `GET /viscosity/products/{id}/export?granularity=&year=&reactor=`(`viscosity_routes.py`
  `viscosity_export`), 관리자 UI 버튼 "Excel 내보내기"(`viscosity.html:25`, `exportCsv`
  `viscosity.js`). mgr_router 라우트 — 정책 ⓑ 로 책임자 전용. `granularity`/`year`/`reactor`
  를 화면 상태 그대로 받아 화면과 같은 표본·단위로 status·기간 집계를 계산한다(8절 GAP-2 해결).
  `granularity`는 허용 집합(`day/week/month/quarter/year`) 밖이면 `quarter`로, `reactor`는
  1~4 밖이면 무시(상세 라우트와 동일 검증).
- 출력은 openpyxl `Workbook` 두 시트:
  - **"측정 원본"**: 화면 배합 기록 표와 같은 필드(한글 헤더) — LOT · 측정일 · 점도 · 판정
    (정상/경고/이상 라벨) · 반응기 · 메모 · 배합 원료 · 원료 LOT · 작성자.
  - **"기간 요약"**: 요청 단위/연도/반응기의 **전체** 기간 집계(화면은 최근 60개만 표시하나
    Excel 은 전체). 컬럼: 기간 · 건수 · 평균 · 전기대비 · 표준편차 · 최소 · 최대 · 이상 · 경고
    (`analyze_product`의 `periods` 그대로).
- 파일명 `viscosity_{code}_{yyyymmdd}.xlsx`, content-type
  `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`.
- 수식 인젝션 방지: `=`,`+`,`-`,`@`,`\t`,`\r`로 시작하는 문자열 셀은 앞에 `'` 부착
  (`_xlsx_safe` `viscosity_routes.py`).
- 회귀 방지: `test_viscosity.py::test_viscosity_export_status_uses_year_filter`(연도 필터가
  '측정 원본' 시트에 반영), `::test_viscosity_export_xlsx_structure_and_period_rows`(PK 매직
  바이트 + 두 시트 이름 + '기간 요약' 행 수 = periods 수 + 파일명 규칙).
- (구 CSV writer 경로는 제거됨 — 이 엔드포인트의 유일한 소비자는 화면 버튼이다.)

---

## 7. 제품(반제품) 자동 생성 규칙

두 갈래로 나뉜다.

- **자동 생성(배합 연계)**: `ensure_product_by_code`(`viscosity_service.py:107-125`). 배합 기록에
  점도를 처음 등록할 때 `product_name`(코드/이름 동일)로 조회하고, 없으면 spec 미설정 상태로
  즉시 INSERT(`is_active=1`, target/limit 없음). recipe 존재 검증·중복 정밀검사 없음(코드 정확
  일치만). 임포트 스크립트도 같은 방식으로 `_ensure_product`
  (`scripts/import_viscosity.py:78-87`).
- **수동 생성(설정 화면)**: `POST /viscosity/products`(`viscosity_routes.py:152-200`).
  - 코드 중복 시 409(`get_product_by_code`),
  - **레시피에 `product_name = code`가 존재해야 함**(없으면 400,
    `viscosity_routes.py:164-172`). 자유 입력 금지 — 코드는 레시피 제품명과 연동되는 키.
  - 프런트는 후보(점도 반제품이 아직 없는 완성 레시피 제품명)에서만 고르게 강제
    (`loadRecipeCandidates`/`createProduct` `viscosity.js:642-768`, `viscosity.html:236-242`).
  - 용도: "첫 배합 전에 기준값·반응기 필수를 미리 세팅"(`viscosity_routes.py:161-163`).

즉 자동 생성은 spec 없는 껍데기를 만들고, 관리자가 나중에 설정 화면에서 target/limit/알림 등을
채운다.

---

## 8. 갭 헌트 (BUG / GAP / POLISH)

### GAP-1 (보안/권한) — manager 전용 점도 변경이 서버에서 강제되지 않음 — ✅ 해결(2026-07-22, 정책 ⓑ)
`viscosity_mgr_router`(제품 생성·수정, 측정 삭제, Excel export)에 이제 `api.py` include 시
`dependencies=[Depends(require_access_level("manager"))]` 을 걸어 **책임자 권한을 서버에서
강제**한다(`src/routers/api.py` viscosity_mgr_router include). op_router(열람·측정 등록)는
설계대로 개방 유지. 화면은 여전히 `can_manage`로 설정/삭제 버튼을 숨기므로 관리 세션에서는
강제가 투명하다. 비로그인/비책임자는 401/403(회귀 방지 `test_viscosity.py::
test_viscosity_manager_writes_denied_when_anonymous`, 개방 유지 `::
test_viscosity_reads_and_registration_stay_open`).
(과거 상태: 각 라우트가 `get_current_user(required=False)`로 actor만 기록하고 거부하지 않았다.)

### GAP-2 (판정 일관성) — CSV export의 status는 항상 전체 연도 기준 — ✅ 해결(2026-07-22)
`viscosity_export`가 이제 `year`·`reactor` 쿼리 파라미터를 받아 product 상세 라우트와 동일하게
`analyze_product(connection, product, year=year, reactor=reactor)`로 판정한다
(`viscosity_routes.py` viscosity_export). 프런트 `exportCsv`가 현재 `state.year`/`state.reactor`를
쿼리로 실어 보내(GET 내비게이션, CSRF 무관) 화면과 CSV 판정이 일치한다(`static/js/viscosity.js`
exportCsv). reactor 는 1~4 밖이면 무시. 회귀 방지 `test_viscosity.py::
test_viscosity_export_status_uses_year_filter`.
(후속 2026-07-23: 이 export 는 CSV → xlsx 로 전환되고 `granularity`도 함께 받게 되었다.
같은 필터 일치 원칙은 그대로이며, 판정은 이제 xlsx '측정 원본' 시트에 반영된다 — 6.2절.)

### GAP-3 (경보 과민 잔존) — anomaly_spike는 일/주 완화에서 제외되지 않음 — ✅ 해결(2026-07-22)
`_period_alerts`가 `anomaly_spike`도 `mean_shift`와 동일한 `coarse`(월/분기/연) 게이트 아래로
넣었다(`viscosity_service.py` `_period_alerts`). 일/주 단위에서는 anomaly_spike 가 더 이상 뜨지
않아 하루 이상 2건 몰림의 과민을 제거. 회귀 방지 `test_viscosity.py::
test_period_anomaly_spike_gated_to_coarse`.

### GAP-4 (중복 생성 여지) — 자동 생성은 코드 정확 일치만 검사 — ✅ 해결(2026-07-23)
`get_product_by_code`가 이제 `WHERE upper(code) = ?`(파라미터=`strip().upper()`)로 조회해
리마인더 쿼리(`daily_reading_reminders`의 `upper(p.code)`)와 **같은 strip+upper 정규화**를
쓴다(`viscosity_service.py` `get_product_by_code`). 자동 생성(`ensure_product_by_code`)이 이
정규화 조회로 기존 제품을 먼저 찾으므로, 대소문자·앞뒤 공백만 다른 `product_name`으로 배합
점도를 등록해도 같은 논리적 제품으로 귀결돼 별도 점도 제품이 생기지 않는다. 저장되는 코드
문자열 자체는 최초 등록값을 보존(레시피 제품명 키 유지). 회귀 방지 `test_viscosity.py::
test_ensure_product_by_code_normalizes_case_and_space`.
(과거: `code = ?` 정확 일치라 리마인더의 `upper()`와 정규화 기준이 어긋나 잠재 중복원이었다.)

### POLISH-1 — `_trend_alerts` run 루프의 죽은 가드 — ✅ 해결(2026-07-23)
`viscosity_service.py` `_trend_alerts`의 상승(up)·하강(down) 루프에서 죽은 `if down > 1: break`
/ 유효했으나 불필요하던 `if up > 1: break` 가드를 모두 제거하고, 두 방향을 **독립·대칭**으로
집계하도록 정리했다(말단 구간은 한 방향으로만 단조라 up/down 중 최대 하나만 RUN_LENGTH 이상).
판정 결과는 불변(순수 단조 증가/감소 tail 검출)이며 의도가 명확해졌다. `run_down` 단위 테스트를
추가했다(`test_viscosity.py::test_run_down_trend` — 종전엔 `run_up`만 있어 회귀 미검출 우려).

### POLISH-2 — sigma_k ≤ 2일 때 경고(warn) 구간 소멸 — ✅ 해결(2026-07-22)
`_control_limits`가 `sigma_k <= WARN_SIGMA`(2.0)이면 경고 밴드 자체를 만들지 않는다(uwl/lwl =
None, `viscosity_service.py:246-`). k ≤ 2 에서 kσ(UCL)가 2σ(UWL) 안쪽/동일이라 '경고가 이상보다
바깥'이 되는 역전을 원천 차단 — 이 경우 판정은 정상↔이상 2단계뿐(경고 없음)으로 명확해진다.
k ≥ 3 운영에서는 동작 불변. 회귀 방지 `test_viscosity.py::
test_warn_band_collapses_when_sigma_k_le_warn_sigma`.

### POLISH-3 — 리마인더에 근무일/휴일 개념 없음
`daily_reading_reminders`는 요일·공휴일을 구분하지 않는다(`viscosity_service.py:581-649`).
`remind_daily`인 반제품은 주말·휴일에도 "오늘 미측정"으로 잡혀, 트레이가 그 날 슬롯에 알릴 수
있다. 근태 알림과 달리 근무일 필터가 없다. 운영상 슬롯이 09/13/16시 근무시간대라 영향은
제한적이지만, 오탐 여지로 기록.

### 관찰(비결함)
- 등록 라우트의 즉시 판정(`classify_value`)은 year+reactor 표본으로 계산하나
  (`viscosity_routes.py:103-105`), 이어 반환하는 재분석은 year만 적용(reactor 없음,
  `viscosity_routes.py:138-140`). 화면은 어차피 재조회로 status를 다시 확정하므로
  (`viscosity.js:565-568`) 사용자 영향 없음.
- `granularity` 기본값이 서버는 `quarter`(`viscosity_routes.py:70`), 프런트 초기값은 `day`
  (`viscosity.js:39`). 프런트가 항상 쿼리로 넘기므로 실동작은 프런트값이 지배.
- 업데이트 모델 `lower_limit`는 `ge=0`(0 허용), 생성 모델은 `gt=0`
  (`models.py:104` vs `85`). 사소한 비대칭.

---

## 검증하지 못한 항목 (unverifiable)
- `viscosity_products` / `viscosity_readings`의 실제 스키마·마이그레이션(예: UNIQUE 인덱스,
  `blend_record_id`·`reactor` 컬럼 정의)은 마이그레이션 파일을 직접 열지 않아 코드 사용처로만
  추정했다(테스트 픽스처 `test_viscosity.py:16-54`가 스키마를 반영하나 실 DB와 동일 보장은 안 됨).
- `attendance_popup.build_viscosity_popup_payload`, `schedule.current_slot_key` 등 트레이
  공용 모듈의 슬롯 시각(09/13/16) 정의는 `viscosity_alerts.py`가 임포트만 하므로 실제 시각
  상수는 `schedule.py`에서 미확인.
- 런타임 실행·테스트 수행은 지시에 따라 하지 않았다(정적 코드 리뷰만). 따라서 위 GAP의 실제
  트리거 빈도(예: CSV/화면 판정 불일치 발생률)는 데이터 의존이라 수치 검증 불가.
