# Status Operator View Plan

> Status 페이지에 당일 작업자별 진행 현황 섹션 추가

## 1. Overview

| Item | Detail |
|------|--------|
| Feature | status-operator-view |
| Priority | High |
| Base | Status 페이지 (위치별 보드 + 10초 자동 갱신) |
| Goal | 당일 작업을 시작한 작업자 기준으로 진행 현황, 현재 계량 대상, 재료 카테고리(액상/파우더) 표시 |

## 2. Problem Statement

현재 Status 페이지는 **위치(Position)별** 진행 현황만 제공한다.
매니저가 "누가 지금 뭘 하고 있는지", "누가 얼마나 진행했는지"를 파악하려면
개별 레시피 카드를 하나씩 확인해야 하며, 당일 기준 필터도 없다.

### Pain Points

1. **작업자 파악 불가** — 어떤 작업자가 어떤 레시피를 계량 중인지 한 눈에 안 보임
2. **당일 구분 없음** — 이전 날짜의 미완료 작업과 오늘 작업이 섞여 있음
3. **재료 유형 불명** — 작업자가 액상/파우더 중 어디를 진행 중인지 알 수 없음

## 3. Feature Items

### 3.1 당일 작업자별 진행 현황 섹션

| Item | Detail |
|------|--------|
| 목표 | 당일 계량을 시작한 작업자별로 진행률, 현재 계량 대상, 카테고리별 완료 수 표시 |
| 조건 | 당일 `measured_at`이 있는 작업자만 표시 (작업 시작한 사람만) |
| 위치 | Summary Grid 아래, Position Board 위 |
| 갱신 | 기존 10초 자동 갱신에 포함 |

### 3.2 작업자 카드에 표시할 정보

| 항목 | 데이터 소스 |
|------|-----------|
| 작업자명 | `recipe_items.measured_by` |
| 당일 완료 스텝 수 / 전체 담당 스텝 수 | `measured_at` 당일 기준 COUNT |
| 진행률 바 | 완료/전체 비율 |
| 현재 계량 중인 레시피 | 해당 작업자의 가장 최근 작업 레시피의 다음 미완료 항목 |
| 카테고리별 완료 수 | materials.category (안료, 첨가제, 미분류 등) 기준 집계 |
| 마지막 계량 시간 | `measured_at` MAX |

### 3.3 materials.category 활용

| Item | Detail |
|------|--------|
| 현재 | category 필드 존재하나 Status 페이지에서 미사용 |
| 변경 | API 응답에 category 포함, 프론트에서 카테고리별 집계 표시 |
| DB 변경 | 없음 (기존 필드 활용) |

## 4. Scope

### In Scope
- 당일 작업자별 집계 API (`GET /api/recipes/operator-progress`)
- Status 페이지에 작업자 섹션 UI
- 카테고리별 완료 수 표시
- 기존 10초 자동 갱신에 통합

### Out of Scope
- 작업자 개인 상세 페이지
- 날짜 범위 선택 (당일 고정)
- 작업자 성과 비교/랭킹

## 5. Dependencies

| Dependency | Status |
|------------|--------|
| recipe_items.measured_by | ✅ 존재 |
| recipe_items.measured_at | ✅ 존재 |
| materials.category | ✅ 존재 (seed: 안료, 첨가제, 미분류) |
| Status 페이지 자동 갱신 | ✅ 10초 폴링 |

## 6. Implementation Order

```
1. [Backend]  작업자별 당일 진행 현황 API
2. [Frontend] common.js API 함수 추가
3. [Frontend] status.html 작업자 섹션 마크업
4. [Frontend] status.css 작업자 카드 스타일
5. [Frontend] status.js 작업자 섹션 렌더링 + 자동 갱신 통합
```

## 7. Risk

| Risk | Impact | Mitigation |
|------|--------|------------|
| 당일 기준 시간대 | 중 | 서버 UTC 기준 date() 사용, 로컬 시간대 고려 필요 시 KST 변환 |
| 작업자 수 많을 때 레이아웃 | 저 | 작업 시작한 사람만 표시, 카드 그리드 자동 wrap |
| category 미분류 재료 | 저 | 자동 등록된 재료는 '미분류'로 표시 |
