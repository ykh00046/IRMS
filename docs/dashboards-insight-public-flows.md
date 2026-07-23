# 대시보드 · 배합 분석 · 내부망 공개 API 흐름

> 조회·분석·연동 표면 문서. 배합 실적(`blend_records`/`blend_details`)과 점도 현황을
> 소비하는 **읽기 위주** 화면(`/dashboard`, `/insight`)과, 트레이 앱·상위 재고
> 대시보드가 소비하는 **내부망 공개 API**를 다룬다.
>
> 쓰기(배합 저장·증량 처리)·인증·근태·점도 등록·증량 계량 규칙 자체는 각자 문서
> (`records-dhr-flows.md`, `auth-session-flows.md`, `blend-weighing-flows.md` 등)가
> 다루므로 여기서는 **경계만** 표시한다. 모든 값 단위는 `g` 고정.
>
> 근거는 `파일:함수/라인` 으로 표기했고 실제 소스로 검증했다. (검증 기준일 2026-07-22)

---

## 0. 라우팅·보호 경계 요약

| 표면 | 마운트 | 인증/보호 | 소비자 |
|------|--------|-----------|--------|
| `/dashboard` 페이지 | `pages.py:dashboard_page` (L192) | **무로그인 개방** (`_app_page_response`, L81) | 사내 브라우저 |
| `/api/dashboard/*` | `api.py` L66 (`prefix="/api"`) | **없음** (조회 전용, 개방) | `dashboard.js` |
| `/insight` 페이지 | `pages.py:insight_page` (L188) | **무로그인 개방** | 사내 브라우저 |
| 배합 분석 API (`/api/blend/*`) | `api.py` L61 | **없음** (개방) | `insight.js` |
| 미확인 증량 카드/목록 (`/api/blend/rescales/unacked`, `.../rescale-ack`) | `api.py` L68 | **책임자(manager) 전용** (`require_access_level`) | `dashboard.js`, `/status` |
| `/api/blend/rescales/summary` | `api.py` L68 | **없음** (개방) | `/status` 배지·모달 |
| `/api/public/*` (4종) | `api.py` L51-53,69 | **internal_only 미들웨어** (사설 IP 또는 트레이 토큰) | 트레이 앱, 상위 재고 대시보드 |

> **핵심**: `/api/public/*` 4개 프리픽스만 `InternalNetworkOnlyMiddleware` 로 보호된다
> (`main.py:create_app` L63-73). 대시보드·배합 분석 API 는 **어떤 인증도 없이** 배합
> 실적 전체(작업자명·제품·물량·자재 LOT)를 반환한다. 앱 전체가 내부망 전제라 현재
> 정책상 개방이지만, 외부 노출(터널) 시 데이터 노출 표면이 된다 → §5 GAP 참조.

---

## 1. 운영 대시보드 (`/dashboard`)

`dashboard_routes.py:build_router` — 모든 지표는 배합 실적 + 점도에서 나온다. 구
계량 워크플로 지표(계량 편차 등)는 데이터가 쌓이지 않아 2026-07 전면 재구축(파일 상단
docstring L1-14). 배합은 편차 0 강제 저장이라 편차 지표 없음.

### 1.1 엔드포인트 전수

| 엔드포인트 | 함수(라인) | 데이터 소스 | 기간(range) | 비고 |
|------------|-----------|-------------|-------------|------|
| `GET /api/dashboard/summary` | `dashboard_summary` (L54) | `blend_records` + `viscosity_service` | `_parse_range` 기본 최근 7일 | KPI + 현재 상태 스냅샷 |
| `GET /api/dashboard/trend` | `dashboard_trend` (L95) | `blend_records` GROUP BY work_date | 기간 필수 | 빈 날짜 0 채움(`_daterange`) |
| `GET /api/dashboard/products` | `dashboard_products` (L123) | `blend_records` GROUP BY product_name | 기간 | `limit` 1~100(기본 10) |
| `GET /api/dashboard/workers` | `dashboard_workers` (L153) | `blend_records` GROUP BY worker | 기간 | 빈 worker→`(미기록)` |
| `GET /api/dashboard/recent` | `dashboard_recent` (L184) | `blend_records` + `viscosity_readings` EXISTS | **기간 무관** id DESC | `limit` 1~50(기본 10) |
| `GET /api/dashboard/export` | `dashboard_export_excel` (L222) | `dashboard_export.build_dashboard_excel` | 기간 | Excel 다운로드 |

