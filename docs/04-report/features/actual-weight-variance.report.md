# actual-weight-variance Completion Report

> **Status**: Complete
>
> **Project**: IRMS
> **Version**: local
> **Author**: Codex
> **Completion Date**: 2026-06-18
> **PDCA Cycle**: actual-weight-variance

## Executive Summary

### 1.1 Project Overview

| Item | Content |
|------|---------|
| Feature | actual-weight-variance |
| Start Date | 2026-06-18 |
| End Date | 2026-06-18 |
| Duration | 1 session |

### 1.2 Results Summary

| Metric | Result |
|--------|--------|
| Completion Rate | 100% |
| Match Rate | 98% |
| QA Verdict | PASS |
| Critical Issues | 0 |

### 1.3 Value Delivered

| Perspective | Content |
|-------------|---------|
| **Problem** | 목표량만 집계되어 실제 과다/과소 투입을 볼 수 없던 measurement-dashboard 후속 과제를 해결했다. |
| **Solution** | `actual_weight` 저장, 실측 기반 재고 차감 fallback, 편차 서비스/API/UI를 구현했다. |
| **Function/UX Effect** | 작업자는 계량 중 실제 투입량을 선택 입력할 수 있고, 관리자는 대시보드에서 실측 입력률과 자재별 편차 TOP 10을 확인한다. |
| **Core Value** | 계획 중심 사용량에서 실제 투입 기반 품질/원가 관리로 확장했다. |

## 1.4 Success Criteria Final Status

| # | Criteria | Status | Evidence |
|---|----------|:------:|----------|
| SC-1 | 전체 Python 테스트 통과 | Met | `pytest -q`: 197 passed |
| SC-2 | JS 테스트 통과 | Met | 5 direct Node scripts passed |
| SC-3 | 실측값 저장 | Met | `recipe_items.actual_weight`, weighing route update |
| SC-4 | 편차 API 제공 | Met | `/api/dashboard/variance/*` 3개 |
| SC-5 | 대시보드 UX 제공 | Met | cards/chart/summary/modal |

**Success Rate**: 5/5 criteria met (100%)

## 1.5 Decision Record Summary

| Source | Decision | Followed? | Outcome |
|--------|----------|:---------:|---------|
| [Plan] | nullable `actual_weight` | Yes | 기존 데이터와 작업 흐름을 보존 |
| [Plan] | blank 입력은 target fallback | Yes | 현장 속도 저하 방지 |
| [Design] | service layer for variance | Yes | 단위 테스트 가능한 집계 구현 |
| [Design] | actual value drives stock deduction when present | Yes | 실제 투입 기반 재고 정확도 개선 |

## 2. Related Documents

| Phase | Document | Status |
|-------|----------|--------|
| Plan | [actual-weight-variance.plan.md](../../01-plan/features/actual-weight-variance.plan.md) | Finalized |
| Design | [actual-weight-variance.design.md](../../02-design/features/actual-weight-variance.design.md) | Finalized |
| Check | [actual-weight-variance.analysis.md](../../03-analysis/features/actual-weight-variance.analysis.md) | Complete |
| QA | [actual-weight-variance.qa-report.md](../../05-qa/actual-weight-variance.qa-report.md) | PASS |

## 3. Completed Items

| ID | Requirement | Status | Notes |
|----|-------------|--------|-------|
| FR-01 | 실제 투입량 저장 | Complete | optional `actual_weight` |
| FR-02 | 실측 기반 재고 차감 | Complete | actual fallback target |
| FR-03 | 편차 요약 | Complete | coverage/net/absolute variance |
| FR-04 | 자재별/레시피별 분석 | Complete | chart + modal |
| FR-05 | 기존 데이터 호환 | Complete | nullable + COALESCE |

## 4. Files Changed

| Area | Files |
|------|-------|
| DB/API | `src/db/schema.py`, `src/db/migrations.py`, `src/routers/models.py`, `src/routers/weighing_routes.py`, `src/routers/dashboard_routes.py` |
| Service | `src/services/variance_service.py`, `src/services/recipe_helpers.py` |
| UI | `templates/work.html`, `templates/dashboard.html`, `static/js/dashboard.js`, `static/js/work.js`, `static/js/work/weighing-actions.js`, `static/js/work/weighing-render.js`, `static/js/common/api-stock.js`, `static/js/common/mappers.js`, CSS files |
| Tests | `tests/test_variance_service.py` |
| Docs | Plan, Design, Analysis, QA, Report |

## 5. Quality Metrics

| Metric | Target | Final | Status |
|--------|--------|-------|--------|
| Design Match Rate | >= 90% | 98% | PASS |
| Full Python Regression | pass | 197 passed | PASS |
| JS Module Tests | pass | 5 scripts passed | PASS |
| Critical Security Issues | 0 | 0 | PASS |

## 6. Incomplete Items

None for this PDCA scope.

## 7. Next PDCA Candidates

| Item | Priority | Rationale |
|------|----------|-----------|
| 편차 허용치 알림 | Medium | 실측 데이터가 쌓이면 threshold 기반 알림이 가능 |
| 저울 장비 자동 연동 | High | 실측 입력률을 높이고 수동 입력 오류를 줄임 |
| 편차 추세 리포트 | Medium | 장기 품질/원가 패턴 분석 |

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 1.0 | 2026-06-18 | Completion report created | Codex |
