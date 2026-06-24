# IRMS 문서 인덱스

> 잉크 레시피 관리 시스템(IRMS) PDCA 문서 현황

## Active

| Feature | Match Rate | Phase | 문서 |
|---------|:----------:|-------|------|
| attendance-public-api-hardening (근태 인증·공개 API 보호) | 100% | PDCA 완료, QA PASS | [Plan](01-plan/features/attendance-public-api-hardening.plan.md) · [Design](02-design/features/attendance-public-api-hardening.design.md) · [Analysis](03-analysis/features/attendance-public-api-hardening.analysis.md) · [QA](05-qa/attendance-public-api-hardening.qa-report.md) · [Report](04-report/features/attendance-public-api-hardening.report.md) |
| weighing-variance-analysis (계량 편차 분석 대시보드) | ~99% | PDCA 완료 (215 tests passed), ⚠ HTML 언어 외부충돌 결정대기 | [Plan](01-plan/features/weighing-variance-analysis.plan.md) · [Design](02-design/features/weighing-variance-analysis.design.md) · [Analysis](03-analysis/features/weighing-variance-analysis.analysis.md) · [QA](05-qa/weighing-variance-analysis.qa-report.md) · [Report](04-report/features/weighing-variance-analysis.report.md) |
| forecast-dashboard-alert (운영 대시 발주 임박 알림) | ~99% | PDCA 완료 (71/71), 브라우저 스모크 검증 | [Plan](01-plan/features/forecast-dashboard-alert.plan.md) · [Design](02-design/features/forecast-dashboard-alert.design.md) · [Analysis](03-analysis/features/forecast-dashboard-alert.analysis.md) · [Report](04-report/features/forecast-dashboard-alert.report.md) |
| material-forecast (자재 소모량 예측·발주 추천) | ~99% | PDCA 완료, 운영자 파라미터 설정 대기 | [Plan](01-plan/features/material-forecast.plan.md) · [Design](02-design/features/material-forecast.design.md) · [Analysis](03-analysis/features/material-forecast.analysis.md) · [Report](04-report/features/material-forecast.report.md) |
| cloudflare-tunnel (외부 접속 + 보안 헤더 강화) | 98% | Report 완료, 운영자 sign-off 대기 | [Plan](01-plan/features/cloudflare-tunnel.plan.md) · [Design](02-design/features/cloudflare-tunnel.design.md) · [Analysis](03-analysis/features/cloudflare-tunnel.analysis.md) · [Report](04-report/features/cloudflare-tunnel.report.md) |

---

## Archive

| 월 | Feature | Match Rate | 문서 인덱스 |
|----|---------|------------|-------------|
| 2026-03 | irms (PoC 1차) | 92% | [archive index](archive/2026-03/_INDEX.md) |
| 2026-03 | irms-improvements (품질 개선) | 97.6% | [archive index](archive/2026-03/_INDEX.md) |
| 2026-05 | split-large-files (Phase 1 — Python) | 99% | [archive index](archive/2026-05/_INDEX.md) |
| 2026-05 | split-common-js (Phase 2 — JavaScript) | 99% | [archive index](archive/2026-05/_INDEX.md) |
| 2026-05 | split-management-js (Phase 3 — JavaScript) | 99% | [archive index](archive/2026-05/_INDEX.md) |