`_parse_range`(L27): `from`/`to` 없으면 최근 7일. `from>to`→400 `INVALID_RANGE`,
파싱 실패→400 `INVALID_DATE`.

### 1.2 대시보드 카드 전수 표

프론트는 `dashboard.js:renderSummary`(L96) 가 채운다. 템플릿 `dashboard.html` L31-76.

| 카드(라벨) | 요소 id | 소스 필드 | 갱신 주기 | 권한 | 특이 |
|-----------|---------|-----------|-----------|------|------|
| 배합 건수 | `card-blend-count` | `summary.blend_count` | 기간 변경/새로고침 시 | 개방 | 기간 내 `status!='canceled'` COUNT |
| 총 배합량 | `card-weight` | `summary.total_weight_g` | 〃 | 개방 | SUM(total_amount) |
| 반제품 종류 | `card-products` | `summary.product_count` | 〃 | 개방 | COUNT(DISTINCT product_name) |
| 점도 이상 | `card-visc-anomaly` | `summary.viscosity_anomaly` | 〃 | 개방 | **기간 무관** — `viscosity_service.overview` 의 `total_anomaly`(최신 연도 스코프). 0 초과 시 빨강 |
| 오늘 점도 미입력 | `card-visc-due` (+ `-codes`) | `summary.viscosity_due_today[]` | 〃 | 개방 | **오늘 기준**(range 무관). `daily_reading_reminders(target_date=today)`. 코드 리스트 노출 |
| **미확인 증량** | `card-rescale-unacked` (+ 목록) | `/api/blend/rescales/unacked` `total`/`items` | `loadRescaleAlert` (L242), 페이지 로드 시 1회 | **책임자 전용** | 카드 자체가 `{% if can_manage %}` 로만 렌더(`dashboard.html` L61) → 비책임자는 DOM 부재로 401 호출 회피(`dashboard.js` L241 주석) |

**제거된 카드(문서화 목적)**: '결재 대기' 카드는 2026-07-15 제거(결재 현장 미사용).
단 **API 필드 `summary.approval_pending` 는 존치**(`dashboard_routes.py` L74-80, L90)
— 죽은 값이 계속 계산·반환된다. `dashboard.js` L101-105 는 요소가 있을 때만 채우는
방어 코드로 실질 미표시. → §5 POLISH.

**차트/표(카드 아님)**: 일별 배합 추이(`chart-trend`, trend), 반제품 TOP10
(`chart-products`, products), 작업자별 실적표(`workers-body`, workers), 최근 배합
기록표(`recent-body`, recent). recent 표는 점도('입력'/'미입력')·결재('완료'/'대기')
열 포함(`dashboard.js:renderRecent` L212).

### 1.3 미확인 증량 흐름(경계)

증량 계량 규칙·저장은 `blend-weighing-flows` 소관. 여기서는 **조회·확인 표면**만:

- 목록: `blend_rescale_ack_routes.py:list_unacked_rescales`(L56) — `rescale_unacked=1
  AND status!='canceled'`, work_date DESC. 책임자 전용.
- 확인: `ack_rescale`(L88) POST — `rescale_unacked=0` UPDATE + audit
  `blend_rescale_acked`. 이미 확인됨이면 멱등(`acked_already=True`).
- 요약(배지용): `rescales_summary`(L128) — `rescale_count>0`, **개방**, LIMIT 1000.
- ack 쓰기는 `IRMS._core.request` 가 `x-csrftoken` 부착(`dashboard.js` L287 주석).

---

## 2. 배합 분석 (`/insight`)

