# Measurement Dashboard Design

> Plan: `docs/01-plan/features/measurement-dashboard.plan.md`

## 0. Resolved Decisions

| # | Question | Decision |
|---|---|---|
| Q1 | 차트 라이브러리 | **Chart.js 로컬 번들** (`static/vendor/chartjs/chart.umd.min.js`) — 오프라인 현장용 |
| Q2 | 편차 분석 대체 | **시간당 계량 속도(throughput)** — 저울 실측 미연동 상태라 `actual_weight` 부재 |
| Q3 | 담당자별 실적 섹션 | **포함** — manager 전용 접근이므로 OK |
| Q4 | 기본 표시 기간 | **7일** |
| Q5 | 기간 필터 상한 | **제한 없음** — 소규모 공장 DB, 성능 여유 |

## 1. Data Model

DB 변경 **없음**. 기존 컬럼 활용:
- `recipes`: `id`, `product_name`, `status`, `created_by`, `completed_at`, `created_at`
- `recipe_items`: `recipe_id`, `material_id`, `value_weight` (목표 = 실제 계량값으로 사용), `value_text`, `measured_at`, `measured_by`
- `materials`: `id`, `name`, `category`

### 집계 기준
- **완료 레시피**: `recipes.status = 'completed'` AND `completed_at` 기간 내
- **계량 항목**: `recipe_items.measured_at IS NOT NULL` AND `measured_at` 기간 내
- **재료 사용량**: `SUM(value_weight)` (measured 항목만)
- **시간당 계량 속도**: 기간 내 `measurement_count / active_hours`
  - `active_hours` = 계량이 발생한 일자별 `(max(measured_at) - min(measured_at)).total_seconds() / 3600`의 합 (일자별 세션 시간 근사)
  - 일자별로 끊어 계산 → 근무 외 시간 왜곡 방지

## 2. API Endpoints

모두 **manager 권한 필수**. 공통 쿼리 파라미터: `from=YYYY-MM-DD`, `to=YYYY-MM-DD` (both inclusive).
- 생략 시 기본값: 최근 7일 (오늘 포함)
- `from > to` → 400 `INVALID_RANGE`

### 2.1 `GET /api/dashboard/summary`

```json
{
  "range": { "from": "2026-04-09", "to": "2026-04-15" },
  "completed_recipe_count": 42,
  "measurement_count": 156,
  "total_weight_g": 28450.5,
  "throughput_per_hour": 12.3
}
```

### 2.2 `GET /api/dashboard/materials?limit=10`

```json
{
  "range": {...},
  "items": [
    { "material_id": 7, "material_name": "PL-835-1", "category": "수지", "total_weight_g": 3200.0, "measurement_count": 24 }
  ]
}
```

정렬 `total_weight_g DESC`, limit 기본 10 / 상한 100.

### 2.3 `GET /api/dashboard/materials/{material_id}/recipes`

드릴다운. 해당 재료가 쓰인 기간 내 계량 항목 목록.

```json
{
  "material_id": 7,
  "material_name": "PL-835-1",
  "recipes": [
    { "recipe_id": 58, "product_name": "...", "measured_at": "...", "weight_g": 32.0, "measured_by": "홍길동" }
  ]
}
```

### 2.4 `GET /api/dashboard/throughput`

```json
{
  "range": {...},
  "total_measurements": 156,
  "total_active_hours": 12.7,
  "throughput_per_hour": 12.28,
  "by_day": [
    { "date": "2026-04-09", "measurement_count": 20, "active_hours": 1.8, "throughput_per_hour": 11.11 }
  ]
}
```

### 2.5 `GET /api/dashboard/trend`

```json
{
  "range": {...},
  "points": [
    { "date": "2026-04-09", "completed_count": 5, "total_weight_g": 3420.0 }
  ]
}
```

- `completed_count` 기준: `recipes.completed_at` 일자
- `total_weight_g`: 해당 일자에 measured된 `recipe_items.value_weight` 합
- 빈 날짜는 0으로 채움

### 2.6 `GET /api/dashboard/operators`

```json
{
  "range": {...},
  "items": [
    { "operator": "홍길동", "measurement_count": 45, "total_weight_g": 8200.0, "completed_recipe_count": 12 }
  ]
}
```

- `operator` = `recipe_items.measured_by`, 미기록 시 "(미기록)"
- 정렬 `measurement_count DESC`

## 3. UI — 신규 `/dashboard` 페이지

### 3.1 라우트
- `GET /dashboard` → `templates/dashboard.html`, **manager 권한**. operator는 `/status`로 리다이렉트.
- `_base_app.html` 상속, 네비게이션에 "대시보드" 링크(manager만 노출).

### 3.2 레이아웃

