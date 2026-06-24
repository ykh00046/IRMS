# Gap 분석 — weighing-variance-analysis

> Design: docs/02-design/features/weighing-variance-analysis.design.md · 분석일 2026-06-18
> 방식: 설계 항목 ↔ 구현 코드/테스트 대조 (PDCA Check)

## 1. 종합

| 항목 | 결과 |
|------|------|
| **Match Rate** | **99%** |
| 설계 항목 | 18 |
| 충족 | 18 (자동화 1건은 대체 검증) |
| Python 테스트 | 206 passed, 10 subtests, 1 warning |
| JavaScript 테스트 | 5 passed |
| 회귀 | 0 |

## 2. 설계 ↔ 구현 대조

| # | 설계 (design §) | 구현 | 상태 |
|---|-----------------|------|:----:|
| 1 | `actual_weight` 컬럼·부분 인덱스 (§2) | schema.py / migrations.py | ✅ |
| 2 | `variance_summary` 폴백·커버리지·편차 (§3) | variance_service.py | ✅ (V1·V2·V3) |
| 3 | `top_material_variances` \|편차\|DESC·실측0 제외 (§3) | variance_service.py | ✅ (V4·V5) |
| 4 | `material_variance_recipes` 편차·편차율·목표0→null (§3) | variance_service.py | ✅ (V6·V7·V9) |
| 5 | `/variance/summary` (§4) | dashboard_routes.py | ✅ |
| 6 | `/variance/materials?limit` (§4) | dashboard_routes.py | ✅ |
| 7 | `/variance/materials/{id}/recipes` 404 (§4) | dashboard_routes.py | ✅ |
| 8 | manager 전용 권한 (§6) | 라우터 dependency | ✅ (V8) |
| 9 | 요약 카드 2종 (커버리지·총편차) (§5.1) | dashboard.html `card-actual-coverage`/`card-variance-total` + renderSummary | ✅ |
| 10 | 편차 차트 TOP10 (§5.2) | `chart-variance` + renderVariances | ✅ |
| 11 | 편차 요약표 (§5.2) | `variance-*` 행 + renderVarianceSummary | ✅ |
| 12 | 드릴다운 모달 (§5.3) | `variance-modal` + openVarianceDrill | ✅ |
| 13 | 기간 필터 연동 (§5.4, G3) | loadAll Promise.all 합류 | ✅ |
| 14 | 읽기 전용 GET·CSRF 무관 (§6) | fetch GET, 쓰기 경로 없음 | ✅ |
| 15 | XSS 방지 escapeHtml (§6) | 모든 출력 escapeHtml | ✅ |
| 16 | 빈 상태 안내(노이즈 제로) (G4) | empty-state + 카드 `-` | ✅ |
| 17 | 한국어 UI 통일 (§5, 메모리) | html/js 전체 한국어 현지화 | ✅ |
| 18 | CSS 신규 class 불필요 (§5.5) | 기존 class 재사용, dashboard.css 무변경 | ✅ |

## 3. 갭 상세

### G-1 (경미) 라우터 404 자동 테스트 부재
- **현상**: `/variance/materials/{없는 id}/recipes` → 404는 manager 인증 세션이 필요하나,
  현 테스트 인프라(test_forecast_dashboard_alert / test_order_sheet_erp 동일)는 manager 세션
  생성 헬퍼가 없어 **비인증 401/403만** 자동 검증한다.
- **대체 검증**: 서비스 레벨 `material_variance_recipes(unknown)→[]`(V9) + 라우트 비인증 차단(V8)으로
  분기 양끝을 고정. 404 분기는 QA(아래 QA 리포트)에서 수기 확인.
- **영향**: 낮음(404는 기존 `/materials/{id}/recipes`와 동일 패턴, 회귀 위험 미미).

## 4. 설계 변경 이력 (구현 중 합의)

- §5 프런트엔드를 **표 단독 → 차트+요약표+모달** 구조로 격상(다른 대시 차트 패널과 일관).
  설계 문서에 반영 완료(§5.1~5.5). 부호 색상 CSS는 차트 막대 색으로 대체 → dashboard.css 무변경.
- 대시보드 문구는 한국어로 통일(메모리 `feedback_korean_ui`).

## 5. 판정

Match Rate 99% (≥90%) → **Report 단계로 진행**. 자동화 갭 G-1은 QA 수기 확인으로 보완.
