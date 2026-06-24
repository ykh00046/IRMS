# Design — weighing-variance-analysis

> Plan: docs/01-plan/features/weighing-variance-analysis.plan.md · 작성일 2026-06-18

## 1. 아키텍처 개요

```
계량(work.js) ── POST /api/weighing/step {actual_weight}
                     │  weighing_routes: recipe_items.actual_weight 저장 (선행)
                     ▼
              recipe_items(value_weight=목표, actual_weight=실측, measured_at)
                     ▲ 집계
/dashboard (manager) ──load──▶ dashboard.js (기간 필터)
        │ GET /api/dashboard/variance/summary
        │ GET /api/dashboard/variance/materials?limit=10
        │ GET /api/dashboard/variance/materials/{id}/recipes   ← 행 클릭
        ▼
   dashboard_routes (variance 3종, manager) ──▶ variance_service
```

가산적(additive) 설계 — 데이터 캡처·서비스·API는 선행 구현됨. 본 PDCA는 **UI 렌더 +
단위 테스트 + 문서**를 더해 기능을 완결한다. 신규 테이블·신규 엔드포인트 없음.

## 2. 데이터 모델 (선행, 회귀 검증 대상)

- `recipe_items.actual_weight REAL` — 계량 완료 시 운영자가 입력한 실측값(g). 미입력 가능(`NULL`).
- 부분 인덱스 `idx_recipe_items_actual_weight ON recipe_items(actual_weight) WHERE actual_weight IS NOT NULL`.
- **목표 = `value_weight`, 실측 = `actual_weight`, 편차 = 실측 − 목표.**
- 재고 차감은 실측 우선(`actual_weight ?? value_weight`) — weighing_routes 선행 반영.

## 3. 서비스 계층 — `variance_service` (선행, 본 PDCA에서 테스트로 고정)

| 함수 | 반환 핵심 | 규칙 |
|------|-----------|------|
| `variance_summary(conn, from_ts, to_ts)` | `measured_count`, `actual_count`, `coverage_pct`, `target_total_g`, `actual_total_g`, `deviation_total_g`, `deviation_pct`, `absolute_deviation_total_g` | 실측 NULL은 `value_weight`로 폴백 합산(총량 보존). 커버리지=actual/measured. |
| `top_material_variances(conn, from_ts, to_ts, limit=10)` | 자재별 `target_total_g`/`actual_total_g`/`deviation_g`/`deviation_pct`/`absolute_deviation_g` | `HAVING actual_count > 0` (실측 있는 자재만), `ORDER BY |편차| DESC`. |
| `material_variance_recipes(conn, material_id, from_ts, to_ts, limit=200)` | 레시피별 `target_weight_g`/`actual_weight_g`/`deviation_g`/`deviation_pct` | `actual_weight IS NOT NULL`만, `ORDER BY |편차| DESC`. 편차율 목표=0이면 `null`. |

- 편차율 = `deviation / target * 100`, **목표=0이면 `null`**(0분모 방지) — `_deviation_fields` 공통.
- 순수 읽기. 정렬·필터는 SQL에 위임.

## 4. API (선행) — `GET /api/dashboard/variance/*`

| 엔드포인트 | 쿼리 | 응답 |
|-----------|------|------|
| `/variance/summary` | `from`, `to` | `{range, measured_count, actual_count, coverage_pct, target_total_g, actual_total_g, deviation_total_g, deviation_pct, absolute_deviation_total_g}` |
| `/variance/materials` | `from`, `to`, `limit(1..100, 기본10)` | `{range, items:[{material_id, material_name, category, measured_count, actual_count, target_total_g, actual_total_g, deviation_g, deviation_pct, absolute_deviation_g}]}` |
| `/variance/materials/{id}/recipes` | `from`, `to` | `{range, material_id, material_name, recipes:[{recipe_id, product_name, ink_name, measured_at, measured_by, target_weight_g, actual_weight_g, deviation_g, deviation_pct}]}` · 없는 자재 404 |

- 라우터 prefix `/dashboard`, dependency `require_access_level("manager")` 상속.
- 기간 파싱은 기존 `_parse_range`(기본 최근 7일) 재사용 → 다른 대시 카드와 일관.

## 5. 프런트엔드 (본 PDCA 신규 — 차트 기반 구조로 확정)

> 구현 합의: 편차 시각화는 **막대 차트 + 요약표 + 드릴다운 모달** 조합(다른 대시보드 차트
> 패널과 일관). 표 단독안에서 차트안으로 격상. 모든 문구는 한국어(메모리 `feedback_korean_ui`).

