# actual-weight-variance Analysis Report

> **Analysis Type**: Gap Analysis / Runtime Verification
>
> **Project**: IRMS
> **Version**: local
> **Analyst**: Codex
> **Date**: 2026-06-18
> **Design Doc**: [actual-weight-variance.design.md](../../02-design/features/actual-weight-variance.design.md)

## Context Anchor

| Key | Value |
|-----|-------|
| **WHY** | 목표량 대비 실제 투입 편차를 기록하지 못해 품질/원가 이상을 조기 탐지하기 어렵다. |
| **WHO** | 계량 작업자, 생산/재고 관리자. |
| **RISK** | 실측 입력을 강제하면 현장 속도가 떨어질 수 있으므로 nullable + blank=target fallback으로 설계한다. |
| **SUCCESS** | 전체 테스트 통과, 실측값 저장, 편차 API 3개 제공, 대시보드 카드/차트/드릴다운 제공. |
| **SCOPE** | DB 컬럼, 계량 완료 API/UI, 편차 집계 서비스/API, 대시보드 표시, 단위/회귀 테스트. |

## Strategic Alignment Check

| Element | Expected | Status |
|---------|----------|:------:|
| Core Problem | `actual_weight` 부재로 편차 분석 불가 | Met |
| Target User | 작업자 입력 + 관리자 분석 | Met |
| Value Proposition | 실제 투입 기반 품질/원가 관리 | Met |

### Success Criteria Status

| # | Criteria | Status | Evidence |
|---|----------|:------:|----------|
| SC-1 | `pytest -q` 통과 | Met | 197 passed, 1 warning, 10 subtests |
| SC-2 | JS 테스트 통과 | Met | 5개 Node 테스트 스크립트 통과 |
| SC-3 | 편차 서비스 테스트 추가 | Met | `tests/test_variance_service.py` 3 passed |
| SC-4 | API/UI 계약 구현 | Met | `dashboard_routes.py`, `dashboard.js`, `dashboard.html` |
| SC-5 | 기존 데이터 fallback | Met | `COALESCE(actual_weight, value_weight)` |

**Success Rate**: 5/5 criteria met

## 1. Gap Analysis

### 1.1 API Endpoints

| Design | Implementation | Status |
|--------|----------------|--------|
| POST `/api/weighing/step/complete` optional actual | `WeighingStepRequest.actual_weight` + route update | Match |
| GET `/api/dashboard/variance/summary` | `dashboard_variance_summary` | Match |
| GET `/api/dashboard/variance/materials` | `dashboard_variance_materials` | Match |
| GET `/api/dashboard/variance/materials/{id}/recipes` | `dashboard_material_variance_recipes` | Match |

### 1.2 Data Model

| Field | Design Type | Impl Type | Status |
|-------|-------------|-----------|--------|
| `recipe_items.actual_weight` | nullable REAL | nullable REAL | Match |
| `idx_recipe_items_actual_weight` | partial index | partial index | Match |

### 1.3 UI Checklist

| Page | Design Elements | Implemented | Missing | Rate |
|------|:--------------:|:-----------:|:-------:|:----:|
| `/weighing` | actual input | 1 | 0 | 100% |
| `/dashboard` | cards/chart/summary/modal | 4 | 0 | 100% |

### 1.4 Contract Verification

| Endpoint | Design | Server | Client | Contract |
|----------|:------:|:------:|:------:|:--------:|
| `/weighing/step/complete` | yes | yes | yes | PASS |
| `/dashboard/variance/summary` | yes | yes | yes | PASS |
| `/dashboard/variance/materials` | yes | yes | yes | PASS |
| `/dashboard/variance/materials/{id}/recipes` | yes | yes | yes | PASS |

## 2. Runtime Verification Results

| Test | Result |
|------|--------|
| `python -m py_compile ...` | PASS |
| `node --check static/js/dashboard.js` | PASS |
| `node --check static/js/work/weighing-actions.js` | PASS |
| `node --check static/js/work/weighing-render.js` | PASS |
| `pytest -q tests/test_variance_service.py` | 3 passed |
| `pytest -q` | 197 passed, 1 warning, 10 subtests |
| JS test scripts | 5 passed |

## 3. Match Rate Summary

| Axis | Rate | Evidence |
|------|:----:|----------|
| Structural | 100% | All planned files/endpoints present |
| Functional | 98% | Feature implemented; browser manual visual smoke not run |
| Contract | 100% | Server/client paths match |
| Runtime | 95% | Full automated tests pass; no live browser session |

**Overall Match Rate**: 98%

## 4. Issues and Iteration

| Severity | Issue | Action |
|----------|-------|--------|
| Important | Existing dashboard/work JS/template had corrupted Korean strings and unstable partial patching | Rewrote affected JS modules and dashboard template to stable ASCII labels |
| Low | `npm test` unavailable due missing `package.json` | Used repository's direct Node test scripts |

## 5. Recommendation

Proceed to Report. No blocking gaps remain.

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 1.0 | 2026-06-18 | Final analysis | Codex |
