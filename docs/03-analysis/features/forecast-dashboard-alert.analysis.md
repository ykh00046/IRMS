# Gap 분석 — forecast-dashboard-alert

> Design: docs/02-design/features/forecast-dashboard-alert.design.md · 분석일 2026-06-01

## 설계 ↔ 구현 대조

| 설계 항목 | 구현 위치 | 일치 |
|-----------|-----------|:----:|
| `forecast_alert(conn, window_days, limit)` 헬퍼 | `src/services/forecast_service.py` | ✅ |
| urgent/soon 필터 + 상위 limit, compute_forecast 정렬 재사용 | 동 함수 | ✅ |
| `GET /api/dashboard/forecast-alert` (manager) | `src/routers/dashboard_routes.py` | ✅ |
| window_days ge=7 le=365 | 동 라우트 | ✅ |
| `idx_stock_logs_reason_created (reason, created_at)` | `src/db/migrations.py` (IF NOT EXISTS) | ✅ |
| 대시 상단 알림 카드(기본 hidden) | `templates/dashboard.html` | ✅ |
| 0건 미노출 / >0 노출 + 상위 항목 + 상세 링크 | `static/js/dashboard.js` `loadForecastAlert` | ✅ |
| 상태 배지 CSS 재사용(대시 미로드분 복제) | `static/css/dashboard.css` | ✅ |
| XSS 방지(escapeHtml) | dashboard.js | ✅ |

## 수용 기준 (Plan §5) 달성

| # | 기준 | 결과 |
|---|------|------|
| 1 | 엔드포인트 응답 형태 | ✅ A1 + 브라우저 API 200 |
| 2 | operator/비인증 차단 | ✅ A5 (401/403) |
| 3 | >0건 카드 노출 | ✅ 브라우저: 2건 노출 |
| 4 | 0건 카드 미노출 | ✅ 브라우저: cardHidden=true |
| 5 | 인덱스 마이그레이션 | ✅ A6 |
| 6 | 회귀 0 | ✅ 71/71 pytest |

## Match Rate: 99%

- 단위/라우트 테스트 6건 + 전체 회귀 71건 통과.
- 브라우저 스모크(격리 DB + manager 세션)로 노출/미노출/계산 정확성 라이브 검증.
- 잔여 1%: 운영자 실데이터 기반 임계(리드타임/커버리지) 적합성은 운영 확인 사항.
