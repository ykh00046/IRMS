# actual-weight-variance Design Document

> **Summary**: 실제 계량값 저장과 관리자 편차 분석 대시보드.
>
> **Project**: IRMS
> **Version**: local
> **Author**: Codex
> **Date**: 2026-06-18
> **Status**: Final
> **Planning Doc**: [actual-weight-variance.plan.md](../../01-plan/features/actual-weight-variance.plan.md)

## Context Anchor

| Key | Value |
|-----|-------|
| **WHY** | 목표량 대비 실제 투입 편차를 기록하지 못해 품질/원가 이상을 조기 탐지하기 어렵다. |
| **WHO** | 계량 작업자, 생산/재고 관리자. |
| **RISK** | 실측 입력을 강제하면 현장 속도가 떨어질 수 있으므로 nullable + blank=target fallback으로 설계한다. |
| **SUCCESS** | 전체 테스트 통과, 실측값 저장, 편차 API 3개 제공, 대시보드 카드/차트/드릴다운 제공. |
| **SCOPE** | DB 컬럼, 계량 완료 API/UI, 편차 집계 서비스/API, 대시보드 표시, 단위/회귀 테스트. |

## 1. Overview

### 1.1 Design Goals

- 기존 계량 완료 흐름을 유지한다.
- 실측값이 있을 때만 새 동작을 적용한다.
- 집계 로직은 라우터에서 분리해 테스트 가능하게 만든다.

## 2. Architecture Options

| Criteria | Option A: Minimal | Option B: Clean | Option C: Pragmatic |
|----------|:-:|:-:|:-:|
| Approach | 라우터에 SQL 직접 추가 | 도메인 계층 대분리 | 서비스 함수 + 기존 라우터 패턴 |
| New Files | 1 | 4+ | 2 |
| Modified Files | 6 | 10+ | 10 |
| Complexity | Low | High | Medium |
| Maintainability | Medium | High | High |
| Risk | Low | Medium | Low |
| Recommendation | 빠른 hotfix | 장기 재설계 | **Selected** |

**Selected**: Option C. 기존 FastAPI + SQLite 구조에서는 `src/services/variance_service.py`가 가장 작은 유지보수 경계다.

## 3. Data Model

```sql
ALTER TABLE recipe_items ADD COLUMN actual_weight REAL;
CREATE INDEX IF NOT EXISTS idx_recipe_items_actual_weight
ON recipe_items(actual_weight) WHERE actual_weight IS NOT NULL;
```

Rules:

- `actual_weight IS NULL`: 실측 미입력, 목표량(`value_weight`)을 집계 fallback으로 사용.
- `actual_weight >= 0`: Pydantic request validation.
- undo/reset 시 `actual_weight`도 함께 NULL 처리.

## 4. API Specification

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| POST | `/api/weighing/step/complete` | 계량 완료, 선택 `actual_weight` 저장 | operator |
| GET | `/api/dashboard/variance/summary` | 기간 편차 요약 | manager |
| GET | `/api/dashboard/variance/materials` | 자재별 편차 TOP N | manager |
| GET | `/api/dashboard/variance/materials/{material_id}/recipes` | 자재별 레시피 편차 상세 | manager |

### Response: variance summary

```json
{
  "measured_count": 3,
  "actual_count": 2,
  "coverage_pct": 66.67,
  "target_total_g": 170.0,
  "actual_total_g": 175.0,
  "deviation_total_g": 5.0,
  "absolute_deviation_total_g": 15.0
}
```

## 5. UI/UX Design

### Weighing Panel

- Input: `Actual weight (g)`, optional.
- Placeholder: target weight when available.
- Blank behavior: backend stores NULL and stock deduction falls back to target weight.

### Dashboard

- Cards: Actual coverage, Net variance.
- Chart: Actual vs target variance TOP 10.
- Table: measured steps, actual captured, target total, actual total, absolute variance.
- Modal: product, ink, target, actual, variance g, variance %, operator, measured at.

## 6. Error Handling

| Code | Cause | Handling |
|------|-------|----------|
| 400 | negative `actual_weight` | Pydantic validation |
| 404 | material not found | `MATERIAL_NOT_FOUND` |
| 401/403 | auth failure | existing auth dependency |

## 7. Security Considerations

- Existing operator/manager access boundaries retained.
- SQL uses parameter binding.
- Dashboard output escapes dynamic text through existing `IRMS.escapeHtml`.

## 8. Test Plan

| Type | Target | Tool | Status |
|------|--------|------|--------|
| L1 Unit | `variance_service` summary/top/detail | pytest | Passed |
| L1 Regression | stock/forecast/route-adjacent tests | pytest | Passed |
| L2 JS Static | dashboard/work modules syntax | node --check | Passed |
| L2 JS Pure | work module factory behavior | node scripts | Passed |
| L3 Full Regression | full Python suite | pytest | Passed |

## 9. Clean Architecture

| Component | Layer | Location |
|-----------|-------|----------|
| Variance calculations | Application service | `src/services/variance_service.py` |
| Dashboard routes | Presentation/API | `src/routers/dashboard_routes.py` |
| Weighing routes | Presentation/API | `src/routers/weighing_routes.py` |
| Dashboard UI | Presentation | `templates/dashboard.html`, `static/js/dashboard.js` |

## 10. Implementation Guide

### 10.1 File Structure

```text
src/services/variance_service.py
src/routers/dashboard_routes.py
src/routers/weighing_routes.py
templates/dashboard.html
static/js/dashboard.js
tests/test_variance_service.py
```

### 10.2 Implementation Order

1. DB migration and model field.
2. Weighing completion persistence and stock deduction fallback.
3. Variance service and dashboard API.
4. Dashboard UI and chart/drilldown.
5. Tests and PDCA reports.

### 10.3 Session Guide

| Module | Scope Key | Description | Estimated Turns |
|--------|-----------|-------------|:---------------:|
| DB/API | `module-1` | schema, model, route, service | 1 |
| UI | `module-2` | work panel, dashboard chart/modal | 1 |
| QA/docs | `module-3` | tests and PDCA docs | 1 |

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 1.0 | 2026-06-18 | Final design | Codex |