`insight.js` 컨트롤러 + `templates/insight.html`. 완료 배합 기록에서 자재/제품/배치를
분석. 백엔드는 `blend_routes.py` → `blend_service.py` 위임.

### 2.1 섹션·엔드포인트 전수

| 섹션(패널) | 프론트 로더 | 엔드포인트 | 서비스 함수 | 기간 필터 |
|-----------|------------|-----------|-------------|-----------|
| 자재별 사용량 | `loadMaterials` (L77) | `GET /api/blend/material-usage` (`blend_routes` L112) | `material_usage` (L341) | 적용 |
| 이상 통계(수동입력·취소) | `loadMistakes` (L237) | `GET /api/blend/mistake-stats` (L130) | `mistake_stats` (L436) | 적용 |
| 자재 LOT 추적 | `traceMaterialLot` (L39) | `GET /api/blend/material-lot-trace` (L200) | `trace_material_lot` (L580) | **무시(전 기간)** |
| 제품별 배합 빈도 | `loadProducts` (L169) | `GET /api/blend/product-usage` (L121) | `product_usage` (L391) | 적용 |
| 배치 상세 | `loadDetails` (L190) | `GET /api/blend/batch-details` (L139) | `batch_details` (L516) | 적용 + `product` |
| 배치 상세 Excel | `exportDetails` (L225) | `GET /api/blend/batch-details/export` (L152) | `batch_details(limit=10000)` | 적용 |

기간 프리셋: 최근 30일(기본)/90일/전체(`insight.js` L268-282). `loadAll`(L257) 이
자재→제품→상세→이상 순 로드.

### 2.2 상세 동작

- **자재별 사용량**(`material_usage` L341): 완료 기록만, `material_name` 그룹, 실제/이론
  SUM + `usage_count`(DISTINCT record). 상단 지표 3종(record_count/total_weight/
  material_count) 함께. 프론트가 비중(%) 계산.
- **이상 통계**(`mistake_stats` L436): 편차 강제로 편차 무의미 → 대신 **수동 입력
  (저울 미사용, manual_entry=1)** + **취소(canceled)** 를 신호로 집계. 작업자별(기록
  단위)·자재별(상세 행 단위). 수동 입력 0인 자재는 표에서 제외(L505).
- **자재 LOT 추적**(`trace_material_lot` L580): 리콜 대응 역추적. **부분 일치
  `LIKE %lot% ESCAPE`**, 취소 기록 포함(누락이 더 위험), `%_\` 이스케이프. LIMIT
  기본 500(1~2000). 서버가 `truncated`/`limit` 를 반환하며, 상한 도달 시 프론트가
  `#insight-trace-note` 안내를 띄운다(LOT 을 더 구체적으로 입력하도록 유도).
  제품 LOT 클릭→`/status?search=` 딥링크(`insight.js` L62).
- **제품별 빈도**(`product_usage` L391): 제품별 배치 수·총량·최근 작업일. Chart TOP10
  + 배치 상세 필터용 `<select>` 채움. 집계 결과(제품 종수 단위)라 표시 상한 불필요.
