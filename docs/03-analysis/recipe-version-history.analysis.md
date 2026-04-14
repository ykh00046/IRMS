# Recipe Version History — Gap Analysis (PDCA Check)

> Design: `docs/02-design/features/recipe-version-history.design.md`
> Plan: `docs/01-plan/features/recipe-version-history.plan.md`
> Date: 2026-04-15
> **Match Rate: 98%** — PASS

## 1. Scores

| Category | Score | Status |
|---|:---:|:---:|
| Design Match | 100% | PASS |
| Architecture Compliance | 100% | PASS |
| Convention Compliance | 98% | PASS |
| **Overall** | **98%** | **PASS** |

## 2. Backend — `src/routers/recipe_routes.py`

| Design Item | Status | Evidence |
|---|---|---|
| `_find_chain_root` with cycle guard | PASS | lines 212–226 |
| `_fetch_chain` recursive CTE (`ORDER BY created_at ASC, id ASC`) | PASS | lines 228–244 |
| `GET /api/recipes/{id}/history` (operator) | PASS | line 246 |
| Response `{root_id, current_id, items:[...]}` | PASS | lines 257, 277 |
| `version_label = v{1..N}` time-ordered | PASS | line 264 |
| `is_current` = max `created_at` | PASS | lines 259, 274 |
| `is_root`, `revision_of`, `remark`, `item_count`, `created_by`, `created_at` | PASS | lines 262–276 |
| 404 for unknown recipe | PASS | lines 249–251 |
| `GET /api/recipes/history/compare?ids=...` (operator) | PASS | lines 279–280 |
| ids validation: ≥2, ≤50, integer parse | PASS | lines 282–288 |
| Same-chain check → 400 `DIFFERENT_CHAINS` | PASS | lines 303–305 |
| `versions[]` ordered with labels from full chain | PASS | lines 311–331 |
| Union material_ids sorted by name | PASS | lines 333–345 |
| `change_status`: same / modified / partial | PASS | lines 374–379 |
| `values[]` with `value_weight`, `value_text`, `version_id` | PASS | lines 360–372 |

## 3. Frontend Template — `templates/management.html`

| Item | Status | Evidence |
|---|---|---|
| `#lookup-history-btn` | PASS | line 219 |
| `#history-modal` with title/subtitle/close | PASS | lines 279–286 |
| `#version-history-body` tbody | PASS | line 300 |
| `#compare-modal`, `#compare-thead`, `#compare-tbody` | PASS | lines 312–322 |

## 4. Frontend JS — `static/js/management.js`

| Item | Status | Evidence |
|---|---|---|
| `handleLookupHistory` → `/api/recipes/{id}/history` | PASS | lines 597–609 |
| `renderHistoryModal` (checkbox, label, current chip, clone) | PASS | lines 611–653 |
| Multi-selection (≥2 gating) | PASS | `updateCompareButtonState` 655–663 |
| `handleCompareVersions` → compare endpoint | PASS | lines 665–680 |
| `renderCompareModal` (sticky first col, status col) | PASS | lines 682–710 |
| "이 버전 복제" reuses `handleLookupClone` | PASS | lines 643–651 |
| `lookupHistoryBtn.disabled` on recipe selection | PASS | line 593 |

## 5. CSS — `static/css/management.css`

| Item | Status | Evidence |
|---|---|---|
| `.compare-table` base + sticky thead | PASS | lines 503–505 |
| `.compare-sticky` first-column | PASS | lines 506–507 |
| `.compare-modified` (yellow) / `.compare-partial` (blue) | PASS | lines 508–511 |

## 6. Plan Success Criteria

| # | Criterion | Status |
|---|---|---|
| 1 | Lookup 탭 → "버전 이력" → 전체 체인 표시 | PASS |
| 2 | 두 버전 선택 → 재료별 차이 표시 (3+ 지원) | PASS |
| 3 | 특정 버전 "복제" → 편집기 seed | PASS |
| 4 | 현재 버전 배지 올바르게 표시 | PASS |

## 7. Differences

### Missing (Design O / Impl X)
**없음.**

### Added (Design X / Impl O) — 모두 non-breaking
- Compare 응답에 `display` 편의 필드 추가
- 추가 에러 코드: `INVALID_IDS`, `SOME_RECIPES_NOT_FOUND`
- History items에 `status` 필드 추가 (UI 상태 칩용)

### Changed
- Plan 문서의 `/history/diff?base=&target=` 제안은 Design 문서에서 `compare?ids=...`로 대체됨 (Q3 결정). 구현은 Design을 따름 — gap 아님.

## 8. Recommendation

Match Rate 98% — 90% 임계치 통과. Iterate 불필요.
**다음 단계**: `/pdca report recipe-version-history`로 완료 보고서 생성.

선택적 문서 보정(코드 gap 아님):
- Design Section 2 JSON 예시에 `display`, `status`, 추가 에러 코드 반영
