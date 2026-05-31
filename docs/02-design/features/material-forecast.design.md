# 자재 소모량 예측·발주 추천 — Design

| 항목 | 값 |
|------|------|
| Feature | `material-forecast` |
| Phase | Design |
| 작성일 | 2026-06-01 |
| 선행 | `docs/01-plan/features/material-forecast.plan.md` |

## 1. 아키텍처 개요

기존 2-tier(FastAPI + SQLite + Jinja2) 패턴을 그대로 따른다. 신규 코드는
순수 계산을 담당하는 **서비스 1개**, 조회/CSV를 담당하는 **라우터 1개**,
**관리 UI 탭 1개 + JS 1개**, **마이그레이션(컬럼 2개)**로 구성한다.

```
material_stock_logs (measurement deltas)  ──┐
materials (stock/lead/cycle/unit_type)    ──┤
                                            ▼
                          src/services/forecast_service.py   (순수 계산)
                                            ▼
                          src/routers/forecast_routes.py     (manager scope)
                              GET /forecast/materials
                              GET /forecast/export
                              PATCH /materials/{id}/forecast-params
                                            ▼
                          templates/management.html  탭 "소모예측·발주"
                          static/js/forecast.js
```

## 2. 데이터 모델 (마이그레이션)

`src/db/migrations.py`의 `apply_schema_migrations()`에 `ensure_column` 2개 추가
(기존 `material-stock-tracking` 블록 바로 아래). `schema.py`의 `CREATE TABLE materials`에도 동일 컬럼 추가.

| 컬럼 | 정의 | 의미 |
|------|------|------|
| `materials.lead_time_days` | `REAL NOT NULL DEFAULT 0` | 발주→입고 리드타임. 0이면 전역 기본(7일) |
| `materials.reorder_cycle_days` | `REAL NOT NULL DEFAULT 0` | 목표 커버리지(한 번 발주로 며칠 버틸지). 0이면 전역 기본(30일) |

신규 테이블 없음. 소모 이력은 기존 `material_stock_logs`를 읽기만 한다.
기존 인덱스 `idx_stock_logs_material(material_id, created_at DESC)`가 기간 집계를 커버한다.

## 3. 예측 알고리즘 (forecast_service.py)

### 3.1 전역 기본 상수

```python
DEFAULT_WINDOW_DAYS = 30        # 분석 기간
DEFAULT_LEAD_TIME_DAYS = 7      # 리드타임 fallback
DEFAULT_REORDER_CYCLE_DAYS = 30 # 목표 커버리지 fallback
SAFETY_FACTOR = 0.5             # soon 판정용 안전버퍼 (리드타임의 50%)
```

### 3.2 소모 집계 (SQL)

```sql
SELECT material_id, SUM(-delta) AS consumed
FROM material_stock_logs
WHERE reason = 'measurement'
  AND delta < 0
  AND created_at >= :cutoff      -- utc_now - window_days
GROUP BY material_id
```
`SUM(-delta)`로 음수 delta를 양의 소모량으로 변환. `reason='measurement'`만 집계해
입고/조정/폐기를 제외(순수 사용량). 취소 계량은 `reverse_measurement`가 로그를 삭제하므로 자동 제외된다.

### 3.3 자재별 계산

`materials`에서 `is_active=1 AND unit_type='weight'`만 대상(Plan §7-3). 각 자재에 대해:

```
avg_daily       = consumed / window_days           # consumed 없으면 0
lead            = lead_time_days  or DEFAULT_LEAD_TIME_DAYS
cycle           = reorder_cycle_days or DEFAULT_REORDER_CYCLE_DAYS
stock           = stock_quantity

if avg_daily <= 0:                                  # 소모 이력 없음
    status = "no_data";  days_remaining = None;  recommended_order = 0
else:
    days_remaining   = stock / avg_daily
    stockout_date    = today + days_remaining (일 단위 floor)
    reorder_point    = avg_daily * lead
    recommended      = max(0, avg_daily * cycle - stock)   # 발주 후 cycle일 커버
    if   days_remaining <= lead:               status = "urgent"
    elif days_remaining <= lead * (1+SAFETY):  status = "soon"
    else:                                      status = "ok"
```

> **0 나눗셈 방지**: `avg_daily<=0` 분기에서 `days_remaining`을 계산하지 않음(Plan 성공기준 §3).
> **음수 재고**: stock<0이면 days_remaining<0 → `urgent`로 자연 분류, recommended는 cycle 전량.
> **소진 예상일 클램프**: 잔여일수가 음수(이미 소진)면 과거 날짜 대신 오늘로 고정한다(`offset = max(0, int(days_remaining))`).

### 3.4 공개 함수

```python
def compute_forecast(connection, *, window_days=DEFAULT_WINDOW_DAYS) -> dict:
    """returns {"params": {...}, "summary": {...}, "items": [ ... ]}"""

def set_forecast_params(connection, material_id, *, lead_time_days, reorder_cycle_days) -> None
```

`items[]` 각 원소:
`{material_id, name, category, unit, stock_quantity, avg_daily, consumed_in_window,
  window_days, lead_time_days(유효값), reorder_cycle_days(유효값), days_remaining,
  predicted_stockout_date, reorder_point, recommended_order_qty, status}`

`summary`: `{total_materials, urgent, soon, ok, no_data, reorder_recommended(=urgent+soon)}`

`params`: `{window_days, default_lead_time_days, default_reorder_cycle_days}` — UI가 적용된 분석기간·전역 기본값을 표시할 수 있도록 반환.

