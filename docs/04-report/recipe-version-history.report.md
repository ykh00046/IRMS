# Recipe Version History — Completion Report

> PDCA Cycle: Plan → Design → Do → Check → **Report**
> Feature: `recipe-version-history`
> Completed: 2026-04-15
> Match Rate: **98%** (PASS)

## 1. Overview

| Item | Detail |
|---|---|
| Goal | 레시피 자동 버전업 이력을 UI에서 조회 · 다중 버전 비교 · 특정 버전 복제 |
| Priority | Medium |
| Level | Dynamic |
| DB 변경 | 없음 (기존 `recipes.revision_of` 활용) |
| API 신규 | 2개 |
| UI 신규 | 모달 2개 (버전 이력, 버전 비교) |

## 2. Problem → Solution

**문제**: IRMS는 이미 `revision_of`로 자동 버전업 이력을 남기고 있었으나, UI에서 이를 볼 경로가 없었음. 책임자가 "어제 레시피는?" 확인하려면 SQL 직접 조회, 버전 간 재료/수량 차이 추적 불가, "예전 버전으로 되돌려달라" 요청에 수동 복제 필요.

**해결**: Management 탭의 "레시피 조회" 화면에 "버전 이력" 버튼 추가 → revision 체인 전체 나열 → N개 체크박스 선택 → 가로 비교 테이블 → 각 버전 "이 버전 복제"로 편집기 seed.

## 3. PDCA Phase Summary

### Plan (docs/01-plan/features/recipe-version-history.plan.md)
- 2개 API, Lookup 탭 확장, 버전 라벨 규칙(`v1, v2, ...`), "현재 버전" 판정
- Open Questions 3개: 체인 탐색 범위 / 독립 레시피 포함 여부 / 동시 비교 버전 수

### Design (docs/02-design/features/recipe-version-history.design.md)
- **Q1**: 순수 revision 체인만 (product 문자열 매칭 없음)
- **Q2**: 체인에 속한 것만 (independent recipes 제외)
- **Q3**: 3개 이상 동시 비교 지원 (가로 스크롤 + sticky 첫 컬럼)
- SQLite `WITH RECURSIVE` CTE로 체인 수집, `_find_chain_root` cycle guard
- `change_status`: same / modified / partial 분류 규칙

### Do
- 백엔드 (`src/routers/recipe_routes.py`)
  - `_find_chain_root(connection, recipe_id)` — cycle-safe root 탐색
  - `_fetch_chain(connection, root_id)` — 재귀 CTE, created_at ASC
  - `GET /api/recipes/{id}/history` — 체인 전체 + version_label + is_current
  - `GET /api/recipes/history/compare?ids=...` — N개 버전 재료별 비교
- 프론트 템플릿 (`templates/management.html`)
  - `#lookup-history-btn`, `#history-modal`, `#compare-modal`
- 프론트 JS (`static/js/management.js`)
  - `handleLookupHistory`, `renderHistoryModal`, `handleCompareVersions`, `renderCompareModal`
  - 체크박스 다중 선택 (2+), "이 버전 복제" → 기존 clone 로직 재사용
- CSS (`static/css/management.css`)
  - `.compare-table`, `.compare-sticky`, `.compare-modified` (노랑), `.compare-partial` (파랑)

### Check (docs/03-analysis/recipe-version-history.analysis.md)
- Match Rate: **98%**
- 누락 없음
- 추가(non-breaking): `display` 편의 필드, `INVALID_IDS`/`SOME_RECIPES_NOT_FOUND` 에러 코드, history items의 `status` 필드
- Plan Success Criteria 4개 전부 PASS

### Act
- Iterate 불필요 (98% >> 90% 임계치)

## 4. Files Changed

**수정만** (신규 파일 없음)
- `src/routers/recipe_routes.py` — helpers + 2개 신규 엔드포인트
- `templates/management.html` — 버튼 + 모달 2개
- `static/js/management.js` — 4개 핸들러 + 모달 close
- `static/css/management.css` — 비교 테이블 sticky/status 색상

## 5. Success Criteria Verification

| # | Criterion | Result |
|---|---|---|
| 1 | Lookup 탭 → "버전 이력" → 전체 체인 표시 | PASS |
| 2 | 두 버전 이상 선택 → 재료별 차이 표시 | PASS (3+ 지원) |
| 3 | 특정 버전 "복제" → 편집기 seed | PASS |
| 4 | 현재 버전 배지 표시 | PASS |

## 6. Key Learnings

1. **Design Q3 upgrade**: Plan에서는 2-버전 diff를 제안했으나 Q&A에서 "3개 이상 비교" 요구로 확장 → 재료 union + change_status 규칙으로 자연스럽게 일반화. 가로 스크롤 + sticky 첫 컬럼으로 UI도 안정적.
2. **재귀 CTE**: SQLite `WITH RECURSIVE`로 체인 수집이 간결하게 처리됨. Python에서 반복 SELECT 대비 성능/코드량 모두 우수.
3. **ID 충돌 발견**: 기존 history 탭 tbody id `history-body`가 새 모달 tbody와 충돌 → `version-history-body`로 rename. 큰 규모 템플릿에서는 네임스페이싱 필요.
4. **복제 로직 재사용**: 각 버전 행의 "이 버전 복제" 버튼이 기존 `handleLookupClone`을 그대로 호출 → 추가 구현 없이 버전 되돌리기 달성.

## 7. Non-breaking Extensions (설계 초과)

- Compare 응답의 `display` 필드 — UI 렌더 편의
- 세분화된 에러 코드 — 디버깅/UX 개선
- History items의 `status` 필드 — 체인 내 각 버전 진행 상태 시각화

## 8. Next Feature

- **#3 measurement dashboard** — 계량 결과 대시보드 (원재료별 누적 사용량, 편차 분석, 기간별 필터)
- **#4 Cloudflare Tunnel external access** — 공장 외부 접속 (책임자 모바일 모니터링)

권장: `/pdca plan measurement-dashboard`로 다음 사이클 시작.
