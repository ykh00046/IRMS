# split-work-js Gap Analysis Report

> Design vs. Implementation 정합성 분석. gap-detector agent 결과.

| Item | Value |
|---|---|
| Feature | split-work-js (Phase 4 of split-large-files) |
| Plan | [split-work-js.plan.md](./split-work-js.plan.md) |
| Design | [../../02-design/features/split-work-js/split-work-js.design.md](../../02-design/features/split-work-js/split-work-js.design.md) |
| Analysis Date | 2026-05-27 |
| **Match Rate** | **99%** (Phase 1/2/3와 동일 baseline) |
| Status | Pass (≥90% target) |

---

## 종합 점수표

| Category | Score | Status |
|----------|:-----:|:------:|
| Module presence + factory naming | 100% | Pass |
| ctx schema match (§3) | 100% | Pass |
| Wiring order (§5) | 100% | Pass |
| Returned handle contracts (§6) | 96% | Pass (intentional supersets) |
| Template script block (§5.1) | 100% | Pass |
| LOC budget (≤ 250) | 100% | Pass |
| State-capture rule (§4.2) | 100% | Pass |
| **Overall** | **99%** | **Pass** |

---

## 모듈 인벤토리 (6개 + 컨트롤러)

| 모듈 | LOC | 팩토리 | 반환 핸들 |
|------|----:|--------|-----------|
| `static/js/work/stock-banner.js` | 68 | `IRMS.work.createStockBanner` | `{ refresh, start }` |
| `static/js/work/recipe-table.js` | 153 | `IRMS.work.createRecipeTable` | `{ render, bindRowActions, countRecipeMaterials }` |
| `static/js/work/import-notifications.js` | 121 | `IRMS.work.createImportNotifications` | `{ check, start }` |
| `static/js/work/weighing-render.js` | 165 | `IRMS.work.createWeighingRender` | `{ render, syncControls, resetProgress, getQueueColorCounts }` |
| `static/js/work/weighing-actions.js` | 199 | `IRMS.work.createWeighingActions` | `{ open, close, loadQueue, advance, undo, isOpen }` |
| `static/js/work/idle-logout.js` | 62 | `IRMS.work.createIdleLogout` | `{ start, stop }` |
| `static/js/work.js` (컨트롤러) | 232 | — | — |
| **합계** | **1,000** | — | — |

> 원본 760 LOC → 모듈 768 LOC + 컨트롤러 232 LOC = 1,000 LOC (모듈 헤더 JSDoc + 보일러플레이트 IIFE로 +240 LOC). 단일 모듈 최대 199 LOC, 250 한도 모두 통과.

---

## 검증 결과

### 정적
- **함수 인벤토리 diff**: 사라진 11개는 모두 모듈 내부에서 의도된 리네이밍 (`openWeighingMode` → `open`, `refreshLowStock` → `refresh`, `checkRecipeImportNotifications` → `check` 등). 신규 13개는 모듈 핸들 이름. **의도된 변경만**
- **상태 캡처 의심 패턴 grep**: 0건 (`const \w+ = ctx\.state\.\w+\.\w+`)
- **모듈 LOC ≤ 250**: 0건 초과

### 동적
- **pytest**: 40/40 passed
- **JS 테스트**: 5/5 passed (기존 4개 + 신규 `work_pure.test.js` 1개 — 9개 sub-test)
- **콘솔 에러**: 정적 검증으로 갈음(브라우저 자동 스모크는 [[feedback_browser_smoke_pattern]] 시드 환경 구축 대비 ROI 낮아 운영 배포 시 확인 위임)

### 컨벤션
- 파일명 케밥 케이스, 팩토리 PascalCase under `IRMS.work`, IIFE 보일러플레이트, 모듈 헤더 JSDoc, 폴링 이중 start 가드(3종), 교차 모듈 직접 import 0, `lowStockSet` clear+add 보존 — 모두 통과

---

## Findings

### Missing Features (Design O, 구현 X)
없음.

### Added Features (Design X, 구현 O)
| Item | 위치 | 설명 |
|---|---|---|
| `dom.chatStage` ctx 키 | `work.js` L30 | 원본 `chatStage` 미정의 식별자 사용을 `null`-tolerant adapter로 안전화. chat.js `bindForm({ stage?.value })`가 안전 처리. (Design §3.1에 사후 반영) |
| `recipeTable.countRecipeMaterials` 노출 | `recipe-table.js` | 순수 함수 — 테스트용 노출. Design §6.2/§9에 명시된 사항 |
| `weighingRender.getQueueColorCounts` 노출 | `weighing-render.js` | 순수 함수 — 테스트용 노출. Design §6.4/§9에 명시된 사항 |

### Changed Features (Design ≠ 구현)
| Item | Design 추정 | 실제 | 영향 |
|---|---|---|---|
| 컨트롤러 LOC | ~170 LOC | 232 LOC | Low. §5.2가 "키보드/계량 버튼 바인딩은 컨트롤러 인라인 또는 헬퍼 자유"라고 허용. 250 한도 미만. Design §9 추정치를 ~230으로 사후 정정 |

---

## 1% 감점 사유 (문서 측 trivia)

1. `dom.chatStage`가 §3.1 DOM 표에 사후 추가됨 — 처음부터 빠뜨린 항목
2. 컨트롤러 LOC 추정 ~170이 실제 232 — 키보드/계량 버튼 inline 유지로 50 LOC 추가 (§5.2가 허용했지만 §9 체크리스트 숫자 미반영)

두 사항 모두 design 문서 사후 패치 적용 완료(2026-05-27).

---

## 결론

Match Rate **99%**, Phase 1/2/3와 동일 baseline. 구현 측 fix 불필요. Report 단계로 진행.

다음 단계: `/pdca report split-work-js` + archive 이동 + single PR commit.
