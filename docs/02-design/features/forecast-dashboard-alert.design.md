# Design — forecast-dashboard-alert

> Plan: docs/01-plan/features/forecast-dashboard-alert.plan.md · 작성일 2026-06-01

## 1. 아키텍처 개요

```
/dashboard (manager) ──load──▶ dashboard.js
                                    │ GET /api/dashboard/forecast-alert
                                    ▼
                        dashboard_routes.build_router()
                                    │ forecast_service.forecast_alert(conn, limit=5)
                                    ▼
                        compute_forecast() ── material_stock_logs (idx_stock_logs_reason_created)
```

가산적(additive) 설계 — 기존 forecast 계산 로직(`compute_forecast`)을 **재사용**하고
얇은 요약 헬퍼만 신설한다. 신규 테이블 없음.

## 2. 서비스 계층 — `forecast_service.forecast_alert`

```python
def forecast_alert(
    connection: sqlite3.Connection,
    *,
    window_days: int = DEFAULT_WINDOW_DAYS,
    limit: int = 5,
) -> dict[str, Any]:
    """발주 임박(urgent+soon) 자재만 추려 대시보드 알림용으로 압축.

    compute_forecast()의 summary/items를 재사용한다. items는 이미
    urgent → soon → ok → no_data, days_remaining 오름차순 정렬이므로
    urgent/soon 필터 후 앞에서 limit개를 취한다.
    """
    full = compute_forecast(connection, window_days=window_days)
    summary = full["summary"]
    reorder = [
        {
            "material_id": it["material_id"],
            "name": it["name"],
            "category": it["category"],
            "unit": it["unit"],
            "status": it["status"],
            "days_remaining": it["days_remaining"],
            "predicted_stockout_date": it["predicted_stockout_date"],
            "recommended_order_qty": it["recommended_order_qty"],
        }
        for it in full["items"]
        if it["status"] in ("urgent", "soon")
    ]
    return {
        "window_days": window_days,
        "reorder_recommended": summary["reorder_recommended"],
        "urgent": summary["urgent"],
        "soon": summary["soon"],
        "shown": min(limit, len(reorder)),
        "items": reorder[:limit],
    }
```

- 순수 읽기. 정렬은 `compute_forecast`에 위임(중복 로직 금지).
- `limit`는 라우터에서 고정(5). 과다 직렬화 방지.

## 3. API — `GET /api/dashboard/forecast-alert`

| 항목 | 값 |
|------|----|
| 라우터 | `dashboard_routes.build_router()` (prefix `/dashboard`, manager 전용) |
| 쿼리 | `window_days: int = 30 (ge=7, le=365)` (forecast와 동일 범위) |
| 권한 | `require_access_level("manager")` (라우터 dependency 상속) |
| 응답 | `forecast_alert()` 반환 dict |

```python
@router.get("/forecast-alert")
async def dashboard_forecast_alert(
    window_days: int = Query(forecast_service.DEFAULT_WINDOW_DAYS, ge=7, le=365),
) -> dict[str, Any]:
    with get_connection() as conn:
        return forecast_service.forecast_alert(conn, window_days=window_days)
```

응답 예:
```json
{
  "window_days": 30,
  "reorder_recommended": 2,
  "urgent": 1,
  "soon": 1,
  "shown": 2,
  "items": [
    {"material_id": 7, "name": "BYK-199", "category": "첨가제", "unit": "g",
     "status": "urgent", "days_remaining": 3.0,
     "predicted_stockout_date": "2026-06-04", "recommended_order_qty": 270.0}
  ]
}
```

## 4. 마이그레이션 — 소비쿼리 인덱스 (Plan G4)

`apply_schema_migrations()`의 stock_logs 인덱스 블록에 1줄 추가:

```python
connection.execute(
    "CREATE INDEX IF NOT EXISTS idx_stock_logs_reason_created "
    "ON material_stock_logs(reason, created_at)"
)
```

근거: `_consumption_by_material`는 `WHERE reason='measurement' AND created_at >= ?`로
**material_id 조건 없이** 스캔한다. 기존 `idx_stock_logs_material(material_id, created_at)`는
선두 컬럼(material_id)이 묶이지 않아 사용되지 못한다. `(reason, created_at)`는 등치(reason) +
범위(created_at) 조합으로 이 쿼리에 정확히 들어맞는다. `IF NOT EXISTS`로 재실행 안전.

## 5. 프런트엔드

### 5.1 dashboard.html
필터 패널(`.dashboard-filter-panel`) 바로 다음, 카드 섹션 위에 알림 패널 추가. 기본 `hidden`.

```html
<section id="forecast-alert" class="panel" hidden>
  <div class="forecast-alert-head">
    <h3 class="panel-title">⚠ 발주 임박</h3>
    <a class="btn btn-sm" href="/management#forecast">소모예측·발주 상세 →</a>
  </div>
  <p id="forecast-alert-summary" class="muted small"></p>
  <div class="table-wrap">
    <table>
      <thead>
        <tr><th>자재</th><th>상태</th><th class="num">잔여일</th>
            <th>소진예상</th><th class="num">권장발주(g)</th></tr>
      </thead>
      <tbody id="forecast-alert-body"></tbody>
    </table>
  </div>
</section>
```

- 공통 `.panel`/`.table-wrap`/`.btn`/`.muted`/`.num` 재사용. 상태 배지는 forecast와 동일
  `.stock-status .stock-negative|low` 재사용.
- `hidden` 속성으로 숨김(메모리 `feedback_css_hidden`).

### 5.2 dashboard.js
페이지 로드 시 1회 fetch(읽기 전용 GET → CSRF 불필요). 기존 IIFE 패턴 답습.

```js
async function loadForecastAlert() {
  const card = document.getElementById("forecast-alert");
  if (!card) return;
  const res = await fetch("/api/dashboard/forecast-alert");
  if (!res.ok) return;            // 권한/오류 시 조용히 숨김 유지
  const d = await res.json();
  if (!d.reorder_recommended) { card.hidden = true; return; }
  // summary + 행 렌더 → card.hidden = false
}
```

- 상태 라벨: urgent="긴급", soon="임박". 한국어 문구.
- 0건이면 카드 미노출(G3, 노이즈 제로).

## 6. 권한·보안

- manager dependency 상속 → operator/비인증 401·403.
- GET 전용, 부작용 없음 → CSRF 무관.
- 출력은 `escapeHtml`로 XSS 방지(forecast.js와 동일 패턴).

## 7. 테스트 시나리오 (`tests/test_forecast_dashboard_alert.py`)

| ID | 시나리오 | 기대 |
|----|----------|------|
| A1 | urgent 1 + soon 1 + ok 1 → forecast_alert | reorder_recommended=2, items 2건, urgent 먼저 |
| A2 | 발주 임박 0건 | reorder_recommended=0, items=[] |
| A3 | limit 적용 | urgent 7건 중 limit=3 → items 3건, shown=3 |
| A4 | 정렬 | urgent가 soon보다 앞, 잔여일 오름차순 |
| A5 | 라우트 권한 | 비인증 `/api/dashboard/forecast-alert` → 401/403 |
| A6 | 인덱스 마이그레이션 | apply 후 `idx_stock_logs_reason_created` 존재 |

## 8. 회귀 방지

- compute_forecast 시그니처/반환 불변(읽기 재사용만).
- dashboard_routes 기존 엔드포인트 무변경(추가만).
- migrations는 append-only + IF NOT EXISTS.