- **배치 상세**(`batch_details` L516): 자재별 비율·이론·실제·편차 평면 목록. work_date
  DESC. LIMIT 기본 2000, 최대 10000. Excel 은 10000 고정(`blend_routes` L161).
  **화면 렌더는 최근 200행만**(`insight.js` `DETAIL_DISPLAY_CAP`) — 200 초과 또는
  서버 `truncated` 시 `#insight-detail-note`("최근 200건만 표시 — 기간을 좁히거나
  Excel 내보내기를 이용하세요") 노출, 요약은 `표시 N / 전체 M행`. **Excel 은 전체
  (백엔드 상한 10000) 그대로 내보낸다** — 화면 200 캡과 무관.

> **표시 상한 정책(데이터 증가 대비)**: 자재별 사용량·이상 통계·제품별 빈도는
> **집계(그룹) 결과**라 행 수가 자재/작업자/제품 종수로 자연 제한되어 캡을 두지 않는다.
> 평면 목록(배치 상세·LOT 추적)만 최근 N 창 + 안내 문구(`.trunc-note`) + `표시 N / 전체 M`
> 를 적용한다. 안내 스타일은 점도 기간표(`.visc-period-truncation`)와 동일 톤.

---

## 3. 내부망 공개 API 전수 (⚠ 계약 변경 주의)

`main.py:create_app`(L63-73)의 `InternalNetworkOnlyMiddleware` 로 보호되는 4개
프리픽스. **상위 시스템(예: `C:\X\Dashboard-Raw_material` 재고 대시보드)과 트레이 앱이
소비하므로 경로·페이로드 변경 시 하위 호환을 반드시 유지**할 것.

### 3.1 보호 방식 (`middleware/internal_only.py`)

- 보호 대상: `/api/public/attendance-alerts`, `/api/public/material-usage`,
  `/api/public/viscosity-reminders`, `/api/public/rescale-alerts` (`main.py` L65-70).
- 개발/내부망: 사설 IP(`127/8, 10/8, 172.16/12, 192.168/16, ::1, fc00::/7`) 허용
  (`_is_private` L28) **또는** 유효 `X-IRMS-Tray-Token`.
- 운영: `REQUIRE_TRAY_API_TOKEN`(기본 `not IS_DEVELOPMENT`=운영 True, `config.py`
  L35) → **토큰 필수**, 없으면 403 `TRAY_TOKEN_REQUIRED`. 토큰은
  `IRMS_TRAY_API_TOKEN`(L34), 토큰 요구 시 미설정이면 부팅 실패(L47-49).
- 토큰 비교는 `hmac.compare_digest`(타이밍 세이프, L61).
- **주의**: `X-Forwarded-For` 를 의도적으로 무시(L38-42 docstring) — 역프록시 없음
  전제. 프록시가 추가되면 모든 클라이언트가 프록시 IP(사설)로 보여 IP 보호가 무력화됨.

### 3.2 엔드포인트 전수

| 경로 | 함수(파일) | 소비자 | 페이로드 |
|------|-----------|--------|----------|
| `GET /api/public/material-usage` | `material_usage` (`public_material_usage_routes.py` L35) | **상위 재고 대시보드** | `{start_date,end_date,group,unit:"g",record_count,total_weight,items:[{period,material_code,material_name,total_actual,total_theory,batch_count,(erp_code)}],truncated,total_item_count}` (truncated=상한 초과 시 items 절단됨) |
| `GET /api/public/material-usage/details` | `material_usage_details` (`public_material_usage_routes.py`) | **상위 재고 대시보드** — LOT 배정(FIFO 정리) | `{start_date,end_date,record_count,items:[{record_id,work_date,product_lot,product_name,worker,erp_code,material_code,material_name,material_lot,ratio,theory_amount,actual_amount,variance}]}` limit≤10000. 행 단위(투입 자재별 1행). erp_code 해석은 집계와 동일 체계 공유(+ERP 형태 저장 코드 5순위 fallback, 30a0db4) — **계약 변경 주의**(상위가 소비) |
| `GET /api/public/attendance-alerts/today` | `today` (`public_attendance_alert_routes.py` L23) | 트레이(근태) | `{date,day_type,total,items[]}` |
| `GET /api/public/attendance-alerts/month` | `month` (L45) | 트레이(근태) 폴러 | `{month,date,total,items[]}` |
| `GET /api/public/viscosity-reminders/due` | `due` (`public_viscosity_reminder_routes.py` L27) | 트레이(점도) | `{date,total,items[]}` |
| `GET /api/public/rescale-alerts` | `rescale_alerts` (`public_rescale_alert_routes.py` L29) | 트레이(증량) | `{count,items:[{id,product_name,product_lot,work_date,worker}]}` LIMIT 20 |

**material-usage 상세**(`blend_service.material_usage_periods` L268):
- 파라미터: `start_date`/`end_date`(YYYY-MM-DD, 기본 이달 1일~오늘), `group=total|day|month`,
  `by_product`(bool). 날짜 형식 `_DATE_RE` 검증(L23), start>end→400.
- `erp_code`: RM 품목코드(재고 시스템 매칭 키, `_resolve_erp_code` L314) — **상위
  시스템의 조인 키이므로 이 필드명/의미 변경은 상위 대시보드를 깨뜨림**.
- 완료(`status='completed'`) 기록만 집계.

**attendance-alerts**: 근태 이상은 월 엑셀 파일에서 계산(`attendance_excel`). 파일 없음
→404, 잠김→503, 형식 오류→500. 데이터가 이미 근태 페이지에 보여 무로그인 정당화
(파일 docstring L1-9).

### 3.3 상위 시스템 계약 체크리스트

경로 변경, 응답 키 이름 변경(특히 `erp_code`/`material_code`/`total_actual`), `unit`
가정(현재 `g`), `group` enum, 트레이 토큰 헤더명(`X-IRMS-Tray-Token`) — 이들 중 하나라도
바꾸면 상위 재고 대시보드/트레이 앱을 동반 수정해야 한다. 테스트: `test_public_
material_usage.py`, `test_public_rescale_alerts.py`.

---

## 4. 트레이 앱 채널 총괄 (v3.1.5)

`tray_client/` — pystray 통합 트레이(`src/main.py`). 알림(기본 켜짐) + 저울(기본
꺼짐, 로컬 전용). 설치본 `IRMS-Notice-Setup-3.1.5.exe`(README L34,106). 각 토글은
`%APPDATA%\IRMS-Notice\config.json` 저장.

| 채널 | 폴러(파일) | 소비 API | 스케줄/주기 | 토글(config) | 특이 |
|------|-----------|----------|-------------|--------------|------|
| 근태 이상 | `AttendanceAlertPoller` (`attendance_alerts.py`) | `/api/public/attendance-alerts/month` | **슬롯 09/13/16시** 각 1회 (`schedule.py`) | `attendance_alerts_enabled` (기본 켜짐) | 슬롯당 1회, 서명 중복 억제 |
| 점도 리마인더 | `ViscosityAlertPoller` (`viscosity_alerts.py`) | `/api/public/viscosity-reminders/due` | **슬롯 09/13/16시** | `viscosity_alerts_enabled` (기본 켜짐) | 대상 반제품 선택은 **웹이 소유**(`/viscosity` remind_daily) |
| 증량 미확인 | `RescaleAlertPoller` (`rescale_alerts.py`) | `/api/public/rescale-alerts` | **완만한 반복 폴링 기본 10분** (`DEFAULT_INTERVAL_SECONDS` L24) | `rescale_alerts_enabled` (기본 켜짐) | 슬롯 아님 — 미확인 남는 동안 매 주기 반복 나그(L61-65) |

- 활성 조건: 각 `_*_active`(`main.py` L180-186) = 채널 토글 AND `_alerts_enabled_today`
  ("현장 알림 오늘만 끄기" 자정 자동 복귀).
- 수동 '바로 확인': 근태/점도/증량 각각 트레이 메뉴에서 `trigger_once`(force) 즉시 폴링
  (`main.py` L216,306; `rescale_alerts.py` L53).
- 저울 연동은 조회·연동 표면 밖(로컬 HTTP 브릿지) — 이 문서 범위 아님.

---

## 5. 갭 헌트 (BUG / GAP / POLISH)

### 권한·정보 노출

- **[GAP] 대시보드/배합 분석 API 전면 무인증** — `/api/dashboard/*`,
  `/api/blend/material-usage|product-usage|batch-details|material-lot-trace|
  mistake-stats` 는 인증 의존성이 없다(`api.py` L61,66; 각 라우터에 `Depends` 인증
  없음). 작업자 실명(`dashboard_workers` L162), 제품·물량, 자재 LOT, 배치 상세가
  내부망 누구에게나 열린다. internal_only 보호는 `/api/public/*` 에만 적용
  (`main.py` L65-70). 현재 내부망 전제라 정책상 개방이나, 터널/외부 노출 시 즉시
  데이터 유출 표면. **정책 확인 필요**.
- **[GAP] `/api/blend/rescales/summary` 개방** (`blend_rescale_ack_routes.py` L128).
  unacked/ack 는 책임자 전용인데 요약(rescale_events 포함, LIMIT 1000)은 무인증.
  주석(L127)은 `/status` 무로그인 화면 소비를 이유로 의도한 개방이라 명시 — 정책상
  일관되나 증량 이벤트(작업자·사유 추정 가능) 노출 범위 재확인 권장.
- **[POLISH] ✅ 해소 — 죽은 API 필드 `approval_pending` 제거** — 결재 카드 제거(2026-07-15)
  후에도 `summary` 가 매 호출 계산·반환하던 죽은 값을 **쿼리·페이로드에서 제거**
  (`dashboard_routes.py`, `dashboard.js` 방어 블록도 삭제). 클라이언트가 읽지 않음을 확인 후 제거.
  회귀 가드: `test_dashboard_routes_respond_without_login` 이 `approval_pending not in body` 단언.
  ⚠ 참고: **다운로드 Excel 보고서**(`dashboard_export.py`)의 `결재 대기(전체)` 행은 별개 표면이라
  존치(요약 API 와 무관). `dashboard.html` L48 주석("API…는 존치")은 이제 낡음 — 템플릿 소유 배치가 정리.

### 무거운 쿼리 / 페이징

- **[GAP] `trace_material_lot` 무인덱스 LIKE 풀스캔** — `d.material_lot LIKE '%..%'`
  (`blend_service.py` L602)는 선행 와일드카드라 인덱스 사용 불가, `blend_details`
  전체 스캔. LIMIT 500 은 결과 컷일 뿐 스캔량은 무제한. 데이터 누적 시 느려짐.
- **[GAP] `material_usage`/`product_usage` 기간 필터 선택적·무페이징** — 프론트가
  '전체' 프리셋을 누르면 `start/end` 없이 전 기간 집계(`insight.js` L278-282 →
  `material_usage` L341, WHERE 에 날짜 없음). 그룹 집계라 행 수는 자재/제품 종류로
  bounded 이지만 스캔은 전 테이블.
- **[POLISH] ✅ 부분 해소 — `batch_details` 대량 반환에 truncated 표면화** — 기본 2000, 최대·Excel
  10000 행(`_BATCH_DETAILS_MAX_ROWS`)까지 한 번에 JSON/시트로 반환하는 것은 그대로이나, 결과가
  LIMIT 에 도달하면 **조용히 자르지 않고** `truncated: true`(+`limit`) 를 페이로드에 실고
  `logging.warning` 을 남긴다(`blend_service.batch_details`). 페이지네이션은 여전히 없음(현 데이터량엔
  무해). 테스트: `test_batch_details_truncation_flag`(`tests/test_dashboard.py`). ⚠ 이 플래그의 UI
  표시(insight 패널의 muted note)는 `insight.js`/`insight.html` 소유 배치 몫(본 배치 범위 밖).
- **[POLISH] `dashboard_summary` 매 호출 점도 전체 집계** — `viscosity_service.overview`
  + `daily_reading_reminders`(`dashboard_routes.py` L81-82)를 range 요청마다 재계산.
  기간 무관 값이라 캐시 여지.
- **[POLISH] ✅ 해소 — `material_usage_periods` by_product×day 조합 폭증 상한** — `group=day&by_product=true`
  로 넓은 기간을 요청하면 items=자재×제품×일수로 커지던 것에 상한(`_MATERIAL_USAGE_MAX_ITEMS`=5000)을
  뒀다. 넘으면 **조용히 자르지 않고** items 를 상한까지 자르되 `truncated: true`·`total_item_count`
  를 페이로드에 실고 `logging.warning`(기간·group·by_product 포함)을 남긴다(`blend_service.py`).
  추가 키는 additive 라 상위 재고 대시보드 계약 호환(§3.3). 테스트:
  `test_material_usage_periods_truncation_flag`(`tests/test_dashboard.py`).

### 공개 API 정보 과다 / stale

- **[GAP] material-usage `erp_code` 노출** — 내부 ERP 품목코드가 내부망 응답에
  포함(`blend_service.py` L314). 상위 시스템 조인 키라 의도된 것이나, 토큰 없는
  내부망 클라이언트 모두가 받는다(운영은 토큰 필수라 완화).
- **[BUG/GAP] internal_only 프록시 우회** — `X-Forwarded-For` 무시(`internal_only.py`
  L38-42). 역프록시/터널 뒤에 두면 모든 요청이 프록시 사설 IP로 보여 IP 게이트가
  전부 통과. 운영은 토큰 요구가 방어선이나, `IRMS_REQUIRE_TRAY_API_TOKEN=0` 으로
  끄면 그대로 뚫림. 프록시 도입 시 신뢰 설정 필수(코드 주석도 동일 경고).
- **[POLISH] ✅ 판정·문서화 — '점도 이상'·'오늘 점도 미입력' 카드는 의도된 현재상태(range 무관)** —
  **결론: 버그 아님. range 를 적용하지 않는 게 정상**이다. 근거:
  - '점도 이상'(`viscosity_service.overview.total_anomaly`)은 spec±σ 이상 판정을 **최신 연도**로 스코프한
    품질 현재상태 지표라, 임의 과거 기간에 재계산하는 것은 의미가 없다.
  - '오늘 점도 미입력'(`daily_reading_reminders(target_date=today)`)은 본질이 **오늘**의 미입력 알림이라
    range 를 붙이면 정의가 깨진다.
  - `dashboard_summary` 코드도 이 둘을 "현재 상태 스냅샷(기간과 무관한 '지금 해야 할 일')"으로 명시(주석).
  → **조치**: range-기반 KPI(배합 건수/총량/종류)와 섞여 오해되지 않도록, 두 카드는 이미 단위 라벨로
  현재상태 축을 표기한다 — '점도 이상'=`건 · 최신 연도`, '오늘 점도 미입력'=라벨 `오늘…` + `알림 대상 기준`
  (`dashboard.html` L50-57). 이 축 표기는 유지하며, 본 문서가 "의도된 현재상태(range 무관)"임을 못 박는다.
  (템플릿 문구를 `(전체 기간)` 으로 바꾸는 것은 부정확 — 각각 '최신 연도'/'오늘'이라 현 라벨이 더 정확.
  템플릿은 본 배치 소유 밖.)
- **[POLISH] ✅ 판정·문서화 — `dashboard_recent` 기간 무시도 의도됨** — '최근 배합 기록' 위젯은
  이름 그대로 **range 와 무관하게 항상 최신 id DESC**(`dashboard_routes.py` `dashboard_recent`, `limit`만
  받고 from/to 파라미터 자체가 없음)로, '최근 활동' 성격의 상시 최신 목록이다(패널 제목도 "최근 배합 기록
  · 전체 보기→/status"). 다른 range-기반 지표와 시점이 다른 것은 설계된 것 — 버그 아님, 문서로 확정.

---

## 6. 검증 불가 / 확인 필요

- **상위 재고 대시보드 실제 소비 형태** — `C:\X\Dashboard-Raw_material` 은 이 저장소
  밖이라 실제 요청 파라미터(`group`/`by_product` 사용 여부)·기대 스키마를 코드로
  검증 불가. `erp_code`/`unit`/키 이름 계약은 상위 코드 확인 필요.
- **운영 토큰 실제 배포 상태** — `IRMS_TRAY_API_TOKEN` 값·트레이 config 의
  `tray_api_token` 일치 여부는 런타임 환경이라 소스만으로 확인 불가.
- **`viscosity_service.overview`/`daily_reading_reminders` 내부 비용** — 점도 서비스는
  본 문서 범위 밖(점도 문서 소관). 대시보드가 소비한다는 사실만 확인.
