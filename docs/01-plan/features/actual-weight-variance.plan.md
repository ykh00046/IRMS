# actual-weight-variance Planning Document

> **Summary**: 계량 목표량과 실제 투입량의 편차를 저장하고 관리자 대시보드에서 분석한다.
>
> **Project**: IRMS
> **Version**: local
> **Author**: Codex
> **Date**: 2026-06-18
> **Status**: Final

## Executive Summary

| Perspective | Content |
|-------------|---------|
| **Problem** | 기존 measurement-dashboard PDCA에서 `actual_weight` 부재로 목표량 대비 실제 투입 편차 분석이 후속 과제로 남아 있었다. 현장에서는 과다/과소 투입 누적을 운영자가 빠르게 확인할 수 없었다. |
| **Solution** | `recipe_items.actual_weight`를 nullable 컬럼으로 추가하고, 계량 완료 시 선택적으로 실측값을 저장한다. 관리자 대시보드에는 편차 요약, 자재별 TOP 10, 레시피별 드릴다운을 추가한다. |
| **Function/UX Effect** | 작업자는 기존 Enter/Space 흐름을 유지하면서 필요 시 실제 투입량을 입력한다. 관리자는 `/dashboard`에서 실측 입력률, 총 편차, 절대 편차가 큰 자재를 즉시 확인한다. |
| **Core Value** | 자재 사용량 예측과 재고 차감이 목표량 중심으로만 움직이던 한계를 줄이고, 실제 투입 기반의 품질/원가 관리 루프를 만든다. |

## Context Anchor

| Key | Value |
|-----|-------|
| **WHY** | 목표량 대비 실제 투입 편차를 기록하지 못해 품질/원가 이상을 조기 탐지하기 어렵다. |
| **WHO** | 계량 작업자, 생산/재고 관리자. |
| **RISK** | 실측 입력을 강제하면 현장 속도가 떨어질 수 있으므로 nullable + blank=target fallback으로 설계한다. |
| **SUCCESS** | 전체 테스트 통과, 실측값 저장, 편차 API 3개 제공, 대시보드 카드/차트/드릴다운 제공. |
| **SCOPE** | DB 컬럼, 계량 완료 API/UI, 편차 집계 서비스/API, 대시보드 표시, 단위/회귀 테스트. |

## 1. Overview

### 1.1 Purpose

`measurement-dashboard`의 알려진 후속 과제였던 편차 분석을 실제 기능으로 완성한다.

### 1.2 Background

기존 대시보드는 `recipe_items.value_weight`를 실제 사용량처럼 집계했다. 이는 계획 사용량 추세에는 충분하지만, 실제 계량 오차나 현장 보정 투입량을 볼 수 없다.

### 1.3 Related Documents

- `docs/archive/2026-04/measurement-dashboard/report.md`
- `docs/archive/2026-04/measurement-dashboard/design.md`

## 2. Scope

### 2.1 In Scope

- [x] `recipe_items.actual_weight` nullable 컬럼과 인덱스 추가
- [x] 계량 완료 요청에 `actual_weight` 선택 필드 추가
- [x] 실제 투입량이 있으면 재고 차감에도 실측값 사용
- [x] 대시보드 편차 요약/자재별/레시피별 API 추가
- [x] 대시보드 카드, 차트, 드릴다운 추가
- [x] 서비스 단위 테스트 및 기존 회귀 테스트

### 2.2 Out of Scope

- 저울 장비 자동 연동
- 실측값 필수 입력 정책
- 편차 허용치별 알림/승인 워크플로

## 3. Requirements

| ID | Requirement | Priority | Status |
|----|-------------|----------|--------|
| FR-01 | 계량 스텝 완료 시 실제 투입량을 선택 입력으로 저장한다. | High | Done |
| FR-02 | 실제 투입량이 있으면 재고 차감량은 실제 투입량을 사용한다. | High | Done |
| FR-03 | 관리자 대시보드에서 실측 입력률, 총 편차, 절대 편차를 확인한다. | High | Done |
| FR-04 | 자재별 편차 TOP 10과 레시피별 드릴다운을 제공한다. | High | Done |
| FR-05 | 기존 실측값이 없는 데이터는 목표량 기준으로 계속 동작한다. | High | Done |

## 4. Success Criteria

- [x] `pytest -q` 통과
- [x] 기존 JS 테스트 통과
- [x] `node --check`로 변경 JS 문법 통과
- [x] 새 편차 서비스 테스트 3개 통과
- [x] 기존 계량 흐름에서 실측 미입력 시 동작 유지

## 5. Risks and Mitigation

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| 실측 입력이 현장 흐름을 느리게 함 | Medium | Medium | 선택 입력으로 두고 blank는 목표량으로 처리 |
| 기존 데이터에 실측값 없음 | Low | High | 집계에서 `COALESCE(actual_weight, value_weight)` 사용 |
| 재고 차감 기준 변경의 회귀 | High | Low | 실측값이 있을 때만 차감량 변경, 기존 stock tests 회귀 실행 |

## 6. Impact Analysis

| Resource | Type | Change Description |
|----------|------|--------------------|
| `recipe_items` | DB | `actual_weight REAL` nullable 추가 |
| `/api/weighing/step/complete` | API | request/response에 `actual_weight` 선택 필드 추가 |
| `/api/dashboard/variance/*` | API | 편차 집계 API 3개 추가 |
| `/dashboard` | UI | 편차 카드/차트/드릴다운 추가 |

## 7. Architecture Considerations

| Decision | Selected | Rationale |
|----------|----------|-----------|
| Architecture option | Option C: Pragmatic Balance | 서비스 함수로 집계를 분리하고 기존 라우터/UI 패턴을 재사용한다. |
| Data model | nullable `recipe_items.actual_weight` | 기존 데이터와 계량 흐름을 깨지 않는다. |
| Stock deduction | actual fallback target | 실제 투입량이 있으면 운영상 재고 정확도가 더 높다. |
| UI policy | Optional input | 저울 자동 연동 전까지 현장 속도를 보존한다. |

## 8. Next Steps

- [x] Design 작성
- [x] 구현
- [x] 분석/반복/QA/Report 완료

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 1.0 | 2026-06-18 | Initial final plan | Codex |