```
┌─ 계량 대시보드 ──────────────────── [새로고침] ┐
│ [오늘] [7일✓] [30일] [기간: ___ ~ ___]         │
├──────────────────────────────────────────────────┤
│ ┌────┐ ┌────┐ ┌────┐ ┌────┐                      │
│ │완료│ │계량│ │총량│ │속도│                      │
│ └────┘ └────┘ └────┘ └────┘                      │
├──────────────────────────────────────────────────┤
│ ┌─ 일자별 추이 ───┐ ┌─ 재료 TOP 10 ──┐            │
│ │ 라인 차트      │ │ 바 차트         │            │
│ └────────────────┘ └─────────────────┘            │
├──────────────────────────────────────────────────┤
│ ┌─ 일자별 시간당 속도 ┐ ┌─ 담당자별 실적 ────┐    │
│ │ 막대 차트           │ │ 표(건수/총량/완료) │    │
│ └─────────────────────┘ └─────────────────────┘    │
└──────────────────────────────────────────────────┘
```

- 재료 바 클릭 → 드릴다운 모달(`/api/dashboard/materials/{id}/recipes`)

### 3.3 기간 필터
- 프리셋(오늘/7일/30일) + date input 2개
- 변경 시 5개 API 병렬 fetch → 각 섹션 갱신
- localStorage에 마지막 기간 저장

## 4. Frontend Implementation

### 4.1 신규 파일
- `templates/dashboard.html`
- `static/js/dashboard.js`
- `static/css/dashboard.css`
- `static/vendor/chartjs/chart.umd.min.js`

### 4.2 수정 파일
- `src/routers/dashboard_routes.py` (신규)
- `src/routers/api.py` — include dashboard router
- `src/routers/pages.py` — `/dashboard` 페이지 라우트
- `templates/_base_app.html` — manager 네비 링크

### 4.3 Chart.js 활용
- 일자별 추이: `line` (이중 Y축: count/weight)
- 재료 TOP: `bar` (가로), 클릭 이벤트
- 일자별 속도: `bar` (세로)

### 4.4 데이터 흐름
```
로드 → 기본 7일
  → Promise.all([summary, trend, materials, throughput, operators])
  → 각 섹션 렌더 + Chart 인스턴스 생성
필터 변경 → 동일 병렬 fetch → Chart.update()
```

## 5. Backend Implementation

### 5.1 파일
- `src/routers/dashboard_routes.py` — manager 전용 라우터
- 권한: `require_access_level("manager")` 의존성

### 5.2 공통 헬퍼
```python
def parse_range(from_: str | None, to_: str | None) -> tuple[str, str, str, str]:
    # 기본 7일, YYYY-MM-DD validation
    # 반환: (from_date, to_date, from_ts, to_ts)
    # from_ts = 'YYYY-MM-DD 00:00:00', to_ts = 'YYYY-MM-DD 23:59:59'
```

### 5.3 시간당 속도 SQL (일자별 세션)
```sql
WITH day_stats AS (
  SELECT substr(measured_at, 1, 10) AS d,
         COUNT(*) AS cnt,
         MIN(measured_at) AS first_at,
         MAX(measured_at) AS last_at
  FROM recipe_items
  WHERE measured_at IS NOT NULL
    AND measured_at BETWEEN ? AND ?
  GROUP BY d
)
SELECT d, cnt, first_at, last_at FROM day_stats ORDER BY d
```

Python에서 `active_hours = max((last - first).total_seconds() / 3600, cnt / 60.0)` — 1건만 있거나 동시 계량으로 0시간이 되는 경우 `cnt/60` (1건당 1분 최소 가정)으로 하한.

## 6. Implementation Order

1. Backend: `dashboard_routes.py` 5개 엔드포인트 + include
2. Page route + 네비 메뉴
3. Chart.js vendor 번들
4. `dashboard.html` 템플릿
5. `dashboard.js`
6. `dashboard.css`
7. QA

## 7. Testing Plan

1. 권한: operator → 차단, manager → 접근
2. 기본 7일 로드, 5개 섹션 렌더
3. 프리셋/커스텀 기간 변경 시 갱신
4. 데이터 0건 기간 → "데이터 없음" 상태
5. 재료 드릴다운 모달
6. 차트 리사이즈 반응
7. `from > to` → 400

## 8. Risks

| 위험 | 완화 |
|------|------|
| Chart.js 60KB | 로컬 번들, manager 페이지에서만 로드 |
| `active_hours` 0 근사 오차 | `cnt/60` 하한 + 일자별 집계로 근무시간 왜곡 방지 |
| 담당자 실명 노출 | manager 전용 페이지 |
| 저울 실측 미연동 | 차후 `actual_weight` 컬럼 추가 시 편차 분석 feature를 별도 사이클로 진행 |