### 5.1 요약 카드 (dashboard-cards)
기존 4개 카드 옆에 2개 추가 — 진입 즉시 핵심 수치 인지(G1):

| 카드 | id | 출처 |
|------|----|------|
| 실측 커버리지 (%) | `card-actual-coverage` | summary.coverage_pct |
| 총 편차 (g) | `card-variance-total` | summary.deviation_total_g |

### 5.2 편차 패널 (dashboard-grid)
`담당자별 실적` 다음 행에 2분할 패널:

- **계량 편차 (목표 대비 실측) TOP 10** — 가로 막대 차트 `<canvas id="chart-variance">`,
  값 = `absolute_deviation_g`. 막대 클릭 → 해당 자재 드릴다운(G2).
- **편차 요약** — 요약표(계량 건수/실측 입력/목표합/실측합/|편차| 합):
  `variance-measured-count`, `variance-actual-count`, `variance-target-total`,
  `variance-actual-total`, `variance-abs-total`.

### 5.3 드릴다운 모달 (`variance-modal`)
기존 `material-modal` 패턴 답습. 컬럼: 제품명·잉크명·목표(g)·실측(g)·편차(g)·편차율(%)·담당자·계량일시.
`/variance/materials/{id}/recipes` 응답을 렌더. 실측 0건이면 `empty-state` 안내.

### 5.4 dashboard.js 통합
- `loadAll(range)`의 `Promise.all`에 `/variance/summary` + `/variance/materials?limit=10`를 합류
  → 프리셋/적용/새로고침 기간 필터에 자동 연동(G3). 읽기 전용 GET → CSRF 불필요.
- `renderSummary(summary, varianceSummary)`가 2개 신규 카드 채움.
- `renderVarianceSummary(data)` 요약표, `renderVariances(data)` 차트(클릭 핸들러로 드릴다운).
- `openVarianceDrill(id, name)` 모달 렌더. 편차율 `null`이면 `-` 표기.
- `fetchJSON` 실패 시 `loadAll` catch에서 한국어 토스트(노이즈 최소).

### 5.5 CSS
신규 class **불필요** — 기존 `.dashboard-cards`/`.metric-card`/`.dashboard-grid`/
`.dashboard-chart-panel`/`.chart-wrap`/`.table-wrap`/`.empty-state`/`.ss-modal-*` 전부 재사용.
편차 강조 색상은 차트 막대 색(`#dc2626`)으로 대체(표/카드는 부호 포함 숫자).

## 6. 권한·보안

- manager dependency 상속 → operator/비인증 401·403.
- GET 전용, 부작용 없음 → CSRF 무관.
- 출력은 `escapeHtml`로 XSS 방지(dashboard.js 동일 패턴).

## 7. 테스트 시나리오 (`tests/test_weighing_variance.py`)

in-memory SQLite로 `recipe_items`/`recipes`/`materials` 최소 스키마 구성(기존 테스트 패턴 답습).

| ID | 시나리오 | 기대 |
|----|----------|------|
| V1 | summary — 실측 일부만 입력 | `coverage_pct` 정확, 실측 NULL은 목표로 폴백 → `target_total`=`actual_total`(미실측분), `deviation_total`=실측분 편차 합 |
| V2 | summary — 실측 0건 | `actual_count=0`, `coverage_pct=0`, `deviation_total_g=0` |
| V3 | summary — 편차율 | 목표합 > 0 → `deviation_pct` 계산, 부호 보존 |
| V4 | materials — \|편차\| 내림차순 + 실측 0 자재 제외 | 실측 있는 자재만, `absolute_deviation_g` DESC 정렬 |
| V5 | materials — limit 적용 | 상위 N건만 반환 |
| V6 | recipes — 자재별 목표/실측/편차 | 실측 입력 행만, `deviation_g`/`deviation_pct` 정확, \|편차\| DESC |
| V7 | recipes — 목표 0 → 편차율 null | `target_weight_g=0`이면 `deviation_pct is None` |
| V8 | 라우트 권한 | 비인증 `/api/dashboard/variance/summary` → 401/403 |
| V9 | 라우트 404 | 없는 material_id `/variance/materials/999/recipes` → 404 (인증 환경) |

## 8. 회귀 방지

- variance_service 시그니처/반환 불변(읽기 재사용만, 테스트로 고정).
- dashboard_routes 기존 엔드포인트 무변경(variance 3종은 선행 추가분).
- weighing_routes의 actual_weight 저장은 선행 — 본 PDCA는 UI/테스트만 추가, 계량 로직 무변경.
- dashboard.html/js/css는 append-only(기존 카드·차트·모달 무변경).