정렬: status 우선순위(urgent=0, soon=1, ok=2, no_data=3) → days_remaining 오름차순(None은 뒤).

## 4. API (forecast_routes.py)

`recipe_stats_routes.py`와 동일하게 `require_access_level("manager")` 의존성으로 라우터 전체를 보호.
`api.py`의 `build_router()`에 등록.

| Method | Path | 설명 | Body/Query |
|--------|------|------|------------|
| GET | `/forecast/materials` | 전체 자재 예측·발주 추천 | `window_days: int = Query(30, ge=7, le=365)` |
| GET | `/forecast/export` | 발주 추천서 CSV | `window_days`, `only_reorder: bool = False` |
| PATCH | `/materials/{material_id}/forecast-params` | 리드타임/커버리지 설정 | `ForecastParamsBody` |

`ForecastParamsBody` (models.py): `lead_time_days: float = Field(ge=0)`, `reorder_cycle_days: float = Field(ge=0)`.
PATCH는 `ensure_material` + `write_audit_log(action="material_forecast_params_set")` + `commit` (stock_routes PATCH 패턴 동일).

CSV 컬럼: `material_name, category, stock_quantity, avg_daily, days_remaining, predicted_stockout_date, recommended_order_qty, status`.
파일명 `irms-forecast-{today}.csv`. `only_reorder=true`면 status in (urgent, soon)만.

## 5. UI (management.html + forecast.js)

### 5.1 탭 추가
재고 탭 옆에 버튼 추가: `<button class="mgmt-tab" data-tab="forecast">소모예측·발주</button>`
패널 `<div class="tab-panel" id="tab-forecast">`.

### 5.2 패널 구성 (기존 .panel/.input/.filter-label/.btn 재사용, 자체 class 금지)
- 상단 요약 배너 `#forecast-banner`(`hidden` 속성 토글) — "발주 권장 N건 (긴급 X)".
- 필터 행: 분석기간 select(30/60/90), "발주 필요만 보기" 체크박스, 새로고침, "발주 추천서 내보내기" 버튼.
- 표: 원재료 / 카테고리 / 현재고 / 일평균 소모 / 예상 소진일 / 잔여일수 / 권장 발주량 / 상태 / 작업(설정).
- 상태 배지: `urgent`=긴급, `soon`=임박, `ok`=정상, `no_data`=데이터없음. 행 색상 강조는 기존 `row-negative`/`row-low` 클래스 재사용(urgent→negative, soon→low).
- 파라미터 설정: 기존 stock-modal 패턴의 별도 모달(`#forecast-modal`, `hidden`)에서 리드타임/커버리지 입력 → PATCH.

### 5.3 forecast.js
`stock.js`와 동일 구조(IIFE, `IRMS.notify`, escapeHtml, fmt). `fetch('/api/forecast/materials?window_days=N')` → render.
탭 클릭(`[data-tab="forecast"]`) 및 초기 1회 fetch로 배너 노출. CSV 버튼은 `window.location = '/api/forecast/export?...'`.

### 5.4 라우팅 등록 확인
`management.html`의 탭 전환 스크립트는 `data-tab`/`tab-panel` 규약 기반이므로 마크업만 추가하면 동작.
`<script src="/static/js/forecast.js">`를 stock.js 옆에 추가.

## 6. 권한·보안

- 모든 forecast 엔드포인트 manager 이상 (operator 차단) — Plan 성공기준 §5.
- 쓰기(PATCH)는 audit log 기록.
- 입력 검증: window_days/lead/cycle 범위 제한(Query ge/le, Pydantic Field ge=0).
- 읽기 전용 집계라 CSRF 무관(GET). PATCH는 기존 CSRF 미들웨어로 보호됨.

## 7. 테스트 설계 (tests/test_material_forecast.py)

| # | 시나리오 | 기대 |
|---|---------|------|
| 1 | 30일간 300g 소모, 현재고 100g | avg_daily=10, days_remaining=10, status=urgent(lead 7 기준 soon~urgent) |
| 2 | 소모 이력 없음 | status=no_data, recommended=0, days_remaining=None (0 나눗셈 없음) |
| 3 | 충분한 재고(현재고 ≫ 소모) | status=ok, recommended=0 |
| 4 | unit_type='count' 자재 | items에서 제외 |
| 5 | 리드타임/커버리지 PATCH 후 권장량 변화 | recommended_order_qty 갱신 |
| 6 | operator 권한 GET | 403 |
| 7 | CSV export only_reorder | urgent/soon 행만 포함 |
| 8 | 음수 재고 | status=urgent, recommended>0 |

## 8. 변경 파일 목록

| 파일 | 변경 |
|------|------|
| `src/db/schema.py` | materials CREATE에 컬럼 2개 |
| `src/db/migrations.py` | ensure_column 2개 |
| `src/services/forecast_service.py` | **신규** |
| `src/routers/forecast_routes.py` | **신규** |
| `src/routers/api.py` | forecast 라우터 등록 |
| `src/routers/models.py` | `ForecastParamsBody` |
| `templates/management.html` | 탭 버튼 + 패널 + 모달 + script |
| `static/js/forecast.js` | **신규** |
| `tests/test_material_forecast.py` | **신규** |

## 9. 회귀 방지

- 기존 `material_stock_logs`·`materials`는 **읽기만**. 차감 로직(stock_service) 무변경.
- 신규 컬럼은 DEFAULT 0으로 기존 행에 안전 적용(ensure_column 멱등).
- 라우터 추가만 하므로 기존 엔드포인트 영향 없음.
